"""TeamStatus 工具 —— 查看当前活跃团队的状态概要。

提供成员列表、运行状态、任务统计、未读邮件数，便于 leader 掌握全局。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import JSONSchema, Tool
from agent.collaboration.mailbox import has_unread
from agent.collaboration.task_list import TaskList
from agent.collaboration.team import TeamManager, get_team_manager


class TeamStatusTool(Tool):
    """查看当前活跃团队的状态概要。"""

    name = "TeamStatus"
    description = (
        "查看当前活跃团队的状态概要。返回：成员列表及状态、任务统计、"
        "leader 未读邮件数。用于 leader 掌握团队全局。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {},
    }

    def __init__(
        self,
        team_mgr: TeamManager | None = None,
        task_list: TaskList | None = None,
    ) -> None:
        self._mgr = team_mgr or get_team_manager()
        self._task_list = task_list

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("查看团队状态")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        team_name = self._mgr.active_team
        if team_name is None:
            return ToolResult.error("当前没有活跃的团队。请先用 TeamCreate 创建团队。")

        team = self._mgr.load(team_name)
        if team is None:
            return ToolResult.error(f"团队 '{team_name}' 不存在或无法加载。")

        # 成员状态
        member_lines = []
        for member in team.members:
            status = "活跃" if member.is_active is not False else "空闲"
            if member.agent_id == team.lead_agent_id:
                status = "leader"
            model_info = f" (model={member.model})" if member.model else ""
            member_lines.append(
                f"- {member.name} [{status}]{model_info}"
            )

        # 任务统计
        task_stats = {"pending": 0, "in_progress": 0, "completed": 0, "total": 0}
        task_lines = []
        if self._task_list is not None:
            try:
                tasks = self._task_list.list_all()
                for task in tasks:
                    task_stats["total"] += 1
                    if task.status in task_stats:
                        task_stats[task.status] += 1
                    owner = task.owner or "未分配"
                    blocker = " ".join(f"#{b}" for b in task.blocked_by) or "无"
                    task_lines.append(
                        f"- #{task.id} [{task.status}] (owner={owner}, blockedBy={blocker}) {task.subject}"
                    )
            except Exception as e:
                task_lines.append(f"  (读取任务列表失败: {e})")

        # 未读邮件
        try:
            unread = has_unread("team-lead", team_name)
        except Exception:
            unread = False

        summary = (
            f"团队: {team.name}\n"
            f"描述: {team.description or '无'}\n"
            f"成员 ({len(team.members)}):\n" + "\n".join(member_lines) + "\n"
            f"任务统计: pending={task_stats['pending']}, "
            f"in_progress={task_stats['in_progress']}, "
            f"completed={task_stats['completed']}, total={task_stats['total']}\n"
        )
        if task_lines:
            summary += "任务列表:\n" + "\n".join(task_lines) + "\n"
        summary += f"leader 未读邮件: {'有' if unread else '无'}"

        return ToolResult(data=summary)
