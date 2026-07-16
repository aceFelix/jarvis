"""LLM 抽象层。

抽象成 Provider 接口，便于切换 Anthropic / OpenAI / 本地模型 / Mock。

设计原则:
- Provider 只负责"消息 + 工具定义 -> 流式响应"的转换，不碰业务逻辑。
- 工具定义、消息格式统一用 agent.core 的内部表示，由 Provider 负责翻译。
- 流式响应统一成 LLMEvent 序列，query_loop 只消费这个抽象。
"""

from agent.llm.base import (
    LLMEvent,
    LLMProvider,
    ProviderError,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    Usage,
)
from agent.llm.mock import MockProvider

__all__ = [
    "LLMEvent",
    "LLMProvider",
    "ProviderError",
    "TextDelta",
    "ToolCall",
    "ToolCallEnd",
    "Usage",
    "MockProvider",
]
