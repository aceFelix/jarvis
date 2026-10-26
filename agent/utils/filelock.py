"""跨进程文件锁（原子创建锁文件 + 轮询退避）。

为什么不用 fcntl/msvcrt：它们在 Python 版本与平台间行为不一致（Windows 上
msvcrt.locking 只锁字节范围且非阻塞语义难控），而我们要的是"同一台机器上多个
jarvis 进程改同一份台账"这种粗粒度互斥 —— O_CREAT|O_EXCL 是各平台都保证的原子
创建，够用且可移植。

本模块由 ``collaboration/task_list.py`` 的私有实现提升而来（W3.1 任务脊柱也需要
同一把锁），提升而非复制：同一逻辑两处各写一遍，将来只会改坏一边。

@author aceFelix
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path


def ensure_dir(path: Path) -> None:
    """确保目录存在（幂等，多进程并发调用安全）。"""
    path.mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def file_lock(lock_path: Path, max_retries: int = 30):
    """跨平台文件锁上下文管理器。

    使用临时 lock 文件 + 轮询实现 Windows 兼容锁：O_CREAT | O_EXCL 原子创建
    成功即持锁，释放即删文件。退避上限 0.1s，默认最多 30 次重试（约 3 秒）后
    抛 ``TimeoutError`` —— 宁可让调用方看到拿不到锁，也不无限期等待。

    Args:
        lock_path: 锁文件路径（父目录会自动创建）。
        max_retries: 获取锁的最大重试次数。

    Yields:
        None；退出上下文时释放锁（锁文件已被其他方删除也不报错）。

    @author aceFelix
    """
    ensure_dir(lock_path.parent)

    # 轮询尝试创建 lock 文件（O_CREAT | O_EXCL = 原子创建）
    for attempt in range(max_retries + 1):
        try:
            fd = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o666,
            )
            os.close(fd)
            break  # 获取锁成功
        except FileExistsError:
            if attempt >= max_retries:
                raise TimeoutError(
                    f"无法在 {max_retries} 次重试内获取文件锁: {lock_path}"
                )
            # 等待后重试（指数退避）
            time.sleep(min(0.005 * (2 ** attempt), 0.1))

    try:
        yield
    finally:
        # 释放锁：删除 lock 文件
        try:
            os.unlink(str(lock_path))
        except FileNotFoundError:
            pass  # 已被删除
