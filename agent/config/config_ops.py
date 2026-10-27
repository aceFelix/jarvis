"""配置的读写执行层：模型工具与终端命令共用同一条路径（阶段 12 P2）。

为什么单独成模块（aceFelix，2026-10-27）：`/config set` 与 `ConfigSet` 要做的事
六步完全一样——禁改兜底 → 逐项校验 → 剔掉"本来就是这个值" → 外科式落盘 →
改运行时并按 `apply_mode` 生效 → 记副作用账本并给出回执。

两处各写一份的代价是具体的（而不是审美问题）：一边补了 `notify_changed` 而另一边
忘了，桌面面板就只在"模型改"时刷新、人手改时不动；一边做了整批原子回写而另一边
没做，中途失败就留下半改状态。所以这里只留一条实现，两个入口都调它。

分界线：本模块只管"值怎么改对"；**要不要问用户**是权限层的事
（`ConfigSet.check_permissions` 直通 W1 管线，终端走 `/config set … --yes` 的两段式），
本模块的 `forbidden` 兜底是"权限层被绕过时也不放行"的最后一道。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.config.config_specs import (
    APPLY_LIVE,
    APPLY_POLICY,
    TIER_AUTO,
    TIER_CONFIRM,
    ConfigSpec,
    forbidden_reason,
    get_spec,
    human_value,
    list_specs,
    notify_changed,
    read_value,
    run_apply_hooks,
    save_value,
    validate_value,
)
from agent.core.actions.journal import (
    STATE_APPLIED,
    UNDO_CONFIG,
    EffectRecord,
    get_journal,
)

#: 一句人话里最多列几项（超出的截断，避免回执变成一屏）
_MAX_ROWS = 12
#: 账本里记的来源（模型调工具 / 人在终端敲）
ENTRY_MODEL = "model"
ENTRY_TERMINAL = "terminal"


@dataclass(frozen=True)
class ApplyOutcome:
    """一次改配置尝试的结果（两个入口拿同一份，各自决定怎么呈现）。"""

    ok: bool
    text: str
    changed: int = 0  # 真正落盘的项数（同一批里的无变更项不计）


# ---- 读：给模型与终端看的同一份清单 ----


def render_spec_list(settings: Any, *, tiers: tuple[str, ...] = ()) -> str:
    """渲染"能改哪些、现在是多少、要不要确认"的清单。

    Args:
        settings: 运行时 Settings（读当前值用）。
        tiers: 只看哪几档；空元组 = auto + confirm 全列（不含 forbidden）。

    Returns:
        多行文本；无项时返回一句"没有可调整的配置项"。
    """
    specs = list_specs(tiers=tiers or (TIER_AUTO, TIER_CONFIRM))
    if not specs:
        return "没有可调整的配置项"
    lines = [f"可调整的配置（{len(specs)} 项）："]
    for spec in specs:
        value = read_value(settings, spec)
        flag = "需确认" if spec.tier == TIER_CONFIRM else "可直接改"
        lines.append(
            f"- {spec.key}｜{spec.label}：{human_value(spec, value)}"
            f"（{flag}）—— {spec.hint}"
        )
    return "\n".join(lines)


# ---- 写：校验 → 落盘 → 生效 → 记账 → 回执 ----


def apply_config_patch(
    settings: Any,
    patch: dict[str, Any],
    *,
    workdir: str = "",
    permission_mode: str = "default",
    reason: str = "",
    entry: str = ENTRY_MODEL,
) -> ApplyOutcome:
    """按协议键改一项或多项配置；失败时如实说明**哪一步**没成。

    Args:
        settings: 运行时 Settings 实例（None 表示当前上下文改不了）。
        patch: ``{协议键: 新值}``；值必须是已解析好的原生类型。
        workdir / permission_mode: 账本上下文（撤销时用来定位）。
        reason: 为什么要改（写进账本，日后回溯）。
        entry: ``ENTRY_MODEL`` / ``ENTRY_TERMINAL``，进账本区分来源。

    Returns:
        ApplyOutcome：``ok`` 为假时 ``text`` 是给用户看的原因，不是异常。
    """
    if settings is None:
        return ApplyOutcome(False, "当前上下文没有配置实例，改不了配置")

    # ① 先把整批校验干净（含 forbidden 兜底：权限层被绕过时这里也不放行）
    pairs: list[tuple[ConfigSpec, Any, Any]] = []  # (spec, 新值, 原值)
    for key, raw in patch.items():
        k = str(key)
        why = forbidden_reason(k)
        if why:
            return ApplyOutcome(False, _forbidden_text(k, why, entry))
        spec = get_spec(k)
        if spec is None:
            return ApplyOutcome(False, f"不认识的配置项 {k}；{_list_hint(entry)}")
        try:
            value = validate_value(spec, raw)
        except ValueError as e:
            return ApplyOutcome(False, str(e))
        pairs.append((spec, value, read_value(settings, spec)))

    # ② 去掉"本来就是这个值"的项。不剔掉的话回执会写成"开 → 开"，还会在账本里
    # 记一条原值等于新值的变更——用户看回退列表时会发现一条"撤了等于没撤"。
    same = [sp for sp, new, old in pairs if new == old]
    pairs = [p for p in pairs if p[1] != p[2]]
    if not pairs:
        labels = "、".join(sp.label for sp in same)
        return ApplyOutcome(True, f"这几项本来就是这个值，不用改：{labels}")

    # ③ 落盘（任一失败就把已写的按原值回写，不留半改状态）
    failed = _save_batch(pairs)
    if failed is not None:
        return ApplyOutcome(False, f"写入 settings.toml 失败（{failed}），运行时未改动")

    # ④ 改运行时 + 按 apply_mode 分派生效，再广播变更
    for spec, value, _old in pairs:
        setattr(settings, spec.attr, value)
    apply_notes = _apply_modes(pairs, settings)
    # 广播放在生效之后：监听器万一要读状态，拿到的已是新值。
    notify_changed({spec.key: value for spec, value, _old in pairs})

    # ⑤ 记副作用账本（原值存进 undo_spec.before，/undo 据此回写）
    before = {spec.key: old for spec, _new, old in pairs}
    rec = EffectRecord(
        tool="ConfigSet",
        args={
            "patch": dict(patch),
            "reason": reason,
            "entry": entry,
            # 当时的上下文：偏好提炼的唯一原料。不记的话"你总是 22 点后调低音量"
            # 无从得出，只能靠猜——而靠猜造偏好正是用户最不想要的魔法。
            "context": _snapshot_context(),
        },
        preview=_render_changes(pairs),
        reversible=True,
        undo_spec={"kind": UNDO_CONFIG, "before": before},
        state=STATE_APPLIED,
        workdir=workdir,
        permission_mode=permission_mode,
    )
    recorded = get_journal().record(rec)

    tail = [f"已改 {len(pairs)} 项：", _render_changes(pairs)]
    if same:
        tail.append("另 " + "、".join(sp.label for sp in same) + " 本来就是这个值，未动")
    tail.extend(apply_notes)
    tail.append(
        f"回退：/undo（账本 {rec.id[:8]}）" if recorded
        else "注意：副作用账本已关闭，本次改动无法用 /undo 回退"
    )
    return ApplyOutcome(True, "\n".join(tail), len(pairs))


def parse_cli_value(spec: ConfigSpec, raw: str) -> Any:
    """把终端输入的一串字转成该配置项要的类型（随后仍走 ``validate_value``）。

    只做**类型转换**，不做范围/格式判定——那件事一处就够了（真源表）。
    转不动就原样返回，让 ``validate_value`` 给出统一的错误文案。

    布尔接受 ``true/false``、``on/off``、``开/关``、``是/否``、``1/0``：
    终端里敲中文比敲 true 更自然，而模型走的是 JSON 布尔，两不相干。
    """
    text = raw.strip()
    if spec.kind == "bool":
        low = text.lower()
        if low in {"true", "on", "1", "yes", "y", "开", "是", "启用", "打开"}:
            return True
        if low in {"false", "off", "0", "no", "n", "关", "否", "关闭", "禁用"}:
            return False
        return text
    if spec.kind == "int":
        try:
            return int(text)
        except ValueError:
            return text
    if spec.kind == "float":
        try:
            return float(text)
        except ValueError:
            return text
    if spec.kind == "list":
        # 逗号/分号分隔；空串与一对引号都表示**清空名单**（不是"没给值"，
        # "没给值"在命令层就已经被拦下了）。这里认引号是为了让
        # `render_cli_value` 的回显能原样解析回来（`/config set k \"\" --yes`）。
        if not text or text in {'""', "''"}:
            return []
        return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
    return text


def render_cli_value(spec: ConfigSpec, value: Any) -> str:
    """把值渲染成**可以再敲回去**的终端写法（`parse_cli_value` 的逆）。

    为什么预览不用原始输入串：用户敲的是 `A, B ,A`，回显“确认请再执行：
    `/config set k A, B ,A --yes`”会让人以为脏值会被存进去。回显规范化后的
    值有两个好处：复制粘贴回去干净，且能当场看到实际会写进文件的内容。
    """
    if spec.kind == "bool":
        return "true" if value else "false"
    if spec.kind == "list":
        items = [str(v) for v in (value or ())]
        return ", ".join(items) if items else '\"\"'
    if spec.kind == "int":
        return str(int(value))
    if spec.kind == "float":
        return f"{float(value):g}"
    return str(value)


# ---- 内部 helpers ----


def _snapshot_context() -> dict[str, Any]:
    """拍一张"改配置那一刻的上下文"（app + hour）给提炼用。

    故意做重事：不调 `current_context()` 里的前台窗口探测以外的东西，且拿不到就
    留空——空上下文的记录不会被提炼成偏好（`_common_when` 返回 None），这比
    "猜一个条件"安全得多。

    @author aceFelix
    """
    try:
        from agent.config import prefs

        ctx = prefs.current_context()
        return {"app": ctx.get("app") or "", "hour": int(ctx.get("hour", -1))}
    except Exception:  # noqa: BLE001 - 记账本不该让改配置失败
        return {}


def _forbidden_text(key: str, why: str, entry: str) -> str:
    """禁改项的拒绝文案：对模型与人说两句不同的话。"""
    if entry == ENTRY_MODEL:
        return f"{key} 不能由模型改：{why}。请让用户自己用 /init 或直接改 settings.toml。"
    return f"{key} 是禁改项：{why}。请直接编辑 ~/.jarvis/settings.toml。"


def _list_hint(entry: str) -> str:
    """"去哪儿看可用键"的指引（两个入口命令名不同）。"""
    return "/config list 可查可用键" if entry == ENTRY_TERMINAL else "先用 ConfigList 查可用键"


def _save_batch(pairs: list[tuple[ConfigSpec, Any, Any]]) -> str | None:
    """逐项落盘；任一项失败则把已写的按原值回写，返回失败项标签（成功返回 None）。"""
    written: list[tuple[ConfigSpec, Any]] = []
    for spec, value, old in pairs:
        if not save_value(spec, value):
            for done, before in written:
                save_value(done, before)
            return spec.label or spec.key
        written.append((spec, old))
    return None


def _apply_modes(pairs: list[tuple[ConfigSpec, Any, Any]], settings: Any = None) -> list[str]:
    """按 apply_mode 去重后逐个生效，返回给人看的说明行。

    两种生效途径，区分依据是“要不要宿主”：

    - ``policy``：打扰预算单例是**进程内**的，重建它就等于生效，终端 REPL 也做得到，
      所以在这里直接做——不需要宿主登记钩子。顺带一句：单例重建不会丢当日计数，
      ``NotifyBudget`` 的计数是落 ``notify_state.json`` 的。
    - ``percept`` / ``reschedule``：改的是**宿主装配出来的东西**，只有宿主知道怎么
      重新装配。宿主登记了钩子就当场生效；没登记（CLI）就如实说“重启后生效”——
      绝不能让用户以为已经生效了。
    """
    notes: list[str] = []
    seen: set[str] = set()
    for spec, _value, _old in pairs:
        if spec.apply_mode == APPLY_LIVE or spec.apply_mode in seen:
            continue
        seen.add(spec.apply_mode)
        if spec.apply_mode == APPLY_POLICY:
            notes.append(_rebuild_policy(settings))
            continue
        ok, lines = run_apply_hooks(spec.apply_mode)
        if ok:
            notes.extend(lines or ["已当场生效"])
        elif lines:
            # 钩子在但没成功：它自己的说明就是最准的原因（比如总开关关着、
            # 根本没有总线可重装配）。此时**不能**再拼一句“宿主未接线”——
            # 那是另一回事，两句放一起会让人去查本来就没坏的接线。
            notes.extend(f"已保存；{line}" for line in lines)
        else:
            notes.append("已保存；当前宿主没有接生效回调（终端场景），重启后端后生效")
    return notes


def _rebuild_policy(settings: Any) -> str:
    """重建打扰预算单例（``init_policy`` 顺手把 ``_budget_singleton`` 置空）。"""
    try:
        from agent.core.policy import init_policy

        init_policy(settings)
    except Exception as e:  # noqa: BLE001 - 生效失败要如实回执，不能谎报成功
        return f"已保存；打扰预算重建失败（{type(e).__name__}），重启后端后生效"
    return "打扰预算已按新参数当场重建（当日计数不清零）"


def _render_changes(pairs: list[tuple[ConfigSpec, Any, Any]]) -> str:
    """把"原值 → 新值"渲染成给人看的几行。"""
    rows = [
        f"- {sp.label}：{human_value(sp, old)} → {human_value(sp, new)}"
        for sp, new, old in pairs
    ]
    if len(rows) > _MAX_ROWS:
        rows = rows[:_MAX_ROWS] + [f"- …另 {len(pairs) - _MAX_ROWS} 项"]
    return "\n".join(rows)
