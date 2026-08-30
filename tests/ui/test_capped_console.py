# -*- coding: utf-8 -*-
"""_CappedConsole 渲染宽度封顶测试。

背景：终端最大化启动后缩窗，已输出的超宽行会被终端折行重排导致画面错乱。
修复方案是给 REPL Console 的渲染宽度封顶（见 agent/ui/cli.py _CappedConsole）。

作者：aceFelix
"""
from __future__ import annotations

import io

import pytest

rich = pytest.importorskip("rich")

from agent.ui.cli import _CappedConsole, _MAX_RENDER_WIDTH  # noqa: E402


def test_size_capped_when_terminal_wider_than_cap(monkeypatch):
    """超宽终端（如最大化 240 列）下，渲染宽度应封顶到 _MAX_RENDER_WIDTH。"""
    monkeypatch.setenv("COLUMNS", "240")
    monkeypatch.setenv("LINES", "50")
    console = _CappedConsole(force_terminal=False)
    assert console.size.width == _MAX_RENDER_WIDTH


def test_size_adapts_when_terminal_narrower_than_cap(monkeypatch):
    """窄窗口（< 封顶值）应自适应实际宽度，不被强制拉宽。"""
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "30")
    console = _CappedConsole(force_terminal=False)
    assert console.size.width <= 80


def test_panel_output_never_exceeds_cap(monkeypatch):
    """关键回归：最大化窗口下渲染的面板，每行宽度不得超过封顶值。

    一旦输出行宽超过窗口后续尺寸，缩窗时终端折行重排 → 画面错乱（本 bug 现象）。
    """
    from rich.panel import Panel

    monkeypatch.setenv("COLUMNS", "240")
    buf = io.StringIO()
    console = _CappedConsole(file=buf, force_terminal=False)
    console.print(
        Panel("provider zhipu\nmodel glm-5.3-flash\nworkdir E:\\J.A.R.V.I.S_Work")
    )
    lines = buf.getvalue().splitlines()
    assert lines, "面板应产生输出"
    assert max(len(line) for line in lines) <= _MAX_RENDER_WIDTH


def test_explicit_width_larger_than_cap_still_capped():
    """即使调用方显式传入超宽 width，渲染仍封顶——宁可窄不可乱。"""
    buf = io.StringIO()
    console = _CappedConsole(file=buf, force_terminal=False, width=240)
    assert console.size.width == _MAX_RENDER_WIDTH
