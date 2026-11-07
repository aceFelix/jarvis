"""长期记忆写入工具（2026-11）。

模型在用户明确要求「记住这件事」时调用本工具，把一条事实追加到长期记忆
``MEMORY.md``。两个范围：

- ``user``：``~/.jarvis/MEMORY.md``，跨项目共享的个人偏好/习惯；
- ``project``：``<当前项目>/.jarvis/MEMORY.md``，只属于这个仓库的事实。

只允许「追加」不允许「覆盖/删除」——整体编辑与清空是桌面设置面板里用户的动作，
避免模型一次误写把历史记忆抹掉。去重与文件路径解析在 ``memory.store``。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.memory.store import append_memory_entry
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool


class MemoryUpdateTool(Tool):
    """把一条长期记忆追加到用户级或项目级 MEMORY.md。

    @author aceFelix
    """

    name = "MemoryUpdate"
    description = (
        "把一条需要长期记住的事实写入 MEMORY.md（跨会话生效）。"
        "**何时使用**：用户明确说「记住…」「以后都…」「别忘了…」，"
        "或你发现一条会反复用到的稳定偏好/项目约定。"
        "**不要使用**：临时任务状态、一次性的路径/命令、密钥与密码、"
        "以及用户没要求记住的闲聊结论。"
        "scope=user 用于跨项目的个人偏好；scope=project 用于当前仓库的约定。"
        "只追加不覆盖，重复内容会自动跳过。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "要记住的一句话（简洁、可独立理解，不超过 2000 字符）",
            },
            "scope": {
                "type": "string",
                "enum": ["user", "project"],
                "description": "user=跨项目个人偏好；project=当前项目专属事实",
            },
        },
        "required": ["content", "scope"],
    }
    max_result_chars = 1_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        """写长期记忆不是只读操作（plan 模式应拒绝）。"""
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        """追加写同一文件需要串行，避免两条记忆互相覆盖。"""
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """固定路径 + 仅追加，风险低，默认放行。"""
        return PermissionResult.allow("写入长期记忆（固定路径、仅追加）")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        content = str(args.get("content", "")).strip()
        scope = str(args.get("scope", "")).strip()
        if not content:
            return ValidationResult.fail("content 不能为空")
        if scope not in ("user", "project"):
            return ValidationResult.fail("scope 必须是 user 或 project")
        if scope == "project" and not ctx.workdir:
            return ValidationResult.fail("当前没有选择项目，无法写入项目级记忆")
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        content = str(args.get("content", "")).strip()
        scope = str(args.get("scope", "")).strip()
        try:
            result = append_memory_entry(ctx.workdir, scope, content)
        except (ValueError, OSError) as e:
            return ToolResult(data=f"写入长期记忆失败: {e}", is_error=True)
        label = "用户级" if scope == "user" else "项目级"
        if result["added"]:
            return ToolResult(
                data=f"已写入{label}长期记忆: {result['entry']}\n文件: {result['path']}"
            )
        return ToolResult(
            data=f"{label}长期记忆已存在相同内容，未重复写入。\n文件: {result['path']}"
        )
