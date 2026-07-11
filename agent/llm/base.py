"""LLM Provider 抽象基类与流式事件类型。

把不同厂商的流式响应统一成下面这套事件序列，query_loop 只依赖这个抽象：

    [TextDelta(text="..."), ..., ToolCall(id, name, input), ToolCallEnd(), TextDelta(...), Stop()]

一轮对话里模型可能输出多段文本 + 多个工具调用 + 结束。
query_loop 消费完事件流后，把 ToolCall 翻译成内部 ToolUseContent，
把 TextDelta 累积成 TextContent。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Union

from agent.core.message import Message
from agent.core.tool import JSONSchema


class ProviderError(RuntimeError):
    """LLM 调用错误（网络/鉴权/限流/超时）。"""


@dataclass
class Usage:
    """token 用量统计。对应原 EMPTY_USAGE。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ThinkingDelta:
    """思维链思考增量（qwen3.7-plus enable_thinking / DeepSeek R1 等）。

    在流式响应中，reasoning_content 先于 content 返回。
    query_loop 将这些累积成 ThinkingContent。
    """

    text: str


@dataclass
class TextDelta:
    """文本增量。query_loop 累积这些成一段 TextContent。"""

    text: str


@dataclass
class ToolCall:
    """模型发起的一个工具调用。

    一次响应流中可能包含多个 ToolCall（并行工具调用）。
    大多数 SDK 会先发 tool_call_start(name) 再发参数 delta，这里为简化，
    直接在事件里给出完整 input（input 可能为空 dict，表示无参调用）。
    """

    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallEnd:
    """标记一个工具调用参数流结束（简化版：和 ToolCall 一起到达）。"""

    id: str


@dataclass
class Stop:
    """响应结束。reason: stop | length | content_filter。"""

    reason: str = "stop"
    usage: Usage = field(default_factory=Usage)


# 一个完整的流由这些事件组成
LLMEvent = Union[ThinkingDelta, TextDelta, ToolCall, ToolCallEnd, Stop]


@dataclass
class ToolDef:
    """给 LLM 的工具定义（Provider 翻译成各厂商格式）。

    name/description/input_schema 直接来自 Tool 实例。
    """

    name: str
    description: str
    input_schema: JSONSchema


class LLMProvider(abc.ABC):
    """LLM Provider 抽象。

    实现者需要把 agent.core 的消息和工具定义翻译成厂商格式，调用 API，
    再把流式响应翻译回 LLMEvent 序列。
    """

    name: str = "base"
    default_model: str = ""

    @abc.abstractmethod
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
        """流式调用。必须 yield LLMEvent 序列，以 Stop 结尾。

        约定:
        - 出错时 raise ProviderError，不要 yield 半截序列。
        - ToolCall 的 input 一定是完整解析后的 dict（不要发参数 delta）。
        """
        ...
        # 让 mypy 满意：这是 async generator
        yield TextDelta("")  # pragma: no cover

    # ---- 思考模式统一控制 ----
    # 各子类按需 override，把开关落地到各自的 API 参数。
    # voice_loop 进入语音模式时调 set_thinking_enabled(False) 关闭思考，
    # 退出时恢复。基类默认空实现（不支持思考的 provider 无需处理）。
    def set_thinking_enabled(self, enabled: bool) -> None:
        """统一开关深度思考模式。默认空实现，子类按需 override。"""
        return None

    def is_thinking_enabled(self) -> bool:
        """返回当前思考模式是否开启。默认 False（不支持思考的 provider）。"""
        return False

    async def close(self) -> None:
        """释放资源（HTTP client 等）。默认空实现。"""
        return None
