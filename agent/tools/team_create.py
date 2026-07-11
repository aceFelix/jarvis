"""TeamCreate 工具 —— 创建多 Agent 协作团队。

致敬 Claude Code 的 TeamCreateTool。

创建团队后：
1. 自动生成共享任务列表（Team = TaskList，1:1 对应）
2. Leader 固定为 "team-lead@teamName"
3. 后续用 Agent 工具派生 teammate 加入团队
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.core.team import TEAM_LEAD_NAME, TeamManager, get_team_manager
from agent.core.tool import JSONSchema, Tool


class TeamCreateTool(Tool):
    """创建多 Agent 协作团队。"""

    name = "TeamCreate"
    description = (
        "创建一个多 Agent 协作团队。创建后自动生成共享任务列表。"
        "后续可以用 Agent 工具（带 team_name 参数）派生 teammate，"
        "用 SendMessage 与队友通信，用 TaskCreate/TaskUpdate 管理任务。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "team_name": {
                "type": "string",
                "description": "团队名（只含字母数字连字符下划线）。如 'my-project'。",
            },
            "description": {
                "type": "string",
                "description": "团队描述（可选）。",
            },
        },
        "required": ["team_name"],
    }

    def __init__(self, team_mgr: TeamManager | None = None) -> None:
        self._mgr = team_mgr or get_team_manager()

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("创建团队")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = args.get("team_name", "").strip()
        if not name:
            return ToolResult.error("team_name 不能为空")

        # 检查是否已在领导团队
        if self._mgr.active_team:
            return ToolResult.error(
                f"当前已在领导团队 '{self._mgr.active_team}'，"
                f"不能同时领导多个团队。请先用 TeamDelete 解散当前团队。"
            )

        try:
            from agent.core.team import sanitize_name
            safe_name = sanitize_name(name)

            team = self._mgr.create(
                name,
                description=args.get("description", ""),
                cwd=ctx.workdir,
            )

            return ToolResult(data=(
                f"团队 '{safe_name}' 已创建。\n"
                f"- 领导者: {TEAM_LEAD_NAME}\n"
                f"- 成员: 1（仅 leader）\n"
                f"- 下一步: 用 Agent 工具派生 teammate（带 team_name='{safe_name}'），"
                f"再用 TaskCreate/TaskUpdate 分配任务。"
            ))
        except RuntimeError as e:
            return ToolResult.error(str(e))
