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

    def __init__(self, event_queue: queue.Queue[dict[str, Any]]) -> None:
        self._event_queue = event_queue

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
        """用户点击窗口关闭按钮时通知 Python 停止会话。"""
        self._event_queue.put_nowait({"type": "__close_session__"})


class _FrontendWindow:
    """运行在子进程中的实际 webview 窗口。

    该类只在子进程内实例化，不直接暴露给父进程。

    @author aceFelix
    """

    def __init__(self, on_close: Callable[[], None] | None = None) -> None:
        self._event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._window: Any = None
        self._on_close = on_close

    def emit(self, event_type: str, payload: Any) -> None:
        self._event_queue.put_nowait({"type": event_type, "payload": payload})

    def run(self) -> None:
        """在子进程主线程中启动 webview（阻塞直到窗口关闭）。"""
        import webview

        try:
            _frontend_log("webview 开始创建窗口")
            api = JSBridge(self._event_queue)
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

            _frontend_log("webview 调用 start()")
            webview.start(debug=False)
            _frontend_log("webview 已退出")
        except Exception as e:
            _frontend_log(f"webview 启动失败: {type(e).__name__}: {e}")
            raise

    def show(self) -> None:
        """将窗口置于前台。"""
        if self._window is None:
            return
        try:
            if hasattr(self._window, "restore"):
                self._window.restore()
            if hasattr(self._window, "raise_window"):
                self._window.raise_window()
        except Exception:
            pass

    def hide(self) -> None:
        if self._window is not None and hasattr(self._window, "hide"):
            try:
                self._window.hide()
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


def _run_realtime_talk(config: dict[str, Any], event_queue: queue.Queue[dict[str, Any]]) -> None:
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
        )
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
) -> None:
    """子进程入口。

    在主线程运行 webview。根据 ``standalone`` 决定是否在本进程内
    同时启动 RealtimeTalk：

    - standalone=True：独立入口 ``--talk``，子进程自己跑完整实时对话。
    - standalone=False：仅显示窗口，RealtimeTalk 由父进程 daemon 驱动，
      本进程只负责渲染 UI 与轮询事件。

    另起一个线程监听父进程命令队列。

    @author aceFelix
    """
    _frontend_log(f"子进程启动 standalone={standalone}")
    frontend = _FrontendWindow()
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
            elif action == CMD_CLOSE:
                stop_event.set()
                frontend.destroy()
            elif action == CMD_EMIT:
                frontend.emit(cmd.get("type", ""), cmd.get("payload"))

    cmd_thread = threading.Thread(target=_command_loop, daemon=True)
    cmd_thread.start()

    rt_thread: threading.Thread | None = None
    if standalone:
        # 独立入口：子进程自己启动 RealtimeTalk
        rt_thread = threading.Thread(
            target=_run_realtime_talk,
            args=(config, frontend._event_queue),
            daemon=True,
        )
        rt_thread.start()

    try:
        # 主线程运行 webview
        frontend.run()
    except Exception as e:
        _frontend_log(f"子进程主循环异常: {type(e).__name__}: {e}")
        raise
    finally:
        # 窗口关闭后停止 RealtimeTalk（如果启动了）
        stop_event.set()
        if rt_thread is not None:
            rt_thread.join(timeout=3)
        _frontend_log("子进程即将退出")
