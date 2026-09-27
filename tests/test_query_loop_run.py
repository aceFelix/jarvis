"""QueryLoop.run() 主流程与异常路径单元测试。

覆盖：主流程（纯文本回复 / 工具调用循环 / 多轮工具 / 同轮多工具）、
中断（abort_event 预置 / LLM 流式被取消 / 工具执行被取消）、
错误处理（上下文过长触发压缩重试、网络错误重试一次、provider 故障转移、
无 fallback 结束）、边界场景（空 assistant 消息、max_iterations 强制停止、
输出截断自动续写、图片输入、工具执行异常补占位）。

测试替身与工厂见 tests/_query_loop_fakes.py；其余分支见
tests/test_query_loop_stream.py / _branches.py / _session.py。

@author aceFelix
"""

from __future__ import annotations

import asyncio

from agent.core.layered_context import LayeredContext
from agent.core.message import TextContent, ToolResultContent
from agent.core.query_loop import QueryLoop
from agent.llm.base import (
    ProviderError,
    Stop,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    Usage,
)

from tests._query_loop_fakes import (
    FakeOrchestrator,
    FakeUI,
    ScriptedProvider,
    _noop_sleep,
    _tool_script,
    make_ctx,
    make_loop,
    registry,
)


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
        """工具执行阶段被取消（Ctrl+C）：优雅退出本轮，不 re-raise。

        必须给这批 tool_use 留下配对结果——否则历史里出现悬空调用，
        该会话此后每次请求都会被 API 拒收（见
        docs/fixlogs/dangling-tool-use-fix.md）。
        """
        provider = ScriptedProvider([_tool_script()])
        orch = FakeOrchestrator()
        orch.raise_cancelled = True
        loop = make_loop(provider, orch, registry)
        ctx, msgs = make_ctx()
        old_event = ctx.abort_event

        stats = await loop.run("干活", ctx)

        assert stats.stopped_reason == "aborted"
        assert old_event.is_set()
        assert ctx.abort_event is not old_event
        # 悬空调用已被占位结果回收
        results = [b for m in msgs for b in m.content if isinstance(b, ToolResultContent)]
        assert [r.tool_use_id for r in results] == ["t1"]
        assert results[0].is_error is True


# ---------------------------------------------------------------------------
# Provider 错误与故障转移
# ---------------------------------------------------------------------------


class TestRunProviderError:
    """ProviderError 各处理路径测试。"""

    async def test_context_too_long_compacts_and_retries(self, registry, monkeypatch):
        """上下文过长：compact_reactive 成功 → 压缩后重试本轮。"""

        async def _fake_compact_reactive(self, provider, model, *, keep_recent=None, max_output_tokens=None):
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

        async def _fake_compact_reactive(self, provider, model, *, keep_recent=None, max_output_tokens=None):
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
# 边界场景
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

    async def test_stop_reason_max_tokens_auto_continue(self, registry):
        """anthropic 原生 stop_reason=max_tokens（含推理预算耗尽截断）→ 同样自动续写。

        回归：此前只认 openai 口径 "length"，anthropic 协议下截断轮被静默当作
        最终答案（只剩 thinking、无正文无工具调用），
        见 docs/fixlogs/serve-mcp-truncation-fix.md。@author aceFelix
        """
        provider = ScriptedProvider([
            [TextDelta("推理的前半段"), Stop(reason="max_tokens", usage=Usage())],
            [TextDelta("续写完成"), Stop(reason="stop")],
        ])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ui = FakeUI()
        ctx, msgs = make_ctx(ui=ui)

        stats = await loop.run("写长文", ctx)

        assert stats.stopped_reason == "stop"
        assert provider.stream_calls == 2
        assert any("自动续写" in w for w in ui.warns)
        assert msgs[-1].role == "assistant" and msgs[-1].get_text() == "续写完成"

    async def test_truncated_tool_use_gets_placeholder_result(self, registry):
        """截断轮已含 tool_use → 补占位结果后再续写，不留悬空调用。

        回归：provider 对残缺 JSON 也会产出工具调用块（anthropic 侧对截断的
        input_json 会容错补全或退化为 {"_raw": ...}），而截断分支不执行工具；
        若不补结果，悬空 tool_use 会让该会话此后每次请求都被 API 拒收。
        """
        provider = ScriptedProvider([
            [ToolCall(id="t1", name="fake_tool", input={}),
             Stop(reason="length", usage=Usage())],
            [TextDelta("续写完成"), Stop(reason="stop")],
        ])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, msgs = make_ctx()

        stats = await loop.run("干活", ctx)

        assert stats.stopped_reason == "stop"
        results = [b for m in msgs for b in m.content if isinstance(b, ToolResultContent)]
        assert [r.tool_use_id for r in results] == ["t1"]
        assert results[0].is_error is True
        assert "截断" in results[0].content
        # 截断轮未执行工具，只补了占位结果
        assert stats.tool_calls == 0
        assert msgs[-1].role == "assistant"

    async def test_orchestrator_exception_padded_and_stopped(self, registry):
        """工具执行期抛非取消异常 → 补占位后结束本轮（不冒泡、不留悬空）。

        回归：以前异常直接冒泡出 run()，而 assistant 消息已入历史，
        结果缺失 → 悬空调用污染会话。
        """
        provider = ScriptedProvider([_tool_script()])
        orch = FakeOrchestrator()
        orch.raise_error = RuntimeError("调度器异常")
        loop = make_loop(provider, orch, registry)
        ui = FakeUI()
        ctx, msgs = make_ctx(ui=ui)

        stats = await loop.run("干活", ctx)

        assert stats.stopped_reason == "tool_error"
        assert any("工具执行异常" in e for e in ui.errors)
        results = [b for m in msgs for b in m.content if isinstance(b, ToolResultContent)]
        assert [r.tool_use_id for r in results] == ["t1"]
        assert results[0].is_error is True
