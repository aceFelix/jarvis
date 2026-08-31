"""工作台单实例守卫测试：端口抢占、聚焦回调、锁文件生命周期。

@author aceFelix
"""

from __future__ import annotations

import socket
import time

from agent.ui.workbench.single_instance import (
    FOCUS_PORT,
    SingleInstanceGuard,
    _lock_path,
    _read_lock,
)


def test_first_guard_acquires_and_second_detects_host():
    """首个守卫抢占成功；第二个守卫检测到驻留实例返回 False。"""
    focused = {"count": 0}

    def on_focus() -> None:
        focused["count"] += 1

    guard1 = SingleInstanceGuard(on_focus=on_focus)
    guard2 = SingleInstanceGuard(on_focus=lambda: None)
    try:
        assert guard1.try_acquire() is True
        # 锁文件写入当前进程信息
        info = _read_lock()
        assert info is not None
        assert info[1] <= time.time() + 1

        # 第二个实例：绑定失败 → 发聚焦指令 → 返回 False
        assert guard2.try_acquire() is False
        # 宿主收到聚焦连接并触发回调（监听线程异步处理，等待一拍）
        deadline = time.time() + 3
        while focused["count"] == 0 and time.time() < deadline:
            time.sleep(0.05)
        assert focused["count"] >= 1
    finally:
        guard1.release()
        guard2.release()


def test_release_frees_port_and_lock():
    """release 后端口可重新抢占，锁文件被清理。"""
    guard = SingleInstanceGuard(on_focus=lambda: None)
    assert guard.try_acquire() is True
    assert _lock_path().exists()
    guard.release()
    # 锁文件清理（仅清本进程持有的）
    assert not _lock_path().exists()

    # 端口释放后可再次抢占
    guard2 = SingleInstanceGuard(on_focus=lambda: None)
    try:
        assert guard2.try_acquire() is True
    finally:
        guard2.release()


def test_focus_signal_via_plain_connection():
    """任意 TCP 连接即聚焦信号（载荷可忽略）。"""
    guard = SingleInstanceGuard(on_focus=lambda: None)
    triggered = {"v": False}
    guard._on_focus = lambda: triggered.__setitem__("v", True)
    try:
        assert guard.try_acquire() is True
        with socket.create_connection(("127.0.0.1", FOCUS_PORT), timeout=2) as conn:
            conn.sendall(b"FOCUS")
        deadline = time.time() + 3
        while not triggered["v"] and time.time() < deadline:
            time.sleep(0.05)
        assert triggered["v"] is True
    finally:
        guard.release()
