"""分层上下文管理器单元测试。

覆盖 LayeredContext 的 messages / append / snapshot / freeze / 清理方法。

@author aceFelix
"""

import pytest
from agent.core.message import Message, TextContent, ToolResultContent
from agent.core.layered_context import LayeredContext


def _make_msg(role: str, text: str) -> Message:
    """快捷创建文本消息。"""
    return Message(role=role, content=[TextContent(text=text)])


def _make_tool_result(text: str) -> Message:
    """快捷创建工具结果消息。"""
    return Message(role="user", content=[ToolResultContent(tool_use_id="t1", content=text)])


class TestLayeredContextBasic:
    """基础属性与方法测试。"""

    def test_empty_context(self) -> None:
        lc = LayeredContext()
        assert lc.messages == []
        assert lc.frozen == []
        assert lc.active == []
        assert lc.snapshot() == []

    def test_init_with_messages(self) -> None:
        msgs = [_make_msg("user", "hello")]
        lc = LayeredContext(msgs)
        assert len(lc.messages) == 1
        assert len(lc.active) == 1
        assert len(lc.frozen) == 0

    def test_append_to_active(self) -> None:
        lc = LayeredContext()
        msg = _make_msg("user", "hello")
        lc.append(msg)
        assert len(lc.active) == 1
        assert lc.messages == [msg]

    def test_extend_active(self) -> None:
        lc = LayeredContext()
        msgs = [_make_msg("user", "a"), _make_msg("assistant", "b")]
        lc.extend(msgs)
        assert len(lc.active) == 2

    def test_replace_active(self) -> None:
        lc = LayeredContext()
        lc.append(_make_msg("user", "old"))
        new_msgs = [_make_msg("user", "new")]
        lc.replace_active(new_msgs)
        assert lc.active == new_msgs
        assert len(lc.messages) == 1

    def test_snapshot_includes_frozen_and_active(self) -> None:
        lc = LayeredContext()
        lc.append(_make_msg("user", "active_msg"))
        # 手动设置冻结区（模拟冻结后的状态）
        frozen_msg = _make_msg("user", "frozen_summary")
        lc._frozen = [frozen_msg]
        snap = lc.snapshot()
        assert len(snap) == 2
        assert snap[0] == frozen_msg
        assert snap[1].content[0].text == "active_msg"

    def test_messages_property(self) -> None:
        lc = LayeredContext()
        frozen_msg = _make_msg("user", "frozen")
        active_msg = _make_msg("user", "active")
        lc._frozen = [frozen_msg]
        lc._active = [active_msg]
        assert lc.messages == [frozen_msg, active_msg]

    def test_frozen_property_is_copy(self) -> None:
        lc = LayeredContext()
        lc._frozen = [_make_msg("user", "original")]
        frozen_copy = lc.frozen
        frozen_copy.clear()
        assert len(lc._frozen) == 1  # 原数据未受影响


class TestLayeredContextTokenEstimation:
    """Token 估算测试。"""

    def test_active_tokens(self) -> None:
        lc = LayeredContext()
        lc.append(_make_msg("user", "hello world"))
        tokens = lc.active_tokens()
        assert tokens > 0

    def test_total_tokens_includes_frozen(self) -> None:
        lc = LayeredContext()
        lc._frozen_tokens = 100
        lc.append(_make_msg("user", "hello"))
        total = lc.total_tokens()
        assert total > 100

    def test_token_caching(self) -> None:
        """active_tokens 每次都重新计算。"""
        lc = LayeredContext()
        t1 = lc.active_tokens()
        lc.append(_make_msg("user", "x" * 500))
        t2 = lc.active_tokens()
        assert t2 > t1


class TestLayeredContextCleanup:
    """清理方法测试（evict_old_images / collapse_old_tool_results）。"""

    def test_evict_old_images_noop_on_empty(self) -> None:
        """空列表不崩溃。"""
        lc = LayeredContext()
        lc.evict_old_images()  # 不抛异常

    def test_collapse_old_tool_results_noop_on_empty(self) -> None:
        """空列表不崩溃。"""
        lc = LayeredContext()
        lc.collapse_old_tool_results()  # 不抛异常

    def test_collapse_preserves_recent(self) -> None:
        """只折叠旧工具结果，保留最近 N 条。"""
        lc = LayeredContext()
        for i in range(10):
            lc.append(_make_tool_result(f"result_{i}"))
        lc.collapse_old_tool_results(keep_recent=3)
        # 最近的 3 条不应被折叠
        active = lc.active
        preserved = [m for m in active if any(
            isinstance(b, ToolResultContent) for b in (m.content if isinstance(m.content, list) else [])
        )]
        assert len(preserved) >= 3


# ── 补充：freeze_if_needed / compact_reactive / evict / collapse 行为 ──


def _make_fake_compact_result(*, summarized: int, kept: int) -> "CompactResult":
    """构造 mock 用的 CompactResult。"""
    from agent.core.memory.compactor import CompactResult

    new_messages = [_make_msg("user", "summary")]
    new_messages += [_make_msg("user", f"kept_{i}") for i in range(kept)]
    return CompactResult(
        new_messages=new_messages,
        summary="summary text",
        pre_compact_tokens=1000,
        post_compact_tokens=200,
        messages_summarized=summarized,
        messages_kept=kept,
    )


class TestFreezeIfNeeded:
    """冻结触发与压缩。"""

    @pytest.mark.asyncio
    async def test_below_threshold_no_freeze(self, monkeypatch) -> None:
        """活跃窗口 token 低于阈值时不做任何压缩。"""
        from agent.core.memory import compactor

        called = False

        async def fake_compact(**kwargs):
            nonlocal called
            called = True
            return _make_fake_compact_result(summarized=5, kept=2)

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        lc.append(_make_msg("user", "short"))
        result = await lc.freeze_if_needed(None, "model")
        assert result is False
        assert called is False  # 未触发压缩

    @pytest.mark.asyncio
    async def test_above_threshold_freezes(self, monkeypatch) -> None:
        """超过阈值触发压缩：摘要进冻结区，最近 N 条回窗口。"""
        from agent.core.memory import compactor

        seen: dict = {}

        async def fake_compact(**kwargs):
            seen.update(kwargs)
            return _make_fake_compact_result(summarized=10, kept=2)

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        # 塞入大量消息让 active_tokens 超默认阈值 8000（40 条 × 1000 字符 ≈ 10000 tokens）
        for i in range(40):
            lc.append(_make_msg("user", "x" * 1000))
        result = await lc.freeze_if_needed(None, "test-model", keep_recent=2)
        assert result is True
        # keep_recent=2 → 冻结区 1 条摘要 + 活跃窗口 2 条
        assert len(lc.frozen) == 1
        assert len(lc.active) == 2
        assert seen["model"] == "test-model"
        assert seen["keep_recent"] == 2

    @pytest.mark.asyncio
    async def test_no_summarized_messages_returns_false(self, monkeypatch) -> None:
        """compact 返回 messages_summarized=0 时不做冻结。"""
        from agent.core.memory import compactor

        async def fake_compact(**kwargs):
            return _make_fake_compact_result(summarized=0, kept=2)

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        for i in range(40):
            lc.append(_make_msg("user", "x" * 1000))
        assert await lc.freeze_if_needed(None, "m") is False

    @pytest.mark.asyncio
    async def test_compact_exception_returns_false(self, monkeypatch) -> None:
        """压缩过程抛异常时静默失败返回 False。"""
        from agent.core.memory import compactor

        async def fake_compact(**kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        for i in range(40):
            lc.append(_make_msg("user", "x" * 1000))
        assert await lc.freeze_if_needed(None, "m") is False

    @pytest.mark.asyncio
    async def test_on_progress_callback(self, monkeypatch) -> None:
        """on_progress 透传给 compact_messages。"""
        from agent.core.memory import compactor

        seen: dict = {}

        async def fake_compact(**kwargs):
            seen.update(kwargs)
            return _make_fake_compact_result(summarized=5, kept=1)

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        for i in range(40):
            lc.append(_make_msg("user", "x" * 1000))
        cb = object()
        await lc.freeze_if_needed(None, "m", keep_recent=1, on_progress=cb)
        assert seen["on_progress"] is cb

    @pytest.mark.asyncio
    async def test_frozen_tokens_cached(self, monkeypatch) -> None:
        """冻结后 _frozen_tokens 被更新（total_tokens 包含冻结区）。"""
        from agent.core.memory import compactor

        async def fake_compact(**kwargs):
            return _make_fake_compact_result(summarized=5, kept=1)

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        for i in range(40):
            lc.append(_make_msg("user", "x" * 1000))
        await lc.freeze_if_needed(None, "m", keep_recent=1)
        assert lc._frozen_tokens > 0
        assert lc.total_tokens() > lc._frozen_tokens


class TestFreezeRatioMode:
    """比例压缩模式（based_on_total=True，总量超窗口比例才冻结）。"""

    @pytest.mark.asyncio
    async def test_below_ratio_no_freeze(self, monkeypatch) -> None:
        """总 token 低于窗口×比例时不压缩（即使活跃窗口超了绝对阈值）。"""
        from agent.core.memory import compactor

        called = False

        async def fake_compact(**kwargs):
            nonlocal called
            called = True
            return _make_fake_compact_result(summarized=5, kept=2)

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        # 10 条 ×1000 字 ≈ 2500 tokens，远低于 128k×50%=64000
        for i in range(10):
            lc.append(_make_msg("user", "x" * 1000))
        # 比例模式：窗口 64000，总 2500 → 不触发
        result = await lc.freeze_if_needed(
            None, "m", window_limit=64000, keep_recent=2, based_on_total=True,
        )
        assert result is False
        assert called is False

    @pytest.mark.asyncio
    async def test_above_ratio_freezes(self, monkeypatch) -> None:
        """总 token（含 base_tokens）超窗口比例时触发压缩。"""
        from agent.core.memory import compactor

        async def fake_compact(**kwargs):
            return _make_fake_compact_result(summarized=20, kept=2)

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        # 40 条 ×1000 字 ≈ 10000 tokens，加 base_tokens=60000 → 总 70000 > 64000
        for i in range(40):
            lc.append(_make_msg("user", "x" * 1000))
        result = await lc.freeze_if_needed(
            None, "m", window_limit=64000, keep_recent=2,
            base_tokens=60000, based_on_total=True,
        )
        assert result is True
        assert len(lc.frozen) == 1
        assert len(lc.active) == 2

    @pytest.mark.asyncio
    async def test_ratio_mode_counts_frozen_tokens(self, monkeypatch) -> None:
        """比例模式总量计入冻结区（不是只看活跃窗口）。"""
        from agent.core.memory import compactor

        called = False

        async def fake_compact(**kwargs):
            nonlocal called
            called = True
            return _make_fake_compact_result(summarized=5, kept=2)

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        # 手工模拟已有冻结区：1 条摘要消息（80000 字 ≈ 20000 tokens）
        lc._frozen = [_make_msg("user", "s" * 80000)]
        lc._frozen_tokens = 20000
        # 活跃窗口只有 1 条小消息（远低于绝对阈值），但总量 20000+ > 限 15000
        lc.append(_make_msg("user", "tiny"))
        result = await lc.freeze_if_needed(
            None, "m", window_limit=15000, keep_recent=2, based_on_total=True,
        )
        assert result is True
        assert called is True

    @pytest.mark.asyncio
    async def test_ratio_mode_debounce(self, monkeypatch) -> None:
        """防抖：冻结后总量增长不足 25% 不重复压缩。"""
        from agent.core.memory import compactor

        calls = []

        async def fake_compact(**kwargs):
            calls.append(kwargs)
            return _make_fake_compact_result(summarized=5, kept=2)

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        # 手工模拟上次冻结后的状态：总量基准 20000 tokens
        lc._frozen = [_make_msg("user", "s" * 80000)]
        lc._frozen_tokens = 20000
        lc._last_freeze_total = 20000
        # 活跃窗口新增少量（500 tokens），总量 20500 < 20000×1.25=25000 → 不重复压
        lc.append(_make_msg("user", "x" * 2000))
        result = await lc.freeze_if_needed(
            None, "m", window_limit=15000, keep_recent=2, based_on_total=True,
        )
        assert result is False
        assert calls == []

    @pytest.mark.asyncio
    async def test_ratio_mode_debounce_expires_after_growth(self, monkeypatch) -> None:
        """防抖过期：总量增长超 25% 后再次允许压缩。"""
        from agent.core.memory import compactor

        async def fake_compact(**kwargs):
            return _make_fake_compact_result(summarized=5, kept=2)

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        lc._frozen = [_make_msg("user", "s" * 80000)]
        lc._frozen_tokens = 20000
        lc._last_freeze_total = 20000
        # 活跃窗口新增 10000 tokens → 总 30000 ≥ 25000 → 触发
        lc.append(_make_msg("user", "y" * 40000))
        result = await lc.freeze_if_needed(
            None, "m", window_limit=15000, keep_recent=2, based_on_total=True,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_absolute_mode_still_uses_active_only(self, monkeypatch) -> None:
        """默认模式（based_on_total=False）仍只看活跃窗口，不受冻结区影响。"""
        from agent.core.memory import compactor

        called = False

        async def fake_compact(**kwargs):
            nonlocal called
            called = True
            return _make_fake_compact_result(summarized=5, kept=2)

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        # 冻结区很大但活跃窗口很小 → 默认模式不触发
        lc._frozen = [_make_msg("user", "s" * 80000)]
        lc._frozen_tokens = 20000
        lc.append(_make_msg("user", "tiny"))
        result = await lc.freeze_if_needed(
            None, "m", window_limit=8000, keep_recent=2,
        )
        assert result is False
        assert called is False


class TestCompactReactive:
    """反应式压缩（无条件）。"""

    @pytest.mark.asyncio
    async def test_compact_reactive_always_compacts(self, monkeypatch) -> None:
        """不检查阈值，直接压缩。"""
        from agent.core.memory import compactor

        called = False

        async def fake_compact(**kwargs):
            nonlocal called
            called = True
            return _make_fake_compact_result(summarized=3, kept=1)

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        lc.append(_make_msg("user", "仅一条消息"))
        result = await lc.compact_reactive(None, "m", keep_recent=1)
        assert result is True
        assert called is True
        assert len(lc.frozen) == 1
        assert len(lc.active) == 1

    @pytest.mark.asyncio
    async def test_compact_reactive_no_summary(self, monkeypatch) -> None:
        from agent.core.memory import compactor

        async def fake_compact(**kwargs):
            return _make_fake_compact_result(summarized=0, kept=0)

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        lc.append(_make_msg("user", "x"))
        assert await lc.compact_reactive(None, "m") is False

    @pytest.mark.asyncio
    async def test_compact_reactive_exception(self, monkeypatch) -> None:
        from agent.core.memory import compactor

        async def fake_compact(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(compactor, "compact_messages", fake_compact)
        lc = LayeredContext()
        lc.append(_make_msg("user", "x"))
        assert await lc.compact_reactive(None, "m") is False


class TestEvictOldImages:
    """淘汰旧图片（真实行为验证）。"""

    def _msg_with_image(self, text: str, has_image: bool) -> Message:
        if has_image:
            from agent.core.message import ImageContent

            return Message(
                role="user",
                content=[ToolResultContent(tool_use_id=f"t-{text}", content=text, images=[ImageContent(data="img")])],
            )
        return _make_tool_result(text)

    def test_keeps_latest_image_evicts_old(self) -> None:
        lc = LayeredContext()
        old = self._msg_with_image("old_shot", has_image=True)
        latest = self._msg_with_image("latest_shot", has_image=True)
        lc.extend([old, latest])
        lc.evict_old_images()
        # 最新一条的图片数据完整保留
        assert len(latest.content[0].images) == 1
        assert latest.content[0].content == "latest_shot"
        # 旧图被替换为 "[截图已处理: ...]"
        assert "[截图已处理" in old.content[0].content
        assert old.content[0].images == []

    def test_only_image_messages_affected(self) -> None:
        lc = LayeredContext()
        text_msg = _make_msg("user", "normal text")
        lc.append(text_msg)
        lc.evict_old_images()  # 不抛异常
        assert text_msg.content[0].text == "normal text"

    def test_evict_keeps_plain_tool_results(self) -> None:
        """无图片的 tool_result 不受影响。"""
        lc = LayeredContext()
        m1 = _make_tool_result("no image")
        lc.append(m1)
        lc.evict_old_images()
        assert "no image" in m1.content[0].content


class TestCollapseOldToolResults:
    """折叠旧工具结果（真实行为验证）。"""

    def test_old_results_collapsed_recent_kept(self) -> None:
        lc = LayeredContext()
        msgs = [_make_tool_result(f"result_{i}") for i in range(5)]
        lc.extend(msgs)
        lc.collapse_old_tool_results(keep_recent=2)
        # 前 3 条被折叠为占位，后 2 条保留原内容
        for i in range(3):
            assert msgs[i].content[0].content.startswith("[工具 t1 已完成]")
        for i in range(3, 5):
            assert msgs[i].content[0].content == f"result_{i}"

    def test_keep_recent_default(self) -> None:
        lc = LayeredContext()
        msgs = [_make_tool_result(f"r{i}") for i in range(6)]
        lc.extend(msgs)
        lc.collapse_old_tool_results()  # 默认 keep_recent=4
        assert msgs[0].content[0].content.startswith("[工具 t1 已完成]")
        assert msgs[-1].content[0].content == "r5"
