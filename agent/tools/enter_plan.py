"""EnterPlanMode 工具 —— 复杂任务先规划、再执行。

用途：
- 模型完成规划阶段后可调此工具请求退出规划模式
- 如已生成方案文件（含路径），系统将其注入后续执行的 system prompt
- 如未指定 plan_file，系统从对话中提取最后生成的方案内容

工作流：
1. 用户说"先出方案再改代码" → 模型调 EnterPlanMode
2. 模型在只读模式下调研、分析、输出方案
3. 模型调 ExitPlanMode(plan_file=".jarvis/PLAN.md")
4. 用户审核方案，确认后进入执行阶段
5. plan 内容注入 system prompt，执行时可参考
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import JSONSchema, Tool


class EnterPlanModeTool(Tool):
    """进入规划模式——切换为只读，拒绝所有写操作。"""

    name = "EnterPlanMode"
    description = (
        "进入规划模式。此后所有写操作（文件写入、命令执行、代码修改）将被禁止，"
        "只允许只读操作（搜索、读取、分析）。适用于复杂任务需要先调研输出方案再执行的场景。"
        "规划完成后用 ExitPlanMode 退出并开始执行。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "进入规划模式的简短理由（如'重构认证模块需要先了解现有代码结构'）",
            },
        },
    }

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("进入规划模式")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        reason = args.get("reason", "").strip()
        old_mode = ctx.permission_mode
        ctx.permission_mode = "plan"
        # 标记：ExitPlanMode 时会用此 flag 重建 mode
        ctx.extra["_plan_mode_entered"] = True
        ctx.extra["_plan_mode_previous"] = old_mode

        msg = "已进入规划模式（只读）。"
        if reason:
            msg += f" 理由: {reason}"
        return ToolResult(data=msg)
