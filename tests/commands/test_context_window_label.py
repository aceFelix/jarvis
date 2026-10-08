"""/context 窗口标签口径测试（2026-10）。

覆盖：
- handle_context：loop.context_window 为用户配置值时文案标「窗口」，
  不允许写成「假设窗口」；未配置（0 / 无 loop 属性）回退 128k 时才标
  「假设窗口」。
- _print_context：window_configured 参数直接驱动标签分流。

ui 用采集桩（只收 info 文本），render_table/render_tree 走真实输出但
pytest 会捕获，不触终端交互。

@author aceFelix
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.commands.handlers.core_commands import _print_context, handle_context


class _CollectUI:
    """只采集 info 文本的 UI 桩，供断言文案。"""

    def __init__(self) -> None:
        self.infos: list[str] = []

    def info(self, text: str) -> None:
        self.infos.append(text)


def _make_ctx(loop, ui: _CollectUI) -> SimpleNamespace:
    """按 handle_context 用到的字段伪造 CommandContext。"""
    return SimpleNamespace(
        ui=ui, loop=loop, messages=[], model="m1", system_prompt="",
    )


def test_handle_context_configured_window_not_labeled_assumed():
    """配置了 context_window=200000 → 文案「窗口 200,000」，不出现「假设」。"""
    ui = _CollectUI()
    ctx = _make_ctx(SimpleNamespace(context_window=200000), ui)
    assert handle_context(ctx, "/context") is True
    header = ui.infos[0]
    assert "窗口 200,000 tokens" in header
    assert "假设窗口" not in header


def test_handle_context_unconfigured_falls_back_to_assumed():
    """未配置窗口（context_window=0）→ 回退 128k 且标注「假设窗口」。"""
    ui = _CollectUI()
    ctx = _make_ctx(SimpleNamespace(context_window=0), ui)
    handle_context(ctx, "/context")
    assert "假设窗口 128,000 tokens" in ui.infos[0]


def test_handle_context_missing_loop_attribute():
    """loop 无 context_window 属性（老对象）→ 同样回退假设口径，不抛错。"""
    ui = _CollectUI()
    ctx = _make_ctx(SimpleNamespace(), ui)
    handle_context(ctx, "/context")
    assert "假设窗口 128,000 tokens" in ui.infos[0]


def test_print_context_label_switch():
    """_print_context 标签由 window_configured 直接决定。"""
    ui = _CollectUI()
    _print_context(ui, [], "m1", window=200000, window_configured=True)
    assert "窗口 200,000" in ui.infos[0] and "假设" not in ui.infos[0]
    ui2 = _CollectUI()
    _print_context(ui2, [], "m1", window=200000, window_configured=False)
    assert "假设窗口 200,000" in ui2.infos[0]
