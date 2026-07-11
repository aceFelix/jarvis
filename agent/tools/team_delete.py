"""TeamDelete 工具 —— 解散多 Agent 协作团队。

致敬 Claude Code 的 TeamDeleteTool。

前置条件：除 leader 外无活跃成员（需先用 SendMessage shutdown 让队友退出）。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.core.team import TeamManager, get_team_manager
from agent.core.tool import JSONSchema, Tool


class TeamDeleteTool(Tool):
    """解散多 Agent 团队。"""

    name = "TeamDelete"
    description = (
        "解散当前团队。删除团队配置、邮件箱和共享任务列表。"
        "前置条件：除 leader 外无活跃成员。"
        "如果无法删除，先用 SendMessage shutdown_request 让队友退出。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, team_mgr: TeamManager | None = None) -> None:
        self._mgr = team_mgr or get_team_manager()

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("解散团队")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        team_name = self._mgr.active_team
        if team_name is None:
            return ToolResult.error("当前没有活跃的团队")

        # 检查活跃成员
        team = self._mgr.load(team_name)
        if team is not None:
            active = team.active_non_lead_members
            if active:
                names = ", ".join(m.name for m in active)
                return ToolResult.error(
                    f"团队 '{team_name}' 还有活跃成员 ({names})，不能删除。\n"
                    f"请先用 SendMessage shutdown_request 让队友退出。"
                )

        try:
            success = self._mgr.delete(team_name)
            if success:
                return ToolResult(data=f"团队 '{team_name}' 已解散")
            return ToolResult.error(f"解散团队 '{team_name}' 失败")
        except RuntimeError as e:
            return ToolResult.error(str(e))
