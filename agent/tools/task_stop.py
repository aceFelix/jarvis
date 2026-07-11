"""TaskStop 工具 —— 停止运行中的后台任务（如 async teammate）。"""

from __future__ import annotations

import asyncio
from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import JSONSchema, Tool


class TaskStopTool(Tool):
    """停止后台任务。"""

    name = "TaskStop"
    description = (
        "停止一个运行中的后台任务（如异步子代理/队友）。"
        "用于取消不再需要的后台工作。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要停止的任务 ID",
            },
        },
        "required": ["task_id"],
    }

    def __init__(self) -> None:
        self._background_tasks: dict[str, asyncio.Task] = {}

    def register(self, task_id: str, task: asyncio.Task) -> None:
        self._background_tasks[task_id] = task

    def unregister(self, task_id: str) -> None:
        self._background_tasks.pop(task_id, None)

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("停止后台任务")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tid = args.get("task_id", "").strip()
        if not tid:
            return ToolResult.error("task_id 不能为空")

        task = self._background_tasks.get(tid)
        if task is None:
            return ToolResult.error(f"后台任务 '{tid}' 未找到（可能已完成或不存在）")

        if task.done():
            self.unregister(tid)
            return ToolResult(data=f"任务 '{tid}' 已完成，无需停止")

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        self.unregister(tid)
        return ToolResult(data=f"后台任务 '{tid}' 已停止")
