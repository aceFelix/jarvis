"""OpenAIProvider 单元测试 — mock HTTP 验证参数构造、流式解析、错误处理。

测试覆盖:
- _derive_name 厂商检测
- stream() 参数构造（thinking 参数注入）
- 流式事件解析（ThinkingDelta / TextDelta / ToolCall / Stop）
- 错误处理（API 错误 → ProviderError）
- 消息序列化（_messages_to_openai）

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.message import Message, TextContent, ToolUseContent, ToolResultContent
from agent.llm.base import (
    LLMEvent,
    ProviderError,
    Stop,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolCallEnd,
    ToolDef,
    Usage,
)
from agent.llm.openai_provider import OpenAIProvider, _messages_to_openai, _parse_tool_args


# ── Mock 辅助 ──

@dataclass
class _MockDelta:
    """模拟 OpenAI stream chunk delta。"""
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list | None = None


@dataclass
class _MockChoice:
    """模拟 OpenAI stream chunk choice。"""
    delta: _MockDelta = field(default_factory=_MockDelta)
    finish_reason: str | None = None


@dataclass
class _MockUsage:
    """模拟 OpenAI usage 信息。"""
    prompt_tokens: int = 100
    completion_tokens: int = 50
    prompt_tokens_details: Any | None = None


@dataclass
class _MockChunk:
    """模拟 OpenAI stream chunk。"""
    choices: list[_MockChoice] = field(default_factory=lambda: [_MockChoice()])
    usage: _MockUsage | None = None


def _make_chunk(
    content: str | None = None,
    reasoning: str | None = None,
    tool_calls: list | None = None,
    finish_reason: str | None = None,
    usage: _MockUsage | None = None,
) -> _MockChunk:
    return _MockChunk(
        choices=[_MockChoice(
            delta=_MockDelta(content=content, reasoning_content=reasoning, tool_calls=tool_calls),
            finish_reason=finish_reason,
        )],
        usage=usage,
    )


def _make_stream(*chunks: _MockChunk):
    """构造模拟的异步流式响应。"""
    async def _gen():
        for c in chunks:
            yield c
    return _gen()


# ── Provider 实例 ──

@pytest.fixture
def provider() -> OpenAIProvider:
    """不带 api_key 的 OpenAIProvider（mock openai 包 + client）。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock()
    with patch("openai.AsyncOpenAI", return_value=mock_client):
        p = OpenAIProvider(api_key="sk-test", base_url=None,
                          enable_thinking=False, thinking_budget=0)
        return p


@pytest.fixture
def thinking_provider() -> OpenAIProvider:
    """开启思考的 OpenAIProvider（mock openai 包 + client）。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock()
    with patch("openai.AsyncOpenAI", return_value=mock_client):
        p = OpenAIProvider(api_key="sk-test", base_url="https://api.deepseek.com/v1",
                          enable_thinking=True, thinking_budget=2000)
        return p


# ── 测试 ──

class TestDeriveName:
    """_derive_name 配置表驱动测试。"""

    def test_dashscope_detection(self) -> None:
        assert OpenAIProvider._derive_name("https://dashscope.aliyuncs.com/v1") == "dashscope"

    def test_deepseek_detection(self) -> None:
        assert OpenAIProvider._derive_name("https://api.deepseek.com/v1") == "deepseek"

    def test_zhipu_detection(self) -> None:
        assert OpenAIProvider._derive_name("https://open.bigmodel.cn/api/v4") == "zhipu"

    def test_empty_url_default(self) -> None:
        assert OpenAIProvider._derive_name("") == "openai"

    def test_unknown_url_fallback(self) -> None:
        assert OpenAIProvider._derive_name("https://api.unknown.com/v1") == "openai_compatible"


class TestParameterConstruction:
    """stream() 请求参数构造测试（thinking 注入）。"""

    @pytest.mark.asyncio
    async def test_deepseek_thinking_params_injected(self, thinking_provider: OpenAIProvider) -> None:
        """DeepSeek provider 应注入 thinking.type + reasoning_effort。"""
        msgs = [Message(role="user", content=[TextContent(text="hello")])]

        mock_create = AsyncMock()
        mock_create.return_value = _make_stream(
            _make_chunk(content="hi", finish_reason="stop",
                       usage=_MockUsage(prompt_tokens=100, completion_tokens=2))
        )
        thinking_provider._client.chat.completions.create = mock_create

        events = [e async for e in thinking_provider.stream(
            model="deepseek-chat", system="sys", messages=msgs, tools=[], max_tokens=100
        )]

        # 验证 thinking 参数被注入
        call_kwargs = mock_create.call_args.kwargs
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"]["thinking"] == {"type": "enabled"}
        assert call_kwargs.get("reasoning_effort") == "high"
        # 验证流式输出
        assert any(isinstance(e, TextDelta) for e in events)
        assert any(isinstance(e, Stop) for e in events)

    @pytest.mark.asyncio
    async def test_dashscope_thinking_params_injected(self) -> None:
        """DashScope provider 应注入 enable_thinking + thinking_budget。"""
        with patch("openai.AsyncOpenAI") as mock_cls:
            p = OpenAIProvider(api_key="sk-test", base_url="https://dashscope.aliyuncs.com/v1",
                              enable_thinking=True, thinking_budget=1000)
        msgs = [Message(role="user", content=[TextContent(text="hello")])]

        mock_create = AsyncMock()
        mock_create.return_value = _make_stream(
            _make_chunk(content="hi", finish_reason="stop",
                       usage=_MockUsage(prompt_tokens=100, completion_tokens=2))
        )
        p._client.chat.completions.create = mock_create

        _ = [e async for e in p.stream(model="qwen", system="sys", messages=msgs, tools=[], max_tokens=100)]

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["extra_body"]["enable_thinking"] is True
        assert call_kwargs["extra_body"]["thinking_budget"] == 1000

    @pytest.mark.asyncio
    async def test_openai_no_thinking_params(self, provider: OpenAIProvider) -> None:
        """OpenAI（不支持思考）不应注入任何 thinking 参数。"""
        msgs = [Message(role="user", content=[TextContent(text="hello")])]

        mock_create = AsyncMock()
        mock_create.return_value = _make_stream(
            _make_chunk(content="hi", finish_reason="stop",
                       usage=_MockUsage(prompt_tokens=100, completion_tokens=2))
        )
        provider._client.chat.completions.create = mock_create

        _ = [e async for e in provider.stream(model="gpt-4o", system="sys", messages=msgs, tools=[], max_tokens=100)]

        call_kwargs = mock_create.call_args.kwargs
        assert "extra_body" not in call_kwargs or "thinking" not in str(call_kwargs.get("extra_body", {}))


class TestStreamParsing:
    """流式事件解析测试。"""

    @pytest.mark.asyncio
    async def test_text_stream(self, provider: OpenAIProvider) -> None:
        """普通文本流应产出 TextDelta → Stop。"""
        msgs = [Message(role="user", content=[TextContent(text="hello")])]

        mock_create = AsyncMock()
        mock_create.return_value = _make_stream(
            _make_chunk(content="Hello"),
            _make_chunk(content=" world", finish_reason="stop",
                       usage=_MockUsage(prompt_tokens=100, completion_tokens=5)),
        )
        provider._client.chat.completions.create = mock_create

        events = [e async for e in provider.stream(
            model="gpt-4o", system="sys", messages=msgs, tools=[], max_tokens=100
        )]

        text_events = [e for e in events if isinstance(e, TextDelta)]
        assert len(text_events) == 2
        assert text_events[0].text == "Hello"
        assert text_events[1].text == " world"

        stop_events = [e for e in events if isinstance(e, Stop)]
        assert len(stop_events) == 1
        assert stop_events[0].usage.input_tokens == 100

    @pytest.mark.asyncio
    async def test_thinking_stream(self, thinking_provider: OpenAIProvider) -> None:
        """思考模式流应产出 ThinkingDelta → TextDelta。"""
        msgs = [Message(role="user", content=[TextContent(text="复杂问题")])]

        mock_create = AsyncMock()
        mock_create.return_value = _make_stream(
            _make_chunk(reasoning="让我思考一下..."),
            _make_chunk(content="答案是42", finish_reason="stop",
                       usage=_MockUsage(prompt_tokens=200, completion_tokens=10)),
        )
        thinking_provider._client.chat.completions.create = mock_create

        events = [e async for e in thinking_provider.stream(
            model="deepseek-chat", system="sys", messages=msgs, tools=[], max_tokens=100
        )]

        thinking_events = [e for e in events if isinstance(e, ThinkingDelta)]
        assert len(thinking_events) == 1
        assert "思考" in thinking_events[0].text

        text_events = [e for e in events if isinstance(e, TextDelta)]
        assert len(text_events) == 1

    @pytest.mark.asyncio
    async def test_tool_call_stream(self, provider: OpenAIProvider) -> None:
        """工具调用流应产出 ToolCall + ToolCallEnd。"""
        msgs = [Message(role="user", content=[TextContent(text="查天气")])]

        # 模拟 tool_call delta（OpenAI 流式分片）
        class _MockToolCall:
            index: int = 0
            id: str = "call_123"
            function: Any = None

        class _MockFunction:
            name: str = "Bash"
            arguments: str = '{"command": "date"}'

        tc = _MockToolCall()
        tc.function = _MockFunction()

        mock_create = AsyncMock()
        mock_create.return_value = _make_stream(
            _make_chunk(tool_calls=[tc], finish_reason="tool_calls",
                       usage=_MockUsage(prompt_tokens=100, completion_tokens=20)),
        )
        provider._client.chat.completions.create = mock_create

        events = [e async for e in provider.stream(
            model="gpt-4o", system="sys", messages=msgs, tools=[
                ToolDef(name="Bash", description="Run a command", input_schema={})
            ], max_tokens=100
        )]

        tool_events = [e for e in events if isinstance(e, ToolCall)]
        assert len(tool_events) == 1
        assert tool_events[0].name == "Bash"
        assert tool_events[0].input == {"command": "date"}

        tool_end = [e for e in events if isinstance(e, ToolCallEnd)]
        assert len(tool_end) == 1


class TestErrorHandling:
    """错误处理测试。"""

    @pytest.mark.asyncio
    async def test_api_error_raises_provider_error(self, provider: OpenAIProvider) -> None:
        """API 调用失败应抛出 ProviderError（含分类后的用户友好消息）。"""
        msgs = [Message(role="user", content=[TextContent(text="hello")])]

        mock_create = AsyncMock(side_effect=RuntimeError("Connection refused"))
        provider._client.chat.completions.create = mock_create

        with pytest.raises(ProviderError, match="网络错误"):
            _ = [e async for e in provider.stream(
                model="gpt-4o", system="sys", messages=msgs, tools=[], max_tokens=100
            )]


class TestMessagesToOpenAI:
    """_messages_to_openai 序列化测试。"""

    def test_simple_user_message(self) -> None:
        msgs = [Message(role="user", content=[TextContent(text="hello")])]
        result = _messages_to_openai(msgs, "system")
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"

    def test_tool_use_and_result_roundtrip(self) -> None:
        """工具调用 + 结果应正确序列化为 OpenAI 格式。"""
        msgs = [
            Message(role="assistant", content=[
                ToolUseContent(id="call_1", name="Bash", input={"cmd": "date"})
            ]),
            Message(role="user", content=[
                ToolResultContent(tool_use_id="call_1", content="2026-07-30")
            ]),
        ]
        result = _messages_to_openai(msgs, "system")
        # 第一个是 system
        assert result[0]["role"] == "system"
        # 第二个是 assistant + tool_calls
        assert result[1]["role"] == "assistant"
        assert result[1]["tool_calls"][0]["function"]["name"] == "Bash"
        # 第三个是 tool result
        assert result[2]["role"] == "tool"
        assert "2026-07-30" in result[2]["content"]

    def test_tool_result_with_images_in_text_mode(self) -> None:
        """纯文本模式下，tool result 中的图片应替换为文字说明。"""
        from agent.core.message import ImageContent
        img = ImageContent(media_type="image/jpeg", data="fake")
        msgs = [
            Message(role="user", content=[
                ToolResultContent(tool_use_id="call_1", content="截图", images=[img])
            ]),
        ]
        result = _messages_to_openai(msgs, "system", skip_images=True)
        tool_content = result[1]["content"]
        assert "纯文本模型" in tool_content


class TestParseToolArgs:
    """_parse_tool_args 容错解析测试。"""

    def test_valid_json(self) -> None:
        assert _parse_tool_args('{"key": "value"}') == {"key": "value"}

    def test_truncated_bracket(self) -> None:
        """截断的 JSON（缺 }）应自动补全。"""
        result = _parse_tool_args('{"cmd": "ls"')
        assert result == {"cmd": "ls"}

    def test_truncated_string_and_bracket(self) -> None:
        """未闭合字符串 + 缺 } 的极端截断应返回空（安全回退）。"""
        result = _parse_tool_args('{"cmd": "ls')
        assert result == {}

    def test_empty_string(self) -> None:
        assert _parse_tool_args("") == {}

    def test_garbled_text_fallback(self) -> None:
        """乱码文本应返回空字典。"""
        assert _parse_tool_args("not json at all") == {}
