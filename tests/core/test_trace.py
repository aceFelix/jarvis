"""操作轨迹采集器的单元测试（2026-11 操作可见性）。

覆盖：事件落盘、字段、耗时、失败摘要、脱敏、轮转、窗口动作旁路、关闭零写入。

@author aceFelix
"""

from __future__ import annotations

import json
import time

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
    # 4 号窗口本轮没动过 → 才算“用户正在看”，必须保留
    rec._involved = {2, 3}
    rec._step_new = [{2}, {3}]
    # 2 是中间产物：由工具显式声明 transient（契约式收尾）
    rec._step_policy = ["transient", None]

    rec._cleanup_transient_windows()

    assert director.closed == [2]          # 声明 transient 的关掉
    assert 3 in director.kept              # 最后一步的结果保留
    assert 4 in director.kept              # 用户自己的前台窗口保留
    assert any(
        e["type"] == "window_action" and e["action"] == "close"
        for e in rec.current_events
    )


def test_cleanup_closes_foreground_window_touched_by_run(tmp_path) -> None:
    """显式关闭动作晚于窗口置前时，被操作过的前台窗口按中间产物关闭。

    2026-11-08 实机（WPS）：``edit_text`` 由 COM 激活了「WPS Office」窗口，
    随后 ``writer close`` 关掉文档。收尾据此判定应用壳是做完的中间产物。
    """
    rec = _recorder(tmp_path)
    director = StubCleanupDirector({1: "终端", 2: "WPS Office"}, fg=2)
    rec.director = director
    rec._before_windows = {1: "终端"}
    rec._involved = {2}
    rec._step_new = []
    rec._show_at = {2: 1.0}
    rec._last_close_intent_at = 2.0   # 关闭动作晚于置前

    rec._cleanup_transient_windows()

    assert director.closed == [2]
    assert 2 not in director.kept


def test_gui_launch_window_is_kept_at_cleanup(tmp_path) -> None:
    """``start firefox`` 打开、后续被置前的窗口属于最终结果，收尾不得关闭。

    2026-11-08 实机：用户要求“用火狐打开贪吃蛇”，火狐在 Bash 后台启动、
    随后被观察器置前（involved），旧规则只保留“最后一步窗口 / 未被操作窗口”，
    任务结束时把火狐关掉了，与用户意图相反。
    """
    rec = _recorder(tmp_path)
    director = StubCleanupDirector({1: "终端", 2: "Mozilla Firefox"}, fg=2)
    rec.director = director
    rec._before_windows = {1: "终端"}
    rec._involved = {2}
    rec._step_new = []
    rec._gui_launch_at = time.monotonic()
    rec.note_action({"action": "show", "ok": True, "hwnd": 2, "message": "已置前"})

    rec._cleanup_transient_windows()

    assert director.closed == []
    assert 2 in director.kept


def test_gui_launch_command_marks_launch_time(tmp_path) -> None:
    """Bash 的 GUI 启动命令要记下时间点，供后续窗口出现时登记保留。"""
    rec = _recorder(tmp_path)
    rec.on_tool_before(
        {
            "tool_name": "Bash",
            "tool_use_id": "g1",
            "tool_input": {"command": 'cmd //c start "" "E:/snake/index.html"'},
        }
    )
    rec.on_tool_after(
        {"tool_name": "Bash", "tool_use_id": "g1", "is_error": False, "result": ""}
    )

    assert rec._gui_launch_at > 0.0


def test_cleanup_keeps_undeclared_windows_by_default(tmp_path) -> None:
    """未声明意图的窗口默认保留：宁可多留一个，也不误关用户要看的东西。"""
    rec = _recorder(tmp_path)
    director = StubCleanupDirector({1: "终端", 2: "某个新窗口"}, fg=1)
    rec.director = director
    rec._before_windows = {1: "终端"}
    rec._involved = {2}
    rec._step_new = []

    rec._cleanup_transient_windows()

    assert director.closed == []
    assert 2 in director.kept


def test_cleanup_closes_declared_transient_window(tmp_path) -> None:
    """工具声明 transient 的窗口，即使没被操作过也要关。"""
    rec = _recorder(tmp_path)
    director = StubCleanupDirector({1: "终端", 2: "中间窗口"}, fg=1)
    rec.director = director
    rec._before_windows = {1: "终端"}
    rec._step_new = [{2}]
    rec._step_policy = ["transient"]

    rec._cleanup_transient_windows()

    assert director.closed == [2]


def test_cleanup_declared_keep_beats_later_close_intent(tmp_path) -> None:
    """工具显式声明 keep 时，优先于关闭动作的启发式判定。"""
    rec = _recorder(tmp_path)
    director = StubCleanupDirector({1: "终端", 2: "结果窗口"}, fg=1)
    rec.director = director
    rec._before_windows = {1: "终端"}
    rec._involved = {2}
    rec._step_new = [{2}]
    rec._step_policy = ["keep"]
    rec._show_at = {2: 1.0}
    rec._last_close_intent_at = 2.0

    rec._cleanup_transient_windows()

    assert director.closed == []
    assert 2 in director.kept


def test_cleanup_close_intent_before_show_keeps_window(tmp_path) -> None:
    """关闭动作早于窗口置前时，不能把后来出现的窗口当中间产物。"""
    rec = _recorder(tmp_path)
    director = StubCleanupDirector({1: "终端", 2: "后开的窗口"}, fg=1)
    rec.director = director
    rec._before_windows = {1: "终端"}
    rec._involved = {2}
    rec._step_new = []
    rec._show_at = {2: 2.0}
    rec._last_close_intent_at = 1.0

    rec._cleanup_transient_windows()

    assert director.closed == []
    assert 2 in director.kept


def test_cleanup_unknown_transient_mode_is_aggressive(tmp_path) -> None:
    """配置为 transient 时恢复旧行为：关掉本轮被操作过的新窗口。"""
    rec = _recorder(tmp_path)
    rec.cleanup_unknown = "transient"
    director = StubCleanupDirector({1: "终端", 2: "被操作的窗口"}, fg=1)
    rec.director = director
    rec._before_windows = {1: "终端"}
    rec._involved = {2}
    rec._step_new = []

    rec._cleanup_transient_windows()

    assert director.closed == [2]


def test_close_intent_detected_from_action_close(tmp_path) -> None:
    """harness（WPS）的 action=close 要记成关闭意图。"""
    rec = _recorder(tmp_path)
    rec.on_tool_before(
        {
            "tool_name": "cli_anything__wps",
            "tool_use_id": "c1",
            "tool_input": {"subcommand": "writer", "action": "close"},
        }
    )
    rec.on_tool_after(
        {
            "tool_name": "cli_anything__wps",
            "tool_use_id": "c1",
            "is_error": False,
            "result": "",
        }
    )

    assert rec._last_close_intent_at > 0.0


def test_window_policy_from_hook_recorded(tmp_path) -> None:
    """orchestrator 透传的 window_policy 要按步骤落到 _step_policy。"""
    rec = _recorder(tmp_path)
    rec.on_tool_before(
        {"tool_name": "Demo", "tool_use_id": "p1", "tool_input": {}}
    )
    rec.on_tool_after(
        {
            "tool_name": "Demo",
            "tool_use_id": "p1",
            "is_error": False,
            "result": "",
            "window_policy": "transient",
        }
    )

    assert rec._step_policy == ["transient"]


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
