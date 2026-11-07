"""操作轨迹采集器的单元测试（2026-11 操作可见性）。

覆盖：事件落盘、字段、耗时、失败摘要、脱敏、轮转、窗口动作旁路、关闭零写入。

@author aceFelix
"""

from __future__ import annotations

import json

from agent.core.trace import (
    MASKED_WINDOW,
    TraceRecorder,
    mask_window_title,
)


class FakeUI:
    """记录终端输出的最小 UI。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, text: str) -> None:
        self.lines.append(text)


class FakeDirector:
    """记录目录定位调用的假 director。"""

    def __init__(self) -> None:
        self.file_calls: list[tuple[str, str]] = []

    def on_file_changed(self, path: str, operation: str, workdir: str = ""):
        self.file_calls.append((path, operation))
        return {"action": "show_dir", "ok": True, "message": "已定位到文件夹"}


class FakeObserver:
    """记录 before/after 调用的假观察器。"""

    def __init__(self) -> None:
        self.before_calls: list[tuple[str, str]] = []
        self.after_calls: list[str] = []

    def before(self, tool, tool_input, tool_use_id, ctx=None):
        self.before_calls.append((tool, tool_use_id))

    def after(self, tool_use_id):
        self.after_calls.append(tool_use_id)

    def cancel_all(self):
        pass


def _recorder(tmp_path, *, keep_runs: int = 20, enabled: bool = True) -> TraceRecorder:
    return TraceRecorder(
        enabled=enabled, keep_runs=keep_runs, ui=FakeUI(), base_dir=tmp_path
    )


def _read_events(recorder: TraceRecorder) -> list[dict]:
    assert recorder.last_run_file is not None
    return [
        json.loads(line)
        for line in recorder.last_run_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_trace_records_full_run(tmp_path) -> None:
    rec = _recorder(tmp_path)
    rec.on_user_prompt({"text": "改一下报告"})
    rec.on_tool_before(
        {
            "tool_name": "FileEdit",
            "tool_use_id": "t1",
            "tool_input": {
                "file_path": "C:/proj/report.md",
                "old_string": "a\nb",
                "new_string": "a\nb\nc",
            },
        }
    )
    rec.on_tool_after(
        {"tool_name": "FileEdit", "tool_use_id": "t1", "result": "ok", "is_error": False}
    )
    rec.on_file_changed({"path": "C:/proj/report.md", "operation": "edit"})
    rec.finish_run()

    events = _read_events(rec)
    types = [e["type"] for e in events]
    assert types == ["run_start", "tool_start", "tool_end", "file_change", "run_end"]
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5]
    assert events[1]["target"] == {"kind": "file", "path": "C:/proj/report.md"}
    assert events[2]["ok"] is True
    assert events[2]["duration_ms"] >= 0
    assert events[3]["diff"] == {"added": 1, "removed": 0}
    assert events[4]["steps"] == 1
    assert events[4]["files_changed"] == 1


def test_trace_failed_tool_keeps_first_error_line(tmp_path) -> None:
    rec = _recorder(tmp_path)
    rec.start_run()
    rec.on_tool_before({"tool_name": "Bash", "tool_use_id": "t2", "tool_input": {}})
    rec.on_tool_after(
        {
            "tool_name": "Bash",
            "tool_use_id": "t2",
            "result": "Permission denied\nstack...",
            "is_error": True,
        }
    )
    rec.finish_run()

    events = _read_events(rec)
    end = [e for e in events if e["type"] == "tool_end"][0]
    assert end["ok"] is False
    assert end["error"] == "Permission denied"
    assert [e for e in events if e["type"] == "run_end"][0]["failed"] == 1


def test_trace_masks_sensitive_window_titles(tmp_path) -> None:
    rec = _recorder(tmp_path)
    rec.start_run()
    rec.on_tool_before(
        {
            "tool_name": "WindowFocus",
            "tool_use_id": "t3",
            "tool_input": {"title": "1Password - Vault"},
        }
    )
    rec.finish_run()

    events = _read_events(rec)
    assert events[1]["target"]["title"] == MASKED_WINDOW
    assert mask_window_title("记事本") == "记事本"


def test_trace_forwards_to_observer_and_director(tmp_path) -> None:
    rec = _recorder(tmp_path)
    director = FakeDirector()
    observer = FakeObserver()
    rec.director = director
    rec.observer = observer
    rec.start_run()
    rec.on_tool_before(
        {
            "tool_name": "WindowFocus",
            "tool_use_id": "t4",
            "tool_input": {"title": "记事本"},
        }
    )
    rec.on_tool_after(
        {"tool_name": "WindowFocus", "tool_use_id": "t4", "result": "ok", "is_error": False}
    )
    rec.on_file_changed({"path": "C:/proj/a.txt", "operation": "write"})
    rec.finish_run()

    assert observer.before_calls == [("WindowFocus", "t4")]
    assert observer.after_calls == ["t4"]
    assert director.file_calls == [("C:/proj/a.txt", "write")]


def test_observer_called_for_unknown_future_tool(tmp_path) -> None:
    """没有白名单：任何工具（包括未来才出现的名字）都走同一条观察路径。"""
    rec = _recorder(tmp_path)
    observer = FakeObserver()
    rec.observer = observer
    rec.start_run()

    rec.on_tool_before(
        {"tool_name": "SomeFutureTool", "tool_use_id": "t8", "tool_input": {}}
    )

    assert observer.before_calls == [("SomeFutureTool", "t8")]


def test_trace_disabled_writes_nothing(tmp_path) -> None:
    rec = _recorder(tmp_path, enabled=False)
    rec.on_user_prompt({"text": "hi"})
    rec.on_tool_before({"tool_name": "Bash", "tool_use_id": "t5", "tool_input": {}})
    rec.finish_run()

    assert list(tmp_path.glob("*.jsonl")) == []
    assert rec.recent_events() == []


def test_trace_rotation_keeps_recent_runs(tmp_path) -> None:
    for index in range(4):
        rec = _recorder(tmp_path, keep_runs=2)
        rec.start_run()
        rec.finish_run()
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 2


class StubCleanupDirector:
    """只实现收尾清理所需的假 director。"""

    def __init__(self, current: dict[int, str], fg: int | None = None) -> None:
        self.current = dict(current)
        self.fg = fg
        self.closed: list[int] = []
        self.kept: set[int] = set()

    def snapshot_records(self):
        return dict(self.current)

    def foreground_hwnd(self):
        return self.fg

    def close_transients(self, before, keep=None):
        keep_set = keep or set()
        self.kept = keep_set
        closed = []
        for hwnd, title in self.current.items():
            if hwnd in before or hwnd in keep_set:
                continue
            self.closed.append(hwnd)
            closed.append(title)
        return {"closed": closed, "skipped": []}


def test_cleanup_closes_intermediate_keeps_final_and_foreground(tmp_path) -> None:
    rec = _recorder(tmp_path)
    director = StubCleanupDirector(
        {1: "终端", 2: "中间窗口", 3: "最终结果", 4: "用户正在看"}, fg=4
    )
    rec.director = director
    rec._before_windows = {1: "终端"}
    rec._involved = {2, 3, 4}
    rec._step_new = [{2}, {3}]

    rec._cleanup_transient_windows()

    assert director.closed == [2]          # 中间产物关掉
    assert 3 in director.kept              # 最后一步的结果保留
    assert 4 in director.kept              # 当前前台保留
    assert any(
        e["type"] == "window_action" and e["action"] == "close"
        for e in rec.current_events
    )


def test_chinese_path_survives_json_roundtrip(tmp_path) -> None:
    rec = _recorder(tmp_path)
    rec.start_run()
    rec.on_tool_before(
        {
            "tool_name": "FileWrite",
            "tool_use_id": "t6",
            "tool_input": {"file_path": "E:/项目/说明.md", "content": "你好"},
        }
    )
    rec.finish_run()
    events = _read_events(rec)
    assert events[1]["target"]["path"] == "E:/项目/说明.md"
