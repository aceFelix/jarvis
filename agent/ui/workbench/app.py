"""工作台主程序：窗口创建、线程编排与入口函数。

运行模型（与 realtime_window 子进程方案不同，工作台本身就是主进程）：

- 主线程：pywebview 窗口（最大化、透明背景）
- 引擎线程：ChatEngine（QueryLoop 文本对话 + /talk 实时语音）
- 采集线程：MetricsCollector（CPU/内存/磁盘）
- 单实例守卫：焦点端口监听 + 锁文件心跳

入口：``run_workbench(settings)``，由 ``jarvis --gui`` / ``--talk`` 调用。
二次双击桌面图标 → 新进程检测到驻留实例 → 发聚焦指令后退出。

@author aceFelix
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

from agent.config.settings import Settings
from agent.ui.workbench.api import WorkbenchAPI
from agent.ui.workbench.engine import ChatEngine
from agent.ui.workbench.metrics import MetricsCollector
from agent.ui.workbench.single_instance import SingleInstanceGuard
from agent.ui.workbench.window_geometry import work_area

# 前端资源目录（本模块同级 assets/）
_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _workbench_log(message: str) -> None:
    """诊断日志写入 ~/.jarvis/workbench.log（窗口进程无终端可见输出）。"""
    try:
        log_path = Path.home() / ".jarvis" / "workbench.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


def _has_webview() -> bool:
    """检查 pywebview 是否已安装。"""
    try:
        import webview  # noqa: F401
        return True
    except ImportError:
        return False


def _window_icon_path() -> str | None:
    """窗口图标（任务栏 / Alt-Tab）：与桌面图标同图案的深蓝实底版。

    透明底 jarvis.ico 在任务栏浅色底上会被合成发白（用户实测反馈），
    故窗口专用实底版 jarvis_window.ico；不存在时经 ensure_window_icon() 生成，
    失败返回 None（窗口保持系统默认图标）。
    """
    try:
        from agent.daemon.autostart import ensure_window_icon

        ico = ensure_window_icon()
        return str(ico) if ico else None
    except Exception:
        return None


def _work_area() -> tuple[int, int, int, int]:
    """主屏工作区（不含任务栏）：无边框窗口铺满它 = 等效最大化且保留任务栏。
    实现已抽到 window_geometry（api.py 的“全屏”切换共用，口径统一）。
    """
    return work_area()


def _enable_frameless_transparency(window: Any) -> None:
    """Windows 像素级透明补丁：让桌面真正从窗口透出来。

    根因：pywebview 的 transparent=True 只把 WebView2 控件底色设透明，
    宿主 WinForms 窗口本身仍是不透明底色（白/灰）；必须给 Form 开
    AllowTransparency（WS_EX_LAYERED，仅无边框窗口可用）才能像素级透明。
    实测（pywebview 6.2 + WebView2）：不开此补丁窗口一片白，开了即透出桌面。
    """
    try:
        from System import Action  # type: ignore  # pythonnet（pywebview 依赖）
        from System.Drawing import Color  # type: ignore
        from webview.platforms.winforms import BrowserView  # type: ignore

        form = BrowserView.instances[window.uid]

        def _patch() -> None:
            try:
                form.AllowTransparency = True
                form.BackColor = Color.Transparent
                _workbench_log("像素级透明补丁已生效（AllowTransparency）")
            except Exception as e:
                _workbench_log(f"透明补丁失败（窗口保持不透明底）: {type(e).__name__}: {e}")
            # 任务栏图标：AllowTransparency 后 Form.Icon 在任务栏不可靠（实测白板），
            # 改用 Win32 WM_SETICON 直接对 hwnd 设图标（同一 UI 线程，时机最晚最稳）
            try:
                from agent.ui.workbench.win32_icon import set_window_icon, set_window_class_icon

                ico = _window_icon_path()
                # form.Handle 是 pythonnet 的 System.IntPtr，不能用 int() 直转，
                # 必须 ToInt64()（旧版兼容：无该方法时降级 int()）
                handle = form.Handle
                hwnd = handle.ToInt64() if hasattr(handle, "ToInt64") else int(handle)
                if not ico:
                    _workbench_log("任务栏图标设置失败：无 ico 路径")
                else:
                    ok = set_window_icon(hwnd, ico)
                    ok2 = set_window_class_icon(hwnd, ico)
                    _workbench_log(f"任务栏图标已设置（WM_SETICON={ok}, GCL={ok2}）: {ico}")
            except Exception as e:
                _workbench_log(f"任务栏图标设置异常: {type(e).__name__}: {e}")

        form.Invoke(Action(_patch))
    except Exception as e:
        _workbench_log(f"透明补丁不可用: {type(e).__name__}: {e}")


class _WindowState:
    """窗口句柄与置前逻辑的封装（供聚焦回调跨线程调用）。"""

    def __init__(self) -> None:
        self.window: Any = None
        self._lock = threading.Lock()

    def set(self, window: Any) -> None:
        with self._lock:
            self.window = window

    def focus(self) -> None:
        """把窗口唤起至前台（最小化则先恢复）。"""
        with self._lock:
            win = self.window
        if win is None:
            return
        try:
            if hasattr(win, "show"):
                win.show()
            if hasattr(win, "restore"):
                win.restore()
            if hasattr(win, "raise_window"):
                win.raise_window()
        except Exception:
            pass


def run_workbench(settings: Settings) -> int:
    """启动三栏工作台窗口（阻塞直到窗口关闭）。

    返回退出码：0 正常；2 已有驻留实例（已发聚焦指令）；
    3 pywebview 未安装。

    @author aceFelix
    """
    if not _has_webview():
        print("pywebview 未安装，无法打开工作台窗口（uv add pywebview）")
        return 3

    import webview

    state = _WindowState()
    guard = SingleInstanceGuard(on_focus=state.focus)
    if not guard.try_acquire():
        _workbench_log("检测到驻留实例，已发送聚焦指令，本进程退出")
        return 2

    event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    command_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    metrics = MetricsCollector(event_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)

    def _on_loaded() -> None:
        """页面加载完成：启动引擎与指标采集，推送初始状态。"""
        engine.start()
        metrics.start()
        event_queue.put_nowait({"type": "init", "payload": api.get_state()})

    is_windows = sys.platform == "win32"
    try:
        _workbench_log("创建工作台窗口（无边框 + 透明铺满工作区）" if is_windows
                       else "创建工作台窗口（非 Windows：常规窗口）")
        if is_windows:
            # 无边框 + 透明才能透出桌面（自绘标题栏提供拖动与窗口控制）；
            # 铺满工作区而非 maximized（无边框最大化会盖住任务栏）
            wx, wy, ww, wh = _work_area()
            window = webview.create_window(
                title="J.A.R.V.I.S 工作台",
                url=str(_ASSETS_DIR / "index.html"),
                x=wx,
                y=wy,
                width=ww,
                height=wh,
                resizable=True,
                frameless=True,
                transparent=True,
                easy_drag=False,  # 用 .pywebview-drag-region 精确控制拖动区（自绘标题栏）
                js_api=api,
            )
        else:
            # 非 Windows：透明支持不一，保守用常规最大化窗口 + 深色底（动画兜底）
            window = webview.create_window(
                title="J.A.R.V.I.S 工作台",
                url=str(_ASSETS_DIR / "index.html"),
                maximized=True,
                resizable=True,
                background_color="#060a12",
                js_api=api,
            )
        state.set(window)
        api.set_window(window)
        if is_windows:
            # 窗口显示后给原生 Form 打像素级透明补丁（见函数注释）
            window.events.shown += lambda: threading.Timer(0.3, _enable_frameless_transparency, args=(window,)).start()
        try:
            window.events.loaded += _on_loaded
        except Exception:
            # 旧版本无 loaded 事件：直接启动（事件可能在页面就绪前到达，前端轮询兜底）
            _on_loaded()

        # 窗口图标走官方 webview.start(icon=...)：Form 构造时即生效（任务栏/Alt-Tab），
        # 与桌面快捷方式同源；后期覆盖 form.Icon 会被 pywebview 构造逻辑盖过，不可靠。
        # 非 Windows 无图标需求时传 None 走默认。
        start_kwargs: dict[str, Any] = {"debug": False}
        if is_windows:
            ico = _window_icon_path()
            if ico:
                start_kwargs["icon"] = ico
                # Windows 10 任务栏按 AppUserModelID 显示图标（否则 fallback 到
                # pythonw.exe 白板图标），窗口启动前必须注册 AUMID + 图标映射。
                try:
                    from agent.ui.workbench.win32_icon import (
                        APP_USER_MODEL_ID,
                        register_app_icon,
                        set_current_process_app_user_model_id,
                    )

                    aumid_ok = set_current_process_app_user_model_id(APP_USER_MODEL_ID)
                    reg_ok = register_app_icon(APP_USER_MODEL_ID, ico)
                    _workbench_log(f"AppUserModelID 注册: aumid_ok={aumid_ok}, reg_ok={reg_ok}, ico={ico}")
                except Exception as e:
                    _workbench_log(f"AppUserModelID 注册异常: {type(e).__name__}: {e}")
        webview.start(**start_kwargs)
    except Exception as e:
        _workbench_log(f"窗口启动失败: {type(e).__name__}: {e}")
        raise
    finally:
        _workbench_log("窗口退出，停止引擎与采集")
        engine.stop()
        metrics.stop()
        guard.release()
    return 0
