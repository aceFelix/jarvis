"""JARVIS 启动动画 —— 蓝色科幻粒子动态光环（方舟反应炉）。

致敬 Claude Code 的启动动画，换成 JARVIS 风格的蓝光圈：
- 同心圆环（外环 / 线圈环 / 内环）
- 旋转的线圈段
- 脉动的能量核心
- 轨道粒子

用 Rich Live 逐帧渲染盲文（Braille）字符画。每个盲文字符承载 2×4 像素，
能在终端里画出细腻的圆弧与光晕，比普通 ASCII 细腻 8 倍。

**自适应**: 根据终端窗口大小动态计算画布尺寸（正方形，最大 128px）。
**两阶段动画 + 静态横幅**:
  Phase-1 启动渐亮 (0~1.0s) —— power 从 0→1 渐亮
  Phase-2 展示循环 (1.0~3.5s) —— 满功率持续旋转/脉动/粒子跑动
  Live 退出后 console.print 输出最终静态画面（含 provider/model/workdir/命令信息，无频闪）

降级策略：终端太窄/太矮/非 TTY/无 Rich 时返回 False，调用方回退简单横幅。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
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
    else:  # banner / done —— 显示信息横幅
        if info is None:
            info = ("", "", "")
        provider, model, workdir = info
        banner_lines: list[Text] = []
        if provider or model:
            line1 = Text()
            line1.append("provider ", style="dim")
            line1.append(provider, style=_color_for(0.7))
            line1.append("   model ", style="dim")
            line1.append(model, style=_color_for(0.7))
            banner_lines.append(line1)
        if workdir:
            line2 = Text()
            line2.append("workdir ", style="dim")
            line2.append(workdir, style=_color_for(0.6))
            banner_lines.append(line2)
        if banner_lines:
            banner_lines.append(
                Text("/help 查看命令    /voice 语音对话    /exit 退出", style="dim")
            )
            from rich.panel import Panel
            banner_text = Text("\n").join(banner_lines)
            parts.append(Align.center(Panel(
                banner_text,
                border_style=_color_for(0.6),
                padding=(0, 1),
            )))
        else:
            parts.append(Align.center(
                Text("/help 查看命令    /voice 语音对话    /exit 退出", style="dim")
            ))
    return Group(*parts)


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

    def _fresh_geo() -> ReactorGeo | None:
        """读取当前终端尺寸，计算最新几何。"""
        try:
            s = console.size
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
        with Live(console=console, refresh_per_second=22,
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
                    # 进入 banner 阶段：立即画最终帧并退出 Live，
                    # 之后用 console.print 一次性输出静态画面，杜绝文字频闪
                    _draw(canvas, geo, total, 1.0)
                    live.update(_compose(canvas, geo, 1.0, total,
                                         phase="done", info=info))
                    time.sleep(0.08)
                    break

                _draw(canvas, geo, elapsed, power)
                live.update(_compose(canvas, geo, power, elapsed,
                                    phase=phase, info=phase_info))
                time.sleep(_FRAME_INTERVAL)
                frame += 1

    except KeyboardInterrupt:
        try:
            final_geo = _fresh_geo()
            if final_geo is not None:
                geo = final_geo
                canvas = BrailleCanvas(geo.w, geo.h)
            _draw(canvas, geo, total, 1.0)
            console.print(_compose(canvas, geo, 1.0, total,
                                   phase="done", info=info))
        except Exception:
            return False
    except Exception:
        return False

    # Live 已退出，用 console.print 输出最终静态横幅（单次绘制，不闪烁）
    try:
        final_geo = _fresh_geo()
        if final_geo is not None:
            geo = final_geo
            canvas = BrailleCanvas(geo.w, geo.h)
        _draw(canvas, geo, total, 1.0)
        console.print(_compose(canvas, geo, 1.0, total,
                               phase="done", info=info))
    except Exception:
        pass
    return True
