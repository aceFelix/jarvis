"""文件状态缓存 —— 追踪文件修改时间，检测编辑冲突。

工作原理:
1. FileRead 读取文件时，记录文件路径 + mtime（最后修改时间）
2. FileEdit / FileWrite 编辑文件前，检查磁盘上文件的 mtime
   - 如果 mtime 和缓存记录的不一致 → 文件被外部修改了，拒绝编辑
   - 如果一致 → 安全，执行编辑后更新 mtime
3. 这样防止模型基于过时的文件内容做编辑，避免覆盖外部改动

存放在 ToolContext.extra["_file_state_cache"] 中，随 ctx 生命周期存在。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _get_cache(ctx) -> dict[str, float]:
    """获取或创建文件状态缓存。"""
    if not hasattr(ctx, "extra") or ctx.extra is None:
        return {}
    return ctx.extra.setdefault("_file_state_cache", {})


def _get_mtime(path: Path) -> float:
    """获取文件 mtime（最后修改时间戳）。失败返回 0。"""
    try:
        return path.stat().st_mtime
    except (OSError, ValueError):
        return 0.0


def record_file_read(ctx, file_path: str) -> None:
    """记录文件读取时的 mtime。

    在 FileRead 工具成功读取后调用。
    """
    cache = _get_cache(ctx)
    path = str(Path(file_path).resolve())
    cache[path] = _get_mtime(Path(path))


def check_file_stale(ctx, file_path: str) -> bool:
    """检查文件是否被外部修改（mtime 和缓存不一致）。

    返回 True 表示文件已被外部修改（冲突），不应编辑。
    返回 False 表示安全（mtime 一致或文件未被读过）。
    """
    cache = _get_cache(ctx)
    path = str(Path(file_path).resolve())

    if path not in cache:
        # 文件从未被模型读过——不阻止编辑（新建文件或首次操作）
        return False

    cached_mtime = cache[path]
    current_mtime = _get_mtime(Path(path))

    # mtime 精度到秒，差异超过 0.5 秒视为外部修改
    return abs(current_mtime - cached_mtime) > 0.5


def record_file_write(ctx, file_path: str) -> None:
    """记录文件写入/编辑后的新 mtime。

    在 FileEdit / FileWrite 成功后调用。
    """
    cache = _get_cache(ctx)
    path = str(Path(file_path).resolve())
    cache[path] = _get_mtime(Path(path))


def invalidate(ctx, file_path: str) -> None:
    """清除文件的缓存记录（文件被删除时调用）。"""
    cache = _get_cache(ctx)
    path = str(Path(file_path).resolve())
    cache.pop(path, None)
