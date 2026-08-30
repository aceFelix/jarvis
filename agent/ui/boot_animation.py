"""JARVIS 启动动画 —— 蓝色科幻粒子动态光环（方舟反应炉）。

JARVIS 风格的蓝光圈：
- 同心圆环（外环 / 线圈环 / 内环）
- 旋转的线圈段
- 脉动的能量核心
- 轨道粒子

用 Rich Live 逐帧渲染盲文（Braille）字符画。每个盲文字符承载 2×4 像素，
能在终端里画出细腻的圆弧与光晕，比普通 ASCII 细腻 8 倍。

**自适应**: 根据终端窗口大小动态计算画布尺寸（正方形，最大 128px）。
**两阶段动画 + 定格帧分流**:
  Phase-1 启动渐亮 (0~1.0s) —— power 从 0→1 渐亮，画布随窗口尺寸动态调整
  Phase-2 展示循环 (1.0~3.5s) —— 满功率持续旋转/脉动/粒子跑动（按真实窗口尺寸居中渲染）
  Live 退出后按"定格瞬间的窗口大小"输出静态定格帧（终端输出一次成像、无法重绘）：
  - 小窗（宽 ≤ 120 列）：静态反应炉定格帧 + 标题 + 紧凑信息面板；
    尺寸与动画最后一帧完全一致（直接继承其几何参数，不缩放）；
    之后放大窗口其尺寸位置固定不变（终端特性）。
  - 大窗/最大化（宽 > 120 列）：大号 J.A.R.V.I.S 方块艺术字 + 紧凑信息面板；
    之后缩回小窗依然整齐不折行。

降级策略：终端太窄/太矮/非 TTY/无 Rich 时返回 False，调用方回退简单横幅。
"""

from __future__ import annotations

import math
import shutil
import time
from dataclasses import dataclass

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.text import Text

# ---- 盲文像素映射 ----
# 盲文字符 U+2800..U+28FF，每个字符是 2 列 × 4 行的点阵。
# 列 dx(0/1) × 行 dy(0..3) → 对应 bit：
_DOT_BITS = (
    (0x01, 0x02, 0x04, 0x08),  # dx=0（左列）
    (0x10, 0x20, 0x40, 0x80),  # dx=1（右列）
)
_BRAILLE_BASE = 0x2800


# ---- JARVIS 蓝色光谱（从暗到亮）----
def _color_for(intensity: float) -> str:
    """按亮度映射到 JARVIS 蓝色谱的一个十六进制颜色。"""
    if intensity >= 0.85:
        return "#bfe8ff"  # 近白青 —— 核心高光
    if intensity >= 0.65:
        return "#5bc8ff"  # 亮青蓝
    if intensity >= 0.45:
        return "#2f8fe0"  # 蓝
    if intensity >= 0.28:
        return "#1c5a96"  # 深蓝
    return "#133352"  # 深海蓝


class BrailleCanvas:
    """2D 像素画布，渲染成盲文字符。每个像素带 0~1 的亮度（max 混合）。"""

    __slots__ = ("w", "h", "_buf")

    def __init__(self, width_px: int, height_px: int) -> None:
        self.w = width_px
        self.h = height_px
        self._buf: list[list[float]] = [[0.0] * width_px for _ in range(height_px)]

    def clear(self) -> None:
        for row in self._buf:
            for i in range(len(row)):
                row[i] = 0.0

    def _set(self, x: int, y: int, v: float) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            if v > self._buf[y][x]:
                self._buf[y][x] = v

    def _add(self, x: int, y: int, v: float) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self._buf[y][x] = min(1.0, self._buf[y][x] + v)

    # ---- 图元 ----

    def ring(self, cx: float, cy: float, r: float, intensity: float = 1.0,
             width: float = 1.0, step: float = 1.0) -> None:
        """圆环（环形带），逐像素填充，无间隙。step>1 时跳步加速大画布。"""
        ri = r - width
        ro = r + width
        x0, x1 = int(cx - ro - 1), int(cx + ro + 2)
        y0, y1 = int(cy - ro - 1), int(cy + ro + 2)
        st = max(1.0, step)
        for y in range(y0, y1):
            ey = float(y)
            for x in range(x0, x1, int(st)):
                d = math.hypot(x - cx, ey - cy)
                if ri <= d <= ro:
                    edge = 1.0 - abs(d - r) / (width + 0.001) * 0.4
                    self._set(x, y, intensity * max(0.4, edge))

    def arc(self, cx: float, cy: float, r: float, a0: float, a1: float,
            intensity: float = 1.0, width: float = 1.0) -> None:
        """一段圆弧（沿线圈段）。"""
        span = a1 - a0
        steps = max(8, int(abs(span) * r * 2.0))
        for i in range(steps + 1):
            a = a0 + span * i / steps
            ca, sa = math.cos(a), math.sin(a)
            self._set(round(cx + r * ca), round(cy + r * sa), intensity)
            if width > 0:
                self._set(round(cx + (r - 1) * ca), round(cy + (r - 1) * sa),
                          intensity * 0.6)
                self._set(round(cx + (r + 1) * ca), round(cy + (r + 1) * sa),
                          intensity * 0.6)

    def disc(self, cx: float, cy: float, r: float, intensity: float = 1.0,
             glow: bool = True) -> None:
        """实心圆，带径向衰减发光。"""
        if r <= 0:
            self._set(round(cx), round(cy), intensity)
            return
        x0, x1 = int(cx - r - 1), int(cx + r + 2)
        y0, y1 = int(cy - r - 1), int(cy + r + 2)
        for y in range(y0, y1):
            ey = float(y)
            for x in range(x0, x1):
                d = math.hypot(x - cx, ey - cy)
                if d <= r:
                    falloff = (1.0 - (d / r) * 0.35) if glow else 1.0
                    self._set(x, y, intensity * falloff)

    def point(self, cx: float, cy: float, intensity: float = 1.0) -> None:
        """亮点 + 十字/对角光晕。"""
        ix, iy = round(cx), round(cy)
        self._set(ix, iy, intensity)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            self._add(ix + dx, iy + dy, intensity * 0.45)
        for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            self._add(ix + dx, iy + dy, intensity * 0.2)

    # ---- 渲染 ----

    def render(self) -> Text:
        """渲染成带逐字配色的 Rich Text。"""
        text = Text(no_wrap=True)
        cols = (self.w + 1) // 2
        rows = (self.h + 3) // 4
        for gr in range(rows):
            for gc in range(cols):
                bits = 0
                vmax = 0.0
                vsum = 0.0
                non = 0
                for dx in (0, 1):
                    for dy in (0, 1, 2, 3):
                        px = gc * 2 + dx
                        py = gr * 4 + dy
                        if 0 <= px < self.w and 0 <= py < self.h:
                            v = self._buf[py][px]
                            if v > 0.15:
                                bits |= _DOT_BITS[dx][dy]
                                vsum += v
                                if v > vmax:
                                    vmax = v
                                non += 1
                if bits == 0:
                    text.append(" ")
                else:
                    ch = chr(_BRAILLE_BASE + bits)
                    avg = vsum / non if non else 0.0
                    inten = vmax * 0.6 + avg * 0.4
                    text.append(ch, style=_color_for(inten))
            if gr < rows - 1:
                text.append("\n")
        return text


# ---- 反应炉几何参数 ----
@dataclass(frozen=True)
class ReactorGeo:
    """缩放后的反应炉几何参数（基于基准 56px 画布线性缩放）。"""
    w: int
    h: int
    cx: float
    cy: float
    r_out: float      # 外环
    r_coil: float     # 线圈环
    r_in: float       # 内环
    r_core: float     # 核心
    r_part: float     # 轨道粒子
    step: float        # 大画布时 ring/disc 跳步系数


# 基准几何（56px 画布）
_BASE_W = 56
_BASE_R_OUT = 23.0
_BASE_R_COIL = 18.0
_BASE_R_IN = 12.0
_BASE_R_CORE = 5.5
_BASE_R_PART = 26.0


def _calc_geo(cons_width: int, cons_height: int) -> ReactorGeo | None:
    """根据终端尺寸计算最佳盲文画布大小和反应炉几何。

    盲文: 每字符=2列×4行像素。
    底部预留 ~8 行给标题/信息文本行。
    返回 None 表示终端太小不适合播放动画。
    """
    # 最小门槛
    if cons_width < 34 or cons_height < 26:
        return None

    # 可用于画布的区域（底部预留：标题2行 + Panel边框2行 + 信息3行 + 间距4行 ≈ 11行）
    avail_rows = cons_height - 11
    if avail_rows < 10:
        return None

    # 像素尺寸（取正方形，以短边为准转像素）
    px_w = cons_width * 2          # 盲文列→像素（每字符 2px 宽）
    px_h = avail_rows * 4          # 盲文行→像素（每字符 4px 高）
    side = min(px_w, px_h)         # 短边

    # 不占满: 反应炉只吃可用高度的 _CANVAS_RATIO，剩余留给命令行输入区。
    # 若取 100%，小窗口下动画结束后的静态横幅会占满整个视口，
    # 提示符和后续对话立刻把横幅顶进 scrollback（"图像往上跑"）。
    # 0.72: 留出约 28% 给命令行，小窗口下反应炉随之等比变小，不被顶走。
    side = side * _CANVAS_RATIO

    # clamp: 最小 44，最大 128（太大逐像素遍历会卡）
    side = max(44, min(128, int(side)))

    scale = side / _BASE_W
    step = max(1.0, scale / 2.0)   # 大画布 ring/disc 跳步

    return ReactorGeo(
        w=side, h=side,
        cx=(side - 1) / 2.0,
        cy=(side - 1) / 2.0,
        r_out=_BASE_R_OUT * scale,
        r_coil=_BASE_R_COIL * scale,
        r_in=_BASE_R_IN * scale,
        r_core=_BASE_R_CORE * scale,
        r_part=_BASE_R_PART * scale,
        step=step,
    )


def _smoothstep(x: float) -> float:
    x = 0.0 if x < 0 else (1.0 if x > 1 else x)
    return x * x * (3 - 2 * x)


def _draw(canvas: BrailleCanvas, geo: ReactorGeo,
          elapsed: float, power: float) -> None:
    """绘制一帧方舟反应炉。elapsed=秒，power=0~1 启动进度。"""
    canvas.clear()
    cx, cy = geo.cx, geo.cy
    st = geo.step
    r_out, r_coil, r_in, r_core, r_part = (
        geo.r_out, geo.r_coil, geo.r_in, geo.r_core, geo.r_part,
    )

    # 外层氛围光晕
    if power > 0.05:
        canvas.ring(cx, cy, r_out + 2.5 * (geo.w / _BASE_W),
                    intensity=0.18 * power, width=1.0, step=st)

    # 外环
    if power > 0.1:
        canvas.ring(cx, cy, r_out, intensity=0.5 * power,
                    width=1.0, step=st)

    # 旋转线圈段（8 段，1.3s 一圈）
    if power > 0.3:
        spin = elapsed * (2 * math.pi / 1.3)
        n = 8
        seg = (2 * math.pi / n) * 0.6
        for i in range(n):
            a0 = spin + i * (2 * math.pi / n)
            canvas.arc(cx, cy, r_coil, a0, a0 + seg,
                       intensity=0.85 * power, width=1.0)
        # 段端点亮点
        for i in range(n):
            a = spin + i * (2 * math.pi / n)
            canvas.point(
                cx + r_coil * math.cos(a),
                cy + r_coil * math.sin(a),
                0.9 * power,
            )

    # 内环（双圈）
    if power > 0.5:
        canvas.ring(cx, cy, r_in, intensity=0.7 * power,
                    width=1.0, step=st)
        canvas.ring(cx, cy, r_in - 2.0 * (geo.w / _BASE_W),
                    intensity=0.35 * power, width=0.8, step=st)

    # 核心（~2.2Hz 脉动）
    if power > 0.65:
        pulse = 0.5 + 0.5 * math.sin(elapsed * 2 * math.pi * 2.2)
        core_r = r_core + 0.6 * pulse * (geo.w / _BASE_W)
        canvas.disc(cx, cy, core_r + 1.8 * (geo.w / _BASE_W),
                    intensity=0.45 * power)
        canvas.disc(cx, cy, core_r,
                    intensity=(0.8 + 0.2 * pulse) * power)
        canvas.disc(cx, cy, core_r * 0.45, intensity=1.0 * power)

    # 轨道粒子（3 颗，反向不同速）
    if power > 0.8:
        for i in range(3):
            a = -elapsed * (2 * math.pi / (1.6 + 0.25 * i)) + i * (2 * math.pi / 3)
            canvas.point(
                cx + r_part * math.cos(a),
                cy + r_part * math.sin(a),
                0.95,
            )


def _compose(canvas: BrailleCanvas, geo: ReactorGeo,
             power: float, elapsed: float, *,
             phase: str, info: tuple[str, str, str] | None = None) -> Group:
    """把反应炉 + 标题/状态 组合成一组居中渲染块。"""
    parts: list = [Align.center(canvas.render())]

    title = Text()
    title.append("J.A.R.V.I.S",
                 style="bold blue")
    parts.append(Align.center(title))

    parts.append(Align.center(
        Text("Just A Rather Very Intelligent System", style="dim size=12")
    ))

    if phase == "boot":
        dots = "." * (int(elapsed * 5) % 4)
        parts.append(Align.center(Text(f"系统启动中{dots}", style="dim cyan")))
    elif phase == "showcase":
        parts.append(Align.center(
            Text("系统就绪", style=f"cyan {_color_for(0.7 + 0.3 * power)}")
        ))
    # done 阶段不再走此函数：定格帧改用 _static_reactor_frame（小窗）
    # 或 _ascii_art_banner（大窗）——画面在 Live 退出瞬间按窗口大小定格，
    # 之后不再随窗口变化。作者：aceFelix
    return Group(*parts)


# 命令提示行：用户要求必须单行展示。分隔符用单空格夹 ·（旧版双空格
# 版 60 cells 超出面板内容宽被拆行；缩减至 54 cells 并配动态面板宽度）。作者：aceFelix
_HELP_HINT = "/help 命令 · /voice 语音 · /talk 实时聊天 · /exit 退出"
# 面板内容宽下限与余量（作者：aceFelix）。下限保证短 provider/model 时面板不局促；
# 余量吸收 cell_len 对 CJK 歧义宽字符（如 · ）与终端实际渲染的偏差。
_BANNER_MIN_CONTENT_WIDTH = 40
_BANNER_WIDTH_SLACK = 4


def _banner_content_width(info: tuple[str, str, str]) -> int:
    """按会话信息动态计算面板内容宽（作者：aceFelix）。

    必须单行的内容（/help 命令提示、provider/model 行）用 cell_len 实测，
    取最长者 + 余量作为面板内容宽——保证它们永远一行装得下；
    workdir 行允许在面板内自动换行（不计入）。
    """
    from rich.cells import cell_len

    provider, model, _workdir = info
    widths = [cell_len(_HELP_HINT)]
    if provider or model:
        widths.append(cell_len(f"provider {provider}   model {model}"))
    return max(max(widths) + _BANNER_WIDTH_SLACK, _BANNER_MIN_CONTENT_WIDTH)


def _banner_total_width(info: tuple[str, str, str]) -> int:
    """面板外框总宽 = 内容宽 + 左右 padding 各 1 + 左右边框各 1。作者：aceFelix"""
    return _banner_content_width(info) + 4

# 居中缩进上限（列）。终端无法预知后续缩窗，缩进越大越居中，
# 但总行宽（横幅+缩进）超过缩窗后宽度就会折行错乱——封顶 20 列兼顾观感与安全。
# 作者：aceFelix
_BANNER_MAX_PAD = 20


def _compact_banner(info: tuple[str, str, str]) -> Any:
    """紧凑会话信息面板（作者：aceFelix）。

    面板宽度按内容动态计算（_banner_total_width）：/help 命令提示与
    provider/model 行必须单行完整展示（绝不用 no_wrap——它只截断不换行），
    workdir 超长时由 Panel 自动换行（用户预期）。标题由调用方单独成行，
    面板内不重复。用于：定格帧信息区、异常/中断路径兜底横幅。
    """
    provider, model, workdir = info

    lines: list[Text] = []
    if provider or model:
        line1 = Text()
        line1.append("provider ", style="dim")
        line1.append(provider, style=_color_for(0.7))
        line1.append("   model ", style="dim")
        line1.append(model, style=_color_for(0.7))
        lines.append(line1)
    if workdir:
        line2 = Text()
        line2.append("workdir  ", style="dim")
        line2.append(workdir, style=_color_for(0.6))
        lines.append(line2)
    lines.append(Text())
    lines.append(Text(_HELP_HINT, style="dim"))

    from rich.panel import Panel
    # 居中由调用方按真实窗口宽 + 安全缩进决定，这里只返回按内容定宽的面板。
    # 作者：aceFelix
    return Panel(
        Text("\n").join(lines),
        width=_banner_total_width(info),
        border_style=_color_for(0.6),
        padding=(0, 1),
    )


# 动画时间线常量（总时长 ≤ 8s）
_BOOT_DURATION = 1.0        # Phase-1 启动渐亮时长
_SHOWCASE_DURATION = 3.5    # Phase-1+Phase-2 总时长（展示循环 ~2.5s）
_BANNER_DURATION = 4.5      # Live 总时长；之后退出 Live 用 console.print 输出静态画面（杜绝文字频闪）
_FRAME_INTERVAL = 0.045     # 帧间隔 (~22fps)

# 反应炉画布占可用高度的比例（0~1）。
# 取 <1 让反应炉不占满视口，下方留出命令行输入区——
# 否则小窗口下动画结束后的静态横幅会占满，提示符与后续对话把它顶进 scrollback（"图像往上跑"）。
# 0.72 = 约 28% 留给命令行；想要反应炉更小就调小此值。
_CANVAS_RATIO = 0.72

# 大窗（最大化）判定阈值（列）。与 REPL 封顶宽度 120 列对齐：
# 定格瞬间真实宽度超过此值即视为最大化窗口——改印 ASCII 艺术字横幅，
# 用户随后缩回默认小窗时窄内容不折行；阈值内才印静态反应炉定格帧。
# 作者：aceFelix
_LARGE_TERMINAL_WIDTH = 120

# 大窗定格帧的居中基准宽（列）：终端业界默认窗口尺寸 80 列（作者：aceFelix）。
# 最大化定格时按此宽度预排版（而非按封顶 120 列居中）：
# 旧实现艺术字居中在 120 列（前导空格 40、总宽 84）超出默认小窗宽度，
# 缩回小窗后被 reflow 吃掉前导空格、排版偏左；按 80 列预居中后，
# 缩回默认小窗恰好居中（最大化状态下轻微偏左，同既有取舍）。
_RESTORE_TARGET_WIDTH = 80

# ---- J.A.R.V.I.S 方块艺术字（7 行字形）----
# 每个字形定宽等长（J 7 列、A/R 8 列、V/S 9 列、I 3 列，逐行校验等长后拼接），
# 总宽约 53 列 ≤ 常见默认小窗 80 列——缩窗不折行。
# 大窗定格帧专用：最大化时不印反应炉（防之后缩窗 reflow 错乱），
# 改印此艺术字 + 紧凑信息面板。作者：aceFelix
_ART_GLYPHS: dict[str, tuple[str, ...]] = {
    "J": (
        "     ██╗",
        "     ██║",
        "     ██║",
        "██   ██║",
        "╚█████╔╝",
        " ╚════╝ ",
        "        ",
    ),
    "A": (
        " █████╗ ",
        "██╔══██╗",
        "███████║",
        "██╔══██║",
        "██║  ██║",
        "╚═╝  ╚═╝",
        "        ",
    ),
    "R": (
        "██████╗ ",
        "██╔══██╗",
        "██████╔╝",
        "██╔══██╗",
        "██║  ██║",
        "╚═╝  ╚═╝",
        "        ",
    ),
    "V": (
        "██╗   ██╗",
        "██║   ██║",
        "██║   ██║",
        "╚██╗ ██╔╝",
        " ╚███╔██╝",
        "  ╚══╝═╝ ",
        "         ",
    ),
    "I": (
        "██╗",
        "██║",
        "██║",
        "██║",
        "██║",
        "╚═╝",
        "   ",
    ),
    "S": (
        "███████╗ ",
        "██╔════╝ ",
        "╚█████╗  ",
        " ╚═══██╗ ",
        "██████╔╝ ",
        "╚═════╝  ",
        "         ",
    ),
}

# 艺术字字形间的空列数（分隔字母，过大则总宽超出小窗安全宽度）。作者：aceFelix
_ART_GAP = 1


def play_boot_animation(console: Console | None,
                        provider: str, model: str, workdir: str) -> bool:
    """播放启动动画（自适应尺寸 + 两阶段展示 + 静态横幅）。

    Phase-1 (0~1.0s):   启动渐亮 —— 反应炉从暗到亮逐步点亮
    Phase-2 (1.0~3.5s): 展示循环 —— 满功率持续旋转/脉动/粒子轨道
    3.5s 后 Live 退出，console.print 输出最终静态画面（含信息文本，无频闪）。
    总时长 ~4.5s，远低于 8s 上限。

    成功返回 True；环境不支持时返回 False（调用方回退简单 Panel）。
    """
    if console is None:
        return False
    # 初始几何（启动时终端大小）
    try:
        init_size = console.size
        is_tty = console.is_terminal
    except Exception:
        return False

    # 动画阶段用无封顶的独立 Console：传入的 console 是 REPL 的封顶 Console
    #（≤120 列），直接用它会让最大化窗口的动画偏左且偏小。
    # Live 是 transient（退出即清除），不会残留超宽行到 scrollback，用真实尺寸安全。
    # 作者：aceFelix
    try:
        live_console = Console()
        if not live_console.is_terminal:
            live_console = console
    except Exception:
        live_console = console

    def _fresh_geo() -> ReactorGeo | None:
        """读取当前终端真实尺寸，计算最新几何（供 Live 动画随窗口缩放）。"""
        try:
            s = live_console.size
        except Exception:
            return None
        return _calc_geo(s.width, s.height)

    geo = _fresh_geo()
    if geo is None:
        return False

    canvas = BrailleCanvas(geo.w, geo.h)
    start = time.monotonic()
    total = _BANNER_DURATION
    info = (provider, model, workdir)
    _resize_check_every = 8  # 每 N 帧检测一次窗口缩放（~0.36s，够灵敏又不浪费）

    try:
        # screen=False（主屏播放）。
        # 注: 曾尝试 screen=True 隔离 scrollback，但 Git Bash/mintty 下进入
        # alternate screen 会抛异常，导致整个动画被 except 吞掉、回退成简单 Panel
        # （反应炉不显示）。scrollback 污染的真正根源是画布占满视口、resize 时
        # 多余行溢出——已通过 _CANVAS_RATIO 缩小画布从源头缓解，故此处保持 False。
        with Live(console=live_console, refresh_per_second=22,
                  transient=True, screen=False) as live:
            frame = 0
            while True:
                elapsed = time.monotonic() - start
                if elapsed >= total:
                    break

                # 窗口缩放自适应：每隔几帧重读终端尺寸，变化则重建画布
                if frame % _resize_check_every == 0:
                    new_geo = _fresh_geo()
                    if new_geo is not None and (
                        new_geo.w != geo.w or new_geo.h != geo.h
                    ):
                        geo = new_geo
                        canvas = BrailleCanvas(geo.w, geo.h)

                power = _smoothstep(min(1.0, elapsed / 0.6))

                # 判断阶段（仅 boot / showcase 在 Live 内刷新）
                if elapsed < _BOOT_DURATION:
                    phase = "boot"
                    phase_info = None
                elif elapsed < _SHOWCASE_DURATION:
                    phase = "showcase"
                    phase_info = None
                else:
                    # 进入 banner 阶段：直接退出 Live（transient 清除动画），
                    # 之后输出紧凑静态横幅——不再印反应炉大画布，
                    # 避免大块静态内容留 scrollback 被缩窗折行错乱。作者：aceFelix
                    break

                _draw(canvas, geo, elapsed, power)
                live.update(_compose(canvas, geo, power, elapsed,
                                    phase=phase, info=phase_info))
                time.sleep(_FRAME_INTERVAL)
                frame += 1

    except KeyboardInterrupt:
        # 中断路径无定格帧：直接输出紧凑横幅兜底（窄宽防折行）。作者：aceFelix
        try:
            console.print(_banner_with_safe_center(info, console.size.width))
        except Exception:
            return False
    except Exception:
        return False

    # Live 已退出：按定格瞬间的真实窗口大小输出最终静态画面（一次成像，无法重绘）。
    # 定格帧直接继承动画最后一帧的几何参数（含缩放跟踪），保证反应炉尺寸与
    # 动画完全一致（用户要求）；大窗/最大化仍改印 ASCII 艺术字。作者：aceFelix
    try:
        console.print(_final_renderable(info, console.size.width, anim_geo=geo))
    except Exception:
        # 定格帧渲染异常 → 退化为紧凑横幅，保证横幅必有输出。作者：aceFelix
        try:
            console.print(_banner_with_safe_center(info, console.size.width))
        except Exception:
            pass
    return True


def _banner_with_safe_center(info: tuple[str, str, str],
                             render_width: int | None = None,
                             assume_width: int | None = None) -> Any:
    """紧凑横幅 + 安全居中（作者：aceFelix）。

    终端无法预知用户之后会不会缩窗：缩进越大越居中，但总行宽超过
    缩窗后宽度就会折行错乱。因此缩进取三重上限：
    min(居中偏移, _BANNER_MAX_PAD, 渲染宽-面板宽)——
    最大化窗口下只是轻微偏左（可接受），窄窗口不折行，换取缩窗永不乱。
    assume_width：大窗定格帧传入 _RESTORE_TARGET_WIDTH，按"缩回默认小窗后"
    的目标宽度预居中，缩回后恰好正中（作者：aceFelix）。
    """
    if assume_width is not None:
        real_w = assume_width
    else:
        try:
            real_w = shutil.get_terminal_size().columns
        except Exception:
            real_w = 80
    box_w = _banner_total_width(info)  # 面板外框总宽（含 padding 与边框）
    candidates = [(real_w - box_w) // 2, _BANNER_MAX_PAD]
    # 窄窗口约束：总宽不得超过实际渲染宽度（否则当场折行）
    if render_width is not None:
        candidates.append(render_width - box_w)
    pad = max(0, min(candidates))
    # 左侧 pad 列缩进近似居中；(1,0,0,pad) = 上/右/下/左 内边距方向外边距。
    return Padding(_compact_banner(info), (1, 0, 0, pad))


def _ascii_art_lines(word: str = "JARVIS") -> list[str]:
    """拼接方块字符艺术字行（作者：aceFelix）。

    按字形定宽拼接、字形间空 _ART_GAP 列。拼接前逐行校验等长，
    字形维护失误（行长不一）会直接抛错，被单测兜住。
    拼接后去掉全空行（字形顶部/底部的纯空格行）：缩窗 reflow 时
    空白行仍带前导缩进，会被终端按新宽度折成多行、打散居中排版。
    作者：aceFelix
    """
    glyphs = [_ART_GLYPHS[c] for c in word.upper()]
    for glyph in glyphs:
        assert len(set(map(len, glyph))) == 1, "艺术字字形各行宽度不一致"
    n_rows = len(glyphs[0])
    gap = " " * _ART_GAP
    rows = [gap.join(g[r] for g in glyphs) for r in range(n_rows)]
    return [r for r in rows if r.strip()]


def _ascii_art_banner(info: tuple[str, str, str],
                      render_width: int | None = None) -> Any:
    """大窗（最大化）定格帧：J.A.R.V.I.S 方块艺术字 + 紧凑信息面板（作者：aceFelix）。

    最大化窗口完成启动时，不印反应炉大画布：终端输出一次成像、无法重绘，
    用户随后缩回默认小窗时宽画布会被折行重排错乱。改印 ≤53 列宽的方块艺术字，
    缩窗后依然整齐。居中基准取 _RESTORE_TARGET_WIDTH（80 列）而非封顶宽：
    旧实现按 120 列居中导致前导空格 40/总宽 84 超出默认小窗，缩回后排版偏左；
    按 80 列预居中后缩回默认小窗恰好正中。艺术字块、副标题、信息面板宽度各异，
    各自按 80 列基准独立计算缩进（共用同一缩进会让窄行偏左——用户实测反馈）。
    窗口再放大时画面尺寸位置固定——终端本质限制，可接受。
    """
    art = Text()
    lines = _ascii_art_lines("JARVIS")
    for i, line in enumerate(lines):
        # 亮度自上而下渐亮，呼应反应炉点亮过程。作者：aceFelix
        art.append(line + "\n", style=_color_for(0.45 + 0.08 * i))
    subtitle = "Just A Rather Very Intelligent System"

    # 逐元素按 80 列基准预居中，并受渲染宽约束防当场折行。
    # 不用 Align.center：它按封顶 console 宽（120）居中，前导空格会超出小窗。
    # 作者：aceFelix
    from rich.cells import cell_len

    def _pre_center_pad(width: int) -> int:
        candidates = [(_RESTORE_TARGET_WIDTH - width) // 2]
        if render_width is not None:
            candidates.append(render_width - width)
        return max(0, min(candidates))

    art_pad = _pre_center_pad(max(len(l) for l in lines))
    sub_pad = _pre_center_pad(cell_len(subtitle))

    return Group(
        Padding(art, (1, 0, 0, art_pad)),
        Padding(Text(subtitle, style="dim"), (0, 0, 0, sub_pad)),
        Text(),
        _banner_with_safe_center(info, render_width=render_width,
                                 assume_width=_RESTORE_TARGET_WIDTH),
    )


def _static_reactor_frame(geo: ReactorGeo, info: tuple[str, str, str]) -> Group:
    """小窗定格帧：满功率静态反应炉 + 标题三行 + 紧凑信息面板（作者：aceFelix）。

    几何参数直接继承动画最后一帧（_calc_geo 计算、含播放中的缩放跟踪），
    定格帧与动画尺寸完全一致。取动画中间时刻（2.0s）的满功率姿态逐元素
    绘制一帧：线圈段、内环、核心与轨道粒子全部可见，定格后永不变化。
    画布最宽 64 列（128px 上限），留在 scrollback 缩窗不折行。标题排版：
    J.A.R.V.I.S / 副标题 / 系统就绪各自单独一行；信息面板内不重复标题。
    之后放大窗口时画面尺寸位置固定——终端本质限制，可接受。
    """
    canvas = BrailleCanvas(geo.w, geo.h)
    _draw(canvas, geo, elapsed=2.0, power=1.0)

    return Group(
        Align.center(canvas.render()),
        Align.center(Text("J.A.R.V.I.S", style="bold blue")),
        Align.center(Text("Just A Rather Very Intelligent System", style="dim")),
        Align.center(Text("系统就绪", style="cyan")),
        Text(),
        _banner_with_safe_center(info),
    )


def _final_renderable(info: tuple[str, str, str],
                      render_width: int | None = None,
                      anim_geo: ReactorGeo | None = None) -> Any:
    """启动完成定格帧分流（作者：aceFelix）。

    读取定格瞬间的真实终端宽度（shutil 直读，不受封顶 Console 影响）：
    - ≤ _LARGE_TERMINAL_WIDTH 列（默认小窗）→ 静态反应炉定格帧；
      几何优先用 anim_geo（动画最后一帧，尺寸与动画完全一致），
      无 anim_geo 时才回退 _calc_geo 重算；之后放大窗口画面固定不变（场景 2）。
    - > _LARGE_TERMINAL_WIDTH 列（最大化）→ ASCII 艺术字 + 信息面板；
      之后缩回默认小窗不折行、画面整齐（场景 1）。
    """
    try:
        cols, rows = shutil.get_terminal_size().columns, shutil.get_terminal_size().lines
    except Exception:
        cols, rows = 80, 24

    if cols <= _LARGE_TERMINAL_WIDTH:
        geo = anim_geo if anim_geo is not None else _calc_geo(cols, rows)
        if geo is not None:
            return _static_reactor_frame(geo, info)
    return _ascii_art_banner(info, render_width=render_width)
