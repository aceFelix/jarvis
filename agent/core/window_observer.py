"""通用窗口观察器：用"窗口事实变化"决定是否跟随，而不是工具名白名单。

设计定稿见 ``docs/plans/window-observer.md``。一句话：

> 工具执行前后拍两次窗口快照，谁新出现、且成了前台，就跟谁；
> 工具入参里写了窗口标题，就按标题跟。

这样未来新增 MCP / 插件 / harness / Bash 工具，只要它真的开了窗，就自动覆盖，
不需要再往任何清单里登记。

安全边界：
- 只跟"新出现且已成为前台"的新窗口，用户自己切已有窗口不跟；
- 文件定位仍走 ``WindowDirector.on_file_changed``，且只对写/改；
- 子代理默认不跟随（避免多个代理抢屏）；
- ``window_follow = off`` 时零快照、零线程。

@author aceFelix
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from agent.core.win32_window import WindowInfo

_TITLE_KEYS = ("title", "window", "window_title")


@dataclass
class ProbeState:
    """一次工具执行的窗口快照基线。"""

    tool_use_id: str
    tool: str
    before: dict[int, WindowInfo]
    before_foreground: int | None
    started: float
    retried: bool = False


class WindowObserver:
    """快照差分 + 前台判定 + 探测调度。

    Args:
        settings: 读取 ``[ui]`` 的 ``window_probe_delay_ms`` 等。
        director: 负责真正执行 show/focus 的 ``WindowDirector``。
        sink: 判定命中后的回调（由 ``TraceRecorder`` 写入事件与终端）。

    @author aceFelix
    """

    def __init__(self, settings: Any, director: Any, sink: Callable[[dict], None]):
        self.director = director
        self._sink = sink
        self.delay_ms = _int_setting(settings, "window_probe_delay_ms", 450)
        self.retry_ms = _int_setting(settings, "window_probe_retry_ms", 1200)
        self.follow_subagents = bool(getattr(settings, "window_follow_subagents", False))
        self._pending: dict[str, ProbeState] = {}
        self._timers: list[threading.Timer] = []
        self._lock = threading.Lock()

    # ---- 对外入口 ----

    def before(
        self,
        tool: str,
        tool_input: dict[str, Any],
        tool_use_id: str,
        ctx: Any = None,
    ) -> None:
        """工具执行前：拍快照；入参声明了窗口标题则立即跟随。"""
        if not self._active(ctx):
            return
        state = ProbeState(
            tool_use_id=tool_use_id,
            tool=tool,
            before=self.director.snapshot_records(),
            before_foreground=self.director.foreground_hwnd(),
            started=time.monotonic(),
        )
        with self._lock:
            self._pending[tool_use_id] = state

        title = _declared_title(tool_input)
        if title:
            action = self.director.show_by_title(title)
            if action:
                self._sink(action)

    def after(self, tool_use_id: str) -> None:
        """工具执行后：安排一次延迟探测（首次未命中再重试一次）。"""
        with self._lock:
            state = self._pending.pop(tool_use_id, None)
        if state is None:
            return
        self._schedule(state, self.delay_ms, retry=False)

    def cancel_all(self) -> None:
        """取消所有待执行探测（回合收尾/关闭开关时调用）。"""
        with self._lock:
            timers = list(self._timers)
            self._timers.clear()
            self._pending.clear()
        for timer in timers:
            timer.cancel()

    # ---- 内部 ----

    def _active(self, ctx: Any) -> bool:
        if not getattr(self.director, "available", False):
            return False
        if not getattr(self.director, "enabled", False):
            return False
        if not self.follow_subagents:
            ui = getattr(ctx, "ui", None) if ctx is not None else None
            if getattr(ui, "activity_id", None):
                return False
        return True

    def _schedule(self, state: ProbeState, delay_ms: int, *, retry: bool) -> None:
        if delay_ms <= 0:
            self._probe(state, retry=retry)
            return
        timer = threading.Timer(delay_ms / 1000.0, self._probe, args=(state, retry))
        timer.daemon = True
        with self._lock:
            self._timers.append(timer)
        timer.start()

    def _probe(self, state: ProbeState, retry: bool = False) -> None:
        after = self.director.snapshot_records()
        if not after:
            return
        fg = self.director.foreground_hwnd()
        new_hwnds = [hwnd for hwnd in after if hwnd not in state.before]

        hit: int | None = None
        for hwnd in new_hwnds:
            win = after[hwnd]
            if win.minimized or not win.title:
                continue
            # 只认"新出现 + 已成为前台"：避免跟随用户自己开的后台窗口
            if hwnd == fg:
                hit = hwnd
                break

        if hit is None:
            if not retry and self.retry_ms > 0:
                state.retried = True
                self._schedule(state, self.retry_ms, retry=True)
            return

        action = self.director.show_hwnd(hit)
        if action:
            self._sink(action)


def _declared_title(tool_input: dict[str, Any]) -> str:
    """从工具入参里提取显式窗口标题（通用字段扫描，不按工具名）。"""
    for key in _TITLE_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _int_setting(settings: Any, name: str, default: int) -> int:
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default
    return max(0, value)
