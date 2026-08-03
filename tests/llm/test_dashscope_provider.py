"""DashScopeProvider 单元测试 — 消息转换与流式事件解析（mock dashscope SDK）。

覆盖内容：
- _messages_to_dashscope / _messages_to_dashscope_multimodal 两种格式转换
- provider 初始化：base_url 的 /api/v1 判定、默认 model
- 思考开关 set_thinking_enabled / is_thinking_enabled / set_model_type
- stream()：多模态 / 纯文本两条路径的事件产出、thinking 参数注入
- 异常路径：HTTP 错误响应、线程内异常 → ProviderError

dashscope SDK 是同步的，测试用假模块替换 sys.modules["dashscope"] 隔离。

@author aceFelix
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent.core.message import ImageContent, Message, TextContent, ToolResultContent, ToolUseContent
from agent.llm.base import ProviderError, Stop, TextDelta, ThinkingDelta, ToolCall, ToolCallEnd, ToolDef, Usage
from agent.llm.dashscope_provider import (
    DashScopeProvider,
    _messages_to_dashscope,
    _messages_to_dashscope_multimodal,
)


# ── Mock 辅助 ──


class _Msg:
    """模拟 DashScope 响应的 message 对象。"""

    def __init__(self, content: Any = None, reasoning_content: str = "", tool_calls: list | None = None) -> None:
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls or []


class _Choice:
    """模拟 choice 对象。"""

    def __init__(self, message: _Msg, finish_reason: str | None = None) -> None:
        self.message = message
        self.finish_reason = finish_reason


class _Output:
    """模拟 output 对象。"""

    def __init__(self, choices: list) -> None:
        self.choices = choices


class _Resp:
    """模拟 DashScope 响应对象。"""

    def __init__(self, output: _Output | None = None, usage: dict | None = None,
                 status_code: int = 200, message: str = "") -> None:
        self.output = output
        self.usage = usage
        self.status_code = status_code
        self.message = message


@pytest.fixture
def fake_dashscope(monkeypatch) -> types.SimpleNamespace:
    """用假模块替换 dashscope，隔离 SDK 全局状态。"""
    fake = types.SimpleNamespace(
        api_key=None,
        base_api_url="https://dashscope.aliyuncs.com/api/v1",
        Generation=MagicMock(),
        MultiModalConversation=MagicMock(),
    )
    monkeypatch.setitem(sys.modules, "dashscope", fake)
    return fake


@pytest.fixture
def provider(fake_dashscope) -> DashScopeProvider:
    """多模态类型的 provider 实例。"""
    return DashScopeProvider(
        api_key="sk-test", model="qwen-plus", enable_thinking=True,
        thinking_budget=1000, model_type="multimodal",
    )


# ─────────────────────────────────────────────────────────────
# 消息格式转换
# ─────────────────────────────────────────────────────────────


class TestMessagesToDashscope:
    """_messages_to_dashscope（纯文本 Generation 格式）。"""

    def test_system_and_user(self) -> None:
        msgs = [Message.system_text("sys"), Message.user_text("你好")]
        out = _messages_to_dashscope(msgs, "SYS")
        assert out[0] == {"role": "system", "content": "SYS"}
        assert out[1] == {"role": "user", "content": "你好"}

    def test_assistant_with_tool_calls(self) -> None:
        msgs = [
            Message(role="assistant", content=[
                TextContent(text="好的"),
                ToolUseContent(id="call_1", name="Bash", input={"cmd": "date"}),
            ]),
        ]
        out = _messages_to_dashscope(msgs, "")
        entry = out[1]
        assert entry["role"] == "assistant"
        assert entry["content"] == "好的"
        assert entry["tool_calls"][0]["id"] == "call_1"
        assert entry["tool_calls"][0]["function"]["name"] == "Bash"
        assert entry["tool_calls"][0]["function"]["arguments"] == '{"cmd": "date"}'

    def test_assistant_tool_call_only_no_content(self) -> None:
        msgs = [Message(role="assistant", content=[ToolUseContent(id="c", name="Bash", input={})])]
        out = _messages_to_dashscope(msgs, "")
        entry = out[1]
        assert "content" not in entry
        assert len(entry["tool_calls"]) == 1

    def test_user_tool_result(self) -> None:
        msgs = [Message(role="user", content=[ToolResultContent(tool_use_id="call_1", content="2026-07-30")])]
        out = _messages_to_dashscope(msgs, "")
        assert out[1]["role"] == "tool"
        assert out[1]["tool_call_id"] == "call_1"
        assert out[1]["content"] == "2026-07-30"

    def test_other_role(self) -> None:
        msgs = [Message(role="developer", content=[TextContent(text="规则")])]
        out = _messages_to_dashscope(msgs, "")
        assert out[1]["role"] == "developer"
        assert out[1]["content"] == "规则"

    def test_system_message_in_list_skipped(self) -> None:
        """messages 里的 system 消息应被跳过（已作为独立参数传入）。"""
        msgs = [Message.system_text("被跳过")]
        out = _messages_to_dashscope(msgs, "SYS")
        assert len(out) == 1
        assert out[0]["content"] == "SYS"


class TestMessagesToDashscopeMultimodal:
    """_messages_to_dashscope_multimodal（MultiModalConversation 格式）。"""

    def test_image_in_user_message(self) -> None:
        img = ImageContent(data="AAA", media_type="image/png")
        msgs = [Message(role="user", content=[TextContent(text="看图"), img])]
        out = _messages_to_dashscope_multimodal(msgs, "SYS")
        assert out[0]["content"] == [{"text": "SYS"}]
        assert out[1]["content"] == [
            {"text": "看图"},
            {"image": "data:image/png;base64,AAA"},
        ]

    def test_skip_images(self) -> None:
        img = ImageContent(data="AAA", media_type="image/png")
        msgs = [Message(role="user", content=[TextContent(text="看图"), img])]
        out = _messages_to_dashscope_multimodal(msgs, "SYS", skip_images=True)
        assert out[1]["content"] == [{"text": "看图"}]

    def test_tool_result_with_images(self) -> None:
        """带图片的 tool_result：文本合并 + 图片收集到 user 消息。"""
        img = ImageContent(data="BBB", media_type="image/jpeg")
        msgs = [Message(role="user", content=[
            ToolResultContent(tool_use_id="c1", content="截图结果", images=[img]),
        ])]
        out = _messages_to_dashscope_multimodal(msgs, "")
        assert out[1]["role"] == "user"
        assert out[1]["content"] == [
            {"text": "截图结果"},
            {"image": "data:image/jpeg;base64,BBB"},
        ]

    def test_tool_result_without_images_role_tool(self) -> None:
        msgs = [Message(role="user", content=[
            ToolResultContent(tool_use_id="c2", content="纯文本结果"),
        ])]
        out = _messages_to_dashscope_multimodal(msgs, "")
        assert out[1]["role"] == "tool"
        assert out[1]["tool_call_id"] == "c2"
        assert out[1]["content"] == [{"text": "纯文本结果"}]

    def test_assistant_content_list(self) -> None:
        msgs = [Message(role="assistant", content=[TextContent(text="回答")])]
        out = _messages_to_dashscope_multimodal(msgs, "")
        assert out[1]["content"] == [{"text": "回答"}]

    def test_other_role(self) -> None:
        msgs = [Message(role="developer", content=[TextContent(text="规则")])]
        out = _messages_to_dashscope_multimodal(msgs, "")
        assert out[1]["role"] == "developer"
        assert out[1]["content"] == [{"text": "规则"}]


# ─────────────────────────────────────────────────────────────
# 初始化 / 开关
# ─────────────────────────────────────────────────────────────


class TestProviderInit:
    """初始化与 base_url 处理。"""

    def test_base_url_with_api_v1(self, fake_dashscope) -> None:
        DashScopeProvider(api_key="sk", base_url="https://proxy.example.com/api/v1/")
        assert fake_dashscope.base_api_url == "https://proxy.example.com/api/v1"

    def test_base_url_without_api_v1_resets_default(self, fake_dashscope) -> None:
        """无 /api/v1 的 base_url 强制重置为 SDK 默认值。"""
        fake_dashscope.base_api_url = "https://wrong.example.com/api/v1"
        DashScopeProvider(api_key="sk", base_url="https://dashscope.aliyuncs.com")
        assert fake_dashscope.base_api_url == "https://dashscope.aliyuncs.com/api/v1"

    def test_base_url_property(self, fake_dashscope) -> None:
        p = DashScopeProvider(api_key="sk")
        assert p.base_url == "https://dashscope.aliyuncs.com/api/v1"

    def test_api_key_assigned(self, fake_dashscope) -> None:
        DashScopeProvider(api_key="sk-test-key")
        assert fake_dashscope.api_key == "sk-test-key"

    def test_default_model(self, provider: DashScopeProvider) -> None:
        assert provider.default_model == "qwen-plus"

    def test_default_model_fallback(self, fake_dashscope) -> None:
        p = DashScopeProvider(api_key="sk")
        assert p.default_model == "qwen3.5-flash"


class TestThinkingToggle:
    """思考模式开关。"""

    def test_default_enabled(self, provider: DashScopeProvider) -> None:
        assert provider.is_thinking_enabled() is True

    def test_disable(self, provider: DashScopeProvider) -> None:
        provider.set_thinking_enabled(False)
        assert provider.is_thinking_enabled() is False
        # 重新开启
        provider.set_thinking_enabled(True)
        assert provider.is_thinking_enabled() is True

    def test_set_model_type(self, provider: DashScopeProvider) -> None:
        provider.set_model_type("text")
        assert provider._model_type == "text"


# ─────────────────────────────────────────────────────────────
# stream() 流式解析
# ─────────────────────────────────────────────────────────────


async def _collect(provider: DashScopeProvider, fake: types.SimpleNamespace, **kw: Any) -> list:
    """通用：构造最小参数调用 stream 并收集事件。"""
    msgs = kw.pop("messages", None) or [Message.user_text("hi")]
    return [e async for e in provider.stream(
        model=kw.pop("model", "qwen-plus"),
        system=kw.pop("system", "sys"),
        messages=msgs,
        tools=kw.pop("tools", []),
        max_tokens=kw.pop("max_tokens", 100),
        **kw,
    )]


class TestStreamMultimodal:
    """多模态路径（MultiModalConversation.call）。"""

    async def test_events(self, provider: DashScopeProvider, fake_dashscope) -> None:
        """ThinkingDelta + TextDelta（增量模式）+ Stop + usage。"""
        fake_dashscope.MultiModalConversation.call.return_value = iter([
            _Resp(output=_Output([_Choice(_Msg(content=[{"text": "Hello"}], reasoning_content="思考1"))])),
            _Resp(
                output=_Output([_Choice(_Msg(content=[{"text": " world"}], reasoning_content="思考2"), finish_reason="stop")]),
                usage={"input_tokens": 100, "output_tokens": 10, "prompt_cache_hit_tokens": 5},
            ),
        ])
        events = await _collect(provider, fake_dashscope)

        thinking = [e for e in events if isinstance(e, ThinkingDelta)]
        assert [t.text for t in thinking] == ["思考1", "思考2"]
        texts = [e.text for e in events if isinstance(e, TextDelta)]
        assert texts == ["Hello", " world"]
        stop = [e for e in events if isinstance(e, Stop)][0]
        assert stop.reason == "stop"
        assert stop.usage.input_tokens == 100
        assert stop.usage.cache_read_tokens == 5

    async def test_call_kwargs(self, provider: DashScopeProvider, fake_dashscope) -> None:
        """thinking 参数注入 + tools + temperature。"""
        fake_dashscope.MultiModalConversation.call.return_value = iter([
            _Resp(output=_Output([_Choice(_Msg(content=[{"text": "ok"}]))])),
        ])
        tools = [ToolDef(name="Bash", description="run", input_schema={"type": "object"})]
        await _collect(provider, fake_dashscope, tools=tools, temperature=0.7)

        kwargs = fake_dashscope.MultiModalConversation.call.call_args.kwargs
        assert kwargs["enable_thinking"] is True
        assert kwargs["thinking_budget"] == 1000
        assert kwargs["stream"] is True
        assert kwargs["result_format"] == "message"
        assert kwargs["max_tokens"] == 100
        assert kwargs["temperature"] == 0.7
        assert kwargs["tools"][0]["function"]["name"] == "Bash"
        assert kwargs["messages"][0]["role"] == "system"

    async def test_thinking_disabled_no_reasoning(self, provider: DashScopeProvider, fake_dashscope) -> None:
        """关闭思考时不发 thinking 参数。"""
        provider.set_thinking_enabled(False)
        fake_dashscope.MultiModalConversation.call.return_value = iter([
            _Resp(output=_Output([_Choice(_Msg(content=[{"text": "x"}]))])),
        ])
        await _collect(provider, fake_dashscope)
        kwargs = fake_dashscope.MultiModalConversation.call.call_args.kwargs
        assert kwargs["enable_thinking"] is False


class TestStreamText:
    """纯文本路径（Generation.call，累积式 content）。"""

    async def test_cumulative_text(self, provider: DashScopeProvider, fake_dashscope) -> None:
        """Generation 的 content 是累积的，只发增量。"""
        provider.set_model_type("text")
        fake_dashscope.Generation.call.return_value = iter([
            _Resp(output=_Output([_Choice(_Msg(content="Hell"))])),
            _Resp(output=_Output([_Choice(_Msg(content="Hello world"), finish_reason="stop")])),
        ])
        events = await _collect(provider, fake_dashscope)

        texts = [e.text for e in events if isinstance(e, TextDelta)]
        assert texts == ["Hell", "o world"]

    async def test_cumulative_reasoning(self, provider: DashScopeProvider, fake_dashscope) -> None:
        """reasoning_content 累积时取增量。"""
        provider.set_model_type("text")
        fake_dashscope.Generation.call.return_value = iter([
            _Resp(output=_Output([_Choice(_Msg(content="", reasoning_content="思考"))])),
            _Resp(output=_Output([_Choice(_Msg(content="", reasoning_content="思考过程"), finish_reason="stop")])),
        ])
        events = await _collect(provider, fake_dashscope)
        thinking = [e.text for e in events if isinstance(e, ThinkingDelta)]
        assert thinking == ["思考", "过程"]

    async def test_thinking_off_syncs_reasoning(self, provider: DashScopeProvider, fake_dashscope) -> None:
        """关闭思考时同步 prev_reasoning，避免后续开启时增量错乱。"""
        provider.set_model_type("text")
        provider.set_thinking_enabled(False)
        fake_dashscope.Generation.call.return_value = iter([
            _Resp(output=_Output([_Choice(_Msg(content="", reasoning_content="累积思考内容"), finish_reason="stop")])),
        ])
        events = await _collect(provider, fake_dashscope)
        assert not any(isinstance(e, ThinkingDelta) for e in events)

    async def test_tool_calls_accumulated(self, provider: DashScopeProvider, fake_dashscope) -> None:
        """工具调用分片累积 → ToolCall + ToolCallEnd。"""
        provider.set_model_type("text")
        fake_dashscope.Generation.call.return_value = iter([
            _Resp(output=_Output([_Choice(_Msg(content="", tool_calls=[
                {"index": 0, "function": {"name": "Bash", "arguments": '{"command": "date"}'}, "id": "call_1"},
            ]))])),
        ])
        events = await _collect(provider, fake_dashscope)
        calls = [e for e in events if isinstance(e, ToolCall)]
        assert len(calls) == 1
        assert calls[0].id == "call_1"
        assert calls[0].name == "Bash"
        assert calls[0].input == {"command": "date"}
        assert any(isinstance(e, ToolCallEnd) for e in events)

    async def test_tool_call_bad_json(self, provider: DashScopeProvider, fake_dashscope) -> None:
        """工具参数非法 JSON → 空 input。"""
        provider.set_model_type("text")
        fake_dashscope.Generation.call.return_value = iter([
            _Resp(output=_Output([_Choice(_Msg(content="", tool_calls=[
                {"index": 0, "function": {"name": "Bash", "arguments": "not json"}},
            ]))])),
        ])
        events = await _collect(provider, fake_dashscope)
        calls = [e for e in events if isinstance(e, ToolCall)]
        assert calls[0].input == {}

    async def test_empty_tool_call_skipped(self, provider: DashScopeProvider, fake_dashscope) -> None:
        """name 和 args 都为空的工具调用被跳过。"""
        provider.set_model_type("text")
        fake_dashscope.Generation.call.return_value = iter([
            _Resp(output=_Output([_Choice(_Msg(content="", tool_calls=[{"index": 0}]))])),
        ])
        events = await _collect(provider, fake_dashscope)
        assert not any(isinstance(e, ToolCall) for e in events)


class TestStreamErrors:
    """异常路径。"""

    async def test_http_error_status(self, provider: DashScopeProvider, fake_dashscope) -> None:
        """HTTP 非 200 → ProviderError。"""
        fake_dashscope.MultiModalConversation.call.return_value = iter([
            _Resp(status_code=429, message="rate limit"),
        ])
        with pytest.raises(ProviderError, match="DashScope API error"):
            await _collect(provider, fake_dashscope)

    async def test_thread_exception(self, provider: DashScopeProvider, fake_dashscope) -> None:
        """线程内异常 → ProviderError（分类后用户消息）。"""
        fake_dashscope.MultiModalConversation.call.side_effect = RuntimeError("Connection refused")
        with pytest.raises(ProviderError, match="网络错误"):
            await _collect(provider, fake_dashscope)

    async def test_no_choices_skipped(self, provider: DashScopeProvider, fake_dashscope) -> None:
        """无 choices 的响应被跳过。"""
        fake_dashscope.MultiModalConversation.call.return_value = iter([
            _Resp(output=None),
            _Resp(output=_Output([_Choice(_Msg(content=[{"text": "ok"}]))])),
        ])
        events = await _collect(provider, fake_dashscope)
        texts = [e.text for e in events if isinstance(e, TextDelta)]
        assert texts == ["ok"]
