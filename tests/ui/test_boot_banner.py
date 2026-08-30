# -*- coding: utf-8 -*-
"""启动定格帧测试（缩窗防错乱方案）。

背景：终端已输出的静态内容无法随窗口缩放重绘（一次成像）。
现方案：Live 退出后按定格瞬间的窗口大小分流——
- 小窗（≤120 列）：静态反应炉定格帧（画布≤64 列）+ 标题 + 紧凑信息面板；
- 大窗/最大化（>120 列）：J.A.R.V.I.S 方块艺术字（≤52 列）+ 紧凑信息面板。
两条路径都保证之后缩窗不折行错乱。

作者：aceFelix
"""
from __future__ import annotations

import io
import os

import pytest

rich = pytest.importorskip("rich")

from rich.console import Console  # noqa: E402

from agent.ui.boot_animation import (  # noqa: E402
    _BANNER_MAX_PAD,
    _HELP_HINT,
    _RESTORE_TARGET_WIDTH,
    _ascii_art_lines,
    _banner_total_width,
    _banner_with_safe_center,
    _calc_geo,
    _compact_banner,
    _final_renderable,
)

_INFO = ("zhipu", "glm-5.3-flash", r"C:\Users\许发明\.jarvis")


class _Utf8Buffer(io.StringIO):
    """固定 UTF-8 编码的渲染 buffer（作者：aceFelix）。

    渲染类测试不得依赖宿主编码环境：显式声明 utf-8，避免 Rich 因
    编码判定差异（ascii_only 等）改变输出形态，保证各平台行为一致。
    """

    encoding = "utf-8"


def _render_lines(renderable, width: int) -> list[str]:
    # 渲染环境必须固定，消除宿主差异（作者：aceFelix）：
    # - encoding=utf-8：避免 Rich 按宿主编码判定 ascii_only 降级边框；
    # - legacy_windows=False：Rich 默认按宿主平台判定，Windows 会把圆角
    #   ╭/╰ 替换成方角 ┌/└，导致本地/CI 渲染形态不一致；固定为非
    #   legacy，所有平台统一渲染 ROUNDED 圆角，断言随之确定。
    buf = _Utf8Buffer()
    Console(file=buf, force_terminal=False, width=width,
            legacy_windows=False).print(renderable)
    return buf.getvalue().splitlines()


# Panel 边框行首字符（作者：aceFelix）：
# _compact_banner 用默认 ROUNDED 框；_render_lines 已固定 legacy_windows=False，
# 各平台统一渲染圆角 ╭/╰。保留 ┌/└/+ 仅作防御性兼容（真实终端降级场景）。
_BOX_TOP_BOTTOM = ("┌", "└", "╭", "╰", "+")


def test_compact_banner_is_narrow_and_short():
    """紧凑横幅：宽度 = 按内容动态计算的外框总宽，高度 ≤10 行（不含反应炉大画布）。"""
    lines = _render_lines(_compact_banner(_INFO), 300)
    assert max(len(l) for l in lines) <= _banner_total_width(_INFO)
    assert len(lines) <= 10


def test_safe_center_caps_indent_on_wide_window():
    """超宽窗口：缩进封顶 _BANNER_MAX_PAD，可见总宽 ≤ 面板外框宽+封顶。"""
    lines = _render_lines(_banner_with_safe_center(_INFO, render_width=120), 300)
    # 行尾空格在终端不可见且无折行危害，测量可见宽度
    assert max(len(l.rstrip()) for l in lines) <= _banner_total_width(_INFO) + _BANNER_MAX_PAD


def test_safe_center_no_wrap_on_narrow_window():
    """关键回归：80 列窄窗口下渲染不折行（总宽 ≤ 渲染宽）。"""
    lines = [l for l in _render_lines(
        _banner_with_safe_center(_INFO, render_width=80), 80) if l.strip()]
    assert max(len(l.rstrip()) for l in lines) <= 80


def test_banner_contains_session_info():
    """横幅必须携带 provider/model/workdir 会话信息。"""
    text = "\n".join(_render_lines(_compact_banner(_INFO), 120))
    assert "zhipu" in text
    assert "glm-5.3-flash" in text
    assert ".jarvis" in text


def test_compact_banner_no_title_and_help_single_line():
    """面板内不重复标题；/help 命令提示必须完整单行（含 /exit 与退出）。"""
    lines = _render_lines(_compact_banner(_INFO), 300)
    text = "\n".join(lines)
    assert "J.A.R.V.I.S" not in text
    assert "Just A Rather" not in text
    help_lines = [l for l in lines if "/help" in l]
    # 必须恰好一行，且该行同时包含 /exit 与 退出（未被拆行或截断）
    assert len(help_lines) == 1
    assert "/exit" in help_lines[0] and "退出" in help_lines[0]
    assert "命令" in help_lines[0] and "实时聊天" in help_lines[0]


def test_help_hint_fits_content_width():
    """/help 行宽度 + 余量 ≤ 面板内容宽（动态宽度自洽性回归）。"""
    from rich.cells import cell_len

    content_w = _banner_total_width(_INFO) - 4
    assert cell_len(_HELP_HINT) <= content_w


def test_compact_banner_long_workdir_wraps_inside_panel():
    """workdir 路径超长时由面板自动换行，不撑破面板宽度。"""
    long_info = ("zhipu", "glm-5.3-flash",
                 r"E:\some\very\long\nested\directory\path\to\workspace\project-x")
    lines = _render_lines(_compact_banner(long_info), 300)
    assert max(len(l) for l in lines) <= _banner_total_width(long_info)
    # workdir 被拆行，但 /help 行仍必须完整单行。作者：aceFelix
    help_lines = [l for l in lines if "/help" in l]
    assert len(help_lines) == 1 and "退出" in help_lines[0]


# ---- 定格帧分流（小窗印反应炉 / 大窗印艺术字）----
# 作者：aceFelix


def _patch_terminal(monkeypatch, cols: int, rows: int) -> None:
    """mock 定格瞬间的真实终端尺寸（shutil.get_terminal_size）。"""
    monkeypatch.setattr(
        "shutil.get_terminal_size",
        lambda *a, **k: os.terminal_size((cols, rows)),
    )


def test_ascii_art_is_narrow_and_uniform():
    """艺术字：总宽 ≤ 60 列（小窗安全）、每行等宽、拼写完整、无全空行。

    全空行在缩窗 reflow 时仍带前导缩进会被折行，必须剔除（作者：aceFelix）。
    """
    lines = _ascii_art_lines("JARVIS")
    assert 4 <= len(lines) <= 7
    widths = {len(l) for l in lines}
    assert len(widths) == 1 and max(widths) <= 60
    assert all(l.strip() for l in lines)
    assert "█" in "".join(lines)


def test_final_renderable_small_window_keeps_reactor(monkeypatch):
    """场景 2：小窗定格 → 静态反应炉保留（盲文画布 + 会话信息）。"""
    _patch_terminal(monkeypatch, cols=100, rows=30)
    text = "\n".join(_render_lines(_final_renderable(_INFO, render_width=100), 100))
    # 盲文区段字符（反应炉画布）必须在场；字符范围 U+2800..U+28FF
    assert any("\u2800" <= ch <= "\u28FF" for ch in text)
    assert "J.A.R.V.I.S" in text
    assert "zhipu" in text


def test_static_frame_title_lines_separated(monkeypatch):
    """定格帧排版：J.A.R.V.I.S / 副标题各自单独一行，不同行拼接。"""
    _patch_terminal(monkeypatch, cols=100, rows=30)
    lines = [l.strip() for l in _render_lines(
        _final_renderable(_INFO, render_width=100), 100)]
    assert "J.A.R.V.I.S" in lines
    assert "Just A Rather Very Intelligent System" in lines
    # 主标题与副标题绝不同行；面板内也不得重复标题
    assert not any("J.A.R.V.I.S" in l and "Just A Rather" in l for l in lines)


def test_static_frame_same_size_as_animation(monkeypatch):
    """定格帧与动画最后一帧尺寸完全一致（用户要求，不得缩放）。"""
    _patch_terminal(monkeypatch, cols=100, rows=30)
    anim_geo = _calc_geo(100, 30)
    lines = _render_lines(
        _final_renderable(_INFO, render_width=100, anim_geo=anim_geo), 100)
    # 盲文画布行数必须与动画几何一致：(h+3)//4
    braille_rows = [l for l in lines
                    if any("\u2800" <= ch <= "\u28FF" for ch in l)]
    assert len(braille_rows) == (anim_geo.h + 3) // 4


def test_final_renderable_maximized_window_uses_art(monkeypatch):
    """场景 1：最大化定格 → 不印反应炉，改印艺术字（缩回小窗不折行）。"""
    _patch_terminal(monkeypatch, cols=180, rows=45)
    text = "\n".join(_render_lines(_final_renderable(_INFO, render_width=120), 180))
    # 盲文画布不应出现；方块艺术字与会话信息必须在场
    assert not any("\u2800" <= ch <= "\u28FF" for ch in text)
    assert "█" in text
    assert "zhipu" in text


def test_maximized_frame_centered_for_default_small_window(monkeypatch):
    """关键回归：最大化定格帧按 80 列预居中——缩回默认小窗后正中（作者：aceFelix）。

    旧实现按封顶 120 列居中（前导空格 40、总宽 84）超出默认小窗，
    缩回后被 reflow 吃掉前导空格、排版偏左（用户实测反馈）。
    测量按块边缘（min 前导 / max 右缘）：字形行自带内部前导/尾随空格，
    逐行测前导会把字形内部空格误计入。
    """
    _patch_terminal(monkeypatch, cols=180, rows=45)
    lines = [l for l in _render_lines(
        _final_renderable(_INFO, render_width=120), 180) if l.strip()]
    # 1) 所有可见行总宽 ≤ 默认小窗宽：缩回后不折行、前导空格不被 reflow 吞掉
    assert max(len(l.rstrip()) for l in lines) <= _RESTORE_TARGET_WIDTH
    # 2) 艺术字块在 80 列下居中：块左缘+块右缘 == 80（奇数余量容差 ±1）
    art_lines = [l for l in lines if "█" in l]
    left = min(len(l) - len(l.lstrip()) for l in art_lines)
    right = max(len(l.rstrip()) for l in art_lines)
    assert abs(left + right - _RESTORE_TARGET_WIDTH) <= 1
    # 3) 副标题独立居中（与艺术字宽度不同，不得共用缩进）
    sub_lines = [l for l in lines if "Just A Rather" in l]
    assert len(sub_lines) == 1
    sub = sub_lines[0]
    sub_lead = len(sub) - len(sub.lstrip())
    assert abs(2 * sub_lead + len(sub.strip()) - _RESTORE_TARGET_WIDTH) <= 1
    # 4) 信息面板同基准居中（取面板边框行；宽度同样去掉前导空格）
    box_lines = [l for l in lines if l.lstrip().startswith(_BOX_TOP_BOTTOM)]
    assert box_lines, "定格帧必须包含信息面板边框"
    for l in box_lines:
        lead = len(l) - len(l.lstrip())
        box_w = len(l.rstrip()) - lead
        assert abs(2 * lead + box_w - _RESTORE_TARGET_WIDTH) <= 1


def test_final_renderable_lines_wrap_safe_on_small_window(monkeypatch):
    """小窗定格帧在 80 列窗口下渲染不折行（总宽 ≤ 渲染宽）。"""
    _patch_terminal(monkeypatch, cols=80, rows=30)
    lines = [l for l in _render_lines(_final_renderable(_INFO, render_width=80), 80)
             if l.strip()]
    assert max(len(l.rstrip()) for l in lines) <= 80
