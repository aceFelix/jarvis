"""Win32 窗口后端：ctypes 封装的枚举 / 恢复 / 置顶 / 移动 / 激活。

为什么不用第三方库：
- "置顶但不激活"（``SWP_NOACTIVATE``）只有直接调 ``SetWindowPos`` 才能精确控制；
- ctypes 是标准库，不新增依赖、不增大安装包、不影响 PyInstaller 冻结；
- pygetwindow 只保留给既有 ``WindowFocus`` 做标题查找，本模块复用同一套匹配口径。

设计要点（2026-11 操作可见性）：
- ``show`` 档全程不调用 ``SetForegroundWindow``，只把窗口置顶，不抢用户键鼠；
- 置顶后由后台定时器自动取消 topmost，避免窗口永远压在最上层；
- 非 Windows 平台所有方法返回 "不支持"，调用方据此降级，不抛异常。

@author aceFelix
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Any

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:  # pragma: no cover - 仅在 Windows 导入
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
    )

    # 常用常量（WinUser.h）
    _SW_RESTORE = 9
    _SWP_NOSIZE = 0x0001
    _SWP_NOMOVE = 0x0002
    _SWP_NOACTIVATE = 0x0010
    _SWP_SHOWWINDOW = 0x0040
    _SPI_GETWORKAREA = 0x0030
    _WM_CLOSE = 0x0010
    _HWND_TOPMOST = ctypes.c_void_p(-1)
    _HWND_NOTOPMOST = ctypes.c_void_p(-2)

    _user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    _user32.SetWindowPos.restype = ctypes.c_bool
    # 64 位下必须显式声明 HWND 参数类型，否则 Python int 会被当成 c_int 截断句柄。
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = ctypes.c_bool
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = ctypes.c_bool
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = ctypes.c_bool
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.EnumWindows.restype = ctypes.c_bool
    _user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.PostMessageW.restype = ctypes.c_bool
    _user32.SystemParametersInfoW.argtypes = [
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    _user32.SystemParametersInfoW.restype = ctypes.c_bool
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = ctypes.c_bool
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = ctypes.c_bool
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_bool]
    _user32.AttachThreadInput.restype = ctypes.c_bool
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD


@dataclass(frozen=True)
class WindowInfo:
    """一个可见顶层窗口的最小信息。

    Attributes:
        hwnd: 窗口句柄。
        title: 窗口标题。
        rect: ``(left, top, right, bottom)`` 屏幕坐标。
        minimized: 是否最小化。
    """

    hwnd: int
    title: str
    rect: tuple[int, int, int, int]
    minimized: bool


class Win32Backend:
    """窗口后端。测试可注入同名方法的假实现（见 ``FakeBackend`` 用法）。

    @author aceFelix
    """

    def __init__(self) -> None:
        self._supported = _IS_WINDOWS

    def supported(self) -> bool:
        """当前平台是否支持窗口控制。"""
        return self._supported

    # ---- 查询 ----

    def list_windows(self) -> list[WindowInfo]:
        """列出可见、有标题、且不属于本进程的顶层窗口。"""
        if not self._supported:
            return []
        result: list[WindowInfo] = []
        own_pid = os.getpid()

        def _collect(hwnd: Any, _lparam: Any) -> bool:
            try:
                if not _user32.IsWindowVisible(hwnd):
                    return True
                length = _user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                _user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if not title:
                    return True
                pid = wintypes.DWORD()
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == own_pid:
                    return True
                rect = wintypes.RECT()
                _user32.GetWindowRect(hwnd, ctypes.byref(rect))
                result.append(
                    WindowInfo(
                        hwnd=int(hwnd),
                        title=title,
                        rect=(rect.left, rect.top, rect.right, rect.bottom),
                        minimized=bool(_user32.IsIconic(hwnd)),
                    )
                )
            except Exception:
                # 枚举期任何单窗口异常都不影响整体
                return True
            return True

        try:
            _user32.EnumWindows(_WNDENUMPROC(_collect), 0)
        except Exception:
            return result
        return result

    def find_by_title(self, title: str) -> WindowInfo | None:
        """按标题查找：精确优先，其次**标题更短**的模糊命中。

        为什么不取第一个：``helloJarvis.txt`` 可能同时命中
        ``helloJarvis.txt - 记事本`` 和 ``E:\\...\\helloJarvis.txt - Notepad++``，
        后者标题更长、往往只是碰巧包含路径，取第一条会把动作发错窗口。
        """
        needle = (title or "").strip()
        if not needle:
            return None
        wins = self.list_windows()
        for win in wins:
            if win.title == needle:
                return win
        lowered = needle.lower()
        matches = [w for w in wins if lowered in w.title.lower()]
        if not matches:
            return None
        matches.sort(key=lambda w: len(w.title))
        return matches[0]

    def foreground(self) -> WindowInfo | None:
        """当前前台窗口。"""
        if not self._supported:
            return None
        try:
            hwnd = _user32.GetForegroundWindow()
            if not hwnd:
                return None
            buf_len = _user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(buf_len + 1)
            _user32.GetWindowTextW(hwnd, buf, buf_len + 1)
            rect = wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
            return WindowInfo(
                hwnd=int(hwnd),
                title=buf.value,
                rect=(rect.left, rect.top, rect.right, rect.bottom),
                minimized=bool(_user32.IsIconic(hwnd)),
            )
        except Exception:
            return None

    def snapshot(self) -> dict[int, WindowInfo]:
        """当前可见窗口的快照（hwnd → 窗口信息），用于识别"本轮新开的窗口"。"""
        return {w.hwnd: w for w in self.list_windows()}

    def foreground_hwnd(self) -> int | None:
        """前台窗口句柄；拿不到时 None。"""
        win = self.foreground()
        return win.hwnd if win else None

    def close(self, hwnd: int) -> bool:
        """发送 WM_CLOSE 让窗口正常关闭（不是强杀，未保存时应用会自己弹确认）。"""
        if not self._supported:
            return False
        try:
            return bool(_user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0))
        except Exception:
            return False

    def work_area(self) -> tuple[int, int, int, int]:
        """主屏可用工作区（已扣任务栏）。"""
        if not self._supported:
            return (0, 0, 1920, 1080)
        rect = wintypes.RECT()
        try:
            _user32.SystemParametersInfoW(_SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
            return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            return (0, 0, 1920, 1080)

    # ---- 动作 ----

    def show(self, hwnd: int, *, move: str = "minimal") -> bool:
        """恢复并置顶窗口，**不激活**。

        Args:
            hwnd: 目标窗口句柄。
            move: ``minimal`` 只在最小化/屏外时挪位；``side`` 总是挪到主屏右侧。

        Returns:
            是否成功置顶。
        """
        if not self._supported:
            return False
        try:
            if _user32.IsIconic(hwnd):
                _user32.ShowWindow(hwnd, _SW_RESTORE)
            rect = wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
            wl, wt, wr, wb = self.work_area()
            off_screen = (
                rect.right <= wl or rect.left >= wr or rect.bottom <= wt or rect.top >= wb
            )
            flags = _SWP_NOACTIVATE | _SWP_SHOWWINDOW
            x, y, w, h = rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
            if move == "side" or off_screen:
                width = max(480, min(int((wr - wl) * 0.45), w))
                height = max(320, min(int((wb - wt) * 0.7), h))
                x = wr - width - 16
                y = wt + 16
                w, h = width, height
            else:
                flags |= _SWP_NOMOVE | _SWP_NOSIZE
            _user32.SetWindowPos(hwnd, _HWND_TOPMOST, x, y, w, h, flags)
            return True
        except Exception:
            return False

    def unset_topmost(self, hwnd: int) -> None:
        """取消置顶，保持位置与尺寸不变。"""
        if not self._supported:
            return
        try:
            _user32.SetWindowPos(
                hwnd,
                _HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
            )
        except Exception:
            pass

    def schedule_unset_topmost(self, hwnd: int, hold_ms: int) -> None:
        """在 ``hold_ms`` 毫秒后后台取消置顶（不阻塞工具执行）。"""
        if not self._supported:
            return
        timer = threading.Timer(max(0.0, hold_ms / 1000.0), self.unset_topmost, args=(hwnd,))
        timer.daemon = True
        timer.start()

    def activate(self, hwnd: int) -> bool:
        """真正激活窗口（仅 ``focus`` 档使用）。

        Windows 前台锁会拒绝后台进程直接抢焦点；这里按业界通行做法用
        ``AttachThreadInput`` 附加到前台线程后再试一次。仍失败则如实返回 False。
        """
        if not self._supported:
            return False
        try:
            if _user32.SetForegroundWindow(hwnd):
                return True
            fg = _user32.GetForegroundWindow()
            if not fg:
                return False
            fg_thread = _user32.GetWindowThreadProcessId(fg, None)
            cur_thread = _kernel32.GetCurrentThreadId()
            _user32.AttachThreadInput(cur_thread, fg_thread, True)
            try:
                ok = bool(_user32.SetForegroundWindow(hwnd))
            finally:
                _user32.AttachThreadInput(cur_thread, fg_thread, False)
            return ok
        except Exception:
            return False

    def open_folder(self, path: str) -> bool:
        """打开文件所在目录并选中该文件（``explorer /select``）。

        Args:
            path: 文件路径；若传入目录则直接打开该目录。
        """
        if not _IS_WINDOWS:
            return False
        try:
            target = os.path.normpath(path)
            if os.path.isdir(target):
                subprocess.Popen(["explorer", target])
            else:
                subprocess.Popen(["explorer", f"/select,{target}"])
            return True
        except Exception:
            return False
