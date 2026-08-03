"""QueryLoop.run() 全流程单元测试。

覆盖主流程（纯文本回复 / 工具调用循环 / 多轮工具 / 同轮多工具）、
中断（abort_event 预置 / LLM 流式被取消 / 工具执行被取消）、
错误处理（上下文过长触发压缩重试、网络错误重试一次、provider 故障转移、
无 fallback 结束）、空 assistant 消息、max_iterations 强制停止、
输出截断（stop reason=length）自动续写、思考内容累积、图片输入、
hooks（user_prompt 改输入 / assistant_response 触发）、in-place 切片同步
回归点（调用方持有的列表引用必须能看到完整对话历史），以及
set_thinking_enabled / is_thinking_enabled / compact_now / _stream_once
等辅助方法。

测试策略：
- ScriptedProvider：按预设脚本序列输出 LLMEvent（支持异常脚本），完全
  控制 LLM 流式行为，并可记录每次调用收到的 messages/tools。
- FakeOrchestrator：记录工具调用并返回固定工具结果。
- 默认 enable_compaction=False 专注主流程；压缩路径单独用
  monkeypatch 验证（compact_reactive / compact_messages / freeze_if_needed）。

@author aceFelix
"""

from __future__ import annotations

import asyncio

import pytest

from agent.core.context import ToolContext
from agent.core.hooks import HookEvent, HookRegistry, HookResult
from agent.core.layered_context import LayeredContext
from agent.core.message import (
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
)
from agent.core.query_loop import QueryLoop, _inject_teammate_notifications
from agent.core.result import ToolResult
from agent.core.tool import Tool, ToolRegistry
from agent.llm.base import (
    LLMEvent,
    LLMProvider,
    ProviderError,
    Stop,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolCallEnd,
    Usage,
)


class ScriptedProvider(LLMProvider):
    """按预设脚本输出事件序列的可控 provider。

    每次 stream() 调用消费脚本列表中的下一个元素：
    - 事件序列（list[LLMEvent]）：逐个 yield
    - 异常实例（BaseException）：直接 raise（模拟 ProviderError / CancelledError）
    - 可调用对象：先以 messages 为参调用，再按其返回值输出
    """

    name = "scripted"
    default_model = "scripted-1"

    def __init__(self, scripts) -> None:
        self.scripts = list(scripts)
        self.stream_calls = 0
        self.last_messages: list[Message] = []
        self.last_tools: list = []
        self._thinking = False

    async def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        tools: list,
        max_tokens: int = 4096,
        temperature: float | None = None,
    ):
        """每次调用消费一个脚本元素。"""
        self.stream_calls += 1
        self.last_messages = list(messages)
        self.last_tools = list(tools)
        if not self.scripts:
            yield Stop(reason="stop", usage=Usage())
            return
        script = self.scripts.pop(0)
        if isinstance(script, BaseException):
            raise script
        if callable(script):
            script = script(messages)
        for ev in script:
            yield ev

    def set_thinking_enabled(self, enabled: bool) -> None:
        """记录思考模式开关。"""
        self._thinking = enabled

    def is_thinking_enabled(self) -> bool:
        """返回当前思考模式状态。"""
        return self._thinking


class FakeOrchestrator:
    """记录工具调用并返回固定工具结果的假编排器。"""

    def __init__(self, result_content: str = "工具执行结果") -> None:
        self.calls: list[list[ToolUseContent]] = []
        self.result_content = result_content
        self.raise_cancelled = False

    async def execute_calls(self, tool_uses, ctx):
        """按输入顺序为每个 tool_use 生成一条固定结果。"""
        self.calls.append(list(tool_uses))
        if self.raise_cancelled:
            raise asyncio.CancelledError()
        return [
            ToolResultContent(tool_use_id=tu.id, content=self.result_content)
            for tu in tool_uses
        ]


class FakeTool(Tool):
    """最小可用工具，供注册表使用。"""

    name = "fake_tool"
    description = "测试工具"
    input_schema = {"type": "object", "properties": {}}

    async def call(self, args, ctx):
        """返回固定成功结果。"""
        return ToolResult.ok("fake_result")


class FakeUI:
    """记录 UI 回调的桩。"""

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warns: list[str] = []
        self.errors: list[str] = []
        self.thinkings: list[str] = []

    def info(self, text: str) -> None:
        self.infos.append(text)

    def warn(self, text: str) -> None:
        self.warns.append(text)

    def error(self, text: str) -> None:
        self.errors.append(text)

    def assistant_text(self, text: str) -> None:
        pass

    def assistant_thinking(self, text: str) -> None:
        self.thinkings.append(text)

    def tool_use(self, tool_name, tool_input, tool_use_id) -> None:
        pass

    def tool_result(self, tool_name, tool_use_id, content, *, is_error=False) -> None:
        pass

    def ask_user(self, prompt: str) -> str:
        """默认拒绝。"""
        return "n"


async def _noop_sleep(*args, **kwargs):
    """替代 asyncio.sleep 的 no-op，加速网络重试测试。"""
    return None


@pytest.fixture
def registry():
    """注册了一个 FakeTool 的工具注册表。"""
    reg = ToolRegistry()
    reg.register(FakeTool())
    return reg


def make_loop(provider, orchestrator, reg, **kwargs):
    """快捷构造 QueryLoop，默认关闭压缩/延迟加载/聊天检测以专注主流程。

    kwargs 可覆盖默认值（如 enable_compaction=True）。
    """
    defaults = {
        "enable_compaction": False,
        "deferred_loading": False,
        "chat_detection": False,
    }
    defaults.update(kwargs)
    return QueryLoop(
        provider=provider,
        registry=reg,
        orchestrator=orchestrator,
        **defaults,
    )


def make_ctx(ui=None) -> tuple[ToolContext, list[Message]]:
    """构造 ToolContext，messages 由外部持有（模拟 repl 调用方）。"""
    messages: list[Message] = []
    ctx = ToolContext(workdir=".", messages=messages, ui=ui)
    return ctx, messages


def _tool_script(tool_id: str = "t1", tool_name: str = "fake_tool") -> list[LLMEvent]:
    """构造一个"发起工具调用"的事件脚本。"""
    return [
        TextDelta("我来调用工具"),
        ToolCall(id=tool_id, name=tool_name, input={}),
        ToolCallEnd(id=tool_id),
        Stop(reason="stop", usage=Usage(input_tokens=10, output_tokens=20)),
    ]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


class TestRunMainFlow:
    """QueryLoop.run 主流程测试。"""

    async def test_run_pure_text_reply(self, registry):
        """纯文本回复：一轮结束，messages 含 user + assistant。"""
        provider = ScriptedProvider(
            [[TextDelta("你好，我是 J.A.R.V.I.S."), Stop(reason="stop", usage=Usage(input_tokens=10, output_tokens=5))]]
        )
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ui = FakeUI()
        ctx, msgs = make_ctx(ui=ui)

        stats = await loop.run("你好", ctx)

        assert stats.stopped_reason == "stop"
        assert stats.iterations == 1
        assert stats.tool_calls == 0
        assert stats.usage.output_tokens == 5
        assert provider.stream_calls == 1
        # 第 1 轮后 messages 里 user + assistant 消息都在
        assert len(msgs) == 2
        assert msgs[0].role == "user" and msgs[0].get_text() == "你好"
        assert msgs[1].role == "assistant" and msgs[1].get_text() == "你好，我是 J.A.R.V.I.S."

    async def test_run_tool_call_loop(self, registry):
        """工具调用循环：assistant 带 tool_use → orchestrator 执行 →
        工具结果回灌 → 再调 LLM → 纯文本结束。"""
        provider = ScriptedProvider([
            _tool_script(),
            [TextDelta("工具结果已收到"), Stop(reason="stop")],
        ])
        orch = FakeOrchestrator()
        loop = make_loop(provider, orch, registry)
        ctx, msgs = make_ctx()

        stats = await loop.run("帮我处理一下", ctx)

        assert stats.stopped_reason == "stop"
        assert stats.iterations == 2
        assert stats.tool_calls == 1
        assert len(orch.calls) == 1 and orch.calls[0][0].name == "fake_tool"
        # user + assistant(tool_use) + user(tool_result) + assistant
        assert len(msgs) == 4
        assert msgs[0].role == "user"
        assert msgs[1].role == "assistant" and len(msgs[1].get_tool_uses()) == 1
        # 工具结果回灌成 user 消息
        assert msgs[2].role == "user"
        assert any(isinstance(b, ToolResultContent) for b in msgs[2].content)
        assert msgs[3].role == "assistant" and msgs[3].get_text() == "工具结果已收到"

    async def test_run_multiple_tool_calls_one_round(self, registry):
        """同一轮模型发出多个 tool_use，应一次性交给 orchestrator。"""
        provider = ScriptedProvider([
            [
                TextDelta("并行调用"),
                ToolCall(id="a", name="fake_tool", input={}),
                ToolCallEnd(id="a"),
                ToolCall(id="b", name="fake_tool", input={}),
                ToolCallEnd(id="b"),
                Stop(reason="stop"),
            ],
            [TextDelta("全部完成"), Stop(reason="stop")],
        ])
        orch = FakeOrchestrator()
        loop = make_loop(provider, orch, registry)
        ctx, _ = make_ctx()

        stats = await loop.run("并行干活", ctx)

        assert stats.tool_calls == 2
        assert len(orch.calls) == 1 and len(orch.calls[0]) == 2

    async def test_run_multi_round_tools(self, registry):
        """多轮工具调用：tool → tool → text。"""
        provider = ScriptedProvider([
            _tool_script("a"),
            _tool_script("b"),
            [TextDelta("完成了"), Stop(reason="stop")],
        ])
        orch = FakeOrchestrator()
        loop = make_loop(provider, orch, registry)
        ctx, msgs = make_ctx()

        stats = await loop.run("开始", ctx)

        assert stats.stopped_reason == "stop"
        assert stats.iterations == 3
        assert stats.tool_calls == 2
        assert len(orch.calls) == 2
        # 历史: user + asst(a) + user(res) + asst(b) + user(res) + asst
        assert len(msgs) == 6

    async def test_run_usage_recorded(self, registry):
        """Stop 事件携带的 usage 应写入 stats。"""
        provider = ScriptedProvider([
            [TextDelta("ok"), Stop(reason="stop", usage=Usage(input_tokens=100, output_tokens=50))],
        ])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, _ = make_ctx()

        stats = await loop.run("hi", ctx)

        assert stats.usage.input_tokens == 100
        assert stats.usage.output_tokens == 50


# ---------------------------------------------------------------------------
# 中断
# ---------------------------------------------------------------------------


class TestRunAbort:
    """中断路径测试。"""

    async def test_abort_event_pre_set(self, registry):
        """run() 前 abort_event 已置位：立即以 aborted 结束，不调 LLM。"""
        provider = ScriptedProvider([[TextDelta("不会执行"), Stop()]])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, _ = make_ctx()
        ctx.abort_event.set()

        stats = await loop.run("你好", ctx)

        assert stats.stopped_reason == "aborted"
        assert stats.iterations == 0
        assert provider.stream_calls == 0

    async def test_cancelled_during_stream(self, registry):
        """LLM 流式输出时被取消（模拟 Ctrl+C）：aborted 并重置 abort_event。"""
        provider = ScriptedProvider([
            asyncio.CancelledError(),
            [TextDelta("ok"), Stop()],
        ])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, _ = make_ctx()
        old_event = ctx.abort_event

        stats = await loop.run("你好", ctx)

        assert stats.stopped_reason == "aborted"
        assert old_event.is_set()
        # 中断后 abort_event 被重置，避免影响后续 run()
        assert ctx.abort_event is not old_event
        assert provider.stream_calls == 1

    async def test_cancelled_during_orchestrator(self, registry):
        """工具执行阶段被取消（Ctrl+C）：优雅退出本轮，不 re-raise。"""
        provider = ScriptedProvider([_tool_script()])
        orch = FakeOrchestrator()
        orch.raise_cancelled = True
        loop = make_loop(provider, orch, registry)
        ctx, _ = make_ctx()
        old_event = ctx.abort_event

        stats = await loop.run("干活", ctx)

        assert stats.stopped_reason == "aborted"
        assert old_event.is_set()
        assert ctx.abort_event is not old_event


# ---------------------------------------------------------------------------
# ProviderError 处理
# ---------------------------------------------------------------------------


class TestRunProviderError:
    """ProviderError 各处理路径测试。"""

    async def test_context_too_long_compacts_and_retries(self, registry, monkeypatch):
        """上下文过长：compact_reactive 成功 → 压缩后重试本轮。"""

        async def _fake_compact_reactive(self, provider, model, *, keep_recent=None):
            return True

        monkeypatch.setattr(LayeredContext, "compact_reactive", _fake_compact_reactive)

        provider = ScriptedProvider([
            ProviderError("Request failed: prompt_too_long tokens ..."),
            [TextDelta("压缩后正常回复"), Stop(reason="stop")],
        ])
        loop = make_loop(provider, FakeOrchestrator(), registry, enable_compaction=True)
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)

        stats = await loop.run("写个报告", ctx)

        assert provider.stream_calls == 2
        assert stats.stopped_reason == "stop"
        assert any("上下文过长" in w for w in ui.warns)

    async def test_context_too_long_compact_fails_ends(self, registry, monkeypatch):
        """上下文过长但压缩失败（compact_reactive 返回 False）→ 以 provider_error 结束。"""

        async def _fake_compact_reactive(self, provider, model, *, keep_recent=None):
            return False

        monkeypatch.setattr(LayeredContext, "compact_reactive", _fake_compact_reactive)

        provider = ScriptedProvider([ProviderError("prompt_too_long ...")])
        loop = make_loop(provider, FakeOrchestrator(), registry, enable_compaction=True)
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)

        stats = await loop.run("写个报告", ctx)

        assert stats.stopped_reason == "provider_error"
        assert any("LLM 调用失败" in e for e in ui.errors)

    async def test_network_error_retries_once(self, registry, monkeypatch):
        """网络错误：自动重试一次后成功。"""
        monkeypatch.setattr("agent.core.query_loop.asyncio.sleep", _noop_sleep)

        provider = ScriptedProvider([
            ProviderError("网络错误: connection reset"),
            [TextDelta("重试成功"), Stop(reason="stop")],
        ])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)

        stats = await loop.run("你好", ctx)

        assert provider.stream_calls == 2
        assert stats.stopped_reason == "stop"
        assert any("网络异常" in w for w in ui.warns)

    async def test_network_error_retries_at_most_once(self, registry, monkeypatch):
        """网络错误最多重试 1 次：第二次错误直接结束。"""
        monkeypatch.setattr("agent.core.query_loop.asyncio.sleep", _noop_sleep)

        provider = ScriptedProvider([
            ProviderError("网络错误: timeout"),
            ProviderError("网络错误: timeout again"),
        ])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)

        stats = await loop.run("你好", ctx)

        assert provider.stream_calls == 2
        assert stats.stopped_reason == "provider_error"
        assert any("LLM 调用失败" in e for e in ui.errors)

    async def test_provider_failover_success(self, registry, monkeypatch):
        """主 provider 失败 → 故障转移到备选厂商并重试成功。"""
        new_provider = ScriptedProvider([[TextDelta("备选厂商回复"), Stop(reason="stop")]])
        monkeypatch.setattr("agent.bootstrap._build_provider", lambda *a, **k: new_provider)

        provider = ScriptedProvider([ProviderError("api error: 401 unauthorized")])
        loop = make_loop(
            provider,
            FakeOrchestrator(),
            registry,
            vendor_fallback="deepseek",
            custom_models={
                "ds-model": {"vendor": "deepseek", "base_url": "http://x", "api_key": "k"}
            },
        )
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)

        stats = await loop.run("你好", ctx)

        assert stats.stopped_reason == "stop"
        assert loop._provider is new_provider
        assert loop._model == "ds-model"
        assert any("备选厂商" in w for w in ui.warns)

    async def test_provider_failover_syncs_thinking_override(self, registry, monkeypatch):
        """故障转移后思考模式覆盖状态应同步到新 provider（语音模式保持关闭）。"""
        new_provider = ScriptedProvider([[TextDelta("备选"), Stop(reason="stop")]])
        monkeypatch.setattr("agent.bootstrap._build_provider", lambda *a, **k: new_provider)

        provider = ScriptedProvider([ProviderError("api error")])
        loop = make_loop(
            provider,
            FakeOrchestrator(),
            registry,
            vendor_fallback="deepseek",
            custom_models={"ds-model": {"vendor": "deepseek", "base_url": "http://x", "api_key": "k"}},
        )
        loop.set_thinking_enabled(False)
        ctx, _ = make_ctx()

        await loop.run("你好", ctx)

        assert new_provider.is_thinking_enabled() is False

    async def test_provider_error_no_fallback_ends(self, registry):
        """非网络错误且无 fallback：以 provider_error 结束，不回灌错误给 LLM。"""
        provider = ScriptedProvider([ProviderError("api error: 500")])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)

        stats = await loop.run("你好", ctx)

        assert stats.stopped_reason == "provider_error"
        assert provider.stream_calls == 1
        assert any("LLM 调用失败" in e for e in ui.errors)


# ---------------------------------------------------------------------------
# 边界情况
# ---------------------------------------------------------------------------


class TestRunEdgeCases:
    """空回复 / max_iterations / 输出截断等边界测试。"""

    async def test_empty_assistant_response(self, registry):
        """模型返回空回复（无任何内容块）→ empty_response，不入历史。"""
        provider = ScriptedProvider([[Stop(reason="stop")]])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ui = FakeUI()
        ctx, msgs = make_ctx(ui=ui)

        stats = await loop.run("你好", ctx)

        assert stats.stopped_reason == "empty_response"
        assert len(msgs) == 1  # 只有 user 消息
        assert any("空回复" in e for e in ui.errors)

    async def test_max_iterations_stops(self, registry):
        """模型持续调用工具达到 max_iterations → 强制停止。"""
        provider = ScriptedProvider([
            _tool_script("a"),
            _tool_script("b"),
            _tool_script("c"),
        ])
        loop = make_loop(provider, FakeOrchestrator(), registry, max_iterations=2)
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)

        stats = await loop.run("开始", ctx)

        assert stats.stopped_reason == "max_iterations"
        assert stats.iterations == 2
        assert provider.stream_calls == 2
        assert stats.tool_calls == 2
        assert any("最大迭代次数" in w for w in ui.warns)

    async def test_stop_reason_length_auto_continue(self, registry):
        """输出截断（stop reason=length）→ 自动续写下一轮。"""
        provider = ScriptedProvider([
            [TextDelta("长回复的第一部分"), Stop(reason="length", usage=Usage())],
            [TextDelta("续写完成"), Stop(reason="stop")],
        ])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ui = FakeUI()
        ctx, msgs = make_ctx(ui=ui)

        stats = await loop.run("写长文", ctx)

        assert stats.stopped_reason == "stop"
        assert provider.stream_calls == 2
        assert any("自动续写" in w for w in ui.warns)
        # 截断续写提示作为 user 消息追加进历史
        texts = [b.text for m in msgs for b in m.content if isinstance(b, TextContent)]
        assert any("输出被截断" in t for t in texts)
        # 最后一条 assistant 消息为续写内容
        assert msgs[-1].role == "assistant" and msgs[-1].get_text() == "续写完成"


# ---------------------------------------------------------------------------
# 思考内容 / 图片 / hooks
# ---------------------------------------------------------------------------


class TestRunContent:
    """思考内容累积、图片输入、hooks 测试。"""

    async def test_thinking_delta_accumulated(self, registry):
        """ThinkingDelta 累积为 ThinkingContent，TextDelta 为 TextContent。"""
        provider = ScriptedProvider([
            [ThinkingDelta("我先分析需求"), TextDelta("正式回答"), Stop(reason="stop")],
        ])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, msgs = make_ctx()

        await loop.run("分析一下", ctx)

        assistant = msgs[-1]
        assert assistant.role == "assistant"
        assert assistant.get_thinking() == "我先分析需求"
        assert assistant.get_text() == "正式回答"
        assert any(isinstance(b, ThinkingContent) for b in assistant.content)

    async def test_run_with_images(self, registry):
        """传入 images 时，user 消息应包含图片内容块。"""
        provider = ScriptedProvider([[TextDelta("看到图片了"), Stop(reason="stop")]])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, msgs = make_ctx()
        img = ImageContent(data="aGVsbG8=", media_type="image/png")

        await loop.run("看下这个", ctx, images=[img])

        assert any(isinstance(b, ImageContent) for b in msgs[0].content)

    async def test_hooks_triggered(self, registry, monkeypatch):
        """user_prompt 钩子可修改输入，assistant_response 钩子在回复后触发。"""
        reg = HookRegistry()
        calls = {"user_prompt": 0, "assistant": 0}

        def on_user_prompt(payload):
            calls["user_prompt"] += 1
            return HookResult(modify_input="被钩子修改的输入")

        async def on_assistant_response(payload):
            calls["assistant"] += 1

        reg.register(HookEvent.USER_PROMPT, on_user_prompt, name="u")
        reg.register(HookEvent.ASSISTANT_RESPONSE, on_assistant_response, name="a")
        monkeypatch.setattr("agent.core.hooks.get_hooks", lambda: reg)

        provider = ScriptedProvider([[TextDelta("好"), Stop(reason="stop")]])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, msgs = make_ctx()

        await loop.run("原始输入", ctx)

        assert calls["user_prompt"] == 1
        assert calls["assistant"] == 1
        # 钩子改写的输入进入对话历史
        assert msgs[0].get_text() == "被钩子修改的输入"


# ---------------------------------------------------------------------------
# 辅助方法
# ---------------------------------------------------------------------------


class TestHelperMethods:
    """set_thinking_enabled / compact_now / _build_tool_defs / _is_chat_only。"""

    async def test_set_thinking_enabled(self, registry):
        """set_thinking_enabled 同步 provider 并记录 override。"""
        provider = ScriptedProvider([])
        loop = make_loop(provider, FakeOrchestrator(), registry)

        # 未设置 override：回退到 provider 默认（不支持思考 → False）
        assert loop._thinking_override is None
        assert loop.is_thinking_enabled() is False
        # 设置 override：同步 provider 并记录
        loop.set_thinking_enabled(True)
        assert provider.is_thinking_enabled() is True
        assert loop.is_thinking_enabled() is True
        # 关闭思考
        loop.set_thinking_enabled(False)
        assert loop.is_thinking_enabled() is False
        # None 清除 override：回退到 provider 当前状态（保持上次设置，不会回滚）
        loop.set_thinking_enabled(None)
        assert loop._thinking_override is None
        assert loop.is_thinking_enabled() is False

    async def test_compact_now_disabled(self, registry):
        """enable_compaction=False 时 compact_now 直接返回 False。"""
        loop = make_loop(ScriptedProvider([]), FakeOrchestrator(), registry)
        ctx, _ = make_ctx()
        assert await loop.compact_now(ctx) is False

    async def test_compact_now_success(self, registry, monkeypatch):
        """compact_now 成功：ctx.messages 被替换为压缩结果，返回 True。"""
        from agent.core.memory.compactor import CompactResult

        new_msg = Message(role="user", content=[TextContent(text="摘要")])

        async def _fake_compact_messages(**kwargs):
            return CompactResult(
                new_messages=[new_msg],
                summary="s",
                pre_compact_tokens=100,
                post_compact_tokens=10,
                messages_summarized=3,
                messages_kept=1,
            )

        monkeypatch.setattr("agent.core.query_loop.compact_messages", _fake_compact_messages)

        loop = make_loop(ScriptedProvider([]), FakeOrchestrator(), registry, enable_compaction=True)
        ctx, msgs = make_ctx()
        msgs.append(Message(role="user", content=[TextContent(text="old")]))

        assert await loop.compact_now(ctx) is True
        assert ctx.messages == [new_msg]
        assert msgs == [new_msg]  # 调用方引用同步

    async def test_compact_now_no_summary_returns_false(self, registry, monkeypatch):
        """压缩结果 messages_summarized == 0 → 返回 False。"""
        from agent.core.memory.compactor import CompactResult

        async def _fake_compact_messages(**kwargs):
            return CompactResult(
                new_messages=[],
                summary="",
                pre_compact_tokens=10,
                post_compact_tokens=10,
                messages_summarized=0,
                messages_kept=2,
            )

        monkeypatch.setattr("agent.core.query_loop.compact_messages", _fake_compact_messages)

        loop = make_loop(ScriptedProvider([]), FakeOrchestrator(), registry, enable_compaction=True)
        ctx, _ = make_ctx()
        assert await loop.compact_now(ctx) is False

    async def test_compact_now_exception_returns_false(self, registry, monkeypatch):
        """压缩抛异常 → 返回 False 并 warn（不影响主流程）。"""

        async def _boom_compact(**kwargs):
            raise RuntimeError("摘要模型挂了")

        monkeypatch.setattr("agent.core.query_loop.compact_messages", _boom_compact)

        loop = make_loop(ScriptedProvider([]), FakeOrchestrator(), registry, enable_compaction=True)
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)
        assert await loop.compact_now(ctx) is False
        assert any("上下文压缩失败" in w for w in ui.warns)

    async def test_build_tool_defs_full_mode(self, registry):
        """deferred_loading=False 时返回注册表全部工具定义。"""
        loop = make_loop(ScriptedProvider([]), FakeOrchestrator(), registry)
        ctx, _ = make_ctx()
        defs = loop._build_tool_defs(ctx)
        assert {d.name for d in defs} == {"fake_tool"}

    async def test_is_chat_only_logic(self, registry):
        """纯聊天检测：短消息无关键词 → True；长消息/关键词/历史工具 → False。"""
        loop = make_loop(ScriptedProvider([]), FakeOrchestrator(), registry)

        ctx, _ = make_ctx()
        ctx.messages.append(Message(role="user", content=[TextContent(text="你好")]))
        assert loop._is_chat_only(ctx) is True

        ctx, _ = make_ctx()
        ctx.messages.append(Message(role="user", content=[TextContent(text="今天天气怎么样")]))
        assert loop._is_chat_only(ctx) is False  # 含动作词"天气"

        ctx, _ = make_ctx()
        ctx.messages.append(Message(role="user", content=[TextContent(text="请给我写一篇关于人工智能的详细报告，要求不少于五百字，还要举例说明各个技术细节。")]))
        assert loop._is_chat_only(ctx) is False  # 长消息

        ctx, _ = make_ctx()
        ctx.messages.append(Message(role="user", content=[TextContent(text="你好")]))
        ctx.messages.append(Message(role="assistant", content=[ToolUseContent(id="t1", name="fake_tool", input={})]))
        assert loop._is_chat_only(ctx) is False  # 历史有工具调用

    async def test_chat_detection_suppresses_tools(self, registry):
        """chat_detection=True 且输入为纯聊天 → 发给 LLM 的工具列表为空。"""
        provider = ScriptedProvider([[TextDelta("嗨"), Stop(reason="stop")]])
        loop = QueryLoop(
            provider=provider,
            registry=registry,
            orchestrator=FakeOrchestrator(),
            enable_compaction=False,
            deferred_loading=True,
            chat_detection=True,
        )
        ctx, _ = make_ctx()
        await loop.run("你好", ctx)
        assert provider.last_tools == []

        # 含动作词的输入 → 正常携带工具
        await loop.run("帮我查一下", ctx)
        assert provider.last_tools != []


# ---------------------------------------------------------------------------
# _stream_once
# ---------------------------------------------------------------------------


class TestStreamOnce:
    """_stream_once 事件累积与异常处理。"""

    async def test_stream_once_accumulates_events(self, registry):
        """思考/文本/工具调用/结束事件按序累积成 assistant 消息。"""
        provider = ScriptedProvider([
            [
                ThinkingDelta("思考"),
                TextDelta("文本"),
                ToolCall(id="t1", name="fake_tool", input={"a": 1}),
                ToolCallEnd(id="t1"),
                TextDelta("尾部"),
                Stop(reason="stop", usage=Usage()),
            ],
        ])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, _ = make_ctx()

        msg, last = await loop._stream_once(ctx)

        assert last.reason == "stop"
        assert isinstance(msg.content[0], ThinkingContent)
        assert msg.content[0].text == "思考"
        texts = [b for b in msg.content if isinstance(b, TextContent)]
        assert texts[0].text == "文本"
        assert texts[1].text == "尾部"
        uses = msg.get_tool_uses()
        assert len(uses) == 1 and uses[0].name == "fake_tool" and uses[0].input == {"a": 1}

    async def test_stream_once_provider_error_empty_blocks(self, registry):
        """ProviderError 且无任何内容块：不追加消息，直接传播异常。"""
        provider = ScriptedProvider([ProviderError("网络错误: boom")])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, msgs = make_ctx()
        msgs.append(Message(role="user", content=[TextContent(text="hi")]))

        with pytest.raises(ProviderError):
            await loop._stream_once(ctx)

        assert len(msgs) == 1  # 未追加

    async def test_stream_once_provider_error_partial_blocks(self, registry):
        """ProviderError 但已产出部分内容：部分内容先落盘再抛异常。"""

        class PartialProvider(LLMProvider):
            """中途失败、已输出部分文本的 provider。"""

            name = "partial"
            default_model = "m"

            async def stream(self, *, model, system, messages, tools, max_tokens=4096, temperature=None):
                yield TextDelta("部分输出")
                raise ProviderError("中途断流")

        loop = make_loop(PartialProvider(), FakeOrchestrator(), registry)
        ctx, msgs = make_ctx()
        msgs.append(Message(role="user", content=[TextContent(text="hi")]))

        with pytest.raises(ProviderError):
            await loop._stream_once(ctx)

        # 部分 assistant 内容被追加到 msgs
        assert len(msgs) == 2
        assert msgs[-1].role == "assistant" and msgs[-1].get_text() == "部分输出"


# ---------------------------------------------------------------------------
# 回归点与协作同步
# ---------------------------------------------------------------------------


class TestRunRegression:
    """in-place 切片同步回归点与队友消息注入。"""

    async def test_inplace_slice_sync_keeps_outer_reference(self, registry):
        """回归点：run() 用 in-place 切片同步而非重绑定，
        调用方持有的列表引用必须能看到完整对话历史。

        修复背景：之前用 ctx.messages = layered.messages 重绑定导致
        调用方列表脱钩，第 2 轮 LLM 标题永不触发、自动保存丢回复。
        """
        holder: list[Message] = []
        provider = ScriptedProvider([
            [TextDelta("第一轮回复"), Stop(reason="stop")],
            [TextDelta("第二轮回复"), Stop(reason="stop")],
        ])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx = ToolContext(workdir=".", messages=holder, ui=None)

        await loop.run("第 1 轮", ctx)

        # 引用未被重绑定
        assert ctx.messages is holder
        # 第 1 轮后 user + assistant 消息都在
        assert len(holder) == 2
        assert holder[0].role == "user" and holder[0].get_text() == "第 1 轮"
        assert holder[1].role == "assistant" and holder[1].get_text() == "第一轮回复"

        # 第 2 轮后历史继续累积在同一引用上
        await loop.run("第 2 轮", ctx)
        assert len(holder) == 4
        assert holder[2].role == "user" and holder[2].get_text() == "第 2 轮"
        assert holder[3].role == "assistant" and holder[3].get_text() == "第二轮回复"

    async def test_teammate_injection_hook_invoked(self, registry, monkeypatch):
        """工具执行后队友通知注入点应被触发，且注入消息进入对话历史。

        修复前: run() 中 _inject_teammate_notifications 读到的 ctx.messages
        不含刚追加到 layered 的工具结果，且注入后长度比较基准错误，
        导致注入消息无法同步回 layered（死代码）。
        修复后: 调用注入前先同步 ctx.messages 到 layered 最新状态，
        注入的额外消息能正确追加到 layered 并保留在对话历史中。
        """
        calls = {"n": 0}

        def fake_inject(ctx):
            calls["n"] += 1
            ctx.messages.append(Message(role="user", content=[TextContent(text="[队友状态更新]")]))

        monkeypatch.setattr("agent.core.query_loop._inject_teammate_notifications", fake_inject)

        provider = ScriptedProvider([
            _tool_script(),
            [TextDelta("完成"), Stop(reason="stop")],
        ])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, msgs = make_ctx()

        await loop.run("干活", ctx)

        # 工具轮后注入点被触发
        assert calls["n"] == 1
        # 注入的队友消息现在能正确进入对话历史
        texts = [b.text for m in msgs for b in m.content if isinstance(b, TextContent)]
        assert any("队友状态更新" in t for t in texts)

    async def test_freeze_if_needed_notifies_ui(self, registry, monkeypatch):
        """压缩开启时 freeze_if_needed 返回 True → UI 收到冻结提示。"""

        async def _fake_freeze(self, provider, model, *, window_limit=None, keep_recent=None, on_progress=None):
            return True

        monkeypatch.setattr(LayeredContext, "freeze_if_needed", _fake_freeze)

        provider = ScriptedProvider([[TextDelta("hi"), Stop(reason="stop")]])
        loop = make_loop(provider, FakeOrchestrator(), registry, enable_compaction=True)
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)

        await loop.run("你好", ctx)

        assert any("上下文冻结完成" in i for i in ui.infos)


# ---------------------------------------------------------------------------
# _inject_teammate_notifications
# ---------------------------------------------------------------------------


class TestInjectTeammateNotifications:
    """队友邮箱消息注入（多 Agent 团队）测试。"""

    def _make_msg(self, mtype, **kwargs):
        """构造一个简单的邮箱消息对象。"""
        defaults = dict(
            type=mtype, summary=None, from_name="队友A", task_subject=None,
            task_id=None, status=None, text=None, request_id=None,
            action=None, tool=None, approve=True,
        )
        defaults.update(kwargs)
        return type("MailMsg", (), defaults)()

    def test_inject_various_types(self, monkeypatch):
        """各类队友消息应渲染成文本注入对话。"""
        from agent.collaboration import mailbox as mailbox_mod
        from agent.collaboration import team as team_mod

        mgr = type("Mgr", (), {"active_team": "proj"})()
        monkeypatch.setattr(team_mod, "get_team_manager", lambda: mgr)

        messages = [
            self._make_msg("idle_notification", summary="空闲等待任务"),
            self._make_msg("task_claimed", task_subject="修复登录 bug", task_id=7),
            self._make_msg("task_completed", status="completed", summary="已完成重构", task_id=8),
            self._make_msg("plan_approval_request", text="计划详情", request_id="r1"),
            self._make_msg("permission_request", action="写文件", tool="FileWrite"),
            self._make_msg("shutdown_response", approve=False),
            self._make_msg("heartbeat"),  # 心跳不渲染
        ]
        monkeypatch.setattr(mailbox_mod, "read_mailbox", lambda *a, **k: messages)

        ctx = ToolContext(workdir=".", messages=[], ui=None)
        _inject_teammate_notifications(ctx)

        text = "".join(
            b.text for m in ctx.messages for b in m.content if isinstance(b, TextContent)
        )
        assert "空闲等待任务" in text
        assert "领取任务" in text and "#7" in text
        assert "[completed]" in text
        assert "请求审批计划" in text
        assert "请求权限" in text
        assert "拒绝关闭" in text
        # 心跳不出现
        assert "心跳" not in text and "heartbeat" not in text

    def test_no_active_team_returns(self, monkeypatch):
        """没有活跃团队时直接返回，不注入任何消息。"""
        from agent.collaboration import team as team_mod

        mgr = type("Mgr", (), {"active_team": None})()
        monkeypatch.setattr(team_mod, "get_team_manager", lambda: mgr)

        ctx = ToolContext(workdir=".", messages=[], ui=None)
        _inject_teammate_notifications(ctx)
        assert ctx.messages == []

    def test_no_messages_returns(self, monkeypatch):
        """邮箱为空时直接返回。"""
        from agent.collaboration import mailbox as mailbox_mod
        from agent.collaboration import team as team_mod

        mgr = type("Mgr", (), {"active_team": "proj"})()
        monkeypatch.setattr(team_mod, "get_team_manager", lambda: mgr)
        monkeypatch.setattr(mailbox_mod, "read_mailbox", lambda *a, **k: [])

        ctx = ToolContext(workdir=".", messages=[], ui=None)
        _inject_teammate_notifications(ctx)
        assert ctx.messages == []

    def test_inject_only_heartbeat_returns(self, monkeypatch):
        """邮箱里只有心跳消息（不渲染）→ 不注入任何文本。"""
        from agent.collaboration import mailbox as mailbox_mod
        from agent.collaboration import team as team_mod

        mgr = type("Mgr", (), {"active_team": "proj"})()
        monkeypatch.setattr(team_mod, "get_team_manager", lambda: mgr)
        monkeypatch.setattr(
            mailbox_mod, "read_mailbox", lambda *a, **k: [self._make_msg("heartbeat")]
        )

        ctx = ToolContext(workdir=".", messages=[], ui=None)
        _inject_teammate_notifications(ctx)
        assert ctx.messages == []

    def test_inject_import_error_returns(self, monkeypatch):
        """协作模块导入失败 → 静默返回，不注入。"""
        import sys

        monkeypatch.setitem(sys.modules, "agent.collaboration.team", None)

        ctx = ToolContext(workdir=".", messages=[], ui=None)
        _inject_teammate_notifications(ctx)
        assert ctx.messages == []


# ---------------------------------------------------------------------------
# 补充覆盖：hooks 故障 / on_assistant_text / 延迟工具 / 清理函数
# ---------------------------------------------------------------------------


class TestExtraCoverage:
    """补充分支覆盖（hooks 故障容错、on_assistant_text、延迟工具、清理函数）。"""

    class _BrokenHooks:
        """trigger 整体抛异常的 hooks 系统桩。"""

        async def trigger(self, *args, **kwargs):
            raise RuntimeError("hooks 系统故障")

    async def test_hooks_broken_do_not_break_run(self, registry, monkeypatch):
        """hooks 系统整体故障 → user_prompt / assistant_response 异常被吞，
        主流程照常完成。"""
        monkeypatch.setattr("agent.core.hooks.get_hooks", lambda: self._BrokenHooks())

        provider = ScriptedProvider([[TextDelta("正常回复"), Stop(reason="stop")]])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, msgs = make_ctx()

        stats = await loop.run("你好", ctx)

        assert stats.stopped_reason == "stop"
        assert len(msgs) == 2  # user + assistant 都保留

    async def test_on_assistant_text_callback(self, registry):
        """TextDelta 同时喂给 on_assistant_text 回调；回调异常被吞不影响主流程。"""
        received: list[str] = []

        def cb(text: str) -> None:
            received.append(text)

        provider = ScriptedProvider([[TextDelta("流式文本"), Stop(reason="stop")]])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, _ = make_ctx()
        ctx.on_assistant_text = cb

        stats = await loop.run("hi", ctx)

        assert stats.stopped_reason == "stop"
        assert received == ["流式文本"]

        # 回调抛异常 → 被吞掉，不影响主流程
        def bad_cb(text: str) -> None:
            raise RuntimeError("tts 故障")

        ctx2, _ = make_ctx()
        ctx2.on_assistant_text = bad_cb
        provider2 = ScriptedProvider([[TextDelta("继续"), Stop(reason="stop")]])
        loop2 = make_loop(provider2, FakeOrchestrator(), registry)

        stats2 = await loop2.run("hi", ctx2)
        assert stats2.stopped_reason == "stop"

    async def test_ui_receives_thinking_deltas(self, registry):
        """带 UI 时 ThinkingDelta 实时推送给 ui.assistant_thinking。"""
        provider = ScriptedProvider([[ThinkingDelta("思考中"), TextDelta("回答"), Stop()]])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)

        await loop.run("分析", ctx)

        assert ui.thinkings == ["思考中"]

    async def test_failover_build_provider_fails(self, registry, monkeypatch):
        """构建备选 provider 失败 → 不故障转移，以 provider_error 结束。"""

        def _boom(*args, **kwargs):
            raise RuntimeError("构建 provider 失败")

        monkeypatch.setattr("agent.bootstrap._build_provider", _boom)

        provider = ScriptedProvider([ProviderError("api error")])
        loop = make_loop(
            provider,
            FakeOrchestrator(),
            registry,
            vendor_fallback="deepseek",
            custom_models={"ds-model": {"vendor": "deepseek", "base_url": "http://x", "api_key": "k"}},
        )
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)

        stats = await loop.run("你好", ctx)

        assert stats.stopped_reason == "provider_error"
        assert loop._provider is provider  # 未切换
        assert any("LLM 调用失败" in e for e in ui.errors)

    async def test_deferred_tool_discovered(self, registry):
        """deferred_loading=True：核心工具始终携带，延迟工具仅在发现后携带。"""
        deferred_tool = FakeTool()
        deferred_tool.name = "lazy_tool"
        deferred_tool.deferred = True
        registry.register(deferred_tool)  # fake_tool（deferred=False）已注册

        loop = QueryLoop(
            provider=ScriptedProvider([]),
            registry=registry,
            orchestrator=FakeOrchestrator(),
            enable_compaction=False,
            deferred_loading=True,
            chat_detection=False,
        )
        ctx, _ = make_ctx()

        # 未发现延迟工具 → 只有核心工具
        assert {d.name for d in loop._build_tool_defs(ctx)} == {"fake_tool"}
        # 发现后 → 携带完整 schema
        ctx.extra["discovered_tools"] = {"lazy_tool"}
        assert {d.name for d in loop._build_tool_defs(ctx)} == {"fake_tool", "lazy_tool"}

    def test_evict_old_images_multiple(self):
        """多图消息：只保留最新一张，旧图替换为文字占位；
        非 user 消息与非 ToolResultContent block 跳过。"""
        from agent.core.query_loop import _evict_old_images

        img1 = ImageContent(data="fake1", media_type="image/jpeg")
        img2 = ImageContent(data="fake2", media_type="image/jpeg")
        msgs = [
            Message(role="user", content=[ToolResultContent(tool_use_id="c1", content="第一张", images=[img1])]),
            Message(role="assistant", content=[TextContent(text="assistant 消息")]),  # 非 user → 跳过
            Message(role="user", content=[TextContent(text="纯文本 user 消息")]),      # 非 tool_result → 跳过
            Message(role="user", content=[ToolResultContent(tool_use_id="c2", content="第二张", images=[img2])]),
        ]
        _evict_old_images(msgs)

        assert msgs[3].content[0].images == [img2]  # 最新保留
        assert msgs[0].content[0].images == []      # 旧图被清
        assert "截图已处理" in msgs[0].content[0].content
        # 非图片消息未被改动
        assert msgs[1].content[0].text == "assistant 消息"
        assert msgs[2].content[0].text == "纯文本 user 消息"

    def test_collapse_old_tool_results(self):
        """旧工具结果折叠为占位，最近 N 条保留完整。"""
        from agent.core.query_loop import _collapse_old_tool_results

        msgs = [
            Message(role="user", content=[ToolResultContent(tool_use_id="c1", content="r1")]),
            Message(role="user", content=[ToolResultContent(tool_use_id="c2", content="r2")]),
            Message(role="user", content=[ToolResultContent(tool_use_id="c3", content="r3")]),
        ]
        _collapse_old_tool_results(msgs, keep_recent=2)

        assert "已完成" in msgs[0].content[0].content
        assert msgs[1].content[0].content == "r2"
        assert msgs[2].content[0].content == "r3"
