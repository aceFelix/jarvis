"""终端优先的置顶策略：用 Git Bash / cmd 时让 HUD 退到终端下面。

用户 2026-11-09 要求：正在使用 git bash / cmd 窗口时，终端应盖住任务驾驶舱与
抓取舱；切到其它程序时两舱恢复常驻置顶。实现要点：

- 只在 Tk 线程调用；每 ``interval`` 秒查一次前台窗口的进程 exe 名；
- 前台是终端类进程时把 root 与抓取舱的 ``-topmost`` 置 False（**不 lower**，
  避免窗口闪烁/跳动），并记住“当前不该置顶”，供 ``__show__`` 重现时沿用；
- 非 Windows 平台恒为置顶，行为与之前完全一致。

@author aceFelix
"""

from __future__ import annotations

import sys
import time
from typing import Any

__all__ = [
    "TerminalAwareTopmost",
    "foreground_process_name",
    "is_terminal_process",
]

# 常见终端宿主：Git Bash(mintty) / cmd(conhost) / PowerShell / Windows Terminal
# / 以及几个常用的第三方终端。全部小写比较。
_TERMINAL_EXES = {
    "mintty.exe",
    "git-bash.exe",
    "bash.exe",
    "conhost.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "windowsterminal.exe",
    "openconsole.exe",
    "wt.exe",
    "alacritty.exe",
    "wezterm-gui.exe",
    "tabby.exe",
    "hyper.exe",
}


def foreground_process_name() -> str:
    """返回前台窗口所属进程的 exe 名（小写）。

    Returns:
        例如 ``mintty.exe``；非 Windows、取不到前台窗口或查询失败时返回空串。
    """
    if not sys.platform.startswith("win"):
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        # PROCESS_QUERY_LIMITED_INFORMATION：无需提权即可查 exe 路径
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return ""
            return str(buffer.value).rsplit("\\", 1)[-1].lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def is_terminal_process(name: str) -> bool:
    """判断进程名是否属于常见终端宿主（Git Bash / cmd / PowerShell 等）。"""
    return (name or "").lower() in _TERMINAL_EXES


class TerminalAwareTopmost:
    """按前台窗口决定 HUD 是否保持置顶（只在 Tk 线程调用）。

    Attributes:
        _root: Tk 根窗口（主驾驶舱）。
        _crawl: 抓取舱管理器（需提供 ``set_topmost(bool)``），可为 None。
        _enabled: 用户是否启用了置顶（``CockpitWindow(topmost=...)``）。
        _terminal: 上一次检测结果，只有状态变化时才真正调用 Tk。

    @author aceFelix
    """

    def __init__(
        self,
        root: Any,
        crawl: Any = None,
        *,
        enabled: bool = True,
        interval: float = 0.4,
    ) -> None:
        """初始化策略。

        Args:
            root: Tk 根窗口。
            crawl: ``CrawlManager``（可空）；需实现 ``set_topmost``。
            enabled: 是否启用置顶（False 时永远不置顶）。
            interval: 前台窗口检测的最小间隔（秒），避免每帧都查进程。
        """
        self._root = root
        self._crawl = crawl
        self._enabled = bool(enabled)
        self._interval = interval
        self._last = 0.0
        self._terminal = False

    @property
    def wanted(self) -> bool:
        """当前是否应保持置顶（终端在前台时为 False）。"""
        return self._enabled and not self._terminal

    def tick(self) -> None:
        """节流检测前台窗口；状态变化时才切换置顶（避免闪烁）。"""
        now = time.monotonic()
        if now - self._last < self._interval:
            return
        self._last = now
        terminal = is_terminal_process(foreground_process_name())
        if terminal == self._terminal:
            return
        self._terminal = terminal
        self._apply()

    def _apply(self) -> None:
        """把当前策略应用到 root 与抓取舱；异常静默（可视化不能影响主流程）。"""
        wanted = self.wanted
        try:
            self._root.attributes("-topmost", wanted)
        except Exception:
            pass
        try:
            if self._crawl is not None:
                self._crawl.set_topmost(wanted)
        except Exception:
            pass
