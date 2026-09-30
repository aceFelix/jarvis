"""QueryLoop 内容累积、辅助方法与 _stream_once 单元测试。

覆盖：思考内容累积、图片输入、hooks（user_prompt 改输入 /
assistant_response 触发）、set_thinking_enabled / is_thinking_enabled /
compact_now 等辅助方法、_stream_once 流式事件与异常处理，以及 in-place
切片同步回归点（调用方持有的列表引用必须能看到完整对话历史）。

测试替身与工厂见 tests/_query_loop_fakes.py。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.core.context import ToolContext
from agent.core.hooks import HookEvent, HookRegistry, HookResult
from agent.core.layered_context import LayeredContext
from agent.core.message import (
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolUseContent,
)
from agent.core.query_loop import QueryLoop, _inject_teammate_notifications
from agent.llm.base import (
    LLMProvider,
    ProviderError,
    Stop,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolCallEnd,
    Usage,
)

from tests._query_loop_fakes import (
    FakeOrchestrator,
    FakeUI,
    ScriptedProvider,
    _tool_script,
    make_ctx,
    make_loop,
    registry,
)


# ---------------------------------------------------------------------------
# 内容累积与 Hooks
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

        # 2026-09-30 桌面端复现句：工作区/环境指代类问句曾被误判为纯聊天，
        # 导致本轮 0 工具发给模型，模型仍凭习惯吐文本态 DSML 调用，被兜底按
        # 空 valid_names 全量过滤，表现为「思考完就停止」且不执行任何工具
        ask = "jarvis当前目录是一个什么项目"
        ctx, _ = make_ctx()
        ctx.messages.append(Message(role="user", content=[TextContent(text=ask)]))
        assert loop._is_chat_only(ctx) is False  # 含工作区词「目录/项目/当前」

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
# 切片同步回归点
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

        async def _fake_freeze(self, provider, model, *, window_limit=None, keep_recent=None, base_tokens=0, on_progress=None, based_on_total=False, refreeze_growth=None, max_output_tokens=None):
            return True

        monkeypatch.setattr(LayeredContext, "freeze_if_needed", _fake_freeze)

        provider = ScriptedProvider([[TextDelta("hi"), Stop(reason="stop")]])
        loop = make_loop(provider, FakeOrchestrator(), registry, enable_compaction=True)
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)

        await loop.run("你好", ctx)

        assert any("上下文冻结完成" in i for i in ui.infos)
