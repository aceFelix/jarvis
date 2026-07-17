"""贾维斯常驻守护进程。

后台常驻 + 全局热键唤起 + 系统托盘菜单。
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from typing import Any, Callable

from agent.config.settings import Settings
from agent.core.context import ToolContext
from agent.core.message import Message
from agent.core.query_loop import QueryLoop
from agent.core.tool import ToolRegistry, build_default_registry, register_dynamic_tools, register_subagent_tool
from agent.core.orchestrator import ToolOrchestrator
from agent.permissions import PermissionChecker
from agent.permissions.modes import parse_mode
from agent.prompts.system import build_system_prompt
from agent.ui.cli import RichCLI


# ---------------------------------------------------------------------------
# 无窗口启动工具（Windows 专用）
# ---------------------------------------------------------------------------

def _is_windows() -> bool:
    """是否运行在 Windows 平台。"""
    return sys.platform == "win32"


def _is_macos() -> bool:
    """是否运行在 macOS 平台。"""
    return sys.platform == "darwin"


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

    daemon.py 位于 <project_root>/agent/daemon/daemon.py，
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
    pythonw = _find_pythonw()
    if not pythonw:
        return 1

    import subprocess

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


def _has_keyboard() -> bool:
    try:
        import keyboard  # noqa: F401
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


class HotkeyListener:
    """全局热键监听器（基于 keyboard 库）。

    注册一个热键组合，按下时触发回调。在独立线程运行。
    keyboard 库在 Windows 上需要管理员权限才能注册部分全局热键，
    权限不足时 add_hotkey 会静默失败（不抛异常但不生效）。
    """

    def __init__(self, hotkey: str, on_trigger: Callable[[], None]) -> None:
        self._hotkey = hotkey
        self._on_trigger = on_trigger
        self._started = False

    @property
    def available(self) -> bool:
        return _has_keyboard()

    def start(self) -> bool:
        """启动热键监听。成功返回 True。"""
        if not self.available or self._started:
            return self._started
        try:
            import keyboard
            keyboard.add_hotkey(self._hotkey, self._on_trigger, suppress=False)
            self._started = True
            return True
        except Exception:
            return False

    def stop(self) -> None:
        if not self._started:
            return
        try:
            import keyboard
            keyboard.remove_all_hotkeys()
        except Exception:
            pass
        self._started = False


class TrayIcon:
    """系统托盘图标（基于 pystray + PIL）。

    在独立线程运行，提供右键菜单: 语音对话 / 文本对话 / 退出。
    菜单项点击通过回调通知主进程。
    """

    def __init__(
        self,
        on_voice: Callable[[], None],
        on_text: Callable[[], None],
        on_quit: Callable[[], None],
        voice_enabled_getter: Callable[[], bool],
        voice_toggle: Callable[[], None],
        realtime_enabled_getter: Callable[[], bool],
        realtime_toggle: Callable[[], None],
    ) -> None:
        self._on_voice = on_voice
        self._on_text = on_text
        self._on_quit = on_quit
        self._voice_enabled_getter = voice_enabled_getter
        self._voice_toggle = voice_toggle
        self._realtime_enabled_getter = realtime_enabled_getter
        self._realtime_toggle = realtime_toggle
        self._icon: Any = None
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return _has_pystray()

    def _make_image(self):
        """生成一个简单的蓝色圆形托盘图标。"""
        from PIL import Image, ImageDraw
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # 外环
        draw.ellipse([4, 4, size - 4, size - 4], outline=(43, 143, 224), width=3)
        # 内环
        draw.ellipse([16, 16, size - 16, size - 16], outline=(91, 200, 255), width=2)
        # 核心
        draw.ellipse([24, 24, size - 24, size - 24], fill=(191, 232, 255))
        return img

    def start(self, *, log_func: Callable[..., None] | None = None) -> bool:
        """启动托盘图标。成功返回 True。

        Args:
            log_func: 可选的日志回调，用于把启动异常写入 daemon.log。
        """
        if not self.available:
            return False
        try:
            import pystray
            from PIL import Image

            image = self._make_image()
            menu = pystray.Menu(
                pystray.MenuItem(
                    lambda item: "语音对话" if self._voice_enabled_getter() else "语音对话：已关闭",
                    self._handle_voice,
                    checked=lambda item: self._voice_enabled_getter(),
                    default=True,
                ),
                pystray.MenuItem(
                    lambda item: "实时聊天" if self._realtime_enabled_getter() else "实时聊天：已关闭",
                    self._handle_realtime_talk,
                    checked=lambda item: self._realtime_enabled_getter(),
                ),
                pystray.MenuItem("文本对话", self._handle_text),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出贾维斯", self._handle_quit),
            )
            self._icon = pystray.Icon("jarvis", image, "J.A.R.V.I.S", menu)
            self._thread = threading.Thread(target=self._icon.run, daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            if log_func:
                log_func("托盘图标启动失败: %s: %s", type(e).__name__, e)
            return False

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None

    def notify(self, title: str, message: str) -> None:
        """弹出系统通知（如果托盘已启动）。"""
        if self._icon is not None:
            try:
                self._icon.notify(message, title)
            except Exception:
                pass

    def update_menu(self) -> None:
        """刷新托盘菜单显示（动态文本/勾选状态）。"""
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception:
                pass

    def _handle_voice(self, item=None) -> None:
        """处理托盘「语音对话」项点击。

        点击勾选菜单项时切换开关状态:
        - 开启 → 清除 pause_event，voice_loop 自动恢复监听
        - 关闭 → 设置 pause_event，voice_loop 停止监听
        不需要显式调用 _trigger_voice，pause_event 是 voice_loop 的
        直接控制信号。
        """
        try:
            self._voice_toggle()
            self.update_menu()
            if self._voice_enabled_getter():
                self.notify("J.A.R.V.I.S", "语音对话已开启")
            else:
                self.notify("J.A.R.V.I.S", "语音对话已关闭")
        except Exception as e:
            try:
                self.notify("J.A.R.V.I.S", f"语音对话操作失败: {e}")
            except Exception:
                pass

    def _handle_realtime_talk(self, item=None) -> None:
        """处理托盘「实时聊天」项点击。

        切换开关状态并通知 daemon 启动/停止实时双工语音对话子进程。
        开启时持久化配置并启动独立进程；关闭时终止进程并更新配置。

        @author aceFelix
        """
        try:
            self._realtime_toggle()
            self.update_menu()
            if self._realtime_enabled_getter():
                self.notify("J.A.R.V.I.S", "实时聊天已开启")
            else:
                self.notify("J.A.R.V.I.S", "实时聊天已关闭")
        except Exception as e:
            try:
                self.notify("J.A.R.V.I.S", f"实时聊天操作失败: {e}")
            except Exception:
                pass

    def _handle_text(self, *_args) -> None:
        try:
            self._on_text()
        except Exception as e:
            # 不再静默吞异常: 出错时托盘通知用户
            try:
                self.notify("J.A.R.V.I.S", f"文本对话失败: {e}")
            except Exception:
                pass

    def _handle_quit(self, *_args) -> None:
        try:
            self._on_quit()
        except Exception:
            pass


class JarvisDaemon:
    """贾维斯常驻守护进程。

    管理:
    - 后台待命状态
    - 热键监听 + 托盘图标
    - 唤起交互（语音/文本）→ 退下回后台

    用法::

        daemon = JarvisDaemon(settings)
        daemon.run()  # 阻塞，直到用户退出
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ui: RichCLI | None = None
        self._loop: QueryLoop | None = None
        self._registry: ToolRegistry | None = None
        self._orchestrator: ToolOrchestrator | None = None
        self._provider = None
        self._mcp_client = None
        self._messages: list[Message] = []
        self._ctx: ToolContext | None = None

        # 唤起信号：热键/托盘触发后置位，daemon 循环检测
        self._wake_event = threading.Event()
        self._wake_mode: str = "voice"  # "voice" 或 "text"
        self._quit_event = threading.Event()
        self._text_terminal_proc: Any = None  # 跟踪已弹出的文本终端子进程
        self._realtime_talk_proc: Any = None  # 跟踪实时聊天子进程
        # 语音开关状态: 用文件作为跨进程 SSOT，self._voice_enabled 仅作内存缓存
        from agent.daemon.voice_state import is_voice_enabled
        self._voice_enabled = is_voice_enabled()
        # 实时聊天开关状态：从配置读取，托盘切换后持久化到 settings.toml
        self._realtime_talk_enabled = bool(getattr(settings, "realtime_talk_auto_start", False))

        self._hotkey = HotkeyListener(
            settings.daemon_hotkey, self._trigger_voice
        )
        self._tray = TrayIcon(
            on_voice=self._trigger_voice,
            on_text=self._trigger_text,
            on_quit=self._trigger_quit,
            voice_enabled_getter=lambda: self._read_voice_enabled(),
            voice_toggle=self._toggle_voice_enabled,
            realtime_enabled_getter=lambda: self._read_realtime_talk_enabled(),
            realtime_toggle=self._toggle_realtime_talk,
        )

    def run(self) -> int:
        """启动 daemon 主循环。阻塞直到用户退出。"""
        # 初始化组件（复用 main.py 的装配逻辑）
        self._setup()

        ui = self._ui
        assert ui is not None

        ui.info("=" * 56)
        ui.info("🏠 贾维斯常驻模式已启动")
        hotkey_status = f"热键 {self._settings.daemon_hotkey}" if self._hotkey.available else "热键不可用（需 pip install keyboard）"
        tray_status = "托盘图标已就绪" if self._tray.available else "托盘不可用（需 pip install pystray）"
        ui.info(f"   {hotkey_status}")
        ui.info(f"   {tray_status}")
        if self._voice_enabled:
            ui.info("   默认进入语音对话 · 说「退下」待机 · 说「贾维斯」唤醒")
            ui.info("   托盘右键「语音对话」可关闭语音模式")
        else:
            ui.info("   语音对话已关闭 · 仅通过托盘/热键唤起交互")

        if self._realtime_talk_enabled:
            ui.info("   实时聊天默认开启 · 启动后会自动打开实时语音对话窗口")
            ui.info("   托盘右键「实时聊天」可关闭自动启动")
        else:
            ui.info("   实时聊天默认关闭 · 可在托盘菜单手动开启")

        ui.info("   Ctrl+C 退出")
        ui.info("=" * 56)

        # 启动热键和托盘
        if self._hotkey.available:
            if self._hotkey.start():
                ui.info(f"✓ 全局热键已注册: {self._settings.daemon_hotkey}")
            else:
                ui.warn("热键注册失败（可能需要管理员权限）")

        if self._tray.available:
            if self._tray.start(log_func=self._daemon_log):
                ui.info("✓ 系统托盘图标已启动")
            else:
                ui.warn("托盘图标启动失败")
                if sys.platform == "win32":
                    ui.warn("  请安装 Windows 托盘依赖: pip install pywin32")
                self._daemon_log("托盘启动失败，请检查依赖")

        if not self._hotkey.available and not self._tray.available:
            ui.warn("⚠ 热键和托盘均不可用，仅能通过语音唤醒词唤起")

        # 主动感知：启动定时任务调度器 + 系统监控器 + 节假日检查（阶段五第三刀）
        self._scheduler.start()
        if self._scheduler.list_pending():
            ui.info(f"⏰ 已加载 {len(self._scheduler.list_pending())} 个待触发提醒")

        if self._monitor.start():
            ui.info("📡 系统资源监控已启动（CPU/内存/磁盘）")
        elif self._settings.monitor_enabled:
            ui.info("📡 系统监控不可用（pip install psutil 启用）")

        # 节假日提醒：检查明天是否节假日
        try:
            from agent.core.daemon.holidays import check_tomorrow_holiday
            holiday_msg = check_tomorrow_holiday()
            if holiday_msg:
                ui.info(f"📅 {holiday_msg}")
                # 托盘通知
                if self._tray and self._tray.available:
                    try:
                        self._tray.notify("节假日提醒", holiday_msg)
                    except Exception:
                        pass
        except Exception:
            pass

        # 启动后根据语音开关决定是否默认进入语音对话模式。
        # voice_loop 内置 待机⇄对话 循环：说「退下」进待机，说「贾维斯」唤醒。
        # 这里只需触发一次，voice_loop 会自己管理后续的待机/唤醒状态。
        if self._voice_enabled:
            ui.info("🎙️ 即将进入语音对话模式（说「退下」可进入待机）")
            self._wake_mode = "voice"
            self._wake_event.set()
        else:
            ui.info("🔇 语音对话已关闭，保持后台待命")

        # 启动后根据实时聊天开关决定是否默认启动实时语音对话子进程。
        # 实时对话在独立进程中运行，不阻塞 daemon 主循环，用户按 ESC 退出。
        if self._realtime_talk_enabled:
            ui.info("🎙️ 实时聊天默认开启，正在启动实时语音对话窗口")
            # 延迟一点启动，让托盘图标和日志先就位
            threading.Timer(1.0, self._start_realtime_talk).start()
        else:
            ui.info("🔇 实时聊天默认关闭，保持后台待命")


        # daemon 主循环
        try:
            while not self._quit_event.is_set():
                # 待命状态：等待唤醒信号（每 0.5s 检查一次，也响应 Ctrl+C）
                if self._wake_event.wait(timeout=0.5):
                    self._wake_event.clear()
                    mode = self._wake_mode
                    if mode == "voice":
                        self._run_voice_session()
                    elif mode == "text":
                        self._run_text_session()
        except KeyboardInterrupt:
            pass
        finally:
            self._cleanup()
            ui.info("贾维斯已退出常驻模式。再见，先生。")

        return 0

    def _setup(self) -> None:
        """装配所有组件（与 main.py repl() 类似，但适配 daemon）。"""
        from agent.main import _build_provider, _build_checker

        settings = self._settings
        self._ui = RichCLI(verbose=settings.verbose, boot_animation=False)
        ui = self._ui

        self._provider = _build_provider(settings)
        self._registry = build_default_registry()
        # 子代理协作工具注入（阶段五第二刀）
        register_subagent_tool(self._registry, provider=self._provider, permission_mode=settings.permission_mode)

        # 视觉监控（阶段五扩展）：mediapipe 实时手势/人脸检测
        from agent.core.daemon.vision_watcher import VisionWatcher
        self._vision_watcher = VisionWatcher(on_event=self._on_vision_event)
        from agent.tools.vision.vision_tools import register_vision_tools
        register_vision_tools(self._registry, watcher_factory=lambda: self._vision_watcher)

        # 主动感知（阶段五第三刀）：定时任务调度器 + 系统监控器
        from agent.core.daemon.scheduler import Scheduler
        from agent.tools.extensions.schedule_tool import register_schedule_tools
        self._scheduler = Scheduler(on_fire=self._on_schedule_fire)
        register_schedule_tools(self._registry, self._scheduler)

        from agent.core.daemon.monitor import SystemMonitor, MonitorConfig
        self._monitor = SystemMonitor(
            config=MonitorConfig(
                enabled=settings.monitor_enabled,
                cpu_threshold=settings.monitor_cpu_threshold,
                memory_threshold=settings.monitor_memory_threshold,
                disk_threshold=settings.monitor_disk_threshold,
                check_interval=settings.monitor_check_interval,
                alert_cooldown=settings.monitor_alert_cooldown,
            ),
            on_alert=self._on_monitor_alert,
        )

        checker = _build_checker(settings)
        self._orchestrator = ToolOrchestrator(
            registry=self._registry, permission_checker=checker
        )

        system_prompt = build_system_prompt(settings.workdir, self._registry, enable_thinking=getattr(settings, 'enable_thinking', True))
        if settings.system_prompt_append:
            system_prompt += "\n\n" + settings.system_prompt_append

        model = settings.model or self._provider.default_model
        self._loop = QueryLoop(
            provider=self._provider,
            registry=self._registry,
            orchestrator=self._orchestrator,
            system=system_prompt,
            model=model,
            max_iterations=settings.max_iterations,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            enable_compaction=settings.context_compaction,
            compaction_threshold=settings.compaction_threshold,
            keep_recent_messages=settings.keep_recent_messages,
            vendor_fallback=settings.vendor_fallback,
            custom_models=settings.custom_models,
        )

        self._ctx = ToolContext(
            workdir=settings.workdir,
            messages=self._messages,
            permission_mode=settings.permission_mode.value,
            ui=ui,
        )

    def _daemon_log(self, fmt: str, *args: object) -> None:
        """写调试日志到 daemon.log（仅关键事件，不刷屏）。"""
        try:
            log_path = _daemon_log_file()
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                ts = time.strftime("%H:%M:%S")
                msg = fmt % args if args else fmt
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    def _toggle_voice_enabled(self) -> None:
        """切换托盘语音对话开关状态（默认开启）。

        关闭时写 voice_state 文件 → voice_loop 检测后进入待机（类似说"退下"），
        不再主动对话，但仍听唤醒词"贾维斯"。
        开启时写 voice_state 文件 → voice_loop 恢复正常对话。

        使用文件而非 threading.Event 作为跨进程信号源:
        - daemon 以 DETACHED_PROCESS 子进程运行，voice_loop 阻塞主线程
        - pystray 回调运行在独立线程，threading.Event 跨线程理论上可用
          但跨进程无效，且未来如拆子进程架构更不适用
        - 文件是单一可信源（SSOT），跨平台、跨进程、跨线程均可读

        @author aceFelix
        """
        from agent.daemon.voice_state import is_voice_enabled, set_voice_enabled
        self._voice_enabled = not is_voice_enabled()
        set_voice_enabled(self._voice_enabled)
        ui = self._ui
        if ui:
            if self._voice_enabled:
                ui.info("🎙️ 语音对话已开启（随时待命）")
            else:
                ui.info('🔇 语音对话已关闭（进入待机，说"贾维斯"唤醒）')

    def _read_voice_enabled(self) -> bool:
        """读取语音开关最新状态（从文件，跨进程 SSOT）。

        @author aceFelix
        """
        from agent.daemon.voice_state import is_voice_enabled
        self._voice_enabled = is_voice_enabled()
        return self._voice_enabled

    def _read_realtime_talk_enabled(self) -> bool:
        """读取实时聊天开关最新状态（内存缓存）。

        @author aceFelix
        """
        return self._realtime_talk_enabled

    def _toggle_realtime_talk(self) -> None:
        """切换托盘「实时聊天」开关状态。

        开启时持久化配置并启动实时语音对话子进程；
        关闭时终止子进程并更新配置到 settings.toml。

        @author aceFelix
        """
        self._realtime_talk_enabled = not self._realtime_talk_enabled
        try:
            from agent.config.settings import save_realtime_talk_auto_start
            save_realtime_talk_auto_start(self._realtime_talk_enabled)
        except Exception as e:
            self._daemon_log("保存实时聊天配置失败: %s", e)

        ui = self._ui
        if self._realtime_talk_enabled:
            if ui:
                ui.info("🎙️ 实时聊天已开启")
            self._start_realtime_talk()
        else:
            if ui:
                ui.info("🔇 实时聊天已关闭")
            self._stop_realtime_talk()

    def _is_realtime_talk_running(self) -> bool:
        """检查实时聊天子进程是否仍在运行。"""
        if self._realtime_talk_proc is None:
            return False
        poll = self._realtime_talk_proc.poll()
        return poll is None

    def _start_realtime_talk(self) -> None:
        """启动实时双工语音对话子进程。

        通过 ``python -m agent.main --talk`` 启动独立进程，
        不阻塞 daemon 主循环，用户在弹出的终端中按 ESC 退出。
        Windows 弹出新控制台窗口；macOS 调用 Terminal.app；
        Linux 尝试常见终端模拟器。

        @author aceFelix
        """
        import subprocess

        if self._is_realtime_talk_running():
            if self._tray and self._tray.available:
                self._tray.notify("J.A.R.V.I.S", "实时聊天已在运行")
            return

        python_exe = _find_python()
        if not python_exe:
            ui = self._ui
            if ui:
                ui.warn("实时聊天失败: 未找到 Python 解释器")
            if self._tray and self._tray.available:
                self._tray.notify("J.A.R.V.I.S", "无法找到 Python 解释器，实时聊天不可用")
            return

        project_root = _project_root()
        workdir = self._settings.workdir or project_root

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            if _is_windows():
                self._realtime_talk_proc = subprocess.Popen(
                    [python_exe, "-m", "agent.main", "--talk", "--workdir", workdir],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    cwd=project_root,
                    env=env,
                )
            elif _is_macos():
                shell_cmd = (
                    f'cd "{workdir}" && '
                    f'"{python_exe}" -m agent.main --talk --workdir "{workdir}"; '
                    f'exec bash'
                )
                applescript = (
                    f'tell application "Terminal"\n'
                    f'    activate\n'
                    f'    do script "{shell_cmd}"\n'
                    f'end tell'
                )
                self._realtime_talk_proc = subprocess.Popen(
                    ["osascript", "-e", applescript],
                    env=env,
                )
            else:
                # Linux: 按优先级尝试常见终端模拟器
                shell_cmd = (
                    f'cd "{workdir}" && '
                    f'"{python_exe}" -m agent.main --talk --workdir "{workdir}"; '
                    f'exec bash'
                )
                terminals = [
                    (["x-terminal-emulator", "-e", f"bash -c '{shell_cmd}'"], "x-terminal-emulator"),
                    (["gnome-terminal", "--", "bash", "-c", shell_cmd], "gnome-terminal"),
                    (["konsole", "-e", "bash", "-c", shell_cmd], "konsole"),
                    (["xterm", "-e", f"bash -c '{shell_cmd}'"], "xterm"),
                ]
                started = False
                for cmd, _name in terminals:
                    try:
                        self._realtime_talk_proc = subprocess.Popen(cmd, env=env)
                        started = True
                        break
                    except FileNotFoundError:
                        continue
                if not started:
                    raise RuntimeError("未找到可用的终端模拟器（尝试安装 xterm 或 gnome-terminal）")

            ui = self._ui
            if ui:
                ui.info("🎙️ 已启动实时聊天窗口")
            if self._tray and self._tray.available:
                self._tray.notify("J.A.R.V.I.S", "实时聊天已启动")
        except Exception as e:
            ui = self._ui
            if ui:
                ui.error(f"启动实时聊天失败: {type(e).__name__}: {e}")
            if self._tray and self._tray.available:
                self._tray.notify("J.A.R.V.I.S", f"启动实时聊天失败: {e}")

    def _stop_realtime_talk(self) -> None:
        """停止实时聊天子进程。

        先发送 SIGTERM，超时未退出则强制 kill。

        @author aceFelix
        """
        if self._realtime_talk_proc is None:
            return
        try:
            poll = self._realtime_talk_proc.poll()
            if poll is None:
                self._realtime_talk_proc.terminate()
                try:
                    self._realtime_talk_proc.wait(timeout=3)
                except Exception:
                    self._realtime_talk_proc.kill()
        except Exception as e:
            self._daemon_log("停止实时聊天进程失败: %s", e)
        finally:
            self._realtime_talk_proc = None

    def _trigger_voice(self) -> None:
        """热键/托盘触发语音对话。

        如果语音模式被关闭（文件状态为 false），提示用户并忽略触发。
        如果语音已在运行中，仅托盘通知"已在语音模式"。
        """
        if not self._read_voice_enabled():
            if self._tray and self._tray.available:
                self._tray.notify("J.A.R.V.I.S", "语音对话已关闭，右键托盘菜单开启")
            return
        # 语音已在运行中（daemon 启动即进入语音模式）
        if self._tray and self._tray.available:
            self._tray.notify("J.A.R.V.I.S", "已在语音对话模式中")

    def _on_schedule_fire(self, task) -> None:
        """定时任务到期回调：托盘通知 + 语音播报。

        在 Scheduler 后台线程触发。语音播报通过 TTS 异步播放，
        不阻塞调度器。如果贾维斯正在对话中，仅托盘通知不打断。
        """
        ui = self._ui
        if ui:
            ui.info(f"⏰ 定时提醒: {task.content}")

        # 托盘通知
        try:
            if self._tray and self._tray.available:
                self._tray.notify("贾维斯提醒", task.content)
        except Exception:
            pass

        # 语音播报（仅当不在对话中时，避免打断正在进行的语音对话）
        try:
            if not self._wake_event.is_set():
                # 待机中，用 TTS 播报提醒
                import threading as _t
                def _speak():
                    try:
                        from agent.voice.tts import CosyVoiceTTS
                        tts = CosyVoiceTTS(
                            api_key=self._settings.api_key,
                            model=self._settings.tts_model,
                            voice=self._settings.tts_voice,
                        )
                        tts.speak(f"先生，提醒您：{task.content}")
                    except Exception:
                        pass
                _t.Thread(target=_speak, daemon=True).start()
        except Exception:
            pass

    def _on_monitor_alert(self, alert) -> None:
        """系统监控告警回调：托盘通知 + 语音告警。"""
        ui = self._ui
        if ui:
            level_icon = "⚠️" if alert.level == "warning" else "🚨"
            ui.info(f"{level_icon} 系统告警 [{alert.alert_type}]: {alert.message}")

        # 托盘通知
        try:
            if self._tray and self._tray.available:
                title = "系统告警" if alert.level != "recovery" else "状态恢复"
                self._tray.notify(title, alert.message)
        except Exception:
            pass

        # 语音告警（仅严重告警才语音，recovery 不打扰）
        if alert.level == "critical":
            try:
                if not self._wake_event.is_set():
                    import threading as _t
                    def _speak():
                        try:
                            from agent.voice.tts import CosyVoiceTTS
                            tts = CosyVoiceTTS(
                                api_key=self._settings.api_key,
                                model=self._settings.tts_model,
                                voice=self._settings.tts_voice,
                            )
                            tts.speak(f"先生，{alert.message}")
                        except Exception:
                            pass
                    _t.Thread(target=_speak, daemon=True).start()
            except Exception:
                pass

    def _on_vision_event(self, event) -> None:
        """视觉监控事件回调：托盘通知 + 语音播报。

        在 VisionWatcher 后台线程触发。手势/人脸事件 → 托盘通知 + TTS 播报。
        AUTO_STOPPED 事件 → 通知用户监控已自动关闭。
        仅待机中才语音播报，不打断正在进行的对话。
        """
        ui = self._ui
        if ui:
            icon = {
                "gesture": "👆",
                "face_appear": "👤",
                "face_disappear": "👋",
                "auto_stopped": "⏹️",
            }.get(event.event_type.value, "👁️")
            ui.info(f"{icon} 视觉事件: {event.description}")

        # 托盘通知
        try:
            if self._tray and self._tray.available:
                self._tray.notify("贾维斯视觉", event.description)
        except Exception:
            pass

        # 语音播报（仅待机中，不打断对话）
        try:
            if not self._wake_event.is_set():
                import threading as _t
                def _speak():
                    try:
                        from agent.voice.tts import CosyVoiceTTS
                        tts = CosyVoiceTTS(
                            api_key=self._settings.api_key,
                            model=self._settings.tts_model,
                            voice=self._settings.tts_voice,
                        )
                        tts.speak(f"先生，{event.description}")
                    except Exception:
                        pass
                _t.Thread(target=_speak, daemon=True).start()
        except Exception:
            pass

    def _trigger_text(self) -> None:
        """托盘触发文本对话。

        daemon 启动后主循环被 _run_voice_session() 内的 voice_loop 永久阻塞
        （stt.listen() 占住主线程），_wake_event 机制对文本对话无效。
        因此必须绕过事件循环，直接暂停语音 + spawn 终端（与 _trigger_quit
        绕过 _quit_event 直接 os._exit 同理）。
        """
        self._daemon_log("[trigger_text] 触发, is_detached=%s", _is_detached())
        if _is_detached():
            # 暂停语音 → 弹出终端窗口
            self._daemon_log("[trigger_text] 暂停语音，弹出文本终端")
            self._spawn_text_terminal()
        else:
            # 前台模式（--with-tray）：走事件循环
            self._wake_mode = "text"
            self._wake_event.set()

    def _trigger_quit(self) -> None:
        """托盘退出：强制终止整个进程。

        daemon 启动后默认进入 voice_loop，其内部 stt.listen() 会阻塞主线程
        （最长 stt_max_seconds 或 standby 6s）。设置 _quit_event 无法及时
        中断阻塞中的 voice_loop。直接 os._exit(0) 保证托盘「退出贾维斯」
        立即生效。资源清理由 OS 回收（与前台 --with-tray 模式行为一致）。
        """
        os._exit(0)

    def _run_voice_session(self) -> None:
        """唤起一次语音对话会话。

        voice_loop 内部通过 voice_state 文件检测开关状态:
        - 文件为 true → 正常对话
        - 文件为 false → 进入待机（只听唤醒词"贾维斯"）

        @author aceFelix
        """
        ui = self._ui
        assert ui is not None and self._loop is not None and self._ctx is not None

        try:
            from agent.voice.voice_loop import voice_loop
            asyncio.run(voice_loop(
                ui, self._settings, self._loop, self._ctx,
                daemon_mode=True,
            ))
        except ImportError as e:
            ui.error(f"语音模块不可用: {e}")
        except Exception as e:
            ui.error(f"语音会话异常: {type(e).__name__}: {e}")
        finally:
            # 语音会话结束后增量保存
            try:
                from agent.main import _auto_save
                _auto_save(ui, self._messages,
                           workdir=self._settings.workdir,
                           model=self._settings.model,
                           provider=self._settings.provider,
                           verbose=False)
            except Exception:
                pass
            ui.info("💤 回到待命状态")

    def _run_text_session(self) -> None:
        """唤起一次文本对话（单轮，exit 回后台）。"""
        ui = self._ui
        assert ui is not None

        # detached（无窗口）模式下 stdin 不可用，弹出一个新的终端窗口
        # 运行 jarvis REPL（自动恢复上次会话，独立进程，关闭不影响 daemon）
        if _is_detached():
            self._spawn_text_terminal()
            return

        assert self._loop is not None and self._ctx is not None
        ui.info("📝 文本对话模式（输入 /back 回后台，/exit 退出贾维斯）")
        while not self._quit_event.is_set():
            try:
                user_input = ui.read_user_input("> ")
            except (EOFError, KeyboardInterrupt):
                break
            if not user_input:
                continue
            stripped = user_input.strip()
            if stripped.lower() in ("/back", "/sleep", "/standby"):
                break
            if stripped.lower() in ("/exit", "/quit"):
                self._quit_event.set()
                break
            # 普通对话
            try:
                asyncio.run(self._loop.run(stripped, self._ctx))
            except KeyboardInterrupt:
                self._ctx.abort_event.set()
                self._ctx = ToolContext(
                    workdir=self._settings.workdir,
                    messages=self._messages,
                    permission_mode=self._settings.permission_mode.value,
                    ui=ui,
                )
            except Exception as e:
                ui.error(f"运行出错: {type(e).__name__}: {e}")
            # 每轮对话后增量保存（防窗口被强杀丢失记忆）
            try:
                from agent.main import _auto_save
                _auto_save(ui, self._messages,
                           workdir=self._settings.workdir,
                           model=self._loop._model or self._settings.model,
                           provider=self._settings.provider,
                           verbose=False)
            except Exception:
                pass
        ui.info("💤 回到待命状态")

    def _spawn_text_terminal(self) -> None:
        """detached 模式下弹出一个新的终端窗口运行 jarvis REPL。

        平台支持:
        - Windows: 优先 Git Bash (mintty)，回退到 cmd。
        - macOS: 用 osascript 调用 Terminal.app 运行 jarvis REPL。
        - Linux: 尝试 x-terminal-emulator / gnome-terminal / xterm。

        新终端运行 ``python -m agent.main --no-boot``（前台 REPL），
        自动恢复上次会话。独立进程，关闭终端不影响 daemon 后台运行。
        """
        import subprocess

        # 检查是否已有文本终端在运行
        if self._text_terminal_proc is not None:
            poll = self._text_terminal_proc.poll()
            if poll is None:
                if self._tray and self._tray.available:
                    self._tray.notify("J.A.R.V.I.S", "文本对话窗口已打开，请切换到该窗口")
                return
            self._text_terminal_proc = None

        python_exe = _find_python()
        if not python_exe:
            if self._tray and self._tray.available:
                self._tray.notify("J.A.R.V.I.S", "无法找到 Python 解释器，文本对话不可用")
            ui = self._ui
            if ui:
                ui.warn("文本对话失败: 未找到 Python 解释器")
            return

        project_root = _project_root()
        workdir = self._settings.workdir or project_root

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            if _is_windows():
                self._spawn_text_terminal_windows(
                    python_exe, project_root, workdir, env
                )
            elif _is_macos():
                self._spawn_text_terminal_macos(
                    python_exe, project_root, workdir, env
                )
            else:
                self._spawn_text_terminal_linux(
                    python_exe, project_root, workdir, env
                )

            if self._tray and self._tray.available:
                self._tray.notify("J.A.R.V.I.S", "已打开文本对话窗口")
        except Exception as e:
            ui = self._ui
            if ui:
                ui.error(f"弹出文本终端失败: {type(e).__name__}: {e}")
            if self._tray and self._tray.available:
                self._tray.notify("J.A.R.V.I.S", f"打开终端失败: {e}")

    def _spawn_text_terminal_windows(
        self, python_exe: str, project_root: str, workdir: str, env: dict
    ) -> None:
        """Windows: 优先 Git Bash (mintty)，回退到 cmd。"""
        import subprocess

        mintty = _find_mintty()
        if mintty:
            self._daemon_log("[spawn] 使用 Git Bash mintty: %s", mintty)
        else:
            self._daemon_log("[spawn] mintty 未找到，回退到 cmd")

        if mintty:
            unix_python = _to_unix_path(python_exe)
            unix_workdir = _to_unix_path(workdir)
            bash_cmd = (
                f'cd "{unix_workdir}" && '
                f'"{unix_python}" -m agent.main --no-boot --workdir "{unix_workdir}" ; '
                f'exec bash'
            )
            self._text_terminal_proc = subprocess.Popen(
                [mintty, "-e", "/usr/bin/bash", "--login", "-i", "-c", bash_cmd],
                env=env,
            )
            ui = self._ui
            if ui:
                ui.info("📝 已弹出 Git Bash 文本对话窗口")
        else:
            cmd_line = f'cmd /k "{python_exe}" -m agent.main --no-boot --workdir "{workdir}"'
            self._text_terminal_proc = subprocess.Popen(
                cmd_line,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                cwd=project_root,
                env=env,
            )
            ui = self._ui
            if ui:
                ui.info("📝 已弹出 CMD 文本对话终端窗口")

    def _spawn_text_terminal_macos(
        self, python_exe: str, project_root: str, workdir: str, env: dict
    ) -> None:
        """macOS: 用 osascript 调用 Terminal.app 运行 jarvis REPL。

        osascript 可以让 Terminal.app 打开新窗口并执行命令，
        执行完后保持窗口打开（不自动关闭）。
        """
        import subprocess

        # 构造在 Terminal.app 中执行的 shell 命令
        # cd 到项目目录 → 运行 jarvis REPL → 执行完后保持 shell
        shell_cmd = (
            f'cd "{workdir}" && '
            f'"{python_exe}" -m agent.main --no-boot --workdir "{workdir}"; '
            f'exec bash'
        )
        # osascript: 让 Terminal.app 执行命令
        # 双重转义：osascript 的 do shell script 需要引号内的引号用 \" 转义
        applescript = (
            f'tell application "Terminal"\n'
            f'    activate\n'
            f'    do script "{shell_cmd}"\n'
            f'end tell'
        )
        self._text_terminal_proc = subprocess.Popen(
            ["osascript", "-e", applescript],
            env=env,
        )
        ui = self._ui
        if ui:
            ui.info("📝 已弹出 Terminal.app 文本对话窗口")

    def _spawn_text_terminal_linux(
        self, python_exe: str, project_root: str, workdir: str, env: dict
    ) -> None:
        """Linux: 尝试用系统默认终端模拟器打开。

        按优先级尝试: x-terminal-emulator > gnome-terminal > konsole > xterm。
        找不到任何终端模拟器时打印警告。
        """
        import subprocess

        shell_cmd = (
            f'cd "{workdir}" && '
            f'"{python_exe}" -m agent.main --no-boot --workdir "{workdir}"; '
            f'exec bash'
        )

        # 按优先级尝试常见终端模拟器
        terminals = [
            (["x-terminal-emulator", "-e", f"bash -c '{shell_cmd}'"], "x-terminal-emulator"),
            (["gnome-terminal", "--", "bash", "-c", shell_cmd], "gnome-terminal"),
            (["konsole", "-e", "bash", "-c", shell_cmd], "konsole"),
            (["xterm", "-e", f"bash -c '{shell_cmd}'"], "xterm"),
        ]

        for cmd, name in terminals:
            try:
                self._text_terminal_proc = subprocess.Popen(cmd, env=env)
                ui = self._ui
                if ui:
                    ui.info(f"📝 已弹出 {name} 文本对话窗口")
                return
            except FileNotFoundError:
                continue

        # 所有终端模拟器都未找到
        ui = self._ui
        if ui:
            ui.warn("未找到可用的终端模拟器（尝试安装 xterm 或 gnome-terminal）")
        if self._tray and self._tray.available:
            self._tray.notify("J.A.R.V.I.S", "无可用终端模拟器")

    def _cleanup(self) -> None:
        """清理资源。"""
        # 退出前最终保存会话
        if self._messages:
            try:
                from agent.main import _auto_save
                _auto_save(self._ui, self._messages,
                           workdir=self._settings.workdir,
                           model=self._settings.model,
                           provider=self._settings.provider,
                           verbose=False)
            except Exception:
                pass
        # 停止实时聊天子进程，避免 daemon 退出后残留
        self._stop_realtime_talk()
        self._hotkey.stop()
        self._tray.stop()
        # 停止视觉监控（释放摄像头）
        if hasattr(self, '_vision_watcher') and self._vision_watcher is not None:
            try:
                self._vision_watcher.stop()
            except Exception:
                pass
        if self._mcp_client is not None:
            try:
                asyncio.run(self._mcp_client.disconnect_all())
            except Exception:
                pass
        if self._provider is not None:
            try:
                asyncio.run(self._provider.close())
            except Exception:
                pass
