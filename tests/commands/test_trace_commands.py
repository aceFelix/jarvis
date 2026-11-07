"""`/trace` 命令处理器的单元测试（2026-11 操作可见性）。

只验证"读最近一轮 / 开关 / 路径"三条对外承诺，不依赖真实 trace 文件。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import agent.commands.handlers.trace_commands as trace_cmd
from agent.commands.handlers.trace_commands import handle_trace


class FakeUI:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, text: str) -> None:
        self.lines.append(text)


class FakeRecorder:
    def __init__(self) -> None:
        self.enabled = True
        self.base_dir = Path("C:/Users/demo/.jarvis/traces")
        self.run_id = ""
        self.last_run_file = None
        self.events = [
            {"type": "tool_start", "tool": "FileEdit", "target": {"kind": "file", "path": "C:/p/a.md"}},
            {"type": "tool_end", "tool": "FileEdit", "ok": True, "duration_ms": 46},
        ]

    def recent_events(self):
        return list(self.events)

    def list_runs(self, limit: int = 10):
        return []

    def set_enabled(self, value: bool) -> None:
        self.enabled = bool(value)


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(ui=FakeUI())


def test_trace_prints_recent_run(monkeypatch) -> None:
    recorder = FakeRecorder()
    monkeypatch.setattr(trace_cmd, "get_recorder", lambda: recorder)
    ctx = _ctx()

    assert handle_trace(ctx, "/trace") is True

    assert any("FileEdit" in line for line in ctx.ui.lines)
    assert any("✓ 46ms" in line for line in ctx.ui.lines)


def test_trace_toggle_updates_recorder(monkeypatch) -> None:
    recorder = FakeRecorder()
    monkeypatch.setattr(trace_cmd, "get_recorder", lambda: recorder)
    ctx = _ctx()

    handle_trace(ctx, "/trace off")
    assert recorder.enabled is False

    handle_trace(ctx, "/trace on")
    assert recorder.enabled is True


def test_trace_path_prints_directory(monkeypatch) -> None:
    recorder = FakeRecorder()
    monkeypatch.setattr(trace_cmd, "get_recorder", lambda: recorder)
    ctx = _ctx()

    handle_trace(ctx, "/trace path")

    assert any("traces" in line for line in ctx.ui.lines)


def test_trace_without_recorder_explains(monkeypatch) -> None:
    monkeypatch.setattr(trace_cmd, "get_recorder", lambda: None)
    ctx = _ctx()

    assert handle_trace(ctx, "/trace") is True
    assert "未启用" in ctx.ui.lines[0]
