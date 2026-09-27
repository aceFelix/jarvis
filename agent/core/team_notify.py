"""多 Agent 团队邮箱自动同步 —— 把队友状态更新注入 leader 的对话。

从 agent/core/query_loop.py 抽出（原为模块级私有函数），保持职责单一：
ReAct 循环负责编排，本模块只负责"读邮箱 → 渲染 → 注入 ctx.messages"。

@author aceFelix
"""

from __future__ import annotations

from agent.core.context import ToolContext
from agent.core.message import Message, TextContent


def inject_teammate_notifications(ctx: ToolContext) -> None:
    """自动读取 team-lead 邮箱，将队友状态更新注入对话。

    队友完成一轮工作后通过文件邮箱发 idle_notification。
    leader 的主循环每轮工具执行后调用此函数，确保 leader "看到"队友动向，
    无需 Sleep 轮询或手动检查。

    支持的消息类型：idle_notification、task_claimed、task_completed、
    plan_approval_request、permission_request、shutdown_response。
    """
    try:
        from agent.collaboration.team import get_team_manager
        from agent.collaboration.mailbox import read_mailbox
    except ImportError:
        return

    mgr = get_team_manager()
    team_name = mgr.active_team
    if team_name is None:
        return

    messages = read_mailbox("team-lead", team_name, unread_only=True, mark_read=True)
    if not messages:
        return

    # 构造注入文本
    lines = ["[以下来自团队队友的状态更新]"]
    has_info = False

    for msg in messages:
        if msg.type == "idle_notification":
            summary = msg.summary or "空闲，等待新任务"
            lines.append(f"- {msg.from_name}: {summary}")
            has_info = True
        elif msg.type == "task_claimed":
            subject = msg.task_subject or "未命名任务"
            lines.append(f"- {msg.from_name}: 领取任务 #{msg.task_id} {subject}")
            has_info = True
        elif msg.type == "task_completed":
            status = msg.status or "completed"
            summary = msg.summary or f"完成任务 #{msg.task_id}"
            lines.append(f"- {msg.from_name}: [{status}] {summary}")
            has_info = True
        elif msg.type == "plan_approval_request":
            plan_text = (msg.text or "未提供计划详情")[:200]
            lines.append(
                f"- {msg.from_name}: 请求审批计划 (request_id={msg.request_id})\n"
                f"  计划: {plan_text}"
            )
            has_info = True
        elif msg.type == "permission_request":
            action = msg.action or "执行操作"
            tool = msg.tool or "未知工具"
            lines.append(
                f"- {msg.from_name}: 请求权限 (request_id={msg.request_id})\n"
                f"  操作: {action} | 工具: {tool}"
            )
            has_info = True
        elif msg.type == "shutdown_response":
            action = "同意关闭" if msg.approve else "拒绝关闭"
            lines.append(f"- {msg.from_name}: {action}")
            has_info = True
        elif msg.type == "heartbeat":
            # 心跳不渲染到对话，仅内部更新健康时间戳（如后续需要）
            continue

    if not has_info:
        return

    lines.append("[以上为团队状态更新，请据此调整任务分配]")

    ctx.messages.append(
        Message(role="user", content=[TextContent(text="\n".join(lines))])
    )
