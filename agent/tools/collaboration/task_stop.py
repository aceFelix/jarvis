"""TaskStop 工具 —— 终止团队中指定的后台 teammate。

通过进程内注册表找到对应 TeammateRunner，调用其 shutdown 方法。
如果注册表中没有，则向该 teammate 发送 shutdown_request 作为兜底。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import JSONSchema, Tool
from agent.collaboration.mailbox import make_shutdown_request, write_mailbox
from agent.collaboration.team import TeamManager, get_team_manager
from agent.collaboration.teammate_registry import get_teammate_registry


class TaskStopTool(Tool):
    """终止团队中指定的后台 teammate。"""

    name = "TaskStop"
    description = (
        "终止团队中指定的后台 teammate。向其发送 shutdown_request 并等待响应。"
        "用于 teammate 异常或任务需要强制收尾的场景。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "要终止的队友名（如 'researcher'）。",
            },
            "reason": {
                "type": "string",
                "description": "终止原因。可选。",
                "default": "leader 请求终止",
            },
        },
        "required": ["name"],
    }

    def __init__(self, team_mgr: TeamManager | None = None) -> None:
        self._mgr = team_mgr or get_team_manager()
        self._registry = get_teammate_registry()

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("终止后台 teammate")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        team_name = self._mgr.active_team
        if team_name is None:
            return ToolResult.error("当前没有活跃的团队。")

        name = args.get("name", "").strip()
        if not name:
            return ToolResult.error("name 不能为空")

        team = self._mgr.load(team_name)
        if team is None:
            return ToolResult.error(f"团队 '{team_name}' 不存在")

        member = team.get_member(name)
        if member is None:
            available = ", ".join(m.name for m in team.members if m.name != "team-lead")
            return ToolResult.error(
                f"团队 '{team_name}' 中没有队友 '{name}'。可用: {available or '无'}"
            )

        reason = args.get("reason", "leader 请求终止")

        # 优先通过注册表直接 shutdown
        runner = self._registry.get(team_name, name)
        if runner is not None:
            try:
                await runner.shutdown(reason=reason)
            except Exception as e:
                return ToolResult.error(f"终止 teammate '{name}' 时出错: {e}")
        else:
            # 兜底：发送 shutdown_request 到其邮箱
            try:
                msg = make_shutdown_request(
                    from_name="team-lead",
                    reason=reason,
                )
                write_mailbox(name, msg, team_name)
            except Exception as e:
                return ToolResult.error(f"向 '{name}' 发送关机请求失败: {e}")

        # 更新成员状态为空闲/不活跃
        try:
            self._mgr.mark_member_active(team_name, name, False)
        except Exception:
            pass

        return ToolResult(data=f"已向 teammate '{name}' 发送终止请求（原因: {reason}）")
