"""工具运行时上下文单元测试。

覆盖 ToolContext 默认字段、UIProtocol 协议，以及 clone_for_subagent
的克隆语义（workdir 覆盖、messages 共享、extra 拷贝、abort_event 独立）。

@author aceFelix
"""

from __future__ import annotations

import asyncio

from agent.core.context import ToolContext
from agent.core.message import Message


def _make_ctx(workdir: str = "/tmp/work", **kwargs) -> ToolContext:
    """快捷构造 ToolContext。"""
    return ToolContext(workdir=workdir, messages=[Message(role="user")], **kwargs)


class TestToolContextDefaults:
    """默认字段行为。"""

    def test_required_fields(self) -> None:
        ctx = ToolContext(workdir="/w", messages=[])
        assert ctx.workdir == "/w"
        assert ctx.messages == []

    def test_abort_event_default(self) -> None:
        ctx = _make_ctx()
        assert isinstance(ctx.abort_event, asyncio.Event)
        assert ctx.abort_event.is_set() is False

    def test_permission_mode_default(self) -> None:
        assert _make_ctx().permission_mode == "default"

    def test_ui_default_none(self) -> None:
        assert _make_ctx().ui is None

    def test_extra_default_empty_dict(self) -> None:
        assert _make_ctx().extra == {}

    def test_on_assistant_text_default_none(self) -> None:
        assert _make_ctx().on_assistant_text is None

    def test_settings_default_none(self) -> None:
        assert _make_ctx().settings is None


class TestCloneForSubagent:
    """子代理上下文克隆。"""

    def test_clone_keeps_workdir(self) -> None:
        ctx = _make_ctx(workdir="/parent")
        cloned = ctx.clone_for_subagent()
        assert cloned.workdir == "/parent"

    def test_clone_overrides_workdir(self) -> None:
        ctx = _make_ctx(workdir="/parent")
        cloned = ctx.clone_for_subagent(workdir="/subagent")
        assert cloned.workdir == "/subagent"

    def test_clone_shares_messages_reference(self) -> None:
        """messages 共享引用（子代理读父对话历史）。"""
        ctx = _make_ctx()
        cloned = ctx.clone_for_subagent()
        assert cloned.messages is ctx.messages

    def test_clone_gets_fresh_abort_event(self) -> None:
        ctx = _make_ctx()
        cloned = ctx.clone_for_subagent()
        assert cloned.abort_event is not ctx.abort_event
        assert isinstance(cloned.abort_event, asyncio.Event)

    def test_clone_copies_extra(self) -> None:
        """extra 浅拷贝：改克隆不影响父。"""
        ctx = _make_ctx()
        ctx.extra["todos"] = [{"content": "a"}]
        cloned = ctx.clone_for_subagent()
        assert cloned.extra["todos"] == [{"content": "a"}]
        cloned.extra["todos"].append({"content": "b"})
        # 浅拷贝：嵌套 list 仍是同一引用
        assert len(ctx.extra["todos"]) == 2

    def test_clone_copies_metadata_fields(self) -> None:
        ui = object()
        settings = object()

        def cb(text: str) -> None:
            pass

        ctx = ToolContext(
            workdir="/w",
            messages=[],
            permission_mode="yolo",
            ui=ui,
            on_assistant_text=cb,
            settings=settings,
        )
        cloned = ctx.clone_for_subagent()
        assert cloned.permission_mode == "yolo"
        assert cloned.ui is ui
        assert cloned.on_assistant_text is cb
        assert cloned.settings is settings
