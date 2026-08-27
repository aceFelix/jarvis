"""RichCLI 思考面板渲染测试（防刷屏修复）。

背景（fixlog: thinking-panel-flood-fix）：
深度思考流式输出用 Rich Live 原地刷新；但非交互终端（stdout 重定向、
部分 IDE 终端、管道）下 Live 无法原地擦除，每次刷新会追加一整帧面板，
导致屏幕被重复递增的"💭 思考过程"面板刷满。

本测试覆盖：
1. 非交互终端降级静态模式：全程只打印一个面板
2. 交互终端仍走 Live 原地刷新
3. 思考→直接工具调用（无正文）时 _end_assistant_line 能收口思考阶段

@author aceFelix
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("rich")

from agent.ui.cli import RichCLI


def _make_cli(terminal: bool) -> tuple[RichCLI, io.StringIO]:
    """构造 RichCLI，console 指向 StringIO。

    Args:
        terminal: True=模拟交互终端（force_terminal），False=模拟重定向输出
    """
    from rich.console import Console

    buf = io.StringIO()
    ui = RichCLI(boot_animation=False)
    ui._console = Console(
        file=buf,
        force_terminal=terminal,  # force_terminal 让 is_terminal=True
        width=100,
        color_system=None,  # 关颜色，方便断言纯文本
    )
    return ui, buf


class TestStaticModeFallback:
    """非交互终端：静态模式，面板只打印一次。"""

    def test_many_deltas_single_panel(self) -> None:
        """50 个增量到达后只应出现一个思考面板（修复前会刷出几十个）。"""
        ui, buf = _make_cli(terminal=False)
        for i in range(50):
            ui.assistant_thinking(f"思考片段{i}\n")
        ui._end_thinking()

        output = buf.getvalue()
        # 面板标题全程只出现一次
        assert output.count("思考过程") == 1
        # 内容完整（首尾片段都在）
        assert "思考片段0" in output
        assert "思考片段49" in output

    def test_no_live_created(self) -> None:
        """非交互终端不应创建 Live 实例。"""
        ui, _buf = _make_cli(terminal=False)
        ui.assistant_thinking("abc")
        assert ui._thinking_live is None
        assert ui._thinking_started is True

    def test_end_by_assistant_text(self) -> None:
        """正文到达时收口思考，面板已打印且缓冲清空。"""
        ui, buf = _make_cli(terminal=False)
        ui.assistant_thinking("想一下")
        ui.assistant_text("答案")

        output = buf.getvalue()
        assert output.count("思考过程") == 1
        assert "答案" in output
        assert ui._thinking_buf == ""


class TestLiveMode:
    """交互终端：保持 Live 原地刷新行为。"""

    def test_live_created_and_stopped(self) -> None:
        """交互终端创建 Live，正文到达时停止。"""
        ui, _buf = _make_cli(terminal=True)
        ui.assistant_thinking("想一下")
        assert ui._thinking_live is not None

        ui.assistant_text("答案")
        assert ui._thinking_live is None
        assert ui._thinking_started is False


class TestThinkingEndOnToolCall:
    """思考→直接工具调用（无正文文本）场景。"""

    def test_end_assistant_line_closes_thinking(self) -> None:
        """_end_assistant_line 应先收口思考阶段。

        deepseek 思考模式典型时序：thinking → tool_call，中间没有
        assistant_text；若不收口，静态模式下面板永远不会打印。
        """
        ui, buf = _make_cli(terminal=False)
        ui.assistant_thinking("决定调用工具")
        ui._end_assistant_line()

        assert buf.getvalue().count("思考过程") == 1
        assert ui._thinking_started is False
        assert ui._thinking_buf == ""

    def test_end_assistant_line_without_thinking(self) -> None:
        """无思考时 _end_assistant_line 只补换行，不报错。"""
        ui, buf = _make_cli(terminal=False)
        ui.assistant_text("正文")
        ui._end_assistant_line()
        assert "正文" in buf.getvalue()
