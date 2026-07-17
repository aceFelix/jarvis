"""TaskGet 工具 —— 获取单个任务的详细信息。"""

from __future__ import annotations

import json
from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.collaboration.task_list import TaskList
from agent.core.tool import JSONSchema, Tool


class TaskGetTool(Tool):
    """获取指定任务的完整信息。"""

    name = "TaskGet"
    description = "获取指定任务的完整信息，包括描述、状态、owner、依赖关系。"
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "任务 ID（数字字符串），如 '1'",
            },
        },
        "required": ["task_id"],
    }

    def __init__(self, task_list: TaskList) -> None:
        self._tl = task_list

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("读取任务")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        task_id = args.get("task_id", "").strip()
        if not task_id:
            return ToolResult.error("task_id 不能为空")

        task = self._tl.read(task_id)
        if task is None:
            return ToolResult.error(f"任务 #{task_id} 不存在（可能已删除）")

        info = json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
        return ToolResult(data=info)
