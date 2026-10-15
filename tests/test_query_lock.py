"""`agent.utils.query_lock.acquire_lock_cancellable` 单元测试。

覆盖三通道 query 锁（桌面 send / 手机 bridge / 微信）等锁语义的三条关键性质：
- 正常路径：返回 True 且调用方确实持有锁（必须自行 release）；
- 取消路径：取消落在等锁窗口时抛 CancelledError，而线程池事后拿到的锁被归还
  （回归用例见 docs/fixlogs/query-lock-cancel-leak-fix.md）；
- 串行路径：锁被占用期间多个等待者都进不来，释放后各自拿到并归还。

注释作者 aceFelix
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from agent.utils.query_lock import acquire_lock_cancellable


async def test_acquire_returns_true_and_holds_lock():
    """空闲锁上正常等锁：返回 True，且锁确被调用方持有（再 acquire 失败）。"""
    lock = threading.Lock()
    assert await acquire_lock_cancellable(lock) is True
    assert not lock.acquire(blocking=False), "锁应仍处于被占用状态"
    lock.release()


async def test_cancel_while_waiting_locks_gives_lock_back():
    """取消落在等锁窗口：抛 CancelledError，且 worker 事后拿到的锁必须被归还。

    构造方式：先用普通线程占住锁，让协程进入"阻塞等待"，再取消它；占锁线程随后
    释放，线程池里那次 acquire 会成功 —— 若无自动归还，锁就被一次已取消的等待吞掉。
    """
    lock = threading.Lock()
    lock.acquire()  # 占住，迫使协程进入等锁窗口
    task = asyncio.create_task(acquire_lock_cancellable(lock))
    await asyncio.sleep(0.05)  # 让 executor 线程真的阻塞在 acquire 上
    assert not task.done()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    lock.release()  # 占锁方放手：worker 会拿到锁
    deadline = time.time() + 5
    while time.time() < deadline:
        if lock.acquire(blocking=False):
            lock.release()
            return  # 锁已被自动归还
        await asyncio.sleep(0.02)
    pytest.fail("取消等锁后线程池没有归还锁，三通道 query 将被永久卡死")


async def test_waiters_serialized_and_lock_reusable():
    """锁被占用时等待者进不来；释放后两个等待者依次拿到并归还，锁仍可复用。"""
    lock = threading.Lock()
    assert await acquire_lock_cancellable(lock) is True
    order: list[str] = []

    async def waiter(tag: str) -> None:
        assert await acquire_lock_cancellable(lock) is True
        order.append(tag)
        lock.release()

    t1 = asyncio.create_task(waiter("a"))
    t2 = asyncio.create_task(waiter("b"))
    await asyncio.sleep(0.05)
    assert order == [], "占用期间不应有等待者拿到锁"

    lock.release()
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=5)
    assert sorted(order) == ["a", "b"]
    # 两位等待者都归还了：锁当前空闲
    assert lock.acquire(blocking=False), "等待者结束后锁应可再次获取"
    lock.release()
