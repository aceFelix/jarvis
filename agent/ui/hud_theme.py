"""HUD 共享视觉常量与文本工具（主驾驶舱 + 多任务浮窗共用）。

把颜色、字体、截断/耗时格式化集中在这里，避免主窗与卡片各抄一份。
@author aceFelix
"""

from __future__ import annotations

from typing import Any

# 深色 HUD 配色
BG = "#04080d"
BG_PANEL = "#07131b"
GRID = "#0a1a24"
CYAN = "#38e1ff"
CYAN_DIM = "#15566a"
TEXT = "#c8e6f2"
DIM = "#62808d"
GREEN = "#46f2a0"
RED = "#ff5f6d"
# 子代理/队友卡专用琥珀色：与青色工具卡区分“谁在干活”的层级
# （2026-11-08 C3-lite.2）。@author aceFelix
AMBER = "#ffb347"
AMBER_DIM = "#6b4416"

FONT_TITLE = ("Microsoft YaHei UI", 13, "bold")
FONT_LABEL = ("Microsoft YaHei UI", 8)
FONT_TEXT = ("Microsoft YaHei UI", 9)
FONT_SMALL = ("Microsoft YaHei UI", 8)
FONT_NUM = ("Consolas", 17, "bold")
FONT_MONO = ("Consolas", 9)
FONT_CARD_TITLE = ("Microsoft YaHei UI", 10, "bold")


def display_width(text: str) -> int:
    """估算显示宽度：CJK/全角按 2 个单元，ASCII 按 1 个。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)


def clip(text: Any, limit: int) -> str:
    """按显示宽度截断，超出用省略号；保证单行不折行。"""
    raw = str(text or "")
    if display_width(raw) <= limit:
        return raw
    out: list[str] = []
    used = 0
    for ch in raw:
        width = 2 if ord(ch) > 0x2E7F else 1
        if used + width > limit - 1:
            break
        out.append(ch)
        used += width
    return "".join(out) + "…"


def fmt_elapsed(ms: float) -> str:
    """总耗时展示：<1s 用 ms，<60s 用秒，其余用分秒。"""
    if ms < 1000:
        return f"{int(ms)}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    return f"{minutes}m{int(seconds % 60):02d}s"


__all__ = [
    "BG",
    "BG_PANEL",
    "GRID",
    "CYAN",
    "CYAN_DIM",
    "TEXT",
    "DIM",
    "GREEN",
    "RED",
    "AMBER",
    "AMBER_DIM",
    "FONT_TITLE",
    "FONT_LABEL",
    "FONT_TEXT",
    "FONT_SMALL",
    "FONT_NUM",
    "FONT_MONO",
    "FONT_CARD_TITLE",
    "display_width",
    "clip",
    "fmt_elapsed",
]
