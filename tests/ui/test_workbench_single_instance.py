"""工作台单实例守卫测试：端口抢占、聚焦回调、锁文件生命周期。

2026-09-10 端口隔离改造（aceFelix）：所有用例改用 port=0（内核分配空闲
端口）——原固定端口 47812 落在 Linux 临时端口范围（32768–60999）内，
共享 CI runner 上其它 localhost 连接可能随机占用它，导致 bind 偶发
EADDRINUSE（Linux CI test_release_frees_port_and_lock /
test_focus_signal_via_plain_connection 首绑连挂复盘）。隔离后测试不再
依赖环境端口空闲，同时保留「release 后同端口可重抢」的回归语义。

@author aceFelix
"""

from __future__ import annotations

import socket
import time

from agent.ui.workbench.single_instance import (
    SingleInstanceGuard,
    _lock_path,
    _read_lock,
)


def test_first_guard_acquires_and_second_detects_host():
    """首个守卫抢占成功；第二个守卫检测到驻留实例返回 False。"""
    focused = {"count": 0}

    def on_focus() -> None:
        focused["count"] += 1

    # guard1 用 port=0 隔离；guard2 指向 guard1 的实际端口以复现多开探测
    guard1 = SingleInstanceGuard(on_focus=on_focus, port=0)
    guard2: SingleInstanceGuard | None = None
    try:
        assert guard1.try_acquire() is True
        assert guard1.port != 0  # 内核已分配实际端口
        # 锁文件写入当前进程信息
        info = _read_lock()
        assert info is not None
        assert info[1] <= time.time() + 1

        # 第二个实例：绑定失败 → 发聚焦指令 → 返回 False
        guard2 = SingleInstanceGuard(on_focus=lambda: None, port=guard1.port)
        assert guard2.try_acquire() is False
        # 宿主收到聚焦连接并触发回调（监听线程异步处理，等待一拍）
        deadline = time.time() + 3
        while focused["count"] == 0 and time.time() < deadline:
            time.sleep(0.05)
        assert focused["count"] >= 1
    finally:
        guard1.release()
        if guard2 is not None:
            guard2.release()


def test_release_frees_port_and_lock():
    """release 后端口可重新抢占，锁文件被清理。"""
    guard = SingleInstanceGuard(on_focus=lambda: None, port=0)
    assert guard.try_acquire() is True
    port = guard.port
    assert _lock_path().exists()
    guard.release()
    # 锁文件清理（仅清本进程持有的）
    assert not _lock_path().exists()

    # 端口释放后可再次抢占：guard2 显式绑回同一端口，
    # 若 release 未彻底释放（Linux close 不唤醒 accept 等）这里会失败
    guard2 = SingleInstanceGuard(on_focus=lambda: None, port=port)
    try:
        assert guard2.try_acquire() is True
    finally:
        guard2.release()


def test_focus_signal_via_plain_connection():
    """任意 TCP 连接即聚焦信号（载荷可忽略）。"""
    guard = SingleInstanceGuard(on_focus=lambda: None, port=0)
    triggered = {"v": False}
    guard._on_focus = lambda: triggered.__setitem__("v", True)
    try:
        assert guard.try_acquire() is True
        with socket.create_connection(("127.0.0.1", guard.port), timeout=2) as conn:
            conn.sendall(b"FOCUS")
        deadline = time.time() + 3
        while not triggered["v"] and time.time() < deadline:
            time.sleep(0.05)
        assert triggered["v"] is True
    finally:
        guard.release()
