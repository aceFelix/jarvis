"""ZaiProvider 单元测试 — mock zai SDK 验证消息转换、thinking 参数与流式解析。

覆盖内容：
- 初始化：api_key / base_url / model 透传，thinking 开关
- stream()：reasoning_content → ThinkingDelta、content → TextDelta、
  tool_calls 分片累积 → ToolCall + ToolCallEnd、usage 解析
- thinking 参数注入（top_level thinking.type + reasoning_effort）
- 纯文本模型 skip_images、异常 → ProviderError、SDK 未安装 → ProviderError

zai-sdk 是同步客户端，测试用 monkeypatch 替换 zai.ZhipuAiClient。

@author aceFelix
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock

import pytest
import zai

from agent.core.message import ImageContent, Message, TextContent
from agent.llm.base import ProviderError, Stop, TextDelta, ThinkingDelta, ToolCall, ToolCallEnd, ToolDef, Usage
from agent.llm.zai_provider import ZaiProvider


class _ZDelta:
    """模拟 OpenAI 兼容的 stream delta。"""

    def __init__(self, content: str | None = None, reasoning_content: str | None = None,
                 tool_calls: list | None = None) -> None:
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls or []


class _ZChoice:
    """模拟 chunk choice。"""

    def __init__(self, delta: _ZDelta, finish_reason: str | None = None) -> None:
        self.delta = delta
        self.finish_reason = finish_reason


class _ZUsage:
    """模拟 usage。"""

    def __init__(self, prompt_tokens: int = 10, completion_tokens: int = 5,
                 details: Any = None) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.prompt_tokens_details = details


class _ZDetails:
    """模拟 prompt_tokens_details.cached_tokens。"""

    def __init__(self, cached_tokens: int = 0) -> None:
        self.cached_tokens = cached_tokens


class _ZFunction:
    """模拟 tool_call 的 function 分片。"""

    def __init__(self, name: str | None = None, arguments: str | None = None) -> None:
        self.name = name
        self.arguments = arguments


class _ZToolCall:
    """模拟 tool_call 分片。"""

    def __init__(self, index: int = 0, id: str | None = None,
                 function: _ZFunction | None = None) -> None:
        self.index = index
        self.id = id
        self.function = function


class _ZChunk:
    """模拟 stream chunk。"""

    def __init__(self, choices: list, usage: _ZUsage | None = None) -> None:
        self.choices = choices
        self.usage = usage


def _chunk(content: str | None = None, reasoning: str | None = None,
           tool_calls: list | None = None, finish_reason: str | None = None,
           usage: _ZUsage | None = None) -> _ZChunk:
    return _ZChunk(
        choices=[_ZChoice(_ZDelta(content=content, reasoning_content=reasoning, tool_calls=tool_calls),
                          finish_reason=finish_reason)],
        usage=usage,
    )


@pytest.fixture
def fake_client(monkeypatch) -> MagicMock:
    """替换 zai.ZhipuAiClient 为假客户端。"""
    client = MagicMock()
    client.chat.completions.create = MagicMock()
    monkeypatch.setattr(zai, "ZhipuAiClient", lambda **kw: client)
    return client


@pytest.fixture
def provider(fake_client: MagicMock) -> ZaiProvider:
    """开启思考的 provider 实例。"""
    return ZaiProvider(api_key="sk-test", model="glm-4.7", enable_thinking=True, thinking_budget=2000)


async def _collect(provider: ZaiProvider, **kw: Any) -> list:
    """收集 stream 事件。"""
    return [e async for e in provider.stream(
        model=kw.pop("model", "glm-4.7"),
        system=kw.pop("system", "sys"),
        messages=kw.pop("messages", [Message.user_text("hi")]),
        tools=kw.pop("tools", []),
        max_tokens=kw.pop("max_tokens", 100),
        **kw,
    )]


# ─────────────────────────────────────────────────────────────
# 初始化 / 开关
# ─────────────────────────────────────────────────────────────


class TestProviderInit:
    """初始化参数透传。"""

    def test_client_kwargs(self, monkeypatch) -> None:
        captured: dict = {}

        def fake_init(**kw: Any) -> MagicMock:
            captured.update(kw)
            return MagicMock()

        monkeypatch.setattr(zai, "ZhipuAiClient", fake_init)
        ZaiProvider(api_key="sk-test", base_url="https://open.bigmodel.cn/api/paas/v4", model="glm-4")
        assert captured["api_key"] == "sk-test"
        assert captured["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
        assert captured["timeout"] == 180.0

    def test_default_model_override(self, fake_client: MagicMock) -> None:
        p = ZaiProvider(api_key="sk", model="glm-5")
        assert p.default_model == "glm-5"

    def test_default_model(self, fake_client: MagicMock) -> None:
        p = ZaiProvider(api_key="sk")
        assert p.default_model == "glm-4.7-flash"

    def test_name(self, fake_client: MagicMock) -> None:
        assert ZaiProvider(api_key="sk").name == "zhipu"

    def test_thinking_toggle(self, fake_client: MagicMock) -> None:
        p = ZaiProvider(api_key="sk", enable_thinking=True)
        assert p.is_thinking_enabled() is True
        p.set_thinking_enabled(False)
        assert p.is_thinking_enabled() is False
        assert p._force_no_thinking is True
        p.set_thinking_enabled(True)
        assert p.is_thinking_enabled() is True

    def test_set_model_type(self, fake_client: MagicMock) -> None:
        p = ZaiProvider(api_key="sk")
        p.set_model_type("text")
        assert p._model_type == "text"

    def test_close_noop(self, fake_client: MagicMock) -> None:
        import asyncio

        p = ZaiProvider(api_key="sk")
        assert asyncio.run(p.close()) is None

    def test_sdk_missing_raises(self, monkeypatch) -> None:
        """zai SDK 不可导入 → ProviderError。"""
        monkeypatch.setitem(sys.modules, "zai", None)
        with pytest.raises(ProviderError, match="zai-sdk 未安装"):
            ZaiProvider(api_key="sk")


# ─────────────────────────────────────────────────────────────
# stream() 流式解析
# ─────────────────────────────────────────────────────────────


class TestStream:
    """流式事件解析。"""

    async def test_text_and_reasoning(self, provider: ZaiProvider, fake_client: MagicMock) -> None:
        """reasoning_content → ThinkingDelta，content → TextDelta。"""
        fake_client.chat.completions.create.return_value = iter([
            _chunk(reasoning="让我想想"),
            _chunk(content="答案是 42"),
            _chunk(content="", finish_reason="stop",
                   usage=_ZUsage(prompt_tokens=100, completion_tokens=5)),
        ])
        events = await _collect(provider)

        thinking = [e.text for e in events if isinstance(e, ThinkingDelta)]
        assert thinking == ["让我想想"]
        texts = [e.text for e in events if isinstance(e, TextDelta)]
        assert texts == ["答案是 42"]
        stop = [e for e in events if isinstance(e, Stop)][0]
        assert stop.reason == "stop"
        assert stop.usage.input_tokens == 100
        assert stop.usage.output_tokens == 5

    async def test_thinking_disabled_no_reasoning_event(
            self, fake_client: MagicMock) -> None:
        """关闭思考时 reasoning_content 不产生事件。"""
        p = ZaiProvider(api_key="sk", enable_thinking=False)
        fake_client.chat.completions.create.return_value = iter([
            _chunk(reasoning="隐藏的思考", content="答案"),
            _chunk(content="", finish_reason="stop"),
        ])
        events = await _collect(p)
        assert not any(isinstance(e, ThinkingDelta) for e in events)
        assert any(isinstance(e, TextDelta) for e in events)

    async def test_tool_call_accumulation(self, provider: ZaiProvider, fake_client: MagicMock) -> None:
        """工具调用分片累积 → ToolCall + ToolCallEnd。"""
        fake_client.chat.completions.create.return_value = iter([
            _chunk(tool_calls=[_ZToolCall(index=0, id="call_1",
                                          function=_ZFunction(name="Bash", arguments='{"cmd":'))]),
            _chunk(tool_calls=[_ZToolCall(index=0, function=_ZFunction(arguments=' "date"}'))]),
            _chunk(content="", finish_reason="tool_calls",
                   usage=_ZUsage(prompt_tokens=10, completion_tokens=20)),
        ])
        events = await _collect(provider)

        calls = [e for e in events if isinstance(e, ToolCall)]
        assert len(calls) == 1
        assert calls[0].id == "call_1"
        assert calls[0].name == "Bash"
        assert calls[0].input == {"cmd": "date"}
        assert any(isinstance(e, ToolCallEnd) for e in events)

    async def test_tool_call_invalid_args(self, provider: ZaiProvider, fake_client: MagicMock) -> None:
        """非法参数 JSON 回退空 dict。"""
        fake_client.chat.completions.create.return_value = iter([
            _chunk(tool_calls=[_ZToolCall(index=0, id="c1",
                                          function=_ZFunction(name="Bash", arguments="not json"))]),
            _chunk(content="", finish_reason="stop"),
        ])
        events = await _collect(provider)
        calls = [e for e in events if isinstance(e, ToolCall)]
        assert calls[0].input == {}

    async def test_empty_tool_call_skipped(self, provider: ZaiProvider, fake_client: MagicMock) -> None:
        """空 name + 空 args 的工具调用被跳过。"""
        fake_client.chat.completions.create.return_value = iter([
            _chunk(tool_calls=[_ZToolCall(index=0, id="", function=_ZFunction())]),
            _chunk(content="", finish_reason="stop"),
        ])
        events = await _collect(provider)
        assert not any(isinstance(e, ToolCall) for e in events)

    async def test_usage_cached_tokens(self, provider: ZaiProvider, fake_client: MagicMock) -> None:
        """prompt_tokens_details.cached_tokens 解析。"""
        fake_client.chat.completions.create.return_value = iter([
            _chunk(content="ok", finish_reason="stop",
                   usage=_ZUsage(prompt_tokens=50, completion_tokens=3,
                                 details=_ZDetails(cached_tokens=20))),
        ])
        events = await _collect(provider)
        stop = [e for e in events if isinstance(e, Stop)][0]
        assert stop.usage.cache_read_tokens == 20

    async def test_thinking_params_injected(self, provider: ZaiProvider, fake_client: MagicMock) -> None:
        """thinking 参数：top_level thinking.type=enabled + reasoning_effort=high。"""
        fake_client.chat.completions.create.return_value = iter([
            _chunk(content="ok", finish_reason="stop"),
        ])
        await _collect(provider, temperature=0.5)
        kwargs = fake_client.chat.completions.create.call_args.kwargs
        assert kwargs["thinking"] == {"type": "enabled"}
        assert kwargs["reasoning_effort"] == "high"
        assert kwargs["temperature"] == 0.5
        assert kwargs["max_tokens"] == 100
        assert kwargs["stream"] is True

    async def test_thinking_off_params(self, fake_client: MagicMock) -> None:
        p = ZaiProvider(api_key="sk", enable_thinking=False)
        fake_client.chat.completions.create.return_value = iter([
            _chunk(content="ok", finish_reason="stop"),
        ])
        await _collect(p)
        kwargs = fake_client.chat.completions.create.call_args.kwargs
        assert kwargs["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in kwargs

    async def test_tools_schema(self, provider: ZaiProvider, fake_client: MagicMock) -> None:
        """工具 schema 透传。"""
        fake_client.chat.completions.create.return_value = iter([
            _chunk(content="ok", finish_reason="stop"),
        ])
        tools = [ToolDef(name="Bash", description="run", input_schema={"type": "object"})]
        await _collect(provider, tools=tools)
        kwargs = fake_client.chat.completions.create.call_args.kwargs
        assert kwargs["tools"][0]["function"]["name"] == "Bash"

    async def test_skip_images_text_model(self, provider: ZaiProvider, fake_client: MagicMock) -> None:
        """纯文本模型：图片转占位符文本。"""
        provider.set_model_type("text")
        fake_client.chat.completions.create.return_value = iter([
            _chunk(content="ok", finish_reason="stop"),
        ])
        img = ImageContent(data="AAA", media_type="image/png")
        msgs = [Message(role="user", content=[TextContent(text="看图"), img])]
        await _collect(provider, messages=msgs)
        kwargs = fake_client.chat.completions.create.call_args.kwargs
        msg_text = kwargs["messages"][-1]["content"][0]["text"]
        assert "图片已省略" in msg_text

    async def test_error_raises_provider_error(self, provider: ZaiProvider, fake_client: MagicMock) -> None:
        """SDK 抛异常 → ProviderError（分类后用户消息）。"""
        fake_client.chat.completions.create.side_effect = RuntimeError("Connection refused")
        with pytest.raises(ProviderError, match="网络错误"):
            await _collect(provider)

    async def test_empty_choices_skipped(self, provider: ZaiProvider, fake_client: MagicMock) -> None:
        """无 choices 的 chunk 被跳过。"""
        fake_client.chat.completions.create.return_value = iter([
            _ZChunk(choices=[]),
            _chunk(content="ok", finish_reason="stop"),
        ])
        events = await _collect(provider)
        texts = [e.text for e in events if isinstance(e, TextDelta)]
        assert texts == ["ok"]
