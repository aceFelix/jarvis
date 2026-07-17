"""TaskList 工具 —— 列出任务列前的所有任务。"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.collaboration.task_list import TaskList
from agent.core.tool import JSONSchema, Tool


class TaskListTool(Tool):
    """列出共享任务列前的所有任务（摘要视图）。"""

    name = "TaskList"
    description = (
        "列出共享任务列前的所有任务，查看进度和分配情况。"
        "返回每个任务的 id、状态、owner、阻塞信息等摘要。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {},
    }

    def __init__(self, task_list: TaskList) -> None:
        self._tl = task_list

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("列出任务")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tasks = self._tl.list_all()
        if not tasks:
            return ToolResult(data="任务列表为空。用 TaskCreate 创建新任务。")

        lines = []
        for t in tasks:
            status_icon = {
                "pending": "○",
                "in_progress": "●",
                "completed": "✓",
            }.get(t.status, "?")

            owner_str = f" [{t.owner}]" if t.owner else ""
            blocked_str = ""
            if t.blocked_by:
                blocked_str = f" (阻塞于: {', '.join(f'#{b}' for b in t.blocked_by)})"

            lines.append(
                f"#{t.id} {status_icon} {t.subject}{owner_str}{blocked_str}"
            )

        return ToolResult(data="\n".join(lines))
