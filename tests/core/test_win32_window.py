"""Win32 窗口后端的平台行为测试。

CI 跑在 Linux 上：非 Windows 必须全部安全降级（返回 False/空），不能抛异常；
Windows 本机只断言平台识别，不触碰真实窗口。

@author aceFelix
"""

from __future__ import annotations

import sys

import pytest

from agent.core.win32_window import Win32Backend, WindowInfo


def test_supported_matches_platform() -> None:
    backend = Win32Backend()
    assert backend.supported() == (sys.platform == "win32")


@pytest.mark.skipif(sys.platform == "win32", reason="仅在非 Windows 验证降级路径")
def test_non_windows_degrades_safely() -> None:
    backend = Win32Backend()
    assert backend.list_windows() == []
    assert backend.find_by_title("记事本") is None
    assert backend.foreground() is None
    assert backend.show(1) is False
    assert backend.activate(1) is False
    assert backend.open_folder("C:/tmp/a.txt") is False
    backend.unset_topmost(1)      # 不抛异常即通过
    backend.schedule_unset_topmost(1, 10)


def test_find_by_title_prefers_shorter_match(monkeypatch) -> None:
    """2026-11-08 实测：同一条文件名同时出现在记事本与 Notepad++ 标题里。

    旧实现取枚举到的第一个，结果把点击发到了标题更长的 Notepad++。
    """
    backend = Win32Backend()
    windows = [
        WindowInfo(1, r"E:\work\helloJarvis.txt - Notepad++", (0, 0, 800, 600), False),
        WindowInfo(2, "helloJarvis.txt - 记事本", (0, 0, 800, 600), False),
    ]
    monkeypatch.setattr(backend, "list_windows", lambda: windows)

    assert backend.find_by_title("helloJarvis.txt").hwnd == 2
    assert backend.find_by_title("helloJarvis.txt - 记事本").hwnd == 2


def test_window_info_is_immutable() -> None:
    win = WindowInfo(1, "标题", (0, 0, 10, 10), False)
    with pytest.raises(Exception):
        win.title = "改不了"  # type: ignore[misc]
