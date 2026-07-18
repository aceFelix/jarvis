"""实时聊天窗口封装（multiprocessing 子进程方案）。

由于 pywebview 必须在主线程运行，而 daemon 主线程被 voice_loop 占用，
因此把独立窗口放在独立子进程中运行。父进程通过 multiprocessing.Queue
与子进程通信：show / hide / close / emit 事件。

子进程入口在 ``process.py`` 中，避免 Windows multiprocessing spawn
重新导入 daemon 的 ``__main__``（``agent/main.py``）导致子进程又进入
 daemon 模式。

优点：
- pywebview 在子进程主线程运行，符合库要求
- daemon 进程不被 GUI 阻塞
- 单次 daemon 生命周期内可复用同一子进程/窗口

@author aceFelix
"""

from __future__ import annotations

import multiprocessing
import threading
from typing import Any, Callable

from agent.ui.realtime_window.process import (
    CMD_CLOSE,
    CMD_EMIT,
    CMD_HIDE,
    CMD_SHOW,
    _frontend_process_main,
)


def _has_webview() -> bool:
    """检查 pywebview 是否已安装。"""
    try:
        import webview  # noqa: F401
        return True
    except ImportError:
        return False


class RealtimeTalkWindow:
    """父进程中的实时聊天窗口控制器。

    管理一个子进程中的 webview 窗口。同一父进程生命周期内
    只维护一个子进程/窗口实例。

    支持两种运行模式：
    - standalone=True（默认）：子进程自行启动 RealtimeTalk，用于
      ``python -m agent.main --talk`` 独立入口。
    - standalone=False：仅运行 webview 窗口，RealtimeTalk 由父进程
      在 daemon 中另行启动并驱动，避免重复占用麦克风与重复连接。

    @author aceFelix
    """

    _instance: RealtimeTalkWindow | None = None
    _lock = threading.Lock()

    def __new__(cls, *args: object, **kwargs: object) -> RealtimeTalkWindow:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(
        self,
        on_close: Callable[[], None] | None = None,
        *,
        standalone: bool = True,
    ) -> None:
        if hasattr(self, "_initialized") and self._initialized:
            self._on_close = on_close or self._on_close
            self._standalone = standalone
            return

        self._initialized = True
        self._on_close: Callable[[], None] | None = on_close
        self._standalone: bool = standalone
        self._command_queue: multiprocessing.Queue | None = None
        self._process: multiprocessing.Process | None = None
        self._config: dict[str, Any] = {}

    @classmethod
    def reset_singleton(cls) -> None:
        """强制重置单例（用于测试或 daemon 退出后清理）。"""
        with cls._lock:
            cls._instance = None

    @property
    def is_open(self) -> bool:
        """子进程窗口是否仍在运行。"""
        return self._process is not None and self._process.is_alive()

    def set_config(self, config: dict[str, Any]) -> None:
        """设置启动子进程时传给 RealtimeTalk 的配置。"""
        self._config = dict(config)

    def show(self) -> bool:
        """显示窗口。若已存在则唤起，否则新建子进程。"""
        if self.is_open:
            self._send_command(CMD_SHOW)
            return True

        if not _has_webview():
            raise ImportError("pywebview 未安装")

        ctx = multiprocessing.get_context("spawn")
        self._command_queue = ctx.Queue()
        self._process = ctx.Process(
            target=_frontend_process_main,
            args=(self._command_queue, self._config, self._standalone),
            daemon=True,
        )
        self._process.start()
        return True

    def hide(self) -> None:
        """隐藏窗口（不结束子进程）。"""
        self._send_command(CMD_HIDE)

    def _notify_close(self) -> None:
        """通知窗口已被要求关闭（不真正结束子进程，仅触发 on_close 回调）。

        由 daemon 在停止实时对话时调用，用于通知 RealtimeTalk 结束会话，
        同时保留窗口实例以便后续复用。

        @author aceFelix
        """
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:
                pass

    def destroy(self) -> None:
        """关闭窗口并结束子进程。"""
        self._send_command(CMD_CLOSE)
        if self._process is not None:
            try:
                self._process.join(timeout=3)
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(timeout=2)
            except Exception:
                pass
            self._process = None
        self._command_queue = None
        self.reset_singleton()

    def emit(self, event_type: str, payload: Any) -> None:
        """向窗口前端发送事件。"""
        self._send_command(CMD_EMIT, type=event_type, payload=payload)

    def _send_command(self, cmd: str, **kwargs: Any) -> None:
        if self._command_queue is None:
            return
        try:
            self._command_queue.put_nowait({"cmd": cmd, **kwargs})
        except Exception:
            pass
