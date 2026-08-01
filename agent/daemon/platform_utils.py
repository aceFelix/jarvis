"""平台 / 进程工具 —— daemon 拆分的纯函数层。

集中管理跨平台判断、解释器定位、终端模拟器搜索、detached 子进程启动
与可选依赖检测。不持有任何状态，供 daemon 各模块复用。

从原 daemon.py 拆分而来（A-01 思路延伸），terminal_spawner.py 亦复用本模块
避免重复实现。

@author aceFelix
"""

from __future__ import annotations

import os
import sys


# ---------------------------------------------------------------------------
# 平台判断
# ---------------------------------------------------------------------------

def _is_windows() -> bool:
    """是否运行在 Windows 平台。"""
    return sys.platform == "win32"


def _is_macos() -> bool:
    """是否运行在 macOS 平台。"""
    return sys.platform == "darwin"


# ---------------------------------------------------------------------------
# 解释器与路径定位
# ---------------------------------------------------------------------------

def _find_pythonw() -> str | None:
    """找到与当前解释器配对的 pythonw.exe（无控制台窗口的 Python）。

    返回 None 表示不可用（非 Windows 或找不到 pythonw.exe）。
    """
    if not _is_windows():
        return None
    exe = sys.executable
    dirname = os.path.dirname(exe)
    basename = os.path.basename(exe).lower()
    if basename == "pythonw.exe":
        return exe
    if basename == "python.exe":
        pythonw = os.path.join(dirname, "pythonw.exe")
        if os.path.exists(pythonw):
            return pythonw
    return None


def _find_python() -> str | None:
    """找到用于弹出文本终端的 Python 解释器路径。

    Windows: 找与当前解释器配对的 python.exe（有控制台窗口）。
             detached daemon 运行在 pythonw.exe 下，spawn 文本终端需要 python.exe。
    macOS: 直接返回 sys.executable（macOS 无 pythonw 区分）。
    Linux: 返回 sys.executable。
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
    # macOS / Linux: 直接用当前解释器
    return sys.executable


def _project_root() -> str:
    """获取项目根目录（agent/ 的父目录）。

    本模块位于 <project_root>/agent/daemon/platform_utils.py，
    往上三级即为项目根目录。
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _find_mintty() -> str | None:
    """找到 Git Bash 的终端模拟器 mintty.exe。

    多策略搜索（优先级从高到低）：
    1. ``shutil.which("mintty")`` — 直接搜 PATH（PortableGit 可能注册了）
    2. 从 ``shutil.which("git")`` 反查 Git 安装根目录
       - 标准 Git for Windows: ``<GitRoot>/bin/git.exe`` →
         mintty 在 ``<GitRoot>/usr/bin/mintty.exe``
       - PortableGit: ``<PortableGit>/mingw64/bin/git.exe`` →
         mintty 在 ``<PortableGit>/usr/bin/mintty.exe``（往上两级）
    3. 硬编码常见路径: ``C:\\Program Files\\Git\\usr\\bin\\mintty.exe``

    返回 mintty.exe 的绝对路径，或 None 表示找不到。
    """
    import shutil

    # 策略 1: 直接搜 mintty（某些安装把它加进 PATH）
    mintty = shutil.which("mintty")
    if mintty and os.path.isfile(mintty):
        return mintty

    # 策略 2: 通过 git 反查
    git = shutil.which("git")
    if git:
        git_bin_dir = os.path.dirname(git)
        git_parent = os.path.dirname(git_bin_dir)      # bin/ 或 mingw64/ 的父目录
        git_grandparent = os.path.dirname(git_parent)   # 再往上一级（PortableGit 需要）

        # 收集所有可能路径，去重
        candidate_dirs = {git_parent, git_grandparent}
        if os.path.basename(git_bin_dir).lower() == "cmd":
            # <GitRoot>/cmd/git.exe → 根是 git_parent 的父目录
            candidate_dirs.add(os.path.dirname(git_grandparent))

        for root in candidate_dirs:
            for candidate in (
                os.path.join(root, "usr", "bin", "mintty.exe"),
                os.path.join(root, "git-bash.exe"),
            ):
                if os.path.isfile(candidate):
                    return candidate

    # 策略 3: 硬编码常见路径
    for path in (
        r"C:\Program Files\Git\usr\bin\mintty.exe",
        r"D:\SoftwareDevelopmentKit\Git\usr\bin\mintty.exe",
    ):
        if os.path.isfile(path):
            return path

    return None


def _to_unix_path(win_path: str) -> str:
    """Windows 路径 → Git Bash 用的 Unix 路径。

    ``C:\\Users\\xxx`` → ``/c/Users/xxx``
    ``E:\\Projects``   → ``/e/Projects``
    """
    drive, rest = os.path.splitdrive(win_path)
    if drive:
        return "/" + drive[0].lower() + rest.replace("\\", "/")
    return win_path.replace("\\", "/")


# ---------------------------------------------------------------------------
# detached 模式
# ---------------------------------------------------------------------------

def _is_detached() -> bool:
    """检测当前进程是否已经在无控制台/无终端模式下运行。

    Windows: GetConsoleWindow() 返回 0 表示没有控制台窗口。
    macOS: stdout 被重定向到文件（非 TTY）表示已 detached。
    Linux: 不支持 detached 模式，始终返回 False。
    """
    if _is_windows():
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            return hwnd == 0
        except Exception:
            return False
    if _is_macos():
        # detached 进程 stdout 被重定向到日志文件，非 TTY
        try:
            return not sys.stdout.isatty()
        except Exception:
            return False
    return False


def _daemon_log_file() -> str:
    """无窗口 daemon 的日志文件路径。"""
    home = os.path.expanduser("~")
    return os.path.join(home, ".jarvis", "daemon.log")


def launch_detached_daemon(script: str, workdir: str | None = None) -> int:
    """以 detached 子进程方式启动 daemon（跨平台）。

    Windows: 用 pythonw.exe 启动无窗口子进程，DETACHED_PROCESS 分离。
    macOS: 用 ``start_new_session=True`` 创建新进程组，脱离终端，
           关闭 Terminal.app 不会杀掉子进程。stdout/stderr 重定向到日志文件。
    Linux: 不支持后台分离，返回 1 让调用方回退到前台运行。

    主进程调用此函数后应立刻退出，子进程独立存活。

    Returns:
        0  — 子进程已成功启动，主进程应退出
        非 0 — 启动失败或不支持，调用方应回退到前台直接运行 daemon
    """
    if _is_windows():
        return _launch_detached_windows(script, workdir)
    if _is_macos():
        return _launch_detached_macos(script, workdir)
    # Linux 不支持后台分离，回退到前台
    return 1


def _launch_detached_windows(script: str, workdir: str | None) -> int:
    """Windows: 用 pythonw.exe 启动无窗口 detached 子进程。"""
    import subprocess
    import time

    pythonw = _find_pythonw()
    if not pythonw:
        return 1

    args = [pythonw, script, "--daemon", "--detached"]
    if workdir:
        args.extend(["--workdir", workdir])

    log_file = _daemon_log_file()
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
    except Exception:
        pass

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0x00000008
    )

    # 关键: 强制 UTF-8 模式。detached 子进程 stdout/stderr 重定向到文件，
    # Windows 默认用系统编码(GBK)写文件。Rich Console 检测到非 TTY 走
    # legacy_windows_render，内部用 GBK 编码，emoji(🏠/✓/⚠️等)编码失败崩溃。
    # PYTHONUTF8=1 让 Python 全局 UTF-8 模式，stdout 默认 UTF-8，Rich 不崩。
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        log_handle = open(log_file, "a", encoding="utf-8")
        log_handle.write(f"\n{'=' * 56}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] daemon 启动\n{'=' * 56}\n")
        log_handle.flush()
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            creationflags=creationflags,
            close_fds=True,
            env=env,
        )
        try:
            log_handle.close()
        except Exception:
            pass
        return 0
    except Exception as e:
        print(f"launch_detached_daemon 失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


def _launch_detached_macos(script: str, workdir: str | None) -> int:
    """macOS: 用 start_new_session=True 启动脱离终端的后台子进程。

    start_new_session=True 等价于 setsid()，创建新会话和新进程组，
    子进程不再受终端控制（关闭 Terminal.app 不会发送 SIGHUP）。
    stdout/stderr 重定向到 ``~/.jarvis/daemon.log``。
    """
    import subprocess
    import time

    args = [sys.executable, script, "--daemon", "--detached"]
    if workdir:
        args.extend(["--workdir", workdir])

    log_file = _daemon_log_file()
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
    except Exception:
        pass

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        log_handle = open(log_file, "a", encoding="utf-8")
        log_handle.write(f"\n{'=' * 56}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] daemon 启动 (macOS)\n{'=' * 56}\n")
        log_handle.flush()
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,  # 关键：新会话，脱离终端控制
            close_fds=True,
            env=env,
        )
        try:
            log_handle.close()
        except Exception:
            pass
        return 0
    except Exception as e:
        print(f"launch_detached_daemon 失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


# ---------------------------------------------------------------------------
# 可选依赖检测
# ---------------------------------------------------------------------------

def _has_keyboard() -> bool:
    try:
        import keyboard  # noqa: F401
        return True
    except ImportError:
        return False


def _has_pynput() -> bool:
    try:
        import pynput  # noqa: F401
        return True
    except ImportError:
        return False


def _has_pystray() -> bool:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False
