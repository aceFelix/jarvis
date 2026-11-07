"""通用窗口观察器的单元测试（快照差分 + 前台判定）。

这些用例守的是架构底线：决策不许依赖工具名；只认"新出现且变前台"的新窗口。

@author aceFelix
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.core.window_observer import WindowObserver
from agent.core.win32_window import WindowInfo


class FakeDirector:
    def __init__(self, snapshots: list[dict[int, WindowInfo]], fg: int | None) -> None:
        self.snapshots = snapshots
        self.fg = fg
        self.available = True
        self.enabled = True
        self.title_calls: list[str] = []
        self.hwnd_calls: list[int] = []
        self.snapshot_calls = 0

    def snapshot_records(self):
        self.snapshot_calls += 1
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]

    def foreground_hwnd(self):
        return self.fg

    def show_by_title(self, title: str):
        self.title_calls.append(title)
        return {"action": "show", "ok": True, "hwnd": 101, "message": title}

    def show_hwnd(self, hwnd: int):
        self.hwnd_calls.append(hwnd)
        return {"action": "show", "ok": True, "hwnd": hwnd, "message": "new"}


def _settings(**over) -> SimpleNamespace:
    base = dict(
        window_probe_delay_ms=0,
        window_probe_retry_ms=0,
        window_follow_subagents=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _win(hwnd: int, title: str = "窗口") -> WindowInfo:
    return WindowInfo(hwnd, title, (0, 0, 600, 400), False)


def _observer(snapshots, fg, **settings_over):
    director = FakeDirector(snapshots, fg)
    actions: list[dict] = []
    observer = WindowObserver(_settings(**settings_over), director, actions.append)
    return observer, director, actions


def test_no_window_change_means_no_action() -> None:
    snap = {1: _win(1, "终端")}
    observer, director, actions = _observer([snap], fg=1)

    observer.before("WhateverTool", {}, "t1")
    observer.after("t1")

    assert actions == []
    assert director.hwnd_calls == []


def test_new_foreground_window_is_followed() -> None:
    before = {1: _win(1, "终端")}
    after = {1: _win(1, "终端"), 2: _win(2, "记事本")}
    observer, director, actions = _observer([before, after], fg=2)

    observer.before("Bash", {"command": "notepad.exe"}, "t2")
    observer.after("t2")

    assert director.hwnd_calls == [2]
    assert actions[0]["hwnd"] == 2


def test_new_background_window_is_not_followed() -> None:
    before = {1: _win(1, "终端")}
    after = {1: _win(1, "终端"), 3: _win(3, "更新提示")}
    observer, director, actions = _observer([before, after], fg=1)

    observer.before("SomeTool", {}, "t3")
    observer.after("t3")

    assert actions == []
    assert director.hwnd_calls == []


def test_declared_title_is_followed_without_new_window() -> None:
    snap = {1: _win(1, "终端"), 5: _win(5, "记事本")}
    observer, director, actions = _observer([snap], fg=1)

    observer.before("WindowFocus", {"title": "记事本"}, "t4")
    observer.after("t4")

    assert director.title_calls == ["记事本"]
    assert actions[0]["hwnd"] == 101


def test_observer_does_not_touch_windows_when_follow_is_off() -> None:
    snap = {1: _win(1, "终端")}
    observer, director, actions = _observer([snap], fg=1)
    director.available = False

    observer.before("Bash", {}, "t5")
    observer.after("t5")

    assert director.snapshot_calls == 0
    assert actions == []


def test_subagent_is_not_followed_by_default() -> None:
    snap = {1: _win(1, "终端")}
    observer, director, actions = _observer([snap], fg=1)
    ctx = SimpleNamespace(ui=SimpleNamespace(activity_id="agent-1"))

    observer.before("Bash", {}, "t6", ctx)
    observer.after("t6")

    assert director.snapshot_calls == 0
    assert actions == []


def test_retry_probe_catches_late_window() -> None:
    """首次探测未命中、重试命中，用同步调用避免 CI 上的定时器抖动。"""
    before = {1: _win(1, "终端")}
    after = {1: _win(1, "终端"), 9: _win(9, "记事本")}
    observer, director, actions = _observer(
        [before, before, after], fg=9, window_probe_delay_ms=0, window_probe_retry_ms=1
    )

    observer.before("Bash", {}, "t7")
    state = observer._pending.pop("t7")
    observer._probe(state, retry=False)   # 第一次：只有旧窗口
    observer._probe(state, retry=True)    # 第二次：新窗口已成为前台

    assert director.hwnd_calls == [9]
