"""TaskUpdate 工具 —— 更新任务状态、分配 owner、设置依赖链。

支持的操作：
- 更新状态（pending → in_progress → completed）
- 分配 owner（格式: agent 名）
- 设置依赖关系（add_blocks / add_blocked_by）
- 更新标题/描述
- 删除任务（status="deleted"）
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.collaboration.task_list import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_DELETED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_PENDING,
)
from agent.collaboration.task_list import TaskList
from agent.core.tool import JSONSchema, Tool


class TaskUpdateTool(Tool):
    """更新共享任务列表中的任务。"""

    name = "TaskUpdate"
    description = (
        "更新任务的字段：状态（pending/in_progress/completed/deleted）、"
        "owner（分配给人）、依赖关系（blocks/blockedBy）。"
        "完成一个任务后立即标记 completed，然后查看 TaskList 找下一个可做的。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要更新的任务 ID",
            },
            "subject": {
                "type": "string",
                "description": "新标题。可选。",
            },
            "description": {
                "type": "string",
                "description": "新描述。可选。",
            },
            "active_form": {
                "type": "string",
                "description": "进行时描述。可选。",
            },
            "status": {
                "type": "string",
                "description": "新状态。用 'deleted' 永久删除任务。",
                "enum": [TASK_STATUS_PENDING, TASK_STATUS_IN_PROGRESS, TASK_STATUS_COMPLETED, TASK_STATUS_DELETED],
            },
            "owner": {
                "type": "string",
                "description": "分配给指定 agent 名。留空字符串取消分配。",
            },
            "add_blocks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "此任务阻塞的任务 ID 列表。A blocks B = B 不能在 A 完成前开始。",
            },
            "add_blocked_by": {
                "type": "array",
                "items": {"type": "string"},
                "description": "阻塞此任务的任务 ID 列表。B blockedBy A = B 不能在 A 完成前开始。",
            },
        },
        "required": ["task_id"],
    }

    def __init__(self, task_list: TaskList) -> None:
        self._tl = task_list

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False  # 任务状态变更不能并行

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("更新任务")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        task_id = args.get("task_id", "").strip()
        if not task_id:
            return ToolResult.error("task_id 不能为空")

        task = self._tl.read(task_id)
        if task is None:
            return ToolResult.error(f"任务 #{task_id} 不存在")

        # 解析参数
        update_kwargs: dict[str, Any] = {}
        if "subject" in args:
            update_kwargs["subject"] = args["subject"]
        if "description" in args:
            update_kwargs["description"] = args["description"]
        if "active_form" in args:
            update_kwargs["active_form"] = args["active_form"]
        if "status" in args:
            update_kwargs["status"] = args["status"]
        if "owner" in args:
            owner = args["owner"]
            update_kwargs["owner"] = owner if owner else None
        if "add_blocks" in args:
            update_kwargs["add_blocks"] = args["add_blocks"]
        if "add_blocked_by" in args:
            update_kwargs["add_blocked_by"] = args["add_blocked_by"]

        result = self._tl.update(task_id, **update_kwargs)

        if args.get("status") == TASK_STATUS_DELETED:
            return ToolResult(data=f"任务 #{task_id} 已删除")

        if result is None:
            return ToolResult.error(f"任务 #{task_id} 更新失败")

        status_str = result.status
        return ToolResult(
            data=f"任务 #{task_id} 已更新: 状态={status_str}"
            + (f", owner={result.owner}" if result.owner else "")
        )
