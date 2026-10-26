"""W1 运行时层命令 —— /away /back /lease /pending（阶段 8）。

暴露 core/policy 的运行时能力给 REPL：
- /away /back：手动切换无人值守（ASK 请求入待批清单，回来呈批）；
- /lease：签发/查看/撤销授权租约（有限期有限范围免打断授权；
  声明式免确认请用 permissions.yaml 规则，租约只管"临时"）；
- /pending：查看 away 期间积压的待批请求。

命令只操作跨进程落盘的状态文件（~/.jarvis/*.json），CLI 与 serve
宿主共享同一份，任一侧切换另一侧即时生效。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


def handle_away(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /away：进入无人值守（需确认的调用入待批清单并拦下）。"""
    from agent.core.policy import get_policy_state
    get_policy_state().set_away(True, source="manual")
    ctx.ui.info("已进入无人值守（/away）：需要确认的操作会记入待批清单，/back 回来呈批")
    return True


def handle_back(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /back：解除无人值守，取回待批清单并给简报。"""
    from agent.core.policy import get_pending_approvals, get_policy_state
    get_policy_state().set_away(False, source="manual")
    items = get_pending_approvals().drain()
    if not items:
        ctx.ui.info("已回到岗位（/back），期间没有待批的请求")
        return True
    lines = [f"已回到岗位（/back），待批 {len(items)} 条："]
    for i, it in enumerate(items, 1):
        lines.append(
            f"  {i}. [{it.get('ts', '')}] {it.get('tool', '')} "
            f"({it.get('args', '')}) — {it.get('reason', '')}"
        )
    # 只呈批不重放：待批清单是 W1 的 ASK 记录，不是可重放队列（阶段 10 交付的是
    # 任务脊柱的受阻步重试，不是这里的重放）。2026-10-26 人工实测：旧文案写着
    # “重放执行属阶段 10 W3”，而 W3 早已交付且仍不支持重放，等于预告了一个不存在的功能。
    lines.append(
        "（只呈批不自动重放：回来说一遍即可；不想再被反问就用 "
        "/lease <工具> [路径] [分钟] 或 permissions.yaml 的 allow 规则放行）"
    )
    ctx.ui.info("\n".join(lines))
    return True


def handle_pending(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /pending：查看当前待批清单（不清空）。"""
    from agent.core.policy import get_pending_approvals
    items = get_pending_approvals().list()
    if not items:
        ctx.ui.info("待批清单为空")
        return True
    lines = [f"待批清单 {len(items)} 条："]
    for i, it in enumerate(items, 1):
        lines.append(
            f"  {i}. [{it.get('ts', '')}] {it.get('tool', '')} "
            f"({it.get('args', '')}) — {it.get('reason', '')}"
        )
    ctx.ui.info("\n".join(lines))
    return True


def handle_lease(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /lease：签发/列表/撤销授权租约。

    语法：
        /lease                       列出生效中的租约
        /lease list                  同上
        /lease revoke <id>           撤销指定租约
        /lease <tools> [路径] [分钟N] 签发新租约
            tools：逗号分隔工具名，支持 fnmatch（如 FileWrite,FileEdit）
            路径：可选 glob（如 ~/work/reports/**），省略=不限路径
            分钟N：可选 TTL，缺省 120 分钟
    """
    from agent.core.policy import get_lease_store
    ui = ctx.ui
    store = get_lease_store()
    parts = stripped.split()[1:]

    # 无参 / list → 列出生效租约
    if not parts or parts[0].lower() == "list":
        active = store.list_active()
        if not active:
            ui.info("当前没有生效中的租约（/lease <tools> [路径] [分钟] 签发）")
            return True
        lines = [f"生效中的租约 {len(active)} 条："]
        for l in active:
            scope = ",".join(l.path_within) if l.path_within else "不限路径"
            note = f"（{l.note}）" if l.note else ""
            lines.append(
                f"  [{l.id}] {','.join(l.tools)} @ {scope} "
                f"→ {l.expires_at}{note}"
            )
        ui.info("\n".join(lines))
        return True

    # revoke <id> → 撤销
    if parts[0].lower() == "revoke":
        if len(parts) < 2:
            ui.warn("用法: /lease revoke <id>（/lease 查看现有租约）")
            return True
        if store.revoke(parts[1]):
            ui.info(f"租约 {parts[1]} 已撤销，相关操作立刻回到逐次确认")
        else:
            ui.warn(f"未找到生效中的租约: {parts[1]}")
        return True

    # 签发：/lease <tools> [路径] [分钟N]
    tools = [t.strip() for t in parts[0].split(",") if t.strip()]
    if not tools:
        ui.warn("用法: /lease <tools> [路径] [分钟N]")
        return True
    path_glob = ""
    ttl_minutes = 120
    for p in parts[1:]:
        pl = p.lower()
        if pl.endswith("m") and pl[:-1].isdigit():
            ttl_minutes = int(pl[:-1])
        elif p.isdigit():
            ttl_minutes = int(p)
        elif not path_glob:
            path_glob = p
    lease = store.grant(tools, [path_glob] if path_glob else None,
                        ttl_minutes=ttl_minutes, note="via /lease")
    scope = path_glob or "不限路径"
    ui.info(
        f"已签发租约 [{lease.id}]：{','.join(tools)} @ {scope}，"
        f"有效期 {ttl_minutes} 分钟（至 {lease.expires_at}）。"
        "命中租约的确认请求将直接放行；路径软提醒与 DENY 级拦截不受租约影响"
    )
    return True
