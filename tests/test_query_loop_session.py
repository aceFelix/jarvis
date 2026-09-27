"""QueryLoop 会话持久化与模型切换单元测试。

覆盖：会话消息落盘与恢复、以及 /model 热切换时的 provider 重建
（思考模式覆盖状态同步）。

测试替身与工厂见 tests/_query_loop_fakes.py。

@author aceFelix
"""

from __future__ import annotations

from agent.core.context import ToolContext
from agent.core.message import Message, TextContent
from agent.core.query_loop import QueryLoop
from agent.llm.base import ProviderError, Stop, TextDelta

from tests._query_loop_fakes import (
    FakeOrchestrator,
    FakeUI,
    ScriptedProvider,
    make_loop,
    registry,
)


# ---------------------------------------------------------------------------
# 会话持久化
# ---------------------------------------------------------------------------


class TestSessionMemoryPersist:
    """链路级测试：压缩成功后，摘要必须持久化到 <workdir>/.jarvis/SESSION_MEMORY.md。

    修复背景：update_session_memory 之前在 query_loop 中被 import 却从未在
    运行时被调用——三个压缩路径（freeze_if_needed / compact_reactive /
    compact_now）都调用了 compact_messages，但没有把 CompactResult 落盘到
    会话记忆文件。函数级测试直接调用 update_session_memory 是"假绿"，
    掩盖了链路断裂。本类断言"压缩之后记忆文件真的被更新"。

    测试策略：不 mock 掉压缩方法本身，而是 mock compact_messages 返回真实
    CompactResult，让三个压缩路径完整执行，再断言 SESSION_MEMORY.md 被创建
    且包含摘要内容。
    """

    # 模拟摘要内容（对应 compactor 的 9 段式输出片段）
    _SUMMARY = "关键决策：采用分层上下文；错误修复：补全 import os"

    def _fake_result(self):
        """构造一个 messages_summarized > 0 的压缩结果。"""
        from agent.core.memory.compactor import CompactResult

        summary_msg = Message(role="user", content=[TextContent(text=f"[摘要] {self._SUMMARY}")])
        return CompactResult(
            new_messages=[summary_msg],
            summary=self._SUMMARY,
            pre_compact_tokens=12000,
            post_compact_tokens=2000,
            messages_summarized=30,
            messages_kept=6,
        )

    def _assert_memory_file(self, tmp_path, *, must_exist=True):
        """断言 <workdir>/.jarvis/SESSION_MEMORY.md 存在且包含摘要。"""
        mem_file = tmp_path / ".jarvis" / "SESSION_MEMORY.md"
        if not must_exist:
            return not mem_file.exists()
        assert mem_file.exists(), f"SESSION_MEMORY.md 未被创建: {mem_file}"
        content = mem_file.read_text(encoding="utf-8")
        assert "关键决策：采用分层上下文" in content, f"摘要未写入记忆文件:\n{content}"
        return True

    async def test_freeze_persists_session_memory(self, registry, monkeypatch, tmp_path):
        """冻结路径：每轮 run() 前 freeze_if_needed 压缩成功 → 摘要落盘。"""
        async def _fake_compact(**kwargs):
            return self._fake_result()

        # freeze_if_needed 内部 `from agent.core.memory.compactor import compact_messages`
        monkeypatch.setattr("agent.core.memory.compactor.compact_messages", _fake_compact)

        provider = ScriptedProvider([[TextDelta("你好"), Stop(reason="stop")]])
        # context_window 传 2（比例 0.5 → 阈值 1），保证总 token 必然超限触发冻结
        loop = make_loop(
            provider, FakeOrchestrator(), registry,
            enable_compaction=True, context_window=2, compact_ratio=0.5,
        )
        ui = FakeUI()
        # workdir 指向临时目录，SESSION_MEMORY.md 会真实写到磁盘
        messages: list[Message] = [
            Message(role="user", content=[TextContent(text="旧消息")]),
        ]
        ctx = ToolContext(workdir=str(tmp_path), messages=messages, ui=ui)

        stats = await loop.run("你好", ctx)

        assert stats.stopped_reason == "stop"
        self._assert_memory_file(tmp_path)

    async def test_reactive_persists_session_memory(self, registry, monkeypatch, tmp_path):
        """反应式路径：LLM 报 context_too_long → compact_reactive 成功 → 摘要落盘。"""
        from agent.core.memory.compactor import compact_messages

        async def _fake_compact(**kwargs):
            return self._fake_result()

        monkeypatch.setattr("agent.core.memory.compactor.compact_messages", _fake_compact)

        provider = ScriptedProvider([
            ProviderError("Request failed: prompt_too_long tokens ..."),
            [TextDelta("压缩后正常回复"), Stop(reason="stop")],
        ])
        loop = make_loop(
            provider, FakeOrchestrator(), registry,
            enable_compaction=True,
        )
        ui = FakeUI()
        ctx = ToolContext(workdir=str(tmp_path), messages=[
            Message(role="user", content=[TextContent(text="旧消息")]),
        ], ui=ui)

        stats = await loop.run("写个报告", ctx)

        assert stats.stopped_reason == "stop"
        assert provider.stream_calls == 2  # 压缩重试后成功
        self._assert_memory_file(tmp_path)

    async def test_compact_now_persists_session_memory(self, registry, monkeypatch, tmp_path):
        """手动路径：/compact 触发 compact_now 成功 → 摘要落盘。"""
        async def _fake_compact(**kwargs):
            return self._fake_result()

        # compact_now 调用的是 query_loop 模块顶部绑定的 compact_messages 引用
        monkeypatch.setattr("agent.core.query_loop.compact_messages", _fake_compact)

        loop = make_loop(
            ScriptedProvider([]), FakeOrchestrator(), registry,
            enable_compaction=True,
        )
        ui = FakeUI()
        ctx = ToolContext(workdir=str(tmp_path), messages=[
            Message(role="user", content=[TextContent(text="旧消息")]),
        ], ui=ui)

        assert await loop.compact_now(ctx) is True
        self._assert_memory_file(tmp_path)

    async def test_compact_no_summary_no_memory(self, registry, monkeypatch, tmp_path):
        """压缩结果 messages_summarized == 0（无摘要）→ 不写记忆文件。"""
        from agent.core.memory.compactor import CompactResult

        async def _fake_compact(**kwargs):
            return CompactResult(
                new_messages=[],
                summary="",
                pre_compact_tokens=100,
                post_compact_tokens=100,
                messages_summarized=0,
                messages_kept=6,
            )

        monkeypatch.setattr("agent.core.query_loop.compact_messages", _fake_compact)

        loop = make_loop(
            ScriptedProvider([]), FakeOrchestrator(), registry,
            enable_compaction=True,
        )
        ui = FakeUI()
        ctx = ToolContext(workdir=str(tmp_path), messages=[
            Message(role="user", content=[TextContent(text="旧消息")]),
        ], ui=ui)

        assert await loop.compact_now(ctx) is False
        mem_file = tmp_path / ".jarvis" / "SESSION_MEMORY.md"
        assert not mem_file.exists(), "无摘要时不应创建记忆文件"


# ---------------------------------------------------------------------------
# 模型切换
# ---------------------------------------------------------------------------


class TestSwitchModel:
    """QueryLoop.switch_model：就地换 provider / 模型，保留会话级状态。"""

    def test_replaces_provider_and_model(self, registry) -> None:
        """换 provider / 模型并返回旧 provider（调用方负责 close）。"""
        old = ScriptedProvider([])
        new = ScriptedProvider([])
        loop = make_loop(old, FakeOrchestrator(), registry)
        loop._model = "old-model"
        usage = loop.session_usage

        returned = loop.switch_model(new, "new-model")

        assert returned is old
        assert loop._provider is new
        assert loop._model == "new-model"
        assert loop.session_usage is usage  # 未重建 QueryLoop：会话 token 累计保留

    def test_same_provider_returns_none(self, registry) -> None:
        """复用同一 provider：返回 None（调用方不 close），模型名照常更新。"""
        provider = ScriptedProvider([])
        loop = make_loop(provider, FakeOrchestrator(), registry)

        assert loop.switch_model(provider, "m2") is None
        assert loop._provider is provider
        assert loop._model == "m2"

    def test_resets_failover_budget(self, registry) -> None:
        """手动切换复位厂商回退预算：新模型仍可触发一次故障转移。"""
        loop = make_loop(ScriptedProvider([]), FakeOrchestrator(), registry)
        loop._failover_tried = True

        loop.switch_model(ScriptedProvider([]), "new-model")

        assert loop._failover_tried is False

    def test_inherits_thinking_override(self, registry, monkeypatch) -> None:
        """语音模式强制关闭思考时，新 provider 必须继承该覆盖。"""
        new = ScriptedProvider([])
        calls: list[bool] = []
        monkeypatch.setattr(new, "set_thinking_enabled", lambda enabled: calls.append(enabled))
        loop = make_loop(ScriptedProvider([]), FakeOrchestrator(), registry)
        loop._thinking_override = False

        loop.switch_model(new, "new-model")

        assert calls == [False]

    def test_without_override_keeps_provider_default(self, registry, monkeypatch) -> None:
        """无覆盖（None）→ 不强制写新 provider，用其自身默认思考态。"""
        new = ScriptedProvider([])
        calls: list[bool] = []
        monkeypatch.setattr(new, "set_thinking_enabled", lambda enabled: calls.append(enabled))
        loop = make_loop(ScriptedProvider([]), FakeOrchestrator(), registry)
        assert loop._thinking_override is None

        loop.switch_model(new, "new-model")

        assert calls == []
