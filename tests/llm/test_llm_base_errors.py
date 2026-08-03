"""LLM 基础模块测试：base.py / errors.py / thinking.py。

覆盖内容：
- base.py: LLMEvent 各事件类型、Usage.total_tokens、LLMProvider 默认空实现
- errors.py: classify 八类错误分类、_extract_raw 的 body/status_code 提取、脱敏
- thinking.py: ThinkingConfig.supported、apply_thinking 的 extra_body/top_level 注入

@author aceFelix
"""

from __future__ import annotations

from agent.llm.base import (
    LLMProvider,
    Stop,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolCallEnd,
    ToolDef,
    Usage,
)
from agent.llm.errors import ErrorCategory, classify
from agent.llm.thinking import THINKING_CONFIGS, ThinkingConfig, apply_thinking


class _ConcreteProvider(LLMProvider):
    """实现 stream 的具体 Provider，用于测试基类默认实现。"""

    name = "test"

    async def stream(self, **kwargs):  # type: ignore[override]
        """返回空事件流。"""
        yield Stop()


# ─────────────────────────────────────────────────────────────
# base.py
# ─────────────────────────────────────────────────────────────


class TestUsage:
    """Usage token 统计。"""

    def test_total_tokens(self) -> None:
        assert Usage(input_tokens=100, output_tokens=50).total_tokens == 150
        assert Usage().total_tokens == 0
        assert Usage(input_tokens=10, output_tokens=20, cache_read_tokens=5).total_tokens == 30

    def test_defaults(self) -> None:
        u = Usage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.cache_read_tokens == 0
        assert u.cache_creation_tokens == 0


class TestEventTypes:
    """LLMEvent 各事件类型可构造。"""

    def test_construct(self) -> None:
        assert ThinkingDelta(text="思考").text == "思考"
        assert TextDelta(text="你好").text == "你好"
        tc = ToolCall(id="call_1", name="Bash", input={"cmd": "date"})
        assert tc.id == "call_1"
        assert ToolCallEnd(id="call_1").id == "call_1"
        stop = Stop(reason="length", usage=Usage(input_tokens=1, output_tokens=2))
        assert stop.reason == "length"
        assert stop.usage.total_tokens == 3
        # 默认值
        assert Stop().reason == "stop"
        assert ToolCall(id="x", name="y").input == {}
        td = ToolDef(name="Bash", description="run", input_schema={"type": "object"})
        assert td.name == "Bash"


class TestLLMProviderDefaults:
    """LLMProvider 基类默认实现。"""

    def test_thinking_defaults(self) -> None:
        p = _ConcreteProvider()
        assert p.is_thinking_enabled() is False
        assert p.set_thinking_enabled(True) is None  # 空实现，不报错

    def test_close_noop(self) -> None:
        import asyncio

        p = _ConcreteProvider()
        asyncio.run(p.close())  # 空实现可正常 await

    def test_stream_async_iterator(self) -> None:
        import asyncio

        p = _ConcreteProvider()
        events = asyncio.run(_collect(p))
        assert len(events) == 1
        assert isinstance(events[0], Stop)


async def _collect(provider: _ConcreteProvider) -> list:
    """把异步生成器收集成列表。"""
    return [e async for e in provider.stream(model="m", system="", messages=[], tools=[])]


# ─────────────────────────────────────────────────────────────
# errors.py
# ─────────────────────────────────────────────────────────────


class _Err(Exception):
    """带 status_code / body 属性的模拟 API 异常。"""

    def __init__(self, msg: str = "", status: int = 0, body: dict | None = None) -> None:
        super().__init__(msg)
        self.status_code = status
        if body is not None:
            self.body = body


class TestClassify:
    """classify 错误分类。"""

    def test_auth_401(self) -> None:
        ce = classify(_Err("invalid_api_key", 401))
        assert ce.category == ErrorCategory.AUTH
        assert "鉴权失败" in ce.user_message

    def test_auth_keyword(self) -> None:
        ce = classify(_Err("Authentication error"))
        assert ce.category == ErrorCategory.AUTH

    def test_rate_limit_429(self) -> None:
        ce = classify(_Err("", 429))
        assert ce.category == ErrorCategory.RATE_LIMIT
        assert "限流" in ce.user_message

    def test_rate_limit_keyword(self) -> None:
        ce = classify(_Err("Too many requests, please retry later"))
        assert ce.category == ErrorCategory.RATE_LIMIT

    def test_model_not_found_404(self) -> None:
        ce = classify(_Err("", 404))
        assert ce.category == ErrorCategory.MODEL_NOT_FOUND
        assert "模型" in ce.user_message

    def test_context_too_long(self) -> None:
        # 注意：消息里不能含 "token"（会先被 AUTH 关键字命中），用 prompt_too_long 触发
        ce = classify(_Err("prompt_too_long: your prompt is too long"))
        assert ce.category == ErrorCategory.CONTEXT_TOO_LONG

    def test_network_timeout(self) -> None:
        ce = classify(_Err("Connection timed out after 30s"))
        assert ce.category == ErrorCategory.NETWORK

    def test_network_503(self) -> None:
        ce = classify(_Err("", 503))
        assert ce.category == ErrorCategory.NETWORK

    def test_server_error_500(self) -> None:
        ce = classify(_Err("", 500))
        assert ce.category == ErrorCategory.SERVER_ERROR
        assert "服务端错误" in ce.user_message

    def test_bad_request_400(self) -> None:
        ce = classify(_Err("bad params", 400))
        assert ce.category == ErrorCategory.BAD_REQUEST
        assert "参数错误" in ce.user_message

    def test_unknown(self) -> None:
        ce = classify(_Err("some weird error"))
        assert ce.category == ErrorCategory.UNKNOWN
        assert "未识别" in ce.user_message

    def test_body_extraction(self) -> None:
        """从 exc.body.error 提取 code + message。"""
        exc = _Err("placeholder", 0, body={"error": {"code": "invalid_api_key", "message": "bad key"}})
        ce = classify(exc)
        assert ce.category == ErrorCategory.AUTH
        assert "[invalid_api_key] bad key" in ce.raw_message

    def test_status_code_message_prefix(self) -> None:
        """无 body 时 raw_message 带 HTTP 前缀。"""
        ce = classify(_Err("oops", 429))
        assert "HTTP 429: oops" in ce.raw_message

    def test_mask_api_key(self) -> None:
        """错误消息中的 sk- 密钥应被脱敏。"""
        ce = classify(_Err("Invalid key sk-abcdefghijklmnopqrstuvwxyz123456"))
        assert "sk-****" in ce.raw_message
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in ce.raw_message

    def test_raw_message_and_user_message_built(self) -> None:
        """user_message 应包含原始错误、标题与解释三部分。"""
        ce = classify(_Err("boom", 500))
        assert "原始错误" in ce.user_message
        assert "[ 服务端错误 ]" in ce.user_message

    def test_user_message_contains_raw(self) -> None:
        ce = classify(_Err("quota exhausted", 429))
        assert "quota exhausted" in ce.user_message


# ─────────────────────────────────────────────────────────────
# thinking.py
# ─────────────────────────────────────────────────────────────


class TestThinkingConfig:
    """ThinkingConfig 配置表。"""

    def test_config_table_keys(self) -> None:
        assert set(THINKING_CONFIGS) == {"dashscope", "deepseek", "zhipu", "dashscope_sdk", "zai_sdk"}

    def test_supported_property(self) -> None:
        assert THINKING_CONFIGS["dashscope"].supported is True
        assert ThinkingConfig(field="").supported is False
        assert ThinkingConfig(field="thinking").supported is True


class TestApplyThinking:
    """apply_thinking 参数注入。"""

    def test_extra_body_on(self) -> None:
        kwargs: dict = {}
        apply_thinking(kwargs, THINKING_CONFIGS["dashscope"], True, 1000)
        assert kwargs["extra_body"] == {"enable_thinking": True, "thinking_budget": 1000}

    def test_extra_body_off(self) -> None:
        kwargs: dict = {}
        apply_thinking(kwargs, THINKING_CONFIGS["dashscope"], False)
        assert kwargs["extra_body"] == {"enable_thinking": False}

    def test_extra_body_no_budget_when_zero(self) -> None:
        kwargs: dict = {}
        apply_thinking(kwargs, THINKING_CONFIGS["dashscope"], True, 0)
        assert kwargs["extra_body"] == {"enable_thinking": True}

    def test_deepseek_reasoning_effort(self) -> None:
        kwargs: dict = {}
        apply_thinking(kwargs, THINKING_CONFIGS["deepseek"], True)
        assert kwargs["extra_body"]["thinking"] == {"type": "enabled"}
        assert kwargs["reasoning_effort"] == "high"

    def test_deepseek_off_no_effort(self) -> None:
        kwargs: dict = {}
        apply_thinking(kwargs, THINKING_CONFIGS["deepseek"], False)
        assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in kwargs

    def test_top_level(self) -> None:
        kwargs: dict = {}
        apply_thinking(kwargs, THINKING_CONFIGS["dashscope_sdk"], True, 2000)
        assert kwargs["enable_thinking"] is True
        assert kwargs["thinking_budget"] == 2000
        assert "extra_body" not in kwargs

    def test_top_level_off(self) -> None:
        kwargs: dict = {}
        apply_thinking(kwargs, THINKING_CONFIGS["dashscope_sdk"], False)
        assert kwargs["enable_thinking"] is False
        assert "thinking_budget" not in kwargs

    def test_zai_sdk_top_level(self) -> None:
        kwargs: dict = {}
        apply_thinking(kwargs, THINKING_CONFIGS["zai_sdk"], True)
        assert kwargs["thinking"] == {"type": "enabled"}
        assert kwargs["reasoning_effort"] == "high"

    def test_unsupported_noop(self) -> None:
        kwargs: dict = {"model": "gpt-4o"}
        apply_thinking(kwargs, ThinkingConfig(field=""), True)
        assert kwargs == {"model": "gpt-4o"}
