"""AskUser 工具 —— 向用户提问。

对应原项目 tools/AskUserQuestionTool/（简化版）。原版支持结构化多选项提问，
v0.1 先做最简单的"开放式文本提问"，让模型能在缺信息时主动询问用户。

返回的 ToolResult.data 会回传给 LLM，让模型基于用户回答继续。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool


class AskUserTool(Tool):
    name = "AskUser"
    description = (
        "向用户提问以获取澄清信息。当任务缺少必要细节（路径、参数、确认意图）时使用。"
        "返回用户的文本回答。避免滥用——能从上下文推断的就不要问。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "要问用户的问题（清晰、具体）",
            }
        },
        "required": ["question"],
    }
    max_result_chars = 2_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False  # 阻塞等待用户输入，不能与其他工具并行

    def requires_user_interaction(self) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        # 向用户提问本身无害
        return PermissionResult.allow("用户交互")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext):
        if not args.get("question", "").strip():
            return ValidationResult.fail("question 不能为空")
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        question = args["question"]
        if not ctx.ui:
            return ToolResult.error("当前环境无法与用户交互（无 UI）")

        answer = ctx.ui.ask_user(question)
        if not answer.strip():
            return ToolResult.ok(data="(用户未作答)")
        return ToolResult.ok(data=f"用户回答: {answer}")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and args.get("question"):
            q = args["question"]
            return f"提问 {q[:40]}{'...' if len(q) > 40 else ''}"
        return None
