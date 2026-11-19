"""终端完成行的耗时拆分（2026-11-18）。

HUD 与终端共用同一条口径：``run_end.tool_ms`` 是工具占用的墙钟并集（并行不重复
计），模型段 = ``duration_ms - tool_ms``。旧 trace 文件没有这个字段，回放时必须
保持原句，不能凭空补一个「模型 0ms」——那会把"没有数据"伪装成"模型没花时间"。

@author aceFelix
"""

from __future__ import annotations

from agent.ui.trace_view import render_event


def _run_end(**extra: object) -> dict:
    """一条最小可渲染的 run_end 事件（模拟 7.6s 那轮）。"""
    event = {
        "type": "run_end",
        "steps": 2,
        "failed": 0,
        "files_changed": 0,
        "duration_ms": 7580,
    }
    event.update(extra)
    return event


def test_run_end_shows_model_and_tool_split() -> None:
    """有 tool_ms 时把 7.6s 拆开：模型 7.0s + 工具 620ms。"""
    line = render_event(_run_end(tool_ms=620))
    assert line is not None
    assert "7.6s" in line
    assert "模型 7.0s" in line
    assert "工具 620ms" in line


def test_run_end_without_tool_ms_keeps_old_sentence() -> None:
    """旧 trace 回放（无 tool_ms）：保持原句，不出现括号。"""
    line = render_event(_run_end())
    assert line is not None
    assert line.endswith("· 7.6s")
    assert "模型" not in line


def test_run_end_with_zero_tool_ms_keeps_old_sentence() -> None:
    """纯模型轮次（工具段为 0）：不显示「模型 7.6s · 工具 0ms」这种废话。"""
    line = render_event(_run_end(tool_ms=0))
    assert line is not None
    assert "模型" not in line
