"""工作台单实例守卫：锁文件心跳 + 本地 TCP 聚焦指令。

机制（对齐用户口径：二次双击桌面图标唤起已驻留窗口，不新建）：

1. 启动时尝试绑定固定本地端口 ``127.0.0.1:47812``：
   - 绑定成功 → 本机无驻留实例，启动聚焦监听线程，继续打开窗口。
   - 绑定失败（端口被占）→ 已有驻留实例，向其发送 ``FOCUS`` 指令，
     宿主窗口置前后本进程直接退出。
2. 同时维护锁文件 ``~/.jarvis/workbench.lock``（PID + 心跳时间戳），
   供诊断与异常场景（端口被无关进程占用）下的兜底判断。

端口选择 47812 固定值：多开判定要求所有实例约定同一端口，
无需动态分配；仅监听 127.0.0.1，不暴露到外网。

@author aceFelix
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Callable

# 单实例约定端口（仅绑定回环地址）
FOCUS_PORT = 47812
# 聚焦指令载荷
FOCUS_CMD = b"FOCUS"
# 锁文件心跳新鲜度阈值（秒）：超过则视为陈旧锁
_LOCK_STALE_SECONDS = 15.0
# 心跳写入间隔（秒）
_HEARTBEAT_INTERVAL = 5.0


def _lock_path() -> Path:
    """锁文件路径：~/.jarvis/workbench.lock"""
    return Path.home() / ".jarvis" / "workbench.lock"


def _read_lock() -> tuple[int, float] | None:
    """读取锁文件中的 (pid, 心跳时间戳)。文件缺失/损坏返回 None。"""
    try:
        text = _lock_path().read_text(encoding="utf-8").strip()
        pid_str, ts_str = text.split(",")[:2]
        return int(pid_str), float(ts_str)
    except Exception:
        return None


def _write_lock() -> None:
    """写入当前进程的锁信息（PID + 当前时间戳）。"""
    try:
        path = _lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{__import__('os').getpid()},{time.time()}", encoding="utf-8")
    except Exception:
        pass


def _clear_lock() -> None:
    """清理本进程持有的锁文件（仅当 PID 匹配时删除，避免误删新实例锁）。"""
    try:
        info = _read_lock()
        if info is not None and info[0] == __import__("os").getpid():
            _lock_path().unlink(missing_ok=True)
    except Exception:
        pass


class SingleInstanceGuard:
    """单实例守卫：负责抢占端口、监听聚焦指令、维护锁文件心跳。

    用法::

        guard = SingleInstanceGuard(on_focus=window_focus_callback)
        if not guard.try_acquire():
            # 已有驻留实例：已向其发送 FOCUS，本进程应退出
            return
        ...
        guard.release()  # 窗口关闭时调用

    @author aceFelix
    """

    def __init__(self, on_focus: Callable[[], None]) -> None:
        self._on_focus = on_focus
        self._server: socket.socket | None = None
        self._listener: threading.Thread | None = None
        self._heartbeat: threading.Thread | None = None
        self._stop = threading.Event()

    def try_acquire(self) -> bool:
        """尝试成为驻留实例。

        返回 True 表示本进程是首个实例（已启动监听）；
        返回 False 表示已存在驻留实例（已发送聚焦指令）。
        """
        try:
            # 注意：不设 SO_REUSEADDR——Windows 下该选项允许两个进程同时绑定
            # 同一端口，会导致单实例检测失效；绑定失败即视为已有驻留实例。
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.bind(("127.0.0.1", FOCUS_PORT))
            server.listen(4)
            server.settimeout(0.5)
        except OSError:
            # 端口被占：向宿主发送聚焦指令
            self._request_focus_from_host()
            return False

        self._server = server
        _write_lock()
        self._listener = threading.Thread(
            target=self._accept_loop, name="workbench-focus-listener", daemon=True
        )
        self._listener.start()
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop, name="workbench-lock-heartbeat", daemon=True
        )
        self._heartbeat.start()
        return True

    def release(self) -> None:
        """释放守卫：关闭监听、清理锁文件。窗口退出时调用。"""
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
        _clear_lock()

    def _accept_loop(self) -> None:
        """接受聚焦连接：收到任意数据即触发窗口置前回调。"""
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                break
            try:
                conn.settimeout(1.0)
                conn.recv(16)  # 读取并忽略载荷，连接本身即聚焦信号
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            try:
                self._on_focus()
            except Exception:
                pass

    def _heartbeat_loop(self) -> None:
        """周期性刷新锁文件心跳时间戳。"""
        while not self._stop.wait(_HEARTBEAT_INTERVAL):
            _write_lock()

    def _request_focus_from_host(self) -> None:
        """向驻留实例发送聚焦指令；失败时静默（宿主会自行保持窗口）。"""
        try:
            with socket.create_connection(("127.0.0.1", FOCUS_PORT), timeout=2.0) as conn:
                conn.sendall(FOCUS_CMD)
        except Exception:
            pass
