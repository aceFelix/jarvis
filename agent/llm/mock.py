"""Mock Provider —— 无需 API key 即可跑通整个骨架。

这是开发调试利器。它会模拟一个"会调用工具的助手"，让你在没有真实 LLM 的
情况下验证:
- query loop 是否正确处理工具调用
- 工具结果是否正确回传给模型
- 权限系统是否在工具调用前生效

脚本可配置: 通过 user 输入的关键词触发不同的模拟响应。
默认行为: 收到包含"列表/ls"的消息时调用 GlobTool，收到"读/read"时调用
FileReadTool，否则回一段固定文本结束。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from agent.core.message import Message
from agent.llm.base import (
    LLMEvent,
    LLMProvider,
    Stop,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    ToolDef,
    Usage,
)


class MockProvider(LLMProvider):
    """模拟 provider，按脚本响应。"""

    name = "mock"
    default_model = "mock-1"

    def __init__(self, *, think_delay: float = 0.3) -> None:
        self._think_delay = think_delay

    async def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        tools: list[ToolDef],
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> AsyncIterator[LLMEvent]:
        # 取最后一条 user 消息作为"指令"
        last_user_text = ""
        for msg in reversed(messages):
            if msg.role == "user":
                last_user_text = msg.get_text().lower()
                break

        tool_names = {t.name for t in tools}

        await asyncio.sleep(self._think_delay)

        # 按关键词触发不同工具调用，演示 query loop 的工具执行路径
        if any(kw in last_user_text for kw in ["列表", "列出", "list", "ls", "文件"]) and "Glob" in tool_names:
            yield TextDelta(text="好的，我来列出当前目录的 Python 文件。\n\n")
            yield ToolCall(
                id="mock_call_1",
                name="Glob",
                input={"pattern": "*.py"},
            )
            yield ToolCallEnd(id="mock_call_1")
            yield TextDelta(text="\n\n以上是当前目录的 Python 文件列表。")
            yield Stop(reason="stop", usage=Usage(input_tokens=50, output_tokens=30))
            return

        if any(kw in last_user_text for kw in ["读", "read", "查看"]) and "FileRead" in tool_names:
            yield TextDelta(text="我来读取这个文件。\n\n")
            yield ToolCall(
                id="mock_call_1",
                name="FileRead",
                input={"file_path": "README.md"},
            )
            yield ToolCallEnd(id="mock_call_1")
            yield Stop(reason="stop", usage=Usage(input_tokens=50, output_tokens=30))
            return

        if any(kw in last_user_text for kw in ["待办", "todo", "计划"]) and "TodoWrite" in tool_names:
            yield TextDelta(text="我来帮你规划任务清单。\n\n")
            yield ToolCall(
                id="mock_call_1",
                name="TodoWrite",
                input={
                    "todos": [
                        {"content": "分析需求", "status": "completed"},
                        {"content": "设计方案", "status": "in_progress"},
                        {"content": "实现功能", "status": "pending"},
                    ]
                },
            )
            yield ToolCallEnd(id="mock_call_1")
            yield Stop(reason="stop", usage=Usage(input_tokens=50, output_tokens=40))
            return

        # 默认: 回一段固定文本，不调用工具
        yield TextDelta(
            text=(
                f"[Mock 模式] 我收到了你的消息: 「{last_user_text[:60]}」\n\n"
                "这是 mock provider 的占位回复。要演示工具调用，请在消息里包含:\n"
                "  - 「列表」/「list」 -> 触发 GlobTool\n"
                "  - 「读」/「read」  -> 触发 FileReadTool\n"
                "  - 「待办」/「todo」-> 触发 TodoWriteTool\n\n"
                "接入真实 LLM 请用 --provider anthropic 或 --provider openai。"
            )
        )
        yield Stop(reason="stop", usage=Usage(input_tokens=20, output_tokens=80))
