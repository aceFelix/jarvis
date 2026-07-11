"""ExitPlanMode 工具 —— 提交方案，退出规划模式进入执行阶段。

致敬 Claude Code 的 ExitPlanModeV2Tool。

工作流：
1. EnterPlanMode → 只读调研分析 → 输出方案
2. ExitPlanMode(plan_file="...") → 保存方案到文件 → 用户审核
3. 用户确认 → 方案注入 system prompt → 恢复写权限 → 开始执行
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent.core.context import ToolContext
from agent.core.message import Message, TextContent
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import JSONSchema, Tool


class ExitPlanModeTool(Tool):
    """退出规划模式，提交方案。"""

    name = "ExitPlanMode"
    description = (
        "退出规划模式，提交制定的方案。如已输出到文件（plan_file），方案将注入后续执行上下文。"
        "如未指定 plan_file，会从对话中提取你最后输出的方案内容。"
        "用户审核通过后，写权限恢复，进入执行阶段。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "plan_file": {
                "type": "string",
                "description": "已写入的方案文件路径（如 '.jarvis/PLAN.md'、'plan.md'）。留空则从对话提取。",
            },
            "summary": {
                "type": "string",
                "description": "方案的简要摘要（3-5 句话），供用户快速审核。",
            },
        },
    }

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        # 需要用户确认才能退出规划——实际上用户必须审核方案
        return PermissionResult.allow("提交方案")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        plan_file = args.get("plan_file", "").strip()
        summary = args.get("summary", "").strip()

        plan_content = ""

        # 1. 从指定文件读取方案
        if plan_file:
            plan_path = Path(plan_file)
            if not plan_path.is_absolute():
                plan_path = Path(ctx.workdir) / plan_file
            if plan_path.exists():
                try:
                    plan_content = plan_path.read_text(encoding="utf-8")
                except Exception as e:
                    return ToolResult.error(f"无法读取方案文件: {e}")
            else:
                return ToolResult.error(f"方案文件不存在: {plan_path}")

        # 2. 未指定文件 → 从对话历史提取最后一段 assistant 文本
        if not plan_content:
            for msg in reversed(ctx.messages):
                if msg.role == "assistant":
                    text = msg.get_text()
                    if text.strip():
                        # 取最后 8000 字符（方案可能很长）
                        plan_content = text.strip()[-8000:]
                        break
            if not plan_content:
                return ToolResult.error("未找到方案内容（请先在对话中输出方案再退出规划）")

        # 3. 保存方案到 .jarvis/PLAN.md（自动落盘）
        jarvis_dir = Path(ctx.workdir) / ".jarvis"
        jarvis_dir.mkdir(parents=True, exist_ok=True)
        plan_path = jarvis_dir / "PLAN.md"
        plan_path.write_text(plan_content, encoding="utf-8")

        # 4. 将方案作为上下文注入到对话
        ctx.permission_mode = ctx.extra.pop("_plan_mode_previous", "default")
        ctx.extra.pop("_plan_mode_entered", None)
        ctx.extra["_plan_content"] = plan_content

        # 追加方案摘要到消息列表（让后续执行时 LLM 可见）
        preview = plan_content[:2000] + ("..." if len(plan_content) > 2000 else "")
        plan_msg = (
            "[以下是用户审核通过的方案，请严格按方案执行]\n\n"
            f"{preview}"
        )
        ctx.messages.append(Message(
            role="user",
            content=[TextContent(text=plan_msg)],
        ))

        result_lines = [
            "✅ 方案已提交",
            f"   已保存到: {plan_path}",
            f"   方案长度: {len(plan_content)} 字符",
        ]
        if summary:
            result_lines.insert(1, f"   摘要: {summary}")
        return ToolResult(data="\n".join(result_lines))
