"""SendMessage 工具 —— Agent 间消息传递。

支持消息类型：
- message: 向特定队友发文本消息
- broadcast: 向所有队友广播
- shutdown_request: 请求队友关闭
- shutdown_response: 响应关机请求
- plan_approval_response: 审批/驳回队友的计划

消息通过文件邮箱传递，队友下一轮自动读取。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.collaboration.mailbox import (
    TeammateMessage,
    broadcast_mailbox,
    make_broadcast,
    make_plain_message,
    make_shutdown_request,
    make_shutdown_response,
    make_plan_approval_response,
    write_mailbox,
)
from agent.core.result import PermissionResult, ToolResult
from agent.collaboration.team import TEAM_LEAD_NAME, TeamManager, get_team_manager
from agent.core.tool import JSONSchema, Tool


class SendMessageTool(Tool):
    """Agent 间消息传递工具。"""

    name = "SendMessage"
    description = (
        "向团队中的队友发送消息。支持以下几种: \n"
        "- message: 向特定队友发送文本消息（用于继续对话、分配任务、询问进展）\n"
        "- broadcast: 向所有队友广播（谨慎使用，如紧急通知）\n"
        "- shutdown_request: 请求队友关闭\n"
        "- shutdown_response: 接收关机请求后的响应\n"
        "- plan_approval_response: 审批/驳回队友的计划"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "description": "消息类型",
                "enum": ["message", "broadcast", "shutdown_request", "shutdown_response", "plan_approval_response"],
                "default": "message",
            },
            "recipient": {
                "type": "string",
                "description": "收件人名（message/shutdown_request/shutdown_response/plan_approval_response 时必需）。如 'researcher'。",
            },
            "content": {
                "type": "string",
                "description": "消息文本",
            },
            "summary": {
                "type": "string",
                "description": "简短摘要（5-10 词）。broadcast 类型必需。",
            },
            "request_id": {
                "type": "string",
                "description": "请求 ID（shutdown_response/plan_approval_response 时必需）。从收到的请求消息中获取。",
            },
            "approve": {
                "type": "boolean",
                "description": "是否批准（shutdown_response/plan_approval_response 时必需）。",
            },
        },
        "required": ["type"],
    }

    def __init__(self, team_mgr: TeamManager | None = None) -> None:
        self._mgr = team_mgr or get_team_manager()

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("Agent 间通信")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        team_name = self._mgr.active_team
        if team_name is None:
            return ToolResult.error("当前没有活跃的团队，无法发送消息。请先用 TeamCreate 创建团队。")

        msg_type = args.get("type", "message")
        recipient = args.get("recipient", "")
        content = args.get("content", "")
        summary = args.get("summary", "")

        try:
            if msg_type == "message":
                return await self._handle_message(team_name, recipient, content, summary)

            elif msg_type == "broadcast":
                return await self._handle_broadcast(team_name, content, summary)

            elif msg_type == "shutdown_request":
                return await self._handle_shutdown_request(team_name, recipient, content)

            elif msg_type == "shutdown_response":
                return await self._handle_shutdown_response(
                    team_name, recipient, content, args
                )

            elif msg_type == "plan_approval_response":
                return await self._handle_plan_response(
                    team_name, recipient, content, args
                )

            else:
                return ToolResult.error(f"未知消息类型: {msg_type}")

        except TimeoutError as e:
            return ToolResult.error(f"消息发送超时: {e}")
        except Exception as e:
            return ToolResult.error(f"消息发送失败: {e}")

    # ---- 消息处理 ----

    async def _handle_message(
        self, team_name: str, recipient: str, text: str, summary: str
    ) -> ToolResult:
        if not recipient:
            return ToolResult.error("recipient 不能为空")
        if not text:
            return ToolResult.error("content 不能为空")

        team = self._mgr.load(team_name)
        if team is None:
            return ToolResult.error(f"团队 '{team_name}' 不存在")

        member = team.get_member(recipient)
        if member is None:
            available = ", ".join(m.name for m in team.members)
            return ToolResult.error(
                f"团队 '{team_name}' 中没有成员 '{recipient}'。可用: {available}"
            )

        msg = make_plain_message(
            from_name=TEAM_LEAD_NAME,
            text=text,
            summary=summary or (text[:50] + "..." if len(text) > 50 else text),
        )
        write_mailbox(recipient, msg, team_name)

        return ToolResult(
            data=f"消息已发给 {recipient}: {summary or text[:60]}"
        )

    async def _handle_broadcast(
        self, team_name: str, text: str, summary: str
    ) -> ToolResult:
        if not text:
            return ToolResult.error("content 不能为空")

        msg = make_broadcast(
            from_name=TEAM_LEAD_NAME,
            text=text,
            summary=summary or "广播消息",
        )
        broadcast_mailbox(TEAM_LEAD_NAME, msg, team_name)

        return ToolResult(data="消息已广播给所有队友")

    async def _handle_shutdown_request(
        self, team_name: str, recipient: str, reason: str
    ) -> ToolResult:
        if not recipient:
            return ToolResult.error("recipient 不能为空")

        team = self._mgr.load(team_name)
        if team is None:
            return ToolResult.error(f"团队 '{team_name}' 不存在")

        member = team.get_member(recipient)
        if member is None:
            return ToolResult.error(f"成员 '{recipient}' 不存在")

        request_id = ""
        msg = make_shutdown_request(
            from_name=TEAM_LEAD_NAME,
            request_id=request_id,
            reason=reason,
        )
        write_mailbox(recipient, msg, team_name)

        return ToolResult(
            data=f"已向 {recipient} 发送关机请求 (request_id={msg.request_id})"
        )

    async def _handle_shutdown_response(
        self, team_name: str, recipient: str, reason: str, args: dict
    ) -> ToolResult:
        if not recipient:
            return ToolResult.error("recipient 不能为空")
        request_id = args.get("request_id", "")
        if not request_id:
            return ToolResult.error("request_id 不能为空（从收到的 shutdown_request 中获取）")
        approve = args.get("approve", True)

        msg = make_shutdown_response(
            from_name=TEAM_LEAD_NAME,
            request_id=request_id,
            approve=approve,
            reason=reason,
        )
        write_mailbox(recipient, msg, team_name)

        action = "批准" if approve else "拒绝"
        return ToolResult(data=f"{action}了 {recipient} 的关机请求")

    async def _handle_plan_response(
        self, team_name: str, recipient: str, feedback: str, args: dict
    ) -> ToolResult:
        if not recipient:
            return ToolResult.error("recipient 不能为空")
        request_id = args.get("request_id", "")
        if not request_id:
            return ToolResult.error("request_id 不能为空（从收到的 plan_approval_request 中获取）")

        msg = make_plan_approval_response(
            from_name=TEAM_LEAD_NAME,
            request_id=request_id,
            approve=args.get("approve", True),
            feedback=feedback,
        )
        write_mailbox(recipient, msg, team_name)

        action = "审批通过" if args.get("approve", True) else "驳回"
        return ToolResult(data=f"{action}了 {recipient} 的计划")
