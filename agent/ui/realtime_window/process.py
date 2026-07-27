"""实时聊天窗口子进程入口。

从 ``window.py`` 分离出来的原因是：Windows 上 multiprocessing spawn
会重新导入 ``__main__`` 模块。如果 daemon 的 ``__main__`` 是
``agent/main.py``，子进程在启动时会先执行 ``main()`` 进入 daemon 模式，
而不是运行 webview。

把子进程入口放到本模块后，multiprocessing spawn 只会导入本模块并调用
``_frontend_process_main``，不会触发 ``agent.main:main()``。

@author aceFelix
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

# 资源目录：process.py 的同级目录下的 assets/
_ASSETS_DIR = Path(__file__).resolve().parent / "assets"

# 命令类型
CMD_SHOW = "show"
CMD_HIDE = "hide"
CMD_CLOSE = "close"
CMD_EMIT = "emit"
CMD_MINIMIZE = "minimize"

# 子进程→父进程事件类型
EVT_END_SESSION = "end_session"        # 用户点击“结束”按钮
EVT_WINDOW_RESTORED = "window_restored"  # 用户从任务栏恢复窗口
EVT_WINDOW_CLOSED = "window_closed"    # 用户点击 X 关闭窗口


def _frontend_log(message: str) -> None:
    """把子进程中的诊断日志写入 ~/.jarvis/realtime_window.log。

    multiprocessing spawn 子进程的标准输出通常不可见，
    通过独立日志文件便于诊断窗口为何没有弹出。

    @author aceFelix
    """
    try:
        log_path = Path.home() / ".jarvis" / "realtime_window.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


class JSBridge:
    """暴露给前端 JS 调用的 Python API。

    JS 通过 pywebview.api.* 调用以下方法，运行在 webview 线程。
    所有方法只做只读/队列操作，避免阻塞 GUI。

    @author aceFelix
    """

    def __init__(
        self,
        event_queue: queue.Queue[dict[str, Any]],
        window: _FrontendWindow | None = None,
        response_queue: Any = None,
    ) -> None:
        self._event_queue = event_queue
        self._window = window
        self._response_queue = response_queue

    def poll_events(self) -> list[dict[str, Any]]:
        """JS 轮询获取 Python 端产生的事件。"""
        items: list[dict[str, Any]] = []
        try:
            while True:
                items.append(self._event_queue.get_nowait())
        except queue.Empty:
            pass
        return items

    def close_session(self) -> None:
        """用户点击“结束”按钮：通知父进程暂停当前会话，窗口保持打开。"""
        _frontend_log("JS: 用户点击结束按钮")
        if self._response_queue is not None:
            try:
                self._response_queue.put_nowait({"event": EVT_END_SESSION})
            except Exception as e:
                _frontend_log(f"close_session 发送事件失败: {e}")

    def resume_session(self) -> None:
        """用户点击“恢复对话”按钮：通知父进程恢复/启动新会话。"""
        _frontend_log("JS: 用户点击恢复对话按钮")
        if self._response_queue is not None:
            try:
                self._response_queue.put_nowait({"event": EVT_WINDOW_RESTORED})
            except Exception as e:
                _frontend_log(f"resume_session 发送事件失败: {e}")


class _FrontendWindow:
    """运行在子进程中的实际 webview 窗口。

    该类只在子进程内实例化，不直接暴露给父进程。

    @author aceFelix
    """

    def __init__(self, on_close: Callable[[], None] | None = None, response_queue: Any = None) -> None:
        self._event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._window: Any = None
        self._on_close = on_close
        self._response_queue = response_queue
        self._minimized = False
        self._shown_once = False

    def _send_response(self, event_type: str) -> None:
        """安全地向父进程发送事件。"""
        if self._response_queue is not None:
            try:
                self._response_queue.put_nowait({"event": event_type})
            except Exception:
                pass

    def _notify_restored(self) -> None:
        """通知父进程窗口已恢复（最小化后重新显示）。"""
        # 只有先 show 过、再 minimized 过，才认为是恢复
        if not self._shown_once or not self._minimized:
            self._minimized = False
            return
        self._minimized = False
        self._send_response(EVT_WINDOW_RESTORED)

    def emit(self, event_type: str, payload: Any) -> None:
        self._event_queue.put_nowait({"type": event_type, "payload": payload})

    def run(self) -> None:
        """在子进程主线程中启动 webview（阻塞直到窗口关闭）。"""
        import webview

        try:
            _frontend_log("webview 开始创建窗口")
            api = JSBridge(self._event_queue, window=self, response_queue=self._response_queue)
            self._window = webview.create_window(
                title="J.A.R.V.I.S Realtime",
                url=str(_ASSETS_DIR / "index.html"),
                width=900,
                height=700,
                resizable=True,
                maximized=True,
                background_color="#000000",
                js_api=api,
            )

            try:
                self._window.events.closed += self._on_closed
            except Exception:
                pass

            # 监听窗口恢复事件（从任务栏点击恢复）
            # pywebview 不同版本/平台触发的事件不同，同时监听 shown/restored/minimized
            try:
                self._window.events.shown += self._on_shown
            except Exception:
                pass
            try:
                self._window.events.restored += self._on_shown
            except Exception:
                pass
            try:
                self._window.events.minimized += self._on_minimized
            except Exception:
                pass
            try:
                self._window.events.closing += self._on_closing
            except Exception:
                pass

            _frontend_log("webview 调用 start()")
            webview.start(debug=False)
            _frontend_log("webview 已退出")
        except Exception as e:
            msg = str(e)
            if "ObjectDisposed" in msg or "WebView2" in msg:
                # WebView2 在启动/关闭瞬间可能被提前释放，记录但不抛异常
                _frontend_log(f"webview ObjectDisposedException: {e}")
            else:
                _frontend_log(f"webview 启动失败: {type(e).__name__}: {e}")
                raise

    def show(self) -> None:
        """显示窗口并置于前台。"""
        if self._window is None:
            return
        try:
            if hasattr(self._window, "show"):
                self._window.show()
            if hasattr(self._window, "restore"):
                self._window.restore()
            if hasattr(self._window, "raise_window"):
                self._window.raise_window()
            # 如果之前处于最小化状态，通知父进程窗口已恢复
            if self._minimized:
                self._notify_restored()
        except Exception:
            pass

    def hide(self) -> None:
        if self._window is not None and hasattr(self._window, "hide"):
            try:
                self._window.hide()
            except Exception:
                pass

    def minimize(self) -> None:
        """最小化窗口到任务栏。@author aceFelix"""
        if self._window is not None and hasattr(self._window, "minimize"):
            try:
                self._window.minimize()
                self._minimized = True
            except Exception:
                pass

    def destroy(self) -> None:
        if self._window is not None and hasattr(self._window, "destroy"):
            try:
                self._window.destroy()
            except Exception:
                pass

    def _on_closed(self) -> None:
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                pass

    def _on_shown(self) -> None:
        """窗口从最小化/隐藏状态恢复时触发，通知父进程启动新会话。"""
        self._shown_once = True
        _frontend_log(f"窗口 shown 事件触发 _minimized={self._minimized}")
        # 只有之前最小化过才认为是恢复；首次 show 时忽略
        if not self._minimized:
            return
        self._notify_restored()

    def _on_minimized(self) -> None:
        """窗口被最小化时记录状态。"""
        self._minimized = True
        _frontend_log("窗口 minimized 事件触发")

    def _on_closing(self) -> None:
        """窗口即将关闭（用户点 X），通知父进程。"""
        _frontend_log("窗口 closing 事件触发")
        self._send_response(EVT_WINDOW_CLOSED)


def _run_realtime_talk(
    config: dict[str, Any],
    event_queue: queue.Queue[dict[str, Any]],
    rt_ref: dict[str, Any],
) -> None:
    """在子进程的独立线程中运行 RealtimeTalk。"""
    import asyncio
    from agent.voice.realtime_talk import RealtimeTalk, DEFAULT_WS_URL
    from agent.ui.realtime_window.bridge import WebviewRealtimeTalkUI

    try:
        _frontend_log("RealtimeTalk 线程启动")
        ui = WebviewRealtimeTalkUI(_FrontendEventEmitter(event_queue))
        rt = RealtimeTalk(
            api_key=config.get("api_key", ""),
            model=config.get("model", "qwen-audio-3.0-realtime-flash"),
            voice=config.get("voice", "longanqian"),
            ws_url=config.get("ws_url") or DEFAULT_WS_URL,
            workdir=config.get("workdir", ""),
        )
        rt_ref["rt"] = rt
        asyncio.run(rt.run(ui))
    except Exception as e:
        _frontend_log(f"RealtimeTalk 线程异常: {type(e).__name__}: {e}")
        raise


class _FrontendEventEmitter:
    """把前端窗口的事件队列包装成 WebviewRealtimeTalkUI 可用的 emitter。"""

    def __init__(self, event_queue: queue.Queue[dict[str, Any]]) -> None:
        self._event_queue = event_queue

    def emit(self, event_type: str, payload: Any) -> None:
        self._event_queue.put_nowait({"type": event_type, "payload": payload})


def _frontend_process_main(
    command_queue: Any,
    config: dict[str, Any],
    standalone: bool = True,
    response_queue: Any = None,
) -> None:
    """子进程入口。

    在主线程运行 webview。根据 ``standalone`` 决定是否在本进程内
    同时启动 RealtimeTalk：

    - standalone=True：独立入口 ``--talk``，子进程自己跑完整实时对话。
    - standalone=False：仅显示窗口，RealtimeTalk 由父进程 daemon 驱动，
      本进程只负责渲染 UI 与轮询事件。

    另起一个线程监听父进程命令队列。

    Args:
        response_queue: 子进程→父进程的事件队列（可选）。

    @author aceFelix
    """
    _frontend_log(f"子进程启动 standalone={standalone}")
    frontend = _FrontendWindow(response_queue=response_queue)
    stop_event = threading.Event()

    def _command_loop() -> None:
        """监听父进程命令。"""
        while not stop_event.is_set():
            try:
                cmd = command_queue.get(timeout=0.1)
            except Exception:
                continue
            if not isinstance(cmd, dict):
                continue
            action = cmd.get("cmd")
            if action == CMD_SHOW:
                frontend.show()
            elif action == CMD_HIDE:
                frontend.hide()
            elif action == CMD_MINIMIZE:
                frontend.minimize()
            elif action == CMD_CLOSE:
                stop_event.set()
                frontend.destroy()
            elif action == CMD_EMIT:
                frontend.emit(cmd.get("type", ""), cmd.get("payload"))

    cmd_thread = threading.Thread(target=_command_loop, daemon=True)
    cmd_thread.start()

    rt_ref: dict[str, Any] = {"rt": None}
    rt_thread: threading.Thread | None = None
    if standalone:
        # 独立入口：子进程自己启动 RealtimeTalk
        rt_thread = threading.Thread(
            target=_run_realtime_talk,
            args=(config, frontend._event_queue, rt_ref),
            daemon=True,
        )
        rt_thread.start()

    # 兜底：子进程退出时再次通知父进程窗口已关闭
    _closed_notified = False

    def _on_window_closed() -> None:
        # 通知父进程窗口已被用户关闭（去重）
        nonlocal _closed_notified
        if not _closed_notified:
            _closed_notified = True
            if response_queue is not None:
                try:
                    response_queue.put_nowait({"event": EVT_WINDOW_CLOSED})
                except Exception:
                    pass
        rt = rt_ref.get("rt")
        if rt is not None:
            try:
                rt._running = False
            except Exception:
                pass

    frontend._on_close = _on_window_closed

    try:
        # 主线程运行 webview
        frontend.run()
    except Exception as e:
        _frontend_log(f"子进程主循环异常: {type(e).__name__}: {e}")
        raise
    finally:
        # 窗口关闭后停止 RealtimeTalk（如果启动了）
        stop_event.set()
        _on_window_closed()
        if rt_thread is not None:
            rt_thread.join(timeout=3)
        _frontend_log("子进程即将退出")
