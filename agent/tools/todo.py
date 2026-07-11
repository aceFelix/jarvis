"""TodoWrite 工具 —— 任务清单管理。

对应原项目 tools/TodoWriteTool/。模型用它来规划和展示进度。
不是文件操作，状态存在 ctx.extra['todos'] 里（会话级）。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool


class TodoWriteTool(Tool):
    name = "TodoWrite"
    description = (
        "创建或更新任务清单。传入完整的 todos 数组（每次都是全量替换）。"
        "用于规划复杂任务、展示进度。status 可选: pending / in_progress / completed。"
        "同一时间应只有一个 in_progress。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "任务列表（全量）",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["todos"],
    }
    max_result_chars = 2_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False  # 改状态，不算只读

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False  # 全量替换，不能并行

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        # 任务管理是安全的，无副作用
        return PermissionResult.allow("任务管理操作")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext):
        todos = args.get("todos")
        if not isinstance(todos, list):
            return ValidationResult.fail("todos 必须是数组")
        in_progress = sum(1 for t in todos if t.get("status") == "in_progress")
        if in_progress > 1:
            return ValidationResult.fail(
                f"同时有 {in_progress} 个 in_progress 任务，应只有一个"
            )
        valid_statuses = {"pending", "in_progress", "completed"}
        for t in todos:
            if t.get("status") not in valid_statuses:
                return ValidationResult.fail(
                    f"无效 status: {t.get('status')}（应为 pending/in_progress/completed）"
                )
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        todos = args["todos"]
        # 存到会话上下文
        ctx.extra["todos"] = list(todos)

        # 展示给用户
        if ctx.ui:
            lines = []
            for i, t in enumerate(todos, start=1):
                status = t.get("status", "pending")
                mark = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(
                    status, "[ ]"
                )
                lines.append(f"  {i}. {mark} {t.get('content', '')}")
            ctx.ui.info("任务清单:\n" + "\n".join(lines))

        done = sum(1 for t in todos if t.get("status") == "completed")
        total = len(todos)
        return ToolResult.ok(
            data=f"任务清单已更新（{done}/{total} 完成）"
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and isinstance(args.get("todos"), list):
            return f"更新任务清单（{len(args['todos'])} 项）"
        return None
