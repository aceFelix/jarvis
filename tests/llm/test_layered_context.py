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
