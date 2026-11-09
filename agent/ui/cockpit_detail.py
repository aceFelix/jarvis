"""主 HUD「工具详情」浮层渲染。

从 ``cockpit_window.py`` 拆出（单文件 900 行上限，2026-11-09）。只负责画一个
覆盖在“数据流/抓取”区域的详情面板：工具名、目标、状态、调用入参与失败错误。

设计边界：
- 结果正文不写 trace（隐私），所以这里只展示**调用细节**；
- 不持有状态、不处理点击；展开/收起由 ``_Hud`` 负责；
- 尺寸/边距由调用方传入，避免和窗口常量循环依赖。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.ui.cockpit import _fmt_ms, describe_target
from agent.ui.hud_theme import (
    BG_PANEL,
    CYAN,
    CYAN_DIM,
    DIM,
    FONT_LABEL,
    FONT_MONO,
    FONT_SMALL,
    GREEN,
    RED,
    TEXT,
    clip,
)


def draw_tool_detail(canvas: Any, entry: dict[str, Any], *, window_w: int, pad: int) -> None:
    """在 Canvas 上画工具详情面板。

    Args:
        canvas: Tk Canvas。
        entry: ``CockpitState.step_history`` 中的一个条目（含 tool/target/input/
            ok/ms/error）。
        window_w: HUD 宽度。
        pad: 左右内边距。

    @author aceFelix
    """
    top, bottom = 306, 548

    def _text(
        x: float,
        y: float,
        content: Any,
        *,
        fill: str = TEXT,
        font: Any = FONT_SMALL,
        anchor: str = "w",
    ) -> None:
        canvas.create_text(x, y, text=str(content), fill=fill, font=font, anchor=anchor)

    canvas.create_rectangle(
        pad, top, window_w - pad, bottom,
        fill=BG_PANEL, outline=CYAN_DIM, width=1,
    )
    _text(pad + 10, top + 18, "工具详情 // DETAIL", fill=CYAN, font=FONT_LABEL)
    _text(
        window_w - pad - 10,
        top + 18,
        "再次点击该步骤收起",
        fill=DIM,
        anchor="e",
    )

    ok = entry.get("ok")
    if ok is True:
        status = f"成功 · {_fmt_ms(entry.get('ms')) or '—'}"
        color = GREEN
    elif ok is False:
        status = f"失败 · {_fmt_ms(entry.get('ms')) or '—'}"
        color = RED
    else:
        status = "运行中"
        color = CYAN

    tool = str(entry.get("tool") or "?")
    target = describe_target(entry.get("target"))
    _text(
        pad + 10,
        top + 42,
        f"{tool}  {target}".strip(),
        fill=TEXT,
        font=("Microsoft YaHei UI", 10, "bold"),
    )
    _text(pad + 10, top + 60, status, fill=color)

    _text(pad + 10, top + 84, "调用入参 // INPUT", fill=CYAN_DIM, font=FONT_LABEL)
    details = entry.get("input") or {}
    y = top + 104
    if not isinstance(details, dict) or not details:
        _text(
            pad + 10,
            y,
            "— 未记录入参（只记录路径/窗口/计数等短字段）",
            fill=DIM,
        )
    else:
        for key, value in list(details.items())[:3]:
            _text(
                pad + 10,
                y,
                f"{key}: {clip(str(value), 46)}",
                fill=TEXT,
                font=FONT_MONO,
            )
            y += 18

    if ok is False:
        error = str(entry.get("error") or "").strip()
        _text(pad + 10, top + 176, "错误 // ERROR", fill=RED, font=FONT_LABEL)
        _text(
            pad + 10,
            top + 196,
            clip(error or "（无错误摘要）", 54),
            fill=RED,
        )

    _text(
        pad + 10,
        bottom - 16,
        "仅展示调用细节；工具结果正文不写入 trace",
        fill=DIM,
    )
