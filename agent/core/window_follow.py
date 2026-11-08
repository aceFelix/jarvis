"""窗口跟随（window follow）：把"正在被操作的窗口"显示到用户眼前。

设计原则（2026-11 操作可见性）：

- **看得见，不夺权**：默认 ``show`` 只置顶、不激活，用户键鼠完全不受影响；
- **可关闭**：``off`` 时一行代码都不执行，行为回到旧版本；
- **可降级**：非 Windows / 找不到窗口 / 前台锁拒绝，都只返回一句说明，不抛异常；
- **节流**：同一窗口 5 秒内不重复置顶；同一目录 60 秒内不重复弹资源管理器。

本模块不直接写终端、不直接写 trace 文件：只返回 ``{"action", "ok", "message"}``，
由 ``agent/core/trace.py`` 统一记录并渲染，保证"窗口动作"和"工具动作"同一条时间线。

@author aceFelix
"""

from __future__ import annotations

import os
import time
from typing import Any

from agent.core.win32_window import Win32Backend, WindowInfo

# 跟随决策已移到 agent/core/window_observer.py：不再维护"工具名 → 是否跟随"白名单。
# 本模块只负责"怎么显示"与"怎么清理"。
_SHOW_THROTTLE_S = 5.0


class WindowDirector:
    """窗口动作执行器：显示（show/focus）、定位目录、收尾清理。

    跟随决策由 ``agent/core/window_observer.py`` 负责；本类不含任何工具白名单。

    Args:
        settings: 已加载配置，读取 ``[ui]`` 段（``window_follow`` 等）。
        backend: 窗口后端；测试注入假实现，默认 ``Win32Backend``。

    @author aceFelix
    """

    def __init__(self, settings: Any, backend: Any | None = None) -> None:
        self._backend = backend or Win32Backend()
        self.mode = str(getattr(settings, "window_follow", "show") or "show").lower()
        if self.mode not in ("off", "show", "focus"):
            self.mode = "show"
        self.hold_ms = _int_setting(settings, "window_follow_hold_ms", 1200, minimum=0)
        self.dir_throttle_s = _int_setting(
            settings, "window_follow_dir_throttle_s", 60, minimum=0
        )
        self.move = str(getattr(settings, "window_follow_move", "minimal") or "minimal")
        if self.move not in ("minimal", "side"):
            self.move = "minimal"
        # 任务收尾清理：只关「本轮新开、非最终结果」的窗口；默认开启。
        self.cleanup = str(getattr(settings, "window_cleanup", "transient") or "transient").lower()
        if self.cleanup not in ("transient", "off"):
            self.cleanup = "transient"
        self.enabled = True
        # 非 Windows 自动降级为 off，并在第一次调用时给一次说明
        if not self._backend.supported():
            self.mode = "off"
            self._unsupported_reason = "当前平台不支持窗口跟随（仅 Windows 完整支持）"
        else:
            self._unsupported_reason = ""
        self._last_show: dict[str, float] = {}
        self._last_dir: dict[str, float] = {}

    # ---- 对外入口 ----

    @property
    def available(self) -> bool:
        """当前平台与配置是否允许窗口动作。"""
        return self.enabled and self.mode != "off"

    def snapshot_records(self) -> dict[int, WindowInfo]:
        """当前可见窗口快照（hwnd → WindowInfo），供观察器差分。"""
        if not self.available:
            return {}
        try:
            return self._backend.snapshot()
        except Exception:
            return {}

    def foreground_hwnd(self) -> int | None:
        """当前前台窗口句柄。"""
        try:
            return self._backend.foreground_hwnd()
        except Exception:
            return None

    def show_by_title(self, title: str) -> dict[str, Any] | None:
        """入参已声明窗口标题：按标题找到并显示。"""
        if not self.available:
            return None
        win = self._backend.find_by_title(title)
        if win is None:
            return {
                "action": "show",
                "ok": False,
                "message": f"未能显示窗口「{title}」：找不到目标窗口",
            }
        return self._show_record(win)

    def show_hwnd(self, hwnd: int) -> dict[str, Any] | None:
        """观察器判定某个新窗口需要显示时调用。"""
        if not self.available:
            return None
        win = self.snapshot_records().get(hwnd)
        if win is None:
            return None
        return self._show_record(win)

    def on_file_changed(
        self, path: str, operation: str, workdir: str = ""
    ) -> dict[str, Any] | None:
        """文件被写/改后调用：定位到所在目录并选中文件。

        ``read`` 不弹窗——读文件时把资源管理器拽到前台会打断用户。
        相对路径按 ``workdir`` 补全，避免定位到进程启动目录而不是项目目录。
        """
        if not self.enabled or self.mode == "off":
            return None
        if operation not in ("write", "edit"):
            return None
        if not path:
            return None
        if not os.path.isabs(path) and workdir:
            path = os.path.join(workdir, path)
        return self._show_dir(path)

    # ---- 内部实现 ----

    def _show_record(self, win: WindowInfo) -> dict[str, Any] | None:
        hwnd = win.hwnd
        now = time.monotonic()
        key = str(hwnd)
        # 首次动作永不节流：不能用 0.0 当"从未执行"的哨兵值——Linux 上
        # time.monotonic() 是开机以来的秒数，刚启动的 CI 机器可能只有几十秒，
        # 直接比较会把第一次显示误判成"5 秒内重复"（2026-11-08 CI 回归）。
        last = self._last_show.get(key)
        if last is not None and now - last < _SHOW_THROTTLE_S:
            return None
        self._last_show[key] = now

        if self.mode == "focus":
            ok = self._backend.activate(hwnd)
            if ok:
                return {
                    "action": "focus",
                    "ok": True,
                    "hwnd": hwnd,
                    "message": f"已激活「{win.title}」并接管输入焦点（{self.hold_ms}ms）",
                }
            return {
                "action": "focus",
                "ok": False,
                "message": f"无法激活「{win.title}」：Windows 前台锁拒绝了本次切换",
            }

        ok = self._backend.show(hwnd, move=self.move)
        if not ok:
            return {
                "action": "show",
                "ok": False,
                "message": f"未能置顶「{win.title}」",
            }
        self._backend.schedule_unset_topmost(hwnd, self.hold_ms)
        return {
            "action": "show",
            "ok": True,
            "hwnd": hwnd,
            "message": f"已把「{win.title}」置于前台（{self.hold_ms}ms，未接管输入）",
        }

    def _show_dir(self, path: str) -> dict[str, Any] | None:
        target = os.path.abspath(path)
        folder = target if os.path.isdir(target) else os.path.dirname(target)
        now = time.monotonic()
        # 同上：只用"确实弹过这个目录"才参与节流；0.0 哨兵值在系统 uptime
        # 小于 dir_throttle_s（默认 60 秒）时会把首次定位目录吞掉。
        last = self._last_dir.get(folder)
        if last is not None and now - last < self.dir_throttle_s:
            return None
        self._last_dir[folder] = now
        ok = self._backend.open_folder(target)
        name = os.path.basename(target.rstrip(os.sep)) or target
        if ok:
            return {
                "action": "show_dir",
                "ok": True,
                "message": f"已定位到「{name}」所在文件夹（未接管输入）",
            }
        return {
            "action": "show_dir",
            "ok": False,
            "message": f"无法打开「{name}」所在文件夹",
        }

    def describe_platform(self) -> str:
        """返回平台降级说明；可用时返回空串（供启动时一次性提示）。"""
        return self._unsupported_reason

    # ---- 任务收尾清理 ----

    def snapshot(self) -> dict[int, str]:
        """当前可见窗口快照（hwnd → 标题）。"""
        try:
            return self._backend.snapshot()
        except Exception:
            return {}

    def close_transients(
        self, before: dict[int, WindowInfo], keep: set[int] | None = None
    ) -> dict[str, list[str]]:
        """关闭「本轮新开、且不在保留集合」的窗口。

        安全边界（不可越过的三条）：
        - **只关本轮新出现的 hwnd**：``before`` 里已有的一律不碰（那是用户自己的窗口）；
        - **保留集合不关**：最后一步打开的窗口、当前前台窗口（用户正在看的结果）；
        - **未保存窗口不关**：标题含 ``*`` 的跳过并如实上报，绝不强杀丢数据。

        关闭用 ``WM_CLOSE`` 而非 ``TerminateProcess``：应用自己有机会弹保存确认。

        Args:
            before: 本轮开始时的窗口快照。
            keep: 需要保留的 hwnd 集合。

        Returns:
            ``{"closed": [...标题], "skipped": [...未保存标题]}``

        @author aceFelix
        """
        if not self.enabled or self.cleanup == "off":
            return {"closed": [], "skipped": []}
        try:
            current = self._backend.snapshot()
        except Exception:
            return {"closed": [], "skipped": []}
        keep_set = keep or set()
        closed: list[str] = []
        skipped: list[str] = []
        for hwnd, win in current.items():
            if hwnd in before or hwnd in keep_set:
                continue
            title = win.title
            if "*" in title:
                skipped.append(title)
                continue
            try:
                if self._backend.close(hwnd):
                    closed.append(title)
            except Exception:
                continue
        return {"closed": closed, "skipped": skipped}


def _int_setting(settings: Any, name: str, default: int, *, minimum: int = 0) -> int:
    """从 settings 读整数并做下限保护（坏配置不炸启动）。"""
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, value)
