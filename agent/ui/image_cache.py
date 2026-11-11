"""图片窗口的纯逻辑层：缩放尺寸、缓存键、存活判定、布局与元信息文案。

为什么不跟窗口写在一起（与 ``cockpit_crawl_data`` 同一定位）：

- Tk 不能在测试里随便起（需要真显示环境），但这些计算**必须**能单测：
  尺寸算错 = 图挤成一团或溢出窗口，缓存键漏掉 mtime = 改了文件还显示旧图；
- 本模块**不 import tkinter、不 import PIL、不读图片字节**，只做纯计算与
  ``pathlib`` 级别的存活检查。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_X",
    "DEFAULT_Y",
    "DRAG_THRESHOLD",
    "FOOTER_Y",
    "MAIN_BOTTOM",
    "MAIN_TOP",
    "META_TOP",
    "PAD",
    "STRIP_HEIGHT",
    "STRIP_TOP",
    "WINDOW_H",
    "WINDOW_W",
    "cache_key",
    "clamp_index",
    "describe_meta",
    "file_alive",
    "file_mtime_ns",
    "fit_size",
    "main_size",
    "strip_positions",
    "thumb_size",
    "window_origin",
]

# ---- 窗口几何（480×600 竖版；坐标集中在这里，窗口只做绘制）----
WINDOW_W = 480
WINDOW_H = 600
PAD = 18

MAIN_TOP = 72
MAIN_BOTTOM = 372
STRIP_TOP = 384
STRIP_HEIGHT = 96
META_TOP = 492
FOOTER_Y = 584

#: 默认停靠屏幕左上角；用户拖动后由 ui_state.json 记忆
DEFAULT_X = 24
DEFAULT_Y = 24

#: 超过这么多像素才算"拖动"，否则算点击（与 main HUD 的 4px 同口径）
DRAG_THRESHOLD = 4

#: 缩略图缓存上限（略大于 image_max，留切换余量）
THUMB_CACHE_MAX = 24


def _as_int(value: Any) -> int:
    """宽容转 int：非法/None 一律 0。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def fit_size(width: Any, height: Any, max_w: Any, max_h: Any) -> tuple[int, int]:
    """等比缩放到不超过 ``(max_w, max_h)``；尺寸非法时返回 ``(0, 0)``。

    只缩不放（``scale`` 上限 1.0）：小图放大只会变糊，且会误导用户以为原图很大。
    """
    w, h = _as_int(width), _as_int(height)
    limit_w, limit_h = _as_int(max_w), _as_int(max_h)
    if w <= 0 or h <= 0 or limit_w <= 0 or limit_h <= 0:
        return 0, 0
    scale = min(limit_w / w, limit_h / h, 1.0)
    return max(1, int(w * scale)), max(1, int(h * scale))


def main_size(width: Any, height: Any) -> tuple[int, int]:
    """大图区适配尺寸（窗口内区，留 4px 余量防贴边）。"""
    return fit_size(width, height, WINDOW_W - 2 * PAD - 8, MAIN_BOTTOM - MAIN_TOP - 8)


def thumb_size(width: Any, height: Any, limit: Any) -> tuple[int, int]:
    """缩略条单张尺寸：最长边不超过 ``limit``（钳到 16 以上防退化成点）。"""
    edge = max(16, _as_int(limit))
    return fit_size(width, height, edge, edge)


def cache_key(path: Any, mtime_ns: Any, thumb_px: Any) -> str:
    """缩略图缓存键：路径 + mtime + 缩略尺寸。

    必须带 mtime：同名文件被覆盖后（例如固定路径反复截图）键一变即失效，
    否则窗口会一直显示改动前的那张图。
    """
    return f"{path}|{_as_int(mtime_ns)}|{_as_int(thumb_px)}"


def file_alive(path: Any) -> bool:
    """图片文件是否还在（``ScreenShot`` 落系统临时目录，可能被清理）。"""
    try:
        return Path(str(path)).is_file()
    except (OSError, ValueError):
        return False


def file_mtime_ns(path: Any) -> int:
    """文件 mtime（纳秒）；取不到返回 0。"""
    try:
        return int(Path(str(path)).stat().st_mtime_ns)
    except (OSError, ValueError):
        return 0


def window_origin(
    screen_w: Any,
    screen_h: Any,
    saved: Any = None,
    *,
    margin: int = DEFAULT_X,
) -> tuple[int, int]:
    """窗口左上角坐标：默认贴左上，有记忆位置则钳制在屏幕内。

    Args:
        screen_w: 屏幕宽。
        screen_h: 屏幕高。
        saved: ``(x, y)`` 记忆位置；``None`` 表示首次启动。
        margin: 默认边距（左上角留白）。
    """
    if saved is None:
        return margin, margin
    try:
        x, y = int(saved[0]), int(saved[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return margin, margin
    max_x = max(0, _as_int(screen_w) - WINDOW_W)
    max_y = max(0, _as_int(screen_h) - WINDOW_H)
    return max(0, min(max_x, x)), max(0, min(max_y, y))


def strip_positions(
    count: Any,
    thumb_px: Any,
    *,
    gap: int = 8,
    left: int = PAD,
) -> list[int]:
    """缩略条每张图的左边缘 x 坐标（单行横向排列）。"""
    step = max(16, _as_int(thumb_px)) + gap
    return [left + index * step for index in range(max(0, _as_int(count)))]


def clamp_index(index: Any, count: Any) -> int:
    """把选中下标钳到 ``[0, count-1]``；空列表返回 0。"""
    total = _as_int(count)
    if total <= 0:
        return 0
    return max(0, min(total - 1, _as_int(index)))


def human_bytes(value: Any) -> str:
    """字节数 → 人读大小；缺失/非法返回空串（不写 "0B" 这种假信息）。"""
    if value is None:
        return ""
    size = _as_int(value)
    if size <= 0:
        return ""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}MB"
    if size >= 1024:
        return f"{int(size / 1024)}KB"
    return f"{size}B"


def describe_meta(ref: dict[str, Any]) -> list[str]:
    """元信息三行：来源/尺寸/格式/体积、产出时间、路径。

    路径单独一行且可能很长：窗口负责再截断（保持纯函数只产出原文）。
    """
    source = str(ref.get("source") or "未知来源")
    width, height = _as_int(ref.get("width")), _as_int(ref.get("height"))
    dims = f"{width}×{height}" if width and height else ""
    media = str(ref.get("media_type") or "").replace("image/", "").upper()
    head = " · ".join(part for part in (source, dims, media, human_bytes(ref.get("bytes"))) if part)
    stamp = str(ref.get("ts") or "")
    return [head, stamp, str(ref.get("path") or "")]
