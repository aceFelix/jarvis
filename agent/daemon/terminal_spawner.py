"""快速文本终端唤起模块。

目标：把「全局热键 → 文本对话窗口可输入」的耗时压到 500ms 以内。

实现三层策略：
1. **窗口复用**：上一次弹出的终端进程仍在运行，直接置顶（最快）。
2. **warm 进程**：daemon 启动后预启动一个隐藏终端，热键按下时显示。
3. **快速冷启动**：spawn 新进程时使用 ``--quick`` 参数，跳过 boot animation、
   MCP、LSP 等可选初始化。

@author aceFelix
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable


def _is_windows() -> bool:
    """是否运行在 Windows 平台。"""
    return sys.platform == "win32"


def _is_macos() -> bool:
    """是否运行在 macOS 平台。"""
    return sys.platform == "darwin"


def _find_python() -> str | None:
    """找到用于弹出文本终端的 Python 解释器路径。

    Windows: 找与当前解释器配对的 python.exe（有控制台窗口）。
    macOS/Linux: 直接返回 sys.executable。
    """
    if _is_windows():
        exe = sys.executable
        dirname = os.path.dirname(exe)
        basename = os.path.basename(exe).lower()
        if basename == "python.exe":
            return exe
        if basename == "pythonw.exe":
            python_exe = os.path.join(dirname, "python.exe")
            if os.path.exists(python_exe):
                return python_exe
        return None
    return sys.executable


def _project_root() -> str:
    """获取项目根目录（agent/ 的父目录）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _find_mintty() -> str | None:
    """找到 Git Bash 的终端模拟器 mintty.exe。

    搜索策略：
    1. ``shutil.which("mintty")``
    2. 从 ``shutil.which("git")`` 反查 Git 安装根目录
    3. 硬编码常见路径

    @author aceFelix
    """
    import shutil

    mintty = shutil.which("mintty")
    if mintty and os.path.isfile(mintty):
        return mintty

    git = shutil.which("git")
    if git:
        git_bin_dir = os.path.dirname(git)
        git_parent = os.path.dirname(git_bin_dir)
        git_grandparent = os.path.dirname(git_parent)
        candidate_dirs = {git_parent, git_grandparent}
        if os.path.basename(git_bin_dir).lower() == "cmd":
            candidate_dirs.add(os.path.dirname(git_grandparent))
        for root in candidate_dirs:
            for candidate in (
                os.path.join(root, "usr", "bin", "mintty.exe"),
                os.path.join(root, "git-bash.exe"),
            ):
                if os.path.isfile(candidate):
                    return candidate

    for path in (
        r"C:\Program Files\Git\usr\bin\mintty.exe",
        r"D:\SoftwareDevelopmentKit\Git\usr\bin\mintty.exe",
    ):
        if os.path.isfile(path):
            return path

    return None


def _to_unix_path(win_path: str) -> str:
    """Windows 路径 → Git Bash 用的 Unix 路径。"""
    drive, rest = os.path.splitdrive(win_path)
    if drive:
        return "/" + drive[0].lower() + rest.replace("\\", "/")
    return win_path.replace("\\", "/")


def _set_foreground_window(pid: int) -> bool:
    """Windows: 尝试把属于指定 PID 的顶层窗口置前。

    先尝试通过 ``pygetwindow`` 按标题查找；失败则枚举顶层窗口匹配 PID。
    返回是否成功。
    """
    if not _is_windows():
        return False
    try:
        import pygetwindow as gw
        # mintty 窗口标题通常包含 "MINGW64" 或 "bash"
        for w in gw.getAllWindows():
            if w.title and ("MINGW64" in w.title or "bash" in w.title.lower() or "jarvis" in w.title.lower()):
                try:
                    w.activate()
                    return True
                except Exception:
                    continue
    except Exception:
        pass

    try:
        import ctypes
        import ctypes.wintypes

        _ENUM_CB = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.wintypes.HWND,
            ctypes.wintypes.LPARAM,
        )

        found_hwnd = ctypes.wintypes.HWND()

        target_pid = pid

        def _callback(hwnd: int, _lparam: int) -> bool:
            nonlocal found_hwnd
            try:
                proc_id = ctypes.wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
                if proc_id.value == target_pid:
                    if ctypes.windll.user32.IsWindowVisible(hwnd):
                        found_hwnd = ctypes.wintypes.HWND(hwnd)
                        return False
            except Exception:
                pass
            return True

        ctypes.windll.user32.EnumWindows(_ENUM_CB(_callback), 0)
        if found_hwnd:
            ctypes.windll.user32.SetForegroundWindow(found_hwnd)
            return True
    except Exception:
        pass
    return False


class FastTerminalSpawner:
    """快速文本终端唤起器。

    负责管理文本终端子进程的生命周期，提供 ``bring_up`` 方法，
    优先复用已有窗口，其次 spawn 新进程。

    Attributes:
        settings: Jarvis 配置对象，用于读取 workdir / 热键相关配置。
        notify: 通知回调，接收 (title, message)。
        log: 日志回调，接收格式字符串和参数。
        warm_enabled: 是否启用预启动 warm 进程。
    """

    def __init__(
        self,
        settings: Any,
        notify: Callable[[str, str], None] | None = None,
        log: Callable[..., None] | None = None,
        *,
        warm_enabled: bool = False,
    ) -> None:
        self._settings = settings
        self._notify = notify or (lambda _t, _m: None)
        self._log = log or (lambda *_a, **_k: None)
        self._warm_enabled = warm_enabled
        self._text_terminal_proc: subprocess.Popen | None = None
        self._warm_proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start_warm(self) -> None:
        """后台预启动一个隐藏的文本终端进程（warm 模式）。"""
        if not self._warm_enabled:
            return
        with self._lock:
            if self._warm_proc is not None and self._warm_proc.poll() is None:
                return
            self._warm_proc = None
        threading.Thread(target=self._spawn_warm, daemon=True).start()

    def bring_up(self) -> None:
        """唤起文本对话窗口到前台。

        执行顺序：
        1. 如果已有普通终端进程在运行，直接置顶窗口。
        2. 如果有 warm 进程可用，显示窗口并标记为当前终端。
        3. 否则 spawn 新终端（使用 --quick 加速）。
        """
        with self._lock:
            # 1. 复用已有普通终端
            if self._text_terminal_proc is not None:
                poll = self._text_terminal_proc.poll()
                if poll is None:
                    if _is_windows():
                        ok = _set_foreground_window(self._text_terminal_proc.pid)
                        if ok:
                            self._notify("J.A.R.V.I.S", "文本对话窗口已置顶")
                            return
                    else:
                        self._notify("J.A.R.V.I.S", "文本对话窗口已打开")
                        return
                self._text_terminal_proc = None

            # 2. 使用 warm 进程
            if self._warm_proc is not None and self._warm_proc.poll() is None:
                self._text_terminal_proc = self._warm_proc
                self._warm_proc = None
                if _is_windows():
                    _set_foreground_window(self._text_terminal_proc.pid)
                self._notify("J.A.R.V.I.S", "文本对话窗口已唤起")
                # 立即启动下一个 warm 进程备用
                self.start_warm()
                return

            self._warm_proc = None

        # 3. 快速冷启动
        self._spawn_quick()

    def stop(self) -> None:
        """清理所有终端子进程（退出 daemon 时调用）。"""
        with self._lock:
            procs = [self._text_terminal_proc, self._warm_proc]
        for proc in procs:
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    def _spawn_quick(self) -> None:
        """spawn 一个新的快速启动终端。"""
        python_exe = _find_python()
        if not python_exe:
            self._notify("J.A.R.V.I.S", "无法找到 Python 解释器，文本对话不可用")
            self._log("文本对话失败: 未找到 Python 解释器")
            return

        project_root = _project_root()
        workdir = getattr(self._settings, "workdir", None) or project_root

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            if _is_windows():
                self._spawn_quick_windows(python_exe, project_root, workdir, env)
            elif _is_macos():
                self._spawn_quick_macos(python_exe, project_root, workdir, env)
            else:
                self._spawn_quick_linux(python_exe, project_root, workdir, env)
            self._notify("J.A.R.V.I.S", "已打开文本对话窗口")
        except Exception as e:
            self._log("弹出文本终端失败: %s: %s", type(e).__name__, e)
            self._notify("J.A.R.V.I.S", f"打开终端失败: {e}")

    def _spawn_quick_windows(
        self, python_exe: str, project_root: str, workdir: str, env: dict
    ) -> None:
        """Windows: 优先 Git Bash，回退 cmd，使用 --quick 参数。"""
        mintty = _find_mintty()
        if mintty:
            self._log("[spawn] 使用 Git Bash mintty: %s", mintty)
        else:
            self._log("[spawn] mintty 未找到，回退到 cmd")

        if mintty:
            unix_python = _to_unix_path(python_exe)
            unix_workdir = _to_unix_path(workdir)
            bash_cmd = (
                f'cd "{unix_workdir}" && '
                f'"{unix_python}" -m agent.main --no-boot --quick --workdir "{unix_workdir}" ; '
                f'exec bash'
            )
            proc = subprocess.Popen(
                [mintty, "-e", "/usr/bin/bash", "--login", "-i", "-c", bash_cmd],
                env=env,
            )
            self._text_terminal_proc = proc
            self._log("已弹出 Git Bash 文本对话窗口（quick 模式）")
        else:
            cmd_line = (
                f'cmd /k "{python_exe}" -m agent.main --no-boot --quick --workdir "{workdir}"'
            )
            proc = subprocess.Popen(
                cmd_line,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                cwd=project_root,
                env=env,
            )
            self._text_terminal_proc = proc
            self._log("已弹出 CMD 文本对话终端窗口（quick 模式）")

    def _spawn_quick_macos(
        self, python_exe: str, project_root: str, workdir: str, env: dict
    ) -> None:
        """macOS: 用 osascript 调用 Terminal.app 运行 jarvis REPL（--quick）。"""
        shell_cmd = (
            f'cd "{workdir}" && '
            f'"{python_exe}" -m agent.main --no-boot --quick --workdir "{workdir}"; '
            f'exec bash'
        )
        applescript = (
            f'tell application "Terminal"\n'
            f'    activate\n'
            f'    do script "{shell_cmd}"\n'
            f'end tell'
        )
        proc = subprocess.Popen(
            ["osascript", "-e", applescript],
            env=env,
        )
        self._text_terminal_proc = proc
        self._log("已弹出 Terminal.app 文本对话窗口（quick 模式）")

    def _spawn_quick_linux(
        self, python_exe: str, project_root: str, workdir: str, env: dict
    ) -> None:
        """Linux: 尝试用系统默认终端模拟器打开（--quick）。"""
        shell_cmd = (
            f'cd "{workdir}" && '
            f'"{python_exe}" -m agent.main --no-boot --quick --workdir "{workdir}"; '
            f'exec bash'
        )
        terminals = [
            (["x-terminal-emulator", "-e", f"bash -c '{shell_cmd}'"], "x-terminal-emulator"),
            (["gnome-terminal", "--", "bash", "-c", shell_cmd], "gnome-terminal"),
            (["konsole", "-e", "bash", "-c", shell_cmd], "konsole"),
            (["xterm", "-e", f"bash -c '{shell_cmd}'"], "xterm"),
        ]
        for cmd, name in terminals:
            try:
                proc = subprocess.Popen(cmd, env=env)
                self._text_terminal_proc = proc
                self._log("已弹出 %s 文本对话窗口（quick 模式）", name)
                return
            except FileNotFoundError:
                continue
        self._log("未找到可用的终端模拟器")
        self._notify("J.A.R.V.I.S", "无可用终端模拟器")

    def _spawn_warm(self) -> None:
        """后台启动一个 warm 文本终端（隐藏窗口，等待唤起）。"""
        python_exe = _find_python()
        if not python_exe:
            return
        project_root = _project_root()
        workdir = getattr(self._settings, "workdir", None) or project_root
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["JARVIS_WARM_TERMINAL"] = "1"

        try:
            if _is_windows():
                mintty = _find_mintty()
                if mintty:
                    unix_python = _to_unix_path(python_exe)
                    unix_workdir = _to_unix_path(workdir)
                    bash_cmd = (
                        f'cd "{unix_workdir}" && '
                        f'"{unix_python}" -m agent.main --no-boot --quick --workdir "{unix_workdir}" ; '
                        f'exec bash'
                    )
                    # mintty 的 --window 参数可隐藏窗口，但不同版本支持不同；
                    # 保守做法：正常启动，依赖 bring_up 时置顶。
                    proc = subprocess.Popen(
                        [mintty, "-e", "/usr/bin/bash", "--login", "-i", "-c", bash_cmd],
                        env=env,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                else:
                    proc = subprocess.Popen(
                        f'cmd /c start /min "" "{python_exe}" -m agent.main --no-boot --quick --workdir "{workdir}"',
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        cwd=project_root,
                        env=env,
                    )
            elif _is_macos():
                shell_cmd = (
                    f'cd "{workdir}" && '
                    f'"{python_exe}" -m agent.main --no-boot --quick --workdir "{workdir}" ; '
                    f'exec bash'
                )
                applescript = (
                    f'tell application "Terminal"\n'
                    f'    do script "{shell_cmd}"\n'
                    f'end tell'
                )
                proc = subprocess.Popen(["osascript", "-e", applescript], env=env)
            else:
                shell_cmd = (
                    f'cd "{workdir}" && '
                    f'"{python_exe}" -m agent.main --no-boot --quick --workdir "{workdir}"; '
                    f'exec bash'
                )
                proc = subprocess.Popen(
                    ["xterm", "-iconic", "-e", f"bash -c '{shell_cmd}'"],
                    env=env,
                )
            with self._lock:
                self._warm_proc = proc
            self._log("warm 文本终端已预启动")
        except Exception as e:
            self._log("预启动 warm 终端失败: %s", e)
