"""TaskCreate 工具 —— 在共享任务列表中创建新任务。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.core.task_list import TaskList
from agent.core.tool import JSONSchema, Tool


class TaskCreateTool(Tool):
    """创建共享任务列表中的任务。"""

    name = "TaskCreate"
    description = (
        "在共享任务列表中创建新任务。用于拆解复杂工作为可追踪的子任务。"
        "每个任务有独立 ID、状态（pending/in_progress/completed）、可分配 owner、支持依赖链。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": "简短标题（祈使句），如 '探索认证模块代码'",
            },
            "description": {
                "type": "string",
                "description": "详细描述，含验收标准、上下文。可选。",
            },
            "active_form": {
                "type": "string",
                "description": "进行时描述，如 '正在探索认证模块'。可选。",
            },
            "owner": {
                "type": "string",
                "description": "分配给指定 agent 名。可选。",
            },
        },
        "required": ["subject"],
    }

    def __init__(self, task_list: TaskList) -> None:
        self._tl = task_list

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("创建任务")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        subject = args.get("subject", "").strip()
        if not subject:
            return ToolResult.error("subject 不能为空")

        task_id = self._tl.create(
            subject=subject,
            description=args.get("description", ""),
            active_form=args.get("active_form"),
            owner=args.get("owner"),
        )

        return ToolResult(data=f"任务 #{task_id} 已创建: {subject}")
