"""HUD 共享视觉规范：三舱色板、背景材质、辉光文本。

## 依据

`posters/jarvis-cyberpunk-design-philosophy.md`（Neon Doctrine）的三条硬规矩：

1. **三色纪律**：深渊黑承载一切；每个舱一个主色，且只有主色有权发光；
   语义色（成/败/警告）跨舱统一，绝不被主色带跑。
2. **所有亮色必须带辉光**：内锐外柔，先铺一层暗辉光再叠亮色，而不是单像素硬线。
3. **暗部必须保留材质**：网格 + 扫描线 + 颗粒噪点。光滑数字黑与粗粝 CRT 颗粒并置。

## 三个舱的主色（2026-11-12 先生指定）

| 舱 | 主色 | 谱系 |
|---|---|---|
| 任务驾驶舱 TASK | 电光青 | ``#00d4ff`` |
| 爬虫驾驶舱 CRAWL | 品红电流 | ``#ff2d78`` |
| 图片驾驶舱 IMAGE | 荧光绿 | ``#39ff88`` |

**任务舱是全局默认**：本模块末尾的旧常量名（``CYAN`` / ``GRID`` …）保留并绑定到
TASK 主题，既保证既有子组件（工具卡、代理卡、详情浮层）零改动换新色，也让新写的
组件默认落在主舱色板上。其它两舱在自己的模块里绑定各自主题。

@author aceFelix
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

__all__ = [
    "AMBER",
    "AMBER_DIM",
    "BG",
    "BG_PANEL",
    "CYAN",
    "CYAN_DIM",
    "DIM",
    "FONT_CARD_TITLE",
    "FONT_LABEL",
    "FONT_MONO",
    "FONT_NUM",
    "FONT_SMALL",
    "FONT_TEXT",
    "FONT_TITLE",
    "GREEN",
    "GRID",
    "HudTheme",
    "RED",
    "RULE",
    "TEXT",
    "THEME_CRAWL",
    "THEME_IMAGE",
    "THEME_TASK",
    "clip",
    "display_width",
    "draw_background",
    "draw_frame",
    "fmt_elapsed",
    "glow_text",
]


@dataclass(frozen=True)
class HudTheme:
    """一个舱的完整色板。

    Attributes:
        name: 舱名（TASK / CRAWL / IMAGE），只用于诊断。
        bg: 深渊黑底（微带该舱主色的色相，让整屏"染上体温"）。
        bg_panel: 面板底色（比 bg 亮一档，形成内嵌层次）。
        grid: 网格与刻度线色（极暗，材质而非信息）。
        scan: 扫描线色（比 grid 更暗，细到几乎不可见）。
        accent: 主色。全屏只有它与它的辉光有权发亮。
        accent_dim: 主色暗档（次要标注）。
        accent_glow: 辉光层（比 accent_dim 亮一档，画在 accent 下面做外柔）。
        rule: 结构线（外框 / 分区线 / 面板边）。必须明显可见又不能抢角标——
            这条线是"机器切面"，读者靠它分辨区块，2026-11-12 用户反馈
            "线条不够清晰"就是因为这里错用了 accent_dim（太暗）。
        text: 正文色（偏该舱色相的近白，不用纯白以免刺眼）。
        dim: 次要文字（说明、单位、占位）。必须保证在 bg / bg_panel 上可读。
        ok: 成功语义色（跨舱统一，不被主色带跑）。
        fail: 失败语义色。
        warn: 警告语义色。
    """

    name: str
    bg: str
    bg_panel: str
    grid: str
    scan: str
    accent: str
    accent_dim: str
    accent_glow: str
    rule: str
    text: str
    dim: str
    ok: str
    fail: str
    warn: str


#: 任务驾驶舱：电光青（#00d4ff 谱系）。全局默认主题。
THEME_TASK = HudTheme(
    name="TASK",
    bg="#03060a",
    bg_panel="#071019",
    grid="#08141c",
    scan="#040a11",
    accent="#00d4ff",
    accent_dim="#0b4d66",
    accent_glow="#007a99",
    rule="#46cdef",
    text="#e6f7ff",
    dim="#bcd9e6",
    ok="#33f0c0",
    fail="#ff4d6a",
    warn="#ffb347",
)

#: 爬虫驾驶舱：品红电流（#ff2d78 谱系）。
THEME_CRAWL = HudTheme(
    name="CRAWL",
    bg="#0a0409",
    bg_panel="#160811",
    grid="#170810",
    scan="#070305",
    accent="#ff2d78",
    accent_dim="#6d1035",
    accent_glow="#a01048",
    rule="#ff5c9a",
    text="#ffe8f1",
    dim="#e0b9c7",
    # 失败色刻意偏橙红：品红主色下的纯红会与 accent 撞成一片，看不出成/败
    fail="#ff6b4a",
    ok="#33f0c0",
    warn="#ffb347",
)

#: 图片驾驶舱：荧光绿（#39ff88）。ok 用更淡的薄荷绿与主色拉开层次。
THEME_IMAGE = HudTheme(
    name="IMAGE",
    bg="#030a07",
    bg_panel="#07160f",
    grid="#081811",
    scan="#030806",
    accent="#39ff88",
    accent_dim="#0f5c33",
    accent_glow="#17995a",
    rule="#59ffab",
    text="#e9fff3",
    dim="#c2e8d6",
    ok="#a9ffd0",
    fail="#ff4d6a",
    warn="#ffd24a",
)


# ---- 字体（三舱共用；不引入外部字体文件，按可用字体近似 Tektur/Silkscreen 的意图）----
# 2026-11-12：字号刻意保持原值。试过整体 +1，但各舱的文本宽度是按字符数 clip 的
# （clip(56) / clip(62) …），字号一涨就会顶出面板，必须同步重算所有截断宽度。
# 本轮可读性问题靠「提亮 + 纹理退后 + 结构线提亮」解决，不动字号。
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


# ---- 背景材质（三舱共用：网格 + 扫描线 + 颗粒 + 侧边刻度）----

_GRID_STEP = 28
_SCAN_STEP = 6
#: 噪点密度分母：w*h/4500 → 480×600 约 64 粒。再多会压掉小字的可读性
#: （2026-11-12 用户反馈「文字看不太清」，主因就是纹理压字）。
_GRAIN_DIVISOR = 4500
_GRAIN_SEED = 20261112


def draw_background(
    canvas: Any,
    theme: HudTheme,
    width: int,
    height: int,
    *,
    grain: bool = True,
) -> None:
    """铺满一个舱的背景：深渊黑底 + 扫描线 + 网格 + 颗粒 + 侧边刻度。

    为什么三个舱共用这一份：设计哲学要求"暗部必须保留材质"，材质若各写一份，
    三个窗口的颗粒密度与网格间距迟早会漂。主色由 ``theme`` 决定，材质恒定。

    调用方负责先 ``canvas.delete("all")``；本函数只铺底，不画边框与内容。

    Args:
        canvas: Tk Canvas。
        theme: 本舱色板。
        width: 画布宽（像素）。
        height: 画布高（像素）。
        grain: 是否铺颗粒噪点（``effects = off`` 时关掉，减少 item 数）。
    """
    canvas.create_rectangle(0, 0, width, height, fill=theme.bg, outline="")
    # 扫描线：比网格更暗、更密，叠加出 CRT 体温，不抢信息
    for y in range(0, height, _SCAN_STEP):
        canvas.create_line(0, y, width, y, fill=theme.scan)
    # 网格：坐标织物
    for x in range(0, width, _GRID_STEP):
        canvas.create_line(x, 0, x, height, fill=theme.grid)
    for y in range(0, height, _GRID_STEP):
        canvas.create_line(0, y, width, y, fill=theme.grid)
    # 颗粒：固定种子，保证每次重绘位置一致（否则整屏雪花闪烁）
    if grain:
        rng = random.Random(_GRAIN_SEED)
        count = max(24, (width * height) // _GRAIN_DIVISOR)
        for _ in range(count):
            gx = rng.randrange(0, max(1, width))
            gy = rng.randrange(0, max(1, height))
            canvas.create_line(gx, gy, gx + 1, gy, fill=theme.grid)
    # 侧边刻度：仪表盘感（左右各一列短刻度，向内 4px）
    for y in range(24, height - 24, 24):
        canvas.create_line(8, y, 12, y, fill=theme.grid)
        canvas.create_line(width - 12, y, width - 8, y, fill=theme.grid)


def draw_frame(
    canvas: Any,
    theme: HudTheme,
    width: int,
    height: int,
    *,
    inset: int = 6,
    corner: int = 16,
) -> None:
    """外框 + 四角 HUD 括号。

    边框走 ``accent_dim``（结构），四角走 ``accent``（发光主角）——设计哲学里
    "角落的 HUD 括号"是机器的切面线，它要比网格亮、比正文暗。

    Args:
        canvas: Tk Canvas。
        theme: 本舱色板。
        width: 画布宽。
        height: 画布高。
        inset: 外框内缩像素。
        corner: 四角括号的臂长。
    """
    canvas.create_rectangle(
        inset, inset, width - inset, height - inset, outline=theme.rule
    )
    for x, y, dx, dy in (
        (inset, inset, 1, 1),
        (width - inset, inset, -1, 1),
        (inset, height - inset, 1, -1),
        (width - inset, height - inset, -1, -1),
    ):
        canvas.create_line(x, y, x + dx * corner, y, fill=theme.accent, width=2)
        canvas.create_line(x, y, x, y + dy * corner, fill=theme.accent, width=2)


def glow_text(
    canvas: Any,
    x: float,
    y: float,
    content: Any,
    *,
    theme: HudTheme,
    font: Any = FONT_TEXT,
    anchor: str = "w",
    tags: tuple[str, ...] = (),
    glow_offset: int = 1,
    fill: str | None = None,
    glow: str | None = None,
) -> int:
    """画一层暗辉光再叠主色亮字（"内锐外柔"）。

    比单层硬色多一个 item，但这是"光即语言"的最低成本实现；只给大数字与标题用，
    正文一律普通 ``create_text``，否则整屏发光等于没有重点。

    Args:
        fill: 覆盖亮层颜色（默认 ``theme.accent``）。语义色数字（成/败）要传它，
            否则失败数会被念成主色。
        glow: 覆盖辉光层颜色（默认 ``theme.accent_glow``）。

    Returns:
        亮色那一层的 canvas item id（供后续 ``itemconfigure`` 改字）。
    """
    glow_color = glow or theme.accent_glow
    main_color = fill or theme.accent
    canvas.create_text(
        x + glow_offset,
        y + glow_offset,
        text=str(content),
        fill=glow_color,
        font=font,
        anchor=anchor,
        tags=tags,
    )
    return canvas.create_text(
        x, y, text=str(content), fill=main_color, font=font, anchor=anchor, tags=tags
    )


# ---- 向后兼容：旧常量 = 任务舱色板 ----
# 既有子组件（工具卡、代理卡、详情浮层）继续 import 这些名字即可获得主舱配色；
# 爬虫舱与图片舱在自己的模块里绑定 THEME_CRAWL / THEME_IMAGE。
BG = THEME_TASK.bg
BG_PANEL = THEME_TASK.bg_panel
GRID = THEME_TASK.grid
RULE = THEME_TASK.rule
CYAN = THEME_TASK.accent
CYAN_DIM = THEME_TASK.accent_dim
TEXT = THEME_TASK.text
DIM = THEME_TASK.dim
GREEN = THEME_TASK.ok
RED = THEME_TASK.fail
# 子代理/队友卡专用琥珀色：与任务舱主色区分"谁在干活"的层级（C3-lite.2）
AMBER = "#ffb347"
AMBER_DIM = "#6b4416"
