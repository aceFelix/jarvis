"""窗口动作执行器（WindowDirector）的单元测试。

跟随决策已移到 WindowObserver（见 test_window_observer.py）；
本文件只验证"怎么显示、怎么定位目录、怎么清理"。

@author aceFelix
"""

from __future__ import annotations

import os
from types import SimpleNamespace

from agent.core.window_follow import WindowDirector
from agent.core.win32_window import WindowInfo


class FakeBackend:
    """记录调用的假窗口后端。"""

    def __init__(self, *, supported: bool = True) -> None:
        self._supported = supported
        self.calls: list[tuple] = []
        self.window = WindowInfo(101, "记事本 - 无标题", (0, 0, 800, 600), False)
        self.fg = WindowInfo(202, "Chrome", (0, 0, 1200, 800), False)
        self.windows = [self.window]

    def supported(self) -> bool:
        return self._supported

    def list_windows(self) -> list[WindowInfo]:
        return list(self.windows)

    def snapshot(self) -> dict[int, WindowInfo]:
        return {w.hwnd: w for w in self.windows}

    def foreground_hwnd(self) -> int | None:
        return self.fg.hwnd

    def close(self, hwnd: int) -> bool:
        self.calls.append(("close", hwnd))
        self.windows = [w for w in self.windows if w.hwnd != hwnd]
        return True

    def find_by_title(self, title: str) -> WindowInfo | None:
        return self.window if title and title in self.window.title else None

    def show(self, hwnd: int, *, move: str = "minimal") -> bool:
        self.calls.append(("show", hwnd, move))
        return True

    def schedule_unset_topmost(self, hwnd: int, hold_ms: int) -> None:
        self.calls.append(("unset", hwnd, hold_ms))

    def activate(self, hwnd: int) -> bool:
        self.calls.append(("activate", hwnd))
        return True

    def open_folder(self, path: str) -> bool:
        self.calls.append(("open", path))
        return True


def _settings(**over) -> SimpleNamespace:
    base = dict(
        window_follow="show",
        window_follow_hold_ms=1200,
        window_follow_dir_throttle_s=60,
        window_follow_move="minimal",
        window_cleanup="transient",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_show_by_title_never_activates() -> None:
    backend = FakeBackend()
    director = WindowDirector(_settings(), backend=backend)

    result = director.show_by_title("记事本")

    assert result["action"] == "show"
    assert ("show", 101, "minimal") in backend.calls
    assert ("unset", 101, 1200) in backend.calls
    assert not any(call[0] == "activate" for call in backend.calls)


def test_show_hwnd_shows_new_window() -> None:
    backend = FakeBackend()
    director = WindowDirector(_settings(), backend=backend)

    result = director.show_hwnd(101)

    assert result["action"] == "show"
    assert ("show", 101, "minimal") in backend.calls


def test_focus_mode_activates() -> None:
    backend = FakeBackend()
    director = WindowDirector(_settings(window_follow="focus"), backend=backend)

    result = director.show_by_title("记事本")

    assert result["action"] == "focus"
    assert ("activate", 101) in backend.calls


def test_show_is_throttled_per_window() -> None:
    backend = FakeBackend()
    director = WindowDirector(_settings(), backend=backend)

    assert director.show_by_title("记事本") is not None
    assert director.show_by_title("记事本") is None
    assert len([c for c in backend.calls if c[0] == "show"]) == 1


def test_first_show_is_not_throttled_on_small_clock(monkeypatch) -> None:
    """系统 uptime 很小时，第一次显示窗口也不能被节流。

    回归 2026-11-08 CI：Linux 的 ``time.monotonic()`` 是开机秒数，GitHub runner
    刚开机时只有几十秒；旧实现拿 0.0 当“从未执行”的哨兵值，把首次动作误判成
    “5 秒内重复”（见 docs/fixlogs/window-follow-throttle-uptime-fix.md）。
    """
    backend = FakeBackend()
    director = WindowDirector(_settings(), backend=backend)
    monkeypatch.setattr("agent.core.window_follow.time.monotonic", lambda: 1.0)

    assert director.show_by_title("记事本") is not None
    assert ("show", 101, "minimal") in backend.calls


def test_missing_window_reports_instead_of_raising() -> None:
    backend = FakeBackend()
    director = WindowDirector(_settings(), backend=backend)

    result = director.show_by_title("不存在的窗口")

    assert result["ok"] is False
    assert "找不到" in result["message"]


def test_file_write_opens_folder_but_read_does_not() -> None:
    backend = FakeBackend()
    director = WindowDirector(_settings(), backend=backend)

    assert director.on_file_changed("C:/proj/a.txt", "edit")["action"] == "show_dir"
    assert ("open", os.path.abspath("C:/proj/a.txt")) in backend.calls

    backend.calls.clear()
    assert director.on_file_changed("C:/proj/b.txt", "read") is None
    assert backend.calls == []


def test_folder_open_is_throttled_per_directory() -> None:
    backend = FakeBackend()
    director = WindowDirector(_settings(), backend=backend)

    assert director.on_file_changed("C:/proj/a.txt", "write") is not None
    assert director.on_file_changed("C:/proj/b.txt", "write") is None
    assert len([c for c in backend.calls if c[0] == "open"]) == 1


def test_relative_file_path_uses_workdir() -> None:
    backend = FakeBackend()
    director = WindowDirector(_settings(), backend=backend)

    director.on_file_changed("a.txt", "write", workdir="C:/proj")

    assert ("open", os.path.abspath("C:/proj/a.txt")) in backend.calls


def test_first_folder_open_is_not_throttled_on_small_clock(monkeypatch) -> None:
    """系统 uptime 很小时，第一次定位目录也不能被节流，但后续仍要节流。

    回归 2026-11-08 CI（Linux/Python 3.14）：``time.monotonic()`` 只有几十秒时，
    旧实现用 0.0 当“从未执行”的哨兵值，把首次 ``show_dir`` 误判成
    “60 秒内重复”，三条窗口跟随测试随机失败。
    """
    backend = FakeBackend()
    director = WindowDirector(_settings(), backend=backend)
    monkeypatch.setattr("agent.core.window_follow.time.monotonic", lambda: 1.0)

    assert director.on_file_changed("C:/proj/a.txt", "edit")["action"] == "show_dir"
    assert ("open", os.path.abspath("C:/proj/a.txt")) in backend.calls
    # 首次放行不等于取消节流：同一目录紧接着再改一次文件应被吞掉
    assert director.on_file_changed("C:/proj/b.txt", "write") is None


def test_cleanup_closes_only_new_windows() -> None:
    backend = FakeBackend()
    director = WindowDirector(_settings(), backend=backend)
    before = backend.snapshot()
    backend.windows.append(WindowInfo(303, "另存为", (0, 0, 600, 400), False))

    result = director.close_transients(before, keep=set())

    assert result["closed"] == ["另存为"]
    assert ("close", 303) in backend.calls


def test_cleanup_skips_unsaved_windows() -> None:
    backend = FakeBackend()
    director = WindowDirector(_settings(), backend=backend)
    before = backend.snapshot()
    backend.windows.append(WindowInfo(404, "*未保存 - 记事本", (0, 0, 600, 400), False))

    result = director.close_transients(before, keep=set())

    assert result["closed"] == []
    assert result["skipped"] == ["*未保存 - 记事本"]
    assert ("close", 404) not in backend.calls


def test_cleanup_can_be_disabled() -> None:
    backend = FakeBackend()
    director = WindowDirector(_settings(window_cleanup="off"), backend=backend)
    before = backend.snapshot()
    backend.windows.append(WindowInfo(505, "临时窗口", (0, 0, 600, 400), False))

    result = director.close_transients(before, keep=set())

    assert result == {"closed": [], "skipped": []}
    assert ("close", 505) not in backend.calls


def test_off_mode_does_nothing() -> None:
    backend = FakeBackend()
    director = WindowDirector(_settings(window_follow="off"), backend=backend)

    assert director.show_by_title("记事本") is None
    assert director.on_file_changed("C:/proj/a.txt", "edit") is None
    assert backend.calls == []


def test_unsupported_platform_degrades_to_off() -> None:
    director = WindowDirector(_settings(), backend=FakeBackend(supported=False))

    assert director.mode == "off"
    assert director.available is False
    assert "不支持" in director.describe_platform()
    assert director.show_by_title("记事本") is None
