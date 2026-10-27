"""偏好账本（阶段 12 P3）：一次说过的"以后…"，下次还在。

`config_specs` 管的是"现在是什么值"，本模块管的是"在什么情况下该是什么值"。
两者的差别不是锦上添花：用户说「太吵了，音量小一点」是一次改动，说「以后我写代码时
别念出来」是一条**带条件的**约定——后者存进 settings.toml 就会变成永远生效，第二天
不写代码时也不出声了。

三条设计底线（都写进代码而不是只写在文档里）：

1. **偏好只能预授权 `auto` 域**。`confirm` / `forbidden` 项不许进偏好：否则
   「以后每天最多提醒 50 次」一句话就绕过了一道本该问人的确认，偏好会变成后门。
   要固化 `confirm` 项，请直接改配置（那会走一次确认）——一次性的事没必要带上条件。
2. **偏好由确定性代码执行，不靠模型自觉**。命中判定与施放都落在决策点上
   （目前是主动播报的出声闸门）。模型忘了它就等于没记住的"记忆"不算记忆。
3. **每条都能追回原话**。账本存 `said`（用户原话）、创建时间与命中次数，
   `/prefs` 随时可看可删——不制造"它为什么这样"的黑箱。

存储：``~/.jarvis/prefs.jsonl``，append-only，读时按 id 合并（与副作用账本同一体例：
最后一条为准）。这样"删一条"和"命中一次"都只是追加一行，不需要重写整个文件。

@author aceFelix
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from agent.config.config_specs import (
    TIER_AUTO,
    get_spec,
    human_value,
    validate_value,
)

#: 账本位置（模块级常量只存相对位置——`Path.home()` 必须在调用时求值，
#: 测试靠 patch 它指向 tmp_path，import 时就拼好路径会测不到）
_REL_PATH = (".jarvis", "prefs.jsonl")

#: 状态：活跃 / 待确认（提炼出来的候选，不生效）/ 已忘记
STATE_ACTIVE = "active"
STATE_SUGGESTED = "suggested"
STATE_REMOVED = "removed"

#: 有决策点能施放的键 → 施放在哪。新增键必须同时把它接进对应调用点：
#: 只加名字不接调用点，就是"能记但不生效"的假功能（比没有更糟）。
ENFORCEABLE_KEYS: frozenset[str] = frozenset({
    "proactive_tts_enabled",   # 主动播报出声闸门（proactive.notify_gate_action）
    "tts_volume",              # 播报音量（serve/hub 构造 TTS 时现算）
    "tts_speech_rate",         # 播报语速（同上）
})

#: 提炼候选的默认回看窗口（天）
SUGGEST_WINDOW_DAYS = 7

#: 一句偏好最多描述多长（用户原话存档，防手滑粘进一大段）
_MAX_SAID_CHARS = 200


def _ledger_path(path: Path | str | None) -> Path:
    """账本文件路径（显式传入优先，否则 ``~/.jarvis/prefs.jsonl``）。"""
    if path is not None:
        return Path(path)
    return Path.home().joinpath(*_REL_PATH)


@dataclass(frozen=True)
class When:
    """一条偏好的适用条件。空条件 = 无条件（总是生效）。

    三个维度都做"子串/前缀"宽松匹配而不是精确相等：进程名有 `Code.exe` /
    `Code - Insiders.exe`，工作目录有子目录——用户说的"写代码时"本来就是这个粒度。

    ``app`` 允许**逗号分隔多个**（`Code.exe,Qoder.exe`，任一命中即算）：
    "写代码时"实际上很少只用一个编辑器，而单值只能命中一个——记错了用户
    只会看到"命中 0 次"，却不知道为什么。
    """

    app: str = ""            # 前台进程名（不分大小写，含即算；多个用逗号分隔）
    workdir: str = ""        # 工作目录前缀（不分大小写）
    hour_from: int = -1      # 起始小时（含），-1 = 不限
    hour_to: int = -1        # 结束小时（含），-1 = 不限

    @property
    def is_empty(self) -> bool:
        """没有任何有效的条件。

        按 `app_names()` 而不是原串判：`app=" , "` 原串非空、却拆不出任何名字，
        存下去就是一条“永远不命中”的幽灵偏好（用户以为记了，其实从不生效）。
        当成空条件，上层才能用“必须带条件”把它拦住。
        """
        return (
            not self.app_names()
            and not self.workdir.strip()
            and self.hour_from < 0
            and self.hour_to < 0
        )

    def app_names(self) -> list[str]:
        """拆分后的程序名（去空、保序）；单个时就是它自己。"""
        return [p.strip() for p in self.app.replace("；", ",").split(",") if p.strip()]

    def matches(self, ctx: Mapping[str, Any]) -> bool:
        """是否命中当前上下文。不知道的条件一律不算命中（宁可不动）。"""
        if self.app:
            current = str(ctx.get("app") or "").lower()
            if not current:
                return False
            wanted = [n.lower() for n in self.app_names()]
            if not any(w in current for w in wanted):
                return False
        if self.workdir:
            current = str(ctx.get("workdir") or "").lower()
            want = self.workdir.lower()
            if not current or not (current == want or current.startswith(want)):
                return False
        if self.hour_from >= 0 or self.hour_to >= 0:
            try:
                hour = int(ctx.get("hour", -1))
            except (TypeError, ValueError):
                return False
            if hour < 0:
                return False
            lo = self.hour_from if self.hour_from >= 0 else 0
            hi = self.hour_to if self.hour_to >= 0 else 23
            if lo <= hi:
                return lo <= hour <= hi
            return hour >= lo or hour <= hi   # 跨午夜，如 22 点到 6 点
        return True

    def specificity(self) -> int:
        """越具体越优先：同时命中"写代码时"与"22 点后"时，先说出的那条更贴切。"""
        return (1 if self.app else 0) + (1 if self.workdir else 0) + (
            1 if self.hour_from >= 0 or self.hour_to >= 0 else 0
        )

    def describe(self) -> str:
        """人话条件（`/prefs` 与回执共用）。"""
        parts: list[str] = []
        if self.app:
            parts.append(f"在 {' / '.join(self.app_names())} 时")
        if self.workdir:
            parts.append(f"在 {self.workdir} 下")
        if self.hour_from >= 0 or self.hour_to >= 0:
            lo = self.hour_from if self.hour_from >= 0 else 0
            hi = self.hour_to if self.hour_to >= 0 else 23
            # 等值（提炼时常见的"同一个小时"）就写"22 点"；跨午夜写"次日"，
            # 否则「22 点到 07 点」读起来像倒着走
            if lo == hi:
                parts.append(f"{lo:02d} 点")
            elif lo > hi:
                parts.append(f"{lo:02d} 点到次日 {hi:02d} 点")
            else:
                parts.append(f"{lo:02d} 点到 {hi:02d} 点")
        return "、".join(parts) if parts else "任何时候"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.app:
            out["app"] = self.app
        if self.workdir:
            out["workdir"] = self.workdir
        if self.hour_from >= 0:
            out["hour_from"] = self.hour_from
        if self.hour_to >= 0:
            out["hour_to"] = self.hour_to
        return out

    @classmethod
    def from_any(cls, raw: Any) -> "When":
        """从 dict / None 还原；坏数据一律退化成"无条件"而不是抛异常。"""
        if isinstance(raw, When):
            return raw
        if not isinstance(raw, Mapping):
            return cls()
        def _int(key: str) -> int:
            try:
                return int(raw.get(key, -1))
            except (TypeError, ValueError):
                return -1
        return cls(
            app=str(raw.get("app") or "").strip(),
            workdir=str(raw.get("workdir") or "").strip(),
            hour_from=_int("hour_from"),
            hour_to=_int("hour_to"),
        )


@dataclass
class Preference:
    """一条偏好：``当 <when> 时，把 <key> 当成 <value>``。"""

    key: str
    value: Any
    when: When = field(default_factory=When)
    said: str = ""            # 用户原话（追溯用）
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: float = field(default_factory=time.time)
    hits: int = 0             # 真正改变了行为的次数
    state: str = STATE_ACTIVE

    def describe(self) -> str:
        spec = get_spec(self.key)
        label = spec.label if spec is not None and spec.label else self.key
        return f"{self.when.describe()} → {label} = {human_value(spec, self.value) if spec else self.value}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "value": self.value,
            "when": self.when.to_dict(),
            "said": self.said,
            "created_at": self.created_at,
            "hits": self.hits,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Preference | None":
        """容错还原：坏行直接丢（账本是可恢复的观测记录，不是权威数据）。"""
        key = str(data.get("key") or "").strip()
        if not key:
            return None
        return cls(
            key=key,
            value=data.get("value"),
            when=When.from_any(data.get("when")),
            said=str(data.get("said") or ""),
            id=str(data.get("id") or uuid.uuid4().hex[:8]),
            created_at=float(data.get("created_at") or 0.0),
            hits=int(data.get("hits") or 0),
            state=str(data.get("state") or STATE_ACTIVE),
        )


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------


def _read_rows(path: Path) -> list[dict[str, Any]]:
    """读全部行（坏行跳过）。文件不存在当成空账本。"""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _append(pref: Preference, path: Path) -> bool:
    """追加一行（整条快照）。写盘失败只返回 False，不抛——记账失败不该让行为失败。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(pref.to_dict(), ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def load_all(*, path: Path | str | None = None) -> list[Preference]:
    """按 id 合并后的全量偏好（含已忘记的，调用方自己按 state 过滤）。

    合并规则与副作用账本一致：同一 id 后写的整条覆盖先写的。
    """
    merged: dict[str, Preference] = {}
    for row in _read_rows(_ledger_path(path)):
        pref = Preference.from_dict(row)
        if pref is not None:
            merged[pref.id] = pref
    return list(merged.values())


def load_active(*, path: Path | str | None = None) -> list[Preference]:
    """活跃偏好（按创建时间先后）。"""
    items = [p for p in load_all(path=path) if p.state == STATE_ACTIVE]
    return sorted(items, key=lambda p: p.created_at)


def load_suggested(*, path: Path | str | None = None) -> list[Preference]:
    """待确认的候选偏好（提炼出来的，**不生效**，等用户点头）。"""
    items = [p for p in load_all(path=path) if p.state == STATE_SUGGESTED]
    return sorted(items, key=lambda p: p.created_at, reverse=True)


def remember(
    key: str,
    value: Any,
    *,
    when: When | Mapping[str, Any] | None = None,
    said: str = "",
    path: Path | str | None = None,
) -> Preference:
    """记下一条偏好。

    Raises:
        ValueError: 键不认识 / 不属于 `auto` 域 / 值不合法。
    """
    spec = get_spec(str(key).strip())
    if spec is None:
        raise ValueError(f"不认识配置项 {key}：偏好只能记 ConfigList 里列得出的键")
    if spec.tier != TIER_AUTO:
        # 这是本模块最重要的一道边界，别放宽
        raise ValueError(
            f"{spec.label or spec.key} 属于「{_tier_label(spec.tier)}」档，不能进偏好："
            "带条件的偏好会固化一次本该当场问人的决定。要长期改它就单独改一次配置。"
        )
    clean = validate_value(spec, value)
    pref = Preference(
        key=spec.key,
        value=clean,
        when=When.from_any(when),
        said=(said or "").strip()[:_MAX_SAID_CHARS],
    )
    _append(pref, _ledger_path(path))
    return pref


def forget(pref_id: str, *, path: Path | str | None = None) -> Preference | None:
    """忘掉一条（追加一条 state=removed 的记录）。找不到返回 None。"""
    target = next((p for p in load_all(path=path) if p.id.startswith(pref_id)), None)
    if target is None or target.state == STATE_REMOVED:
        return None
    target.state = STATE_REMOVED
    _append(target, _ledger_path(path))
    return target


def clear_all(*, path: Path | str | None = None) -> int:
    """忘掉全部活跃偏好，返回条数。"""
    count = 0
    for pref in load_active(path=path):
        if forget(pref.id, path=path) is not None:
            count += 1
    return count


def accept(pref_id: str, *, path: Path | str | None = None) -> Preference | None:
    """接受一条候选（suggested → active）。这一步是"同过用户才生效"的落地。"""
    target = next(
        (p for p in load_all(path=path)
         if p.id.startswith(pref_id) and p.state == STATE_SUGGESTED),
        None,
    )
    if target is None:
        return None
    target.state = STATE_ACTIVE
    _append(target, _ledger_path(path))
    return target


def reject(pref_id: str, *, path: Path | str | None = None) -> Preference | None:
    """拒掉一条候选（suggested → removed）。拒了就不再提。"""
    target = next(
        (p for p in load_all(path=path)
         if p.id.startswith(pref_id) and p.state == STATE_SUGGESTED),
        None,
    )
    if target is None:
        return None
    target.state = STATE_REMOVED
    _append(target, _ledger_path(path))
    return target


# ---------------------------------------------------------------------------
# 提炼：从账本里的历史改动里看出"你可能在反复表达同一件事"
# ---------------------------------------------------------------------------


def suggest_from_records(
    records: list[Mapping[str, Any]],
    *,
    now: float | None = None,
    window_days: int = SUGGEST_WINDOW_DAYS,
    path: Path | str | None = None,
) -> list[Preference]:
    """扫一遍账本里的配置改动，提炼出候选偏好（state=suggested，不生效）。

    提炼规则（字少但守得住）：同一键被改成同一值≥ 2 次，且这些改动**有共同的
    上下文**（同一个前台程序，或同一个小时）时，才产出一条候选。

    为什么必须有"共同上下文"这一条：没有共同点就是没有条件，那就不是偏好而是
    "用户想要这个新值"——直接改配置就行（且已改过）。凭两次改动就编一个条件，
    是用户最不想要的那种"变魔法"。

    Args:
        records: 副作用账本记录（每项至少含 tool/ts/args/undo_spec 风格字段，
            直接传 ``journal.recent()`` 的返回值即可）。
        now: 当前时刻（测试用）。

    Returns:
        新建的候选列表（已写入账本；同一条已存在时不会重复造）。
    """
    if now is None:
        now = time.time()
    since = now - window_days * 86400

    # 1) 收：只要 ConfigSet 且带上下文的记录，按 (键, 值) 分组
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for rec in records:
        if str(rec.get("tool") or "") != "ConfigSet":
            continue
        try:
            ts = float(rec.get("ts") or 0.0)
        except (TypeError, ValueError):
            continue
        if ts < since:
            continue
        args = rec.get("args") or {}
        if not isinstance(args, Mapping):
            continue
        patch = args.get("patch") or {}
        if not isinstance(patch, Mapping) or len(patch) != 1:
            continue   # 一次改多项的不提炼：分不清是哪一项让用户不满意
        key, value = next(iter(patch.items()))
        ctx = args.get("context") or {}
        if not isinstance(ctx, Mapping):
            ctx = {}
        groups.setdefault((str(key), json.dumps(value, ensure_ascii=False, sort_keys=True)), []).append(
            {**dict(rec), "_ctx": ctx, "_value": value}
        )

    # 去重时连 removed 一起看：用户拒过的、或接受后又忘掉的，不应再被自动重提
    # （"拒了就闭嘴"）；他要重复可直接说，手动记不受任何限制。
    existing = {_signature(p.key, p.value, p.when) for p in load_all(path=path)}

    created: list[Preference] = []
    for (key, value_json), rows in groups.items():
        if len(rows) < 2:
            continue
        spec = get_spec(key)
        if spec is None or spec.tier != TIER_AUTO:
            continue   # 提炼也只能落 auto 域（同记偏好的边界）
        when = _common_when(rows)
        if when is None or when.is_empty:
            continue   # 没有共同上下文 → 不是偏好，不编
        signature = _signature(key, rows[0]["_value"], when)
        if signature in existing:
            continue
        said = _evidence_text(spec.label or key, rows, when)
        pref = Preference(
            key=key,
            value=rows[0]["_value"],
            when=when,
            said=said,
            state=STATE_SUGGESTED,
        )
        if _append(pref, _ledger_path(path)):
            created.append(pref)
            existing.add(signature)
    return created


def _signature(key: str, value: Any, when: When) -> tuple[str, str, str]:
    """(键, 值, 条件) 的稳定签名——用于去重。

    条件本身是 dict（不可哈希），直接当集合元素会 `TypeError: unhashable type`；
    统一过一遍排序后的 JSON 字符串，既可比又不依赖字典顺序。
    """
    value_key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    when_key = json.dumps(when.to_dict(), ensure_ascii=False, sort_keys=True)
    return (key, value_key, when_key)


def _common_when(rows: list[Mapping[str, Any]]) -> When | None:
    """找这些改动的共同上下文；找不到共同点返回 None（宁可不提）。"""
    apps = [str(r["_ctx"].get("app") or "") for r in rows]
    same_app = [a for a in apps if a]
    if len(same_app) >= 2 and len(set(same_app)) == 1:
        return When(app=same_app[0])
    hours: list[int] = []
    for r in rows:
        try:
            hours.append(int(r["_ctx"].get("hour", -1)))
        except (TypeError, ValueError):
            hours.append(-1)
    known = [h for h in hours if 0 <= h <= 23]
    if len(known) >= 2 and len(set(known)) == 1:
        hour = known[0]
        return When(hour_from=hour, hour_to=hour)
    return None


def _evidence_text(label: str, rows: list[Mapping[str, Any]], when: When) -> str:
    """候选的"凭据"：把"你为什么觉得这是习惯"写出来，用户才能判断要不要接受。"""
    reasons = [str((r.get("args") or {}).get("reason") or "").strip() for r in rows]
    said = next((s for s in reasons if s), "")
    tail = f"（当时你说：{said}）" if said else ""
    # `describe()` 的 app 分支已自带"在"（"在 Code.exe 时"），模板不能再拼一个，
    # 否则会出"你在在 Code.exe 时…"这种话
    return f"你{when.describe()}把「{label}」改成了这个值共 {len(rows)} 次{tail}"[:_MAX_SAID_CHARS]


# ---------------------------------------------------------------------------
# 求值（推导层的核心）
# ---------------------------------------------------------------------------


def evaluate(
    ctx: Mapping[str, Any],
    *,
    keys: frozenset[str] | set[str] | None = None,
    path: Path | str | None = None,
    count_hit: bool = True,
) -> Preference | None:
    """按当前上下文求出一条该生效的偏好（没有就 None）。

    Args:
        ctx: 上下文（``app`` / ``workdir`` / ``hour``），缺的维度不会命中。
        keys: 只在这些键里找（默认只找可施放的键）。
        path: 账本路径（测试用）。
        count_hit: 命中后是否累加并落盘命中次数。
    """
    wanted = keys if keys is not None else ENFORCEABLE_KEYS
    best: Preference | None = None
    for pref in load_active(path=path):
        if pref.key not in wanted:
            continue
        if not pref.when.matches(ctx):
            continue
        if best is None or pref.when.specificity() > best.when.specificity():
            best = pref
    if best is not None and count_hit:
        best.hits += 1
        _append(best, _ledger_path(path))
    return best


def effective(
    key: str,
    base: Any,
    *,
    ctx: Mapping[str, Any] | None = None,
    path: Path | str | None = None,
    count_hit: bool = True,
) -> tuple[Any, str]:
    """算出"在这个上下文下，这个配置项该是多少"。

    这是施放层的**唯一入口**：出声闸门与音量/语速都调它，而不是各自写一份
    "查表 + 比键名 + 判值"——否则每接一个新决策点都会多一份容易写错的口径。

    Args:
        key: 配置键（必须在 `ENFORCEABLE_KEYS` 里）。
        base: 没命中偏好时的原值。
        ctx: 上下文；None 则现采（`current_context`，可能拿不到 app）。

    Returns:
        ``(生效值, 原因)``。没命中时``原因``为空串（调用方据此不写日志）。
    """
    if key not in ENFORCEABLE_KEYS:
        return base, ""
    hit = evaluate(
        ctx if ctx is not None else current_context(),
        keys={key}, path=path, count_hit=count_hit,
    )
    if hit is None:
        return base, ""
    said = f"（你说过「{hit.said}」）" if hit.said and not hit.said.startswith("你在") else ""
    return hit.value, f"上下文偏好：{hit.when.describe()}→ {hit.key} = {hit.value}{said}"


def current_context(*, workdir: str = "", hour: int | None = None) -> dict[str, Any]:
    """尽力而为地采集当前上下文；拿不到的维度留空（"不知道"绝不等于"命中"）。"""
    if hour is None:
        hour = time.localtime().tm_hour
    return {
        "app": current_foreground_app(),
        "workdir": workdir or "",
        "hour": int(hour),
    }


def current_foreground_app() -> str:
    """当前前台进程名；探测不可用返回空串（无 GUI 会话 / 平台不支持）。"""
    try:
        from agent.core.percept.source_window import current_foreground_app as _probe

        return _probe()
    except Exception:  # noqa: BLE001 - 上下文采集失败不该影响决策，退化成"不知道"
        return ""


def _tier_label(tier: str) -> str:
    from agent.config.config_specs import TIER_CONFIRM, TIER_FORBIDDEN

    return {TIER_CONFIRM: "改前问人", TIER_FORBIDDEN: "禁改"}.get(tier, tier)
