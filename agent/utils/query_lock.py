"""threading.Lock 的取消安全等待（三通道 query 锁共用）。

背景：桌面 send、手机 bridge、微信三条通道共用同一把 `threading.Lock` 串行化 query。
asyncio 侧不能直接 `lock.acquire()`（阻塞会冻结引擎 loop），所以惯用
`await loop.run_in_executor(None, lock.acquire)` 把阻塞等待丢进线程池。

这里的坑是**取消**：桌面「停止」按钮走 `reply.abort` → `task.cancel()`，若取消正好
落在"等锁"期间（登记任务句柄之后、拿到锁之前），`await` 立刻抛 `CancelledError`，
而线程池里那次 `acquire()` 稍后仍会成功并把锁拿走 —— 原语不会替调用方归还，于是：

1. 锁被永久占用，后续任意通道的 query 全部卡死（比"停不下来"更糟）；
2. 本轮的 `finally` 收尾（释放锁、清 `_send_task`、发 `assistant_done`）整段被跳过，
   前端气泡悬在"正在回复"永不落地。

本模块把这一处理收敛成一处，四个调用点复用；用法约定见 `acquire_lock_cancellable`。

@author aceFelix
"""
from __future__ import annotations

import asyncio
import threading


async def acquire_lock_cancellable(lock: threading.Lock) -> bool:
    """线程池等待 `lock.acquire()`，返回调用方是否真正持有该锁。

    - 正常返回 `True`：锁已持有，调用方必须在 `finally` 里 `release()`。
    - 抛 `CancelledError`（或其他异常）：等待中被取消，此时锁**将由线程池自行归还**，
      调用方不得再 `release()`（否则解未持有的锁会抛 `RuntimeError`）。因此调用方应
      先置 `locked = False`，仅在本函数正常返回后置 `True`，并按 `locked` 决定是否释放。

    实现要点：不能直接 `await` 线程池 future——外层任务被 cancel 时 asyncio 会连带
    把该 future 标为 CANCELLED（即使线程已在 `acquire()` 上阻塞、实际取消不掉），
    于是它的结果再也读不到，我们也就无从知道“锁已被拿走”。故用 `asyncio.shield`
    把内层 future 保住：取消只影响外层等待，内层照常跑完。被取消时给**内层**
    future 挂 done 回调：worker 真的拿到锁（result 为 True）时立刻 `release()`；
    若 worker 在排入线程池前就被取消，`result()` 抛 CancelledError，无锁可还，静默跳过。

    @author aceFelix
    """
    inner = asyncio.get_running_loop().run_in_executor(None, lock.acquire)
    try:
        return bool(await asyncio.shield(inner))
    except BaseException:
        def _give_back(done: asyncio.Future) -> None:
            """worker 事后拿到锁就马上归还，避免锁被一次已取消的等待吞掉。"""
            try:
                if done.result():
                    lock.release()
            except BaseException:
                pass  # 工作在线程池开工前就被取消：没有锁需要归还
        inner.add_done_callback(_give_back)
        raise
