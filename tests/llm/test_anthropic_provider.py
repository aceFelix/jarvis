"""AnthropicProvider 单元测试 — 消息转换与流式事件解析（mock anthropic SDK）。

覆盖内容：
- _block_to_anthropic 各内容块分支（text / tool_use / image / tool_result 含多模态）
- _messages_to_anthropic 的 system 提取与 ThinkingContent 过滤
- _derive_name 的 base_url 推断（deepseek / anthropic / anthropic_compatible）
- stream() 事件解析：TextDelta / ToolCall / ToolCallEnd / Stop、usage、JSON 容错
- 异常路径：流内异常 → ProviderError；get_final_message 失败 → 空 usage

@author aceFelix
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import anthropic
import pytest

from agent.core.message import (
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
)
from agent.llm.anthropic_provider import (
    AnthropicProvider,
    _block_to_anthropic,
    _messages_to_anthropic,
)
from agent.llm.base import ProviderError, Stop, TextDelta, ToolCall, ToolCallEnd, ToolDef, Usage


class _Ev:
    """通用假事件对象（属性动态赋值）。"""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _FakeStream:
    """模拟 anthropic messages.stream 的异步上下文管理器 + 事件迭代。"""

    def __init__(self, events: list, final: Any = None) -> None:
        self._events = list(events)
        self._final = final

    async def __aenter__(self) -> "_FakeStream":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def __aiter__(self) -> AsyncIterator:
        async def _gen():
            for e in self._events:
                yield e

        return _gen()

    async def get_final_message(self) -> Any:
        return self._final


class _RaisingStream:
    """迭代时抛出异常的假流（模拟网络中断）。"""

    async def __aenter__(self) -> "_RaisingStream":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def __aiter__(self) -> AsyncIterator:
        async def _gen():
            raise RuntimeError("Connection refused")
            yield  # pragma: no cover

        return _gen()

    async def get_final_message(self) -> Any:
        raise AssertionError("不应到达 get_final_message")


def _final_message(usage: Any = None, stop_reason: str = "end_turn") -> Any:
    """构造 get_final_message 的返回值。"""
    return _Ev(usage=usage, stop_reason=stop_reason)


@pytest.fixture
def provider(monkeypatch) -> AnthropicProvider:
    """替换 anthropic.AsyncAnthropic 为假客户端。"""
    mock_client = MagicMock()
    mock_client.close = AsyncMock()
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: mock_client)
    p = AnthropicProvider(api_key="sk-test", base_url=None)
    p._client = mock_client
    return p


# ─────────────────────────────────────────────────────────────
# 内容块转换
# ─────────────────────────────────────────────────────────────


class TestBlockToAnthropic:
    """_block_to_anthropic 各分支。"""

    def test_text_content(self) -> None:
        assert _block_to_anthropic(TextContent(text="你好")) == {"type": "text", "text": "你好"}

    def test_tool_use_content(self) -> None:
        block = ToolUseContent(id="toolu_1", name="Bash", input={"cmd": "date"})
        assert _block_to_anthropic(block) == {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "Bash",
            "input": {"cmd": "date"},
        }

    def test_image_content(self) -> None:
        block = ImageContent(data="AAA", media_type="image/png")
        assert _block_to_anthropic(block) == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "AAA"},
        }

    def test_tool_result_without_images(self) -> None:
        block = ToolResultContent(tool_use_id="toolu_1", content="结果", is_error=True)
        assert _block_to_anthropic(block) == {
            "type": "tool_result",
            "tool_use_id": "toolu_1",
            "content": "结果",
            "is_error": True,
        }

    def test_tool_result_with_images(self) -> None:
        """带图片的 tool_result 序列化为 [text, image...] 列表（多模态）。"""
        img = ImageContent(data="BBB", media_type="image/jpeg")
        block = ToolResultContent(tool_use_id="toolu_1", content="截图", images=[img])
        result = _block_to_anthropic(block)
        assert result["type"] == "tool_result"
        assert result["content"] == [
            {"type": "text", "text": "截图"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "BBB"}},
        ]

    def test_unknown_block_raises(self) -> None:
        """未知内容块应抛 ValueError。"""
        with pytest.raises(ValueError, match="Unknown content block"):
            _block_to_anthropic(ThinkingContent(text="思考"))


class TestMessagesToAnthropic:
    """_messages_to_anthropic 消息转换。"""

    def test_system_extracted_and_joined(self) -> None:
        msgs = [
            Message.system_text("系统一"),
            Message.system_text("系统二"),
            Message.user_text("你好"),
        ]
        system, api_msgs = _messages_to_anthropic(msgs)
        assert system == "系统一\n\n系统二"
        assert len(api_msgs) == 1
        assert api_msgs[0]["role"] == "user"

    def test_thinking_content_filtered(self) -> None:
        """assistant 消息中的 ThinkingContent 必须被过滤（兼容端点不支持）。"""
        msgs = [
            Message(role="assistant", content=[
                ThinkingContent(text="思考过程"),
                TextContent(text="正式回答"),
            ]),
        ]
        _, api_msgs = _messages_to_anthropic(msgs)
        blocks = api_msgs[0]["content"]
        assert blocks == [{"type": "text", "text": "正式回答"}]

    def test_role_mapping(self) -> None:
        msgs = [Message(role="assistant", content=[ToolUseContent(id="t", name="Bash", input={})])]
        _, api_msgs = _messages_to_anthropic(msgs)
        assert api_msgs[0]["role"] == "assistant"
        assert api_msgs[0]["content"][0]["type"] == "tool_use"


# ─────────────────────────────────────────────────────────────
# _derive_name / 思考开关 / close
# ─────────────────────────────────────────────────────────────


class TestDeriveName:
    """_derive_name 的 base_url 推断。"""

    def test_empty_url(self) -> None:
        assert AnthropicProvider._derive_name("") == "anthropic"

    def test_deepseek(self) -> None:
        assert AnthropicProvider._derive_name("https://api.deepseek.com/anthropic") == "deepseek"

    def test_anthropic_dot_com(self) -> None:
        assert AnthropicProvider._derive_name("https://api.anthropic.com") == "anthropic"

    def test_anthropic_compatible(self) -> None:
        assert AnthropicProvider._derive_name("https://proxy.example.com/v1") == "anthropic_compatible"


class TestProviderBasics:
    """provider 实例基础行为。"""

    def test_name_property(self, monkeypatch) -> None:
        mock_client = MagicMock()
        monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: mock_client)
        p = AnthropicProvider(api_key="sk", base_url="https://api.deepseek.com/anthropic")
        assert p.name == "deepseek"
        p2 = AnthropicProvider(api_key="sk", base_url=None)
        assert p2.name == "anthropic"

    def test_thinking_toggle(self, provider: AnthropicProvider) -> None:
        # 思考模式默认开启（与 OpenAI Provider 的 enable_thinking=True 保持一致）
        assert provider.is_thinking_enabled() is True
        provider.set_thinking_enabled(False)
        assert provider.is_thinking_enabled() is False
        provider.set_thinking_enabled(True)
        assert provider.is_thinking_enabled() is True

    async def test_close(self, provider: AnthropicProvider) -> None:
        await provider.close()
        provider._client.close.assert_awaited_once()

    def test_default_model(self) -> None:
        assert AnthropicProvider.default_model == "claude-3-5-sonnet-20241022"


# ─────────────────────────────────────────────────────────────
# stream() 事件解析
# ─────────────────────────────────────────────────────────────


class TestStream:
    """流式事件解析。"""

    async def test_text_deltas_and_usage(self, provider: AnthropicProvider) -> None:
        """文本增量 + usage 解析 + Stop。"""
        events = [
            _Ev(type="message_start", message=_Ev(id="m1")),
            _Ev(type="content_block_start", index=0, content_block=_Ev(type="text")),
            _Ev(type="content_block_delta", index=0, delta=_Ev(type="text_delta", text="Hello")),
            _Ev(type="content_block_delta", index=0, delta=_Ev(type="text_delta", text=" world")),
        ]
        final = _final_message(
            usage=_Ev(input_tokens=10, output_tokens=5,
                      cache_read_input_tokens=2, cache_creation_input_tokens=3),
            stop_reason="end_turn",
        )
        provider._client.messages.stream.return_value = _FakeStream(events, final)

        msgs = [Message.user_text("hi")]
        out = [e async for e in provider.stream(
            model="claude-3-5-sonnet", system="sys", messages=msgs, tools=[], max_tokens=100
        )]

        texts = [e.text for e in out if isinstance(e, TextDelta)]
        assert texts == ["Hello", " world"]
        stop = [e for e in out if isinstance(e, Stop)][0]
        assert stop.reason == "end_turn"
        assert stop.usage.input_tokens == 10
        assert stop.usage.output_tokens == 5
        assert stop.usage.cache_read_tokens == 2
        assert stop.usage.cache_creation_tokens == 3

    async def test_tool_call_events(self, provider: AnthropicProvider) -> None:
        """input_json_delta 累积 → ToolCall + ToolCallEnd。"""
        events = [
            _Ev(type="content_block_start", index=0,
                content_block=_Ev(type="tool_use", id="toolu_1", name="Bash")),
            _Ev(type="content_block_delta", index=0,
                delta=_Ev(type="input_json_delta", partial_json='{"command": "date"}')),
        ]
        provider._client.messages.stream.return_value = _FakeStream(
            events, _final_message(usage=_Ev(input_tokens=1, output_tokens=1))
        )

        msgs = [Message.user_text("查时间")]
        out = [e async for e in provider.stream(
            model="claude", system="", messages=msgs,
            tools=[ToolDef(name="Bash", description="run", input_schema={})],
        )]

        calls = [e for e in out if isinstance(e, ToolCall)]
        assert len(calls) == 1
        assert calls[0].id == "toolu_1"
        assert calls[0].name == "Bash"
        assert calls[0].input == {"command": "date"}
        ends = [e for e in out if isinstance(e, ToolCallEnd)]
        assert len(ends) == 1
        assert ends[0].id == "toolu_1"

    async def test_json_repair_and_multi_tool(self, provider: AnthropicProvider) -> None:
        """未闭合 JSON 自动补全；非法 JSON 回退 _raw。"""
        events = [
            _Ev(type="content_block_start", index=0,
                content_block=_Ev(type="tool_use", id="t1", name="Read")),
            _Ev(type="content_block_delta", index=0,
                delta=_Ev(type="input_json_delta", partial_json='{"file_path": "a.txt"')),
            _Ev(type="content_block_delta", index=0,
                delta=_Ev(type="input_json_delta", partial_json="}")),
            _Ev(type="content_block_start", index=1,
                content_block=_Ev(type="tool_use", id="t2", name="Glob")),
            _Ev(type="content_block_delta", index=1,
                delta=_Ev(type="input_json_delta", partial_json="not json at all")),
        ]
        provider._client.messages.stream.return_value = _FakeStream(
            events, _final_message(usage=_Ev(input_tokens=1, output_tokens=1))
        )

        msgs = [Message.user_text("x")]
        out = [e async for e in provider.stream(
            model="claude", system="", messages=msgs, tools=[]
        )]

        calls = [e for e in out if isinstance(e, ToolCall)]
        assert len(calls) == 2
        assert calls[0].id == "t1"
        assert calls[0].input == {"file_path": "a.txt"}
        assert calls[1].id == "t2"
        assert calls[1].input == {"_raw": "not json at all"}

    async def test_no_tool_calls_only_stop(self, provider: AnthropicProvider) -> None:
        """无工具调用时仅产出 Stop。"""
        provider._client.messages.stream.return_value = _FakeStream(
            [_Ev(type="message_start")],
            _final_message(usage=_Ev(input_tokens=3, output_tokens=3), stop_reason="stop"),
        )
        msgs = [Message.user_text("hi")]
        out = [e async for e in provider.stream(
            model="claude", system="", messages=msgs, tools=[]
        )]
        assert len(out) == 1
        assert isinstance(out[0], Stop)

    async def test_final_message_failure_falls_back(self, provider: AnthropicProvider) -> None:
        """get_final_message 失败 → 空 usage + stop 兜底。"""
        class _FailFinal(_FakeStream):
            async def get_final_message(self):
                raise RuntimeError("final message not supported")

        provider._client.messages.stream.return_value = _FailFinal(
            [_Ev(type="content_block_delta", index=0, delta=_Ev(type="text_delta", text="hi"))]
        )
        msgs = [Message.user_text("hi")]
        out = [e async for e in provider.stream(
            model="claude", system="", messages=msgs, tools=[]
        )]
        stop = [e for e in out if isinstance(e, Stop)][0]
        assert stop.reason == "stop"
        assert stop.usage.total_tokens == 0

    async def test_stream_error_raises_provider_error(self, provider: AnthropicProvider) -> None:
        """流内异常 → ProviderError（分类后的用户消息）。"""
        provider._client.messages.stream.return_value = _RaisingStream()
        msgs = [Message.user_text("hi")]
        with pytest.raises(ProviderError, match="网络错误"):
            _ = [e async for e in provider.stream(
                model="claude", system="", messages=msgs, tools=[]
            )]

    async def test_request_kwargs_construction(self, provider: AnthropicProvider) -> None:
        """请求参数：cache_control、system 合并、temperature、默认 model。

        思考模式默认开启时不传 temperature（DeepSeek 文档：思考模式不支持 temperature），
        所以这里先关闭思考模式再验证 temperature 传递。
        """
        provider.set_thinking_enabled(False)  # 关闭思考以验证 temperature 传递
        provider._client.messages.stream.return_value = _FakeStream(
            [_Ev(type="message_start")],
            _final_message(usage=_Ev(input_tokens=1, output_tokens=1)),
        )
        msgs = [Message.system_text("来自消息的 system"), Message.user_text("hi")]
        tools = [ToolDef(name="Bash", description="run", input_schema={"type": "object"})]
        _ = [e async for e in provider.stream(
            model="", system="基础 system", messages=msgs, tools=tools,
            max_tokens=50, temperature=0.3,
        )]

        kwargs = provider._client.messages.stream.call_args.kwargs
        assert kwargs["model"] == AnthropicProvider.default_model
        # system 消息合并：基础 system + 消息里的 system
        assert "来自消息的 system" in kwargs["system"][0]["text"]
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        # 最后一个 tool 上标记 cache_control
        assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        assert kwargs["temperature"] == 0.3
        assert kwargs["max_tokens"] == 50
        assert kwargs["messages"][0]["role"] == "user"

    async def test_no_tools_no_tools_key(self, provider: AnthropicProvider) -> None:
        """无工具时请求里不应有 tools 键。"""
        provider._client.messages.stream.return_value = _FakeStream(
            [_Ev(type="message_start")],
            _final_message(usage=_Ev(input_tokens=1, output_tokens=1)),
        )
        _ = [e async for e in provider.stream(
            model="claude", system="", messages=[Message.user_text("hi")], tools=[]
        )]
        kwargs = provider._client.messages.stream.call_args.kwargs
        assert "tools" not in kwargs
