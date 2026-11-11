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

from agent.ui.cockpit import _fmt_bytes, _fmt_ms, _short_url, describe_target
from agent.ui.hud_theme import (
    BG_PANEL,
    CYAN,
    CYAN_DIM,
    DIM,
    FONT_LABEL,
    FONT_MONO,
    FONT_SMALL,
    FONT_TEXT,
    GREEN,
    GRID,
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


def draw_section(canvas: Any, title: str, y: float, *, window_w: int, pad: int) -> None:
    """小节标题 + 右侧延长线。

    从 ``_Hud._section`` 上提：数据流与抓取两块的绘制搬到本模块后，标题这条
    108px 的缩进线也必须只有一处定义，否则主窗与搬迁后的函数各写一份会漂移。

    @author aceFelix
    """
    canvas.create_text(pad, y, text=title, fill=CYAN_DIM, font=FONT_LABEL, anchor="w")
    canvas.create_line(pad + 108, y, window_w - pad, y, fill=GRID)


def draw_feed(canvas: Any, state: Any, *, window_w: int, pad: int) -> None:
    """数据流：最近 8 条事件摘要。

    Args:
        canvas: Tk Canvas。
        state: ``CockpitState``（只读 ``feed``）。
        window_w: HUD 宽度。
        pad: 左右内边距。
    """
    draw_section(canvas, "数据流 // STREAM", 312, window_w=window_w, pad=pad)
    items = list(state.feed)[-8:]
    if not items:
        canvas.create_text(
            pad, 340, text="— 暂无事件", fill=DIM, font=FONT_SMALL, anchor="w"
        )
        return
    y = 336
    for line in items:
        canvas.create_text(
            pad, y, text=clip(line, 56), fill=DIM, font=FONT_MONO, anchor="w"
        )
        y += 17


def draw_fetches(canvas: Any, state: Any, *, window_w: int, pad: int) -> None:
    """抓取：最近 3 条 URL / 状态 / 耗时 / 字节。"""
    draw_section(canvas, "抓取 // FETCH", 486, window_w=window_w, pad=pad)
    items = list(state.fetches)[-3:]
    if not items:
        canvas.create_text(
            pad, 512, text="— 暂无抓取", fill=DIM, font=FONT_SMALL, anchor="w"
        )
        return
    y = 508
    for item in items:
        ok = item.get("status") == "ok"
        canvas.create_text(
            pad, y, text="✓" if ok else "✗",
            fill=GREEN if ok else RED, font=FONT_TEXT, anchor="w",
        )
        canvas.create_text(
            pad + 16, y, text=clip(_short_url(item.get("url")), 34),
            fill=TEXT, font=FONT_MONO, anchor="w",
        )
        meta = " · ".join(
            part for part in (
                _fmt_ms(item.get("duration_ms")),
                _fmt_bytes(item.get("bytes")),
            ) if part
        )
        canvas.create_text(
            window_w - pad, y, text=meta, fill=DIM, font=FONT_MONO, anchor="e"
        )
        y += 22
