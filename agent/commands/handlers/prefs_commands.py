"""/prefs 命令 —— 偏好账本的用户面入口（阶段 12 P3）。

四种形态：

    /prefs                  列出已生效偏好 + 待确认候选
    /prefs suggest          扫一遍副作用账本，提炼出候选（不生效，等你点头）
    /prefs accept <id>      接受一条候选（这一步之后才会真正生效）
    /prefs forget <id>      忘掉/拒掉一条（候选也可以直接拒）
    /prefs clear            忘掉全部已生效偏好

为什么用户面必须先有这一条：偏好是"以后自动这样"的东西，一旦记错而用户看不到清单，
就变成他解释不了的怪行为（"为什么今天不念了？"）。可列、可删、可拒比"记得准"更重要。

为什么提炼不挂在定时器上：每天凌晨扫一遍，没新意的时候只会多响一次——而 W1 打扰预算
存在的意义就是"别无关紧要地响"。所以提炼是**按需触发**（这条命令），并且**它自己不
生效**，必须 accept。用户主动问时才提，比机器主动猜要好。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.config import prefs as prefs_mod

if TYPE_CHECKING:
    from agent.commands.router import CommandContext

_USAGE = (
    "用法：\n"
    "  /prefs                  列出已生效偏好与待确认候选\n"
    "  /prefs suggest          扫一遍最近的改配置记录，提炼候选（等你确认才生效）\n"
    "  /prefs accept <id>      接受一条候选\n"
    "  /prefs forget <id>      忘掉一条（候选也能这样拒掉）\n"
    "  /prefs clear            忘掉全部已生效偏好"
)


def _esc(text: str) -> str:
    """Rich 标记转义（用户原话里可能有方括号，不转义会被当样式标签吞掉）。"""
    try:
        from rich.markup import escape

        return escape(str(text))
    except Exception:  # noqa: BLE001 - 无 rich 就退化成纯文本
        return str(text)


def _job_records() -> list[dict]:
    """把副作用账本读成提炼要的形态（读不到就当空，不抛）。"""
    try:
        from agent.core.actions import get_journal

        return [rec.to_dict() for rec in get_journal().recent(200)]
    except Exception:  # noqa: BLE001 - 账本不可用不该让命令炸
        return []


def _safe_ctx() -> dict:
    """取一次当前上下文；取不到当空（空上下文不会命中任何带条件的偏好）。

    一次列表只取一次：每条偏好各探一次前台窗口是没必要的 Windows 调用。
    """
    try:
        return prefs_mod.current_context()
    except Exception:  # noqa: BLE001 - 取不到就少标一个勾
        return {}


def _context_line(now_ctx: dict | None = None) -> str:
    """当前上下文的可读行（带"现在会不会命中"，方便一眼看出条件写错了）。"""
    ctx = now_ctx if now_ctx is not None else _safe_ctx()
    if not ctx:
        return ""
    app = str(ctx.get("app") or "")
    hour = ctx.get("hour", -1)
    where = f"前台 {app}" if app else "前台未知（无 GUI 会话）"
    return f"（当前上下文：{where}，{hour} 点）"


def _show_list(ctx: "CommandContext") -> None:
    """列出已生效偏好 + 待确认候选（两节分开，因为一个在生效一个没生效）。"""
    active = prefs_mod.load_active()
    suggested = prefs_mod.load_suggested()
    if not active and not suggested:
        ctx.ui.info(
            "还没有记过任何偏好。\n"
            "偏好是带条件的：「以后我写代码时别念出来」这类，可用 /prefs 随时查看与取消。\n"
            "一次性的调整（「音量小一点」）不在这里，它直接改了配置，可用 /undo 回退。"
        )
        return

    now_ctx = _safe_ctx()

    if suggested:
        lines = [f"待确认的偏好 {len(suggested)} 条（**还没生效**，接受后才会自动执行）："]
        for pref in suggested:
            lines.append(f"- {pref.id}｜{pref.describe()}")
            lines.append(f"    依据：{pref.said or '（无）'}")
        lines.append("  接受：/prefs accept <id>    不要：/prefs forget <id>")
        lines.append(_context_line(now_ctx))
        ctx.ui.info(_esc("\n".join(lines)))

    if active:
        lines = [f"已生效的偏好 {len(active)} 条（新→旧）："]
        for pref in sorted(active, key=lambda p: p.created_at, reverse=True):
            said = f"  原话：「{pref.said}」" if pref.said else ""
            # 现在会不会命中，直接标出来：
            # 写错了程序名（比如记成 Code.exe 但实际在用 Qoder.exe）只能靠这一行发现
            now = "✓ 此刻命中" if pref.when.matches(now_ctx) else "－ 此刻不命中"
            lines.append(f"- {pref.id}｜{pref.describe()}（命中 {pref.hits} 次，{now}）{said}")
        lines.append("（命中时由系统自动执行，不依赖模型是否记得；取消用 /prefs forget <id>）")
        lines.append(_context_line(now_ctx))
        ctx.ui.info(_esc("\n".join(lines)))


def handle_prefs(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /prefs [suggest | accept <id> | forget <id> | clear]。"""
    parts = stripped.split()
    sub = parts[1].lower() if len(parts) > 1 else ""

    if sub in ("", "list"):
        _show_list(ctx)
    elif sub == "suggest":
        created = prefs_mod.suggest_from_records(_job_records())
        if not created:
            ctx.ui.info(
                "没有提炼出新的候选。\n"
                "规则是：同一项被你改成同一个值 ≥2 次，且这几次有共同上下文（同一个程序/同一小时）"
                "——没有共同点就不猜。"
            )
        else:
            lines = [f"提炼出 {len(created)} 条候选（**还没生效**）："]
            for pref in created:
                lines.append(f"- {pref.id}｜{pref.describe()}")
                lines.append(f"    依据：{pref.said}")
            lines.append("  接受：/prefs accept <id>    不要：/prefs forget <id>")
            ctx.ui.info(_esc("\n".join(lines)))
    elif sub == "accept":
        if len(parts) < 3:
            ctx.ui.warn("用法: /prefs accept <id>（/prefs 可看 id）")
            return True
        target = prefs_mod.accept(parts[2])
        if target is None:
            ctx.ui.warn(f"没找到 id 以 {parts[2]} 开头的待确认候选")
        else:
            ctx.ui.info(f"已接受并生效：{_esc(target.describe())}")
    elif sub == "forget":
        if len(parts) < 3:
            ctx.ui.warn("用法: /prefs forget <id>（/prefs 可看 id）")
            return True
        target = prefs_mod.forget(parts[2]) or prefs_mod.reject(parts[2])
        if target is None:
            ctx.ui.warn(f"没找到 id 以 {parts[2]} 开头的偏好或候选")
        else:
            ctx.ui.info(f"已取消：{_esc(target.describe())}")
    elif sub == "clear":
        count = prefs_mod.clear_all()
        ctx.ui.info(f"已忘掉全部 {count} 条偏好" if count else "本来就没有偏好可忘")
    else:
        ctx.ui.warn(f"不认识的子命令 {sub}")
        ctx.ui.info(_USAGE)
    return True
