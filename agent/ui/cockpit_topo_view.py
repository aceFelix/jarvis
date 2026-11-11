"""拓扑视图共享组件（P3.6）：驾驶舱与撕出独立窗共用。

- ``TopoViewState``：缩放/平移/展开/选中的共享视图状态，外弹与收回零迁移；
- ``TopoView``：按 ``TopologyLayout`` 在任意 Tk Canvas 上绘制边/节点/+N 聚合，
  并承载缩放、平移、复位的视图变换；
- ``TearOffGesture``：撕出手势状态机（纯逻辑：区域 + 位移，无时间阈值）；
- 收回动画插值与 fit-to-view 计算（纯函数）。

窗口事件绑定、生命周期与动画帧驱动留在 ``cockpit_crawl.py`` /
``cockpit_topo_detach.py``；本模块不创建任何 Tk 窗口。

@author aceFelix
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from agent.ui.cockpit_crawl_topo import TopologyLayout
from agent.ui.hud_theme import (
    THEME_CRAWL as _C,
    FONT_SMALL,
    clip,
)

# 缩放范围与 P3.5 保持一致
MIN_SCALE = 0.55
MAX_SCALE = 3.0

# 手势阈值：8px 区分点击与拖动；80px / 出原区域区分真撕出与取消
ARM_PX = 8
DROP_PX = 80

# 收回动画规格（220ms / 14 帧 / ease-out）
ANIM_MS = 220
ANIM_FRAMES = 14


@dataclass
class TopoViewState:
    """驾驶舱与独立窗共享的拓扑视图状态。"""

    scale: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    expanded_layers: dict[int, int] = field(default_factory=dict)
    selected_url: str | None = None

    def reset(self) -> None:
        """新轮次或双击右键复位：回到初始视图。"""
        self.scale = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.expanded_layers = {}
        self.selected_url = None


def ease_out(t: float) -> float:
    """ease-out 三次曲线：``1-(1-t)³``，t 钳制到 [0,1]。"""
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - (1.0 - t) ** 3


def interpolate_rect(
    r0: Iterable[float], r1: Iterable[float], t: float
) -> tuple[float, float, float, float]:
    """把矩形 ``(x, y, w, h)`` 从 r0 按 ease-out 插值到 r1。"""
    e = ease_out(t)
    a = list(r0)
    b = list(r1)
    return tuple(a[i] + (b[i] - a[i]) * e for i in range(4))  # type: ignore[return-value]


def alpha_at(t: float) -> float:
    """收回动画透明度：1.0 → 0.2 渐隐（避免闪没）。"""
    return 1.0 - 0.8 * ease_out(t)


def fit_view(
    points: Iterable[tuple[float, float]],
    width: float,
    height: float,
    *,
    margin: float = 0.05,
) -> tuple[float, float, float] | None:
    """fit-to-view：让全部逻辑坐标点恰好落入画布（留边距）。

    Args:
        points: 逻辑坐标 ``(x, y)``（布局坐标减去原点偏移后的值）。
        width / height: 可用画布尺寸。
        margin: 四边留白比例。

    Returns:
        ``(scale, pan_x, pan_y)``；无点或画布非法时返回 None。
        scale 钳制在 0.2–MAX_SCALE（fit 允许小于交互缩放下限，否则大图永远铺不下）。
    """
    pts = list(points)
    if not pts or width <= 0 or height <= 0:
        return None
    min_x = min(p[0] for p in pts)
    max_x = max(p[0] for p in pts)
    min_y = min(p[1] for p in pts)
    max_y = max(p[1] for p in pts)
    box_w = max(1.0, max_x - min_x)
    box_h = max(1.0, max_y - min_y)
    usable_w = width * max(0.1, 1.0 - 2 * margin)
    usable_h = height * max(0.1, 1.0 - 2 * margin)
    scale = max(0.2, min(MAX_SCALE, min(usable_w / box_w, usable_h / box_h)))
    pan_x = (width - box_w * scale) / 2 - min_x * scale
    pan_y = (height - box_h * scale) / 2 - min_y * scale
    return scale, pan_x, pan_y


class TearOffGesture:
    """撕出手势状态机（纯逻辑，不碰 Tk）。

    用法：拓扑区内左键按下 ``press`` → B1-Motion 调 ``motion``（armed 转 True
    时创建幽灵）→ 松开调 ``release`` 得 ``detach`` / ``cancel`` / ``click``；
    Esc 或外部中断调 ``cancel``。
    """

    def __init__(self) -> None:
        self.active = False
        self.armed = False
        self._start = (0.0, 0.0)

    def press(self, x: float, y: float) -> None:
        """记录按下起点（只在拓扑区内调用）。"""
        self.active = True
        self.armed = False
        self._start = (float(x), float(y))

    def motion(self, x: float, y: float) -> bool:
        """拖动：累计位移 ≥8px 进入 armed；返回当前是否应显示幽灵。"""
        if not self.active:
            return False
        if not self.armed and self._distance(x, y) >= ARM_PX:
            self.armed = True
        return self.armed

    def release(self, x: float, y: float, *, inside_topo: bool) -> str:
        """松手判定：``detach`` 生成独立窗；``cancel`` 放弃；``click`` 走原点击。"""
        if not self.active:
            return "none"
        armed = self.armed
        dist = self._distance(x, y)
        self.cancel()
        if not armed:
            return "click"
        return "detach" if (not inside_topo or dist >= DROP_PX) else "cancel"

    def cancel(self) -> None:
        """取消本次跟踪（Esc / 窗口销毁 / 其他手势抢占）。"""
        self.active = False
        self.armed = False

    def _distance(self, x: float, y: float) -> float:
        return math.hypot(float(x) - self._start[0], float(y) - self._start[1])


class TopoView:
    """把 ``TopologyLayout`` 画到一块 Canvas 上，并管理共享视图变换。

    坐标约定与 P3.5 一致：布局以 ``x0=pad, y0=top`` 构建；绘制时先把节点
    坐标减去原点得到逻辑坐标，再施加 ``scale/pan`` 并加回原点。

    ``on_change`` 在视图状态变化后调用（由管理器重绘双方窗口）。
    """

    def __init__(self, canvas: Any, state: TopoViewState, on_change: Callable[[], None]) -> None:
        self.canvas = canvas
        self.state = state
        self.on_change = on_change
        self.topo_urls: dict[int, str] = {}
        self.aggregate_depths: dict[int, int] = {}
        self._pan_last: tuple[float, float] | None = None

    # ---- 坐标变换 ----

    def point(self, lx: float, ly: float, pad: float, top: float) -> tuple[float, float]:
        """逻辑坐标 → 当前画布坐标。"""
        return (
            pad + self.state.pan_x + lx * self.state.scale,
            top + self.state.pan_y + ly * self.state.scale,
        )

    # ---- 绘制 ----

    def draw(self, layout: TopologyLayout, *, pad: float, top: float) -> None:
        """按布局与当前视图变换画边、节点与 +N 聚合；选中节点加亮环。"""
        self.topo_urls = {}
        self.aggregate_depths = {}
        if layout.is_empty():
            self.canvas.create_text(pad, top + 4, text="— 无拓扑数据", fill=_C.dim, font=FONT_SMALL, anchor="w")
            return
        for parent, child in layout.edges:
            x1, y1 = self.point(parent.x - pad, parent.y - top, pad, top)
            x2, y2 = self.point(child.x - pad, child.y - top, pad, top)
            self.canvas.create_line(x1, y1, x2, y2, fill=_C.accent_dim, width=1)
        scale = self.state.scale
        for index, node in enumerate(layout.nodes):
            color = _C.ok if node.status == "ok" else (_C.fail if node.status == "error" else _C.dim)
            tags = ("topo_node", f"topo_idx:{index}")
            x, y = self.point(node.x - pad, node.y - top, pad, top)
            selected = node.url == self.state.selected_url
            if selected:
                self.canvas.create_oval(
                    x - 7 * scale, y - 7 * scale, x + 7 * scale, y + 7 * scale,
                    outline=_C.accent, width=1, tags=tags,
                )
            self.canvas.create_oval(
                x - 4 * scale, y - 4 * scale, x + 4 * scale, y + 4 * scale,
                fill=color, outline="", tags=tags,
            )
            self.canvas.create_text(
                x, y + 12 * scale, text=clip(node.label, 10),
                fill=_C.accent if selected else _C.dim, font=FONT_SMALL, anchor="n", tags=tags,
            )
            self.topo_urls[index] = node.url
        for index, item in enumerate(layout.aggregates):
            ax, ay = self.point(item["x"] - pad, item["y"] - top, pad, top)
            self.canvas.create_text(
                ax, ay, text=f"+{item['count']}", fill=_C.accent_dim, font=FONT_SMALL,
                anchor="center", tags=("topo_aggregate", f"aggregate_idx:{index}"),
            )
            self.aggregate_depths[index] = int(item.get("depth", 0))

    # ---- 缩放 / 平移 / 复位 ----

    def clamp_pan(self, width: float, height: float) -> None:
        """限制平移范围，保证总有一部分拓扑仍在可视区内。"""
        margin_x = width * 0.75
        margin_y = height * 0.75
        self.state.pan_x = max(-width * self.state.scale + margin_x,
                               min(width - margin_x, self.state.pan_x))
        self.state.pan_y = max(-height * self.state.scale + margin_y,
                               min(height - margin_y, self.state.pan_y))

    def zoom(self, focus_x: float, focus_y: float, factor: float,
             pad: float, top: float, width: float, height: float) -> None:
        """以鼠标位置为锚点缩放，并防止视图被拖到不可找回。"""
        old_scale = self.state.scale
        new_scale = max(MIN_SCALE, min(MAX_SCALE, old_scale * factor))
        if new_scale == old_scale:
            return
        local_x = focus_x - pad
        local_y = focus_y - top
        logical_x = (local_x - self.state.pan_x) / old_scale
        logical_y = (local_y - self.state.pan_y) / old_scale
        self.state.scale = new_scale
        self.state.pan_x = local_x - logical_x * new_scale
        self.state.pan_y = local_y - logical_y * new_scale
        self.clamp_pan(width, height)
        self.on_change()

    def pan_start(self, x: float, y: float, top: float, bottom: float) -> None:
        """右键/中键按下：在拓扑区内才开始平移。"""
        self._pan_last = (float(x), float(y)) if top <= y <= bottom else None

    def pan_to(self, x: float, y: float, width: float, height: float) -> None:
        """按鼠标位移平移。"""
        if self._pan_last is None:
            return
        last_x, last_y = self._pan_last
        self.state.pan_x += x - last_x
        self.state.pan_y += y - last_y
        self._pan_last = (float(x), float(y))
        self.clamp_pan(width, height)
        self.on_change()

    def reset_view(self) -> None:
        """双击右键：缩放、平移、展开复位（选中保留，符合高亮语义）。"""
        self.state.scale = 1.0
        self.state.pan_x = 0.0
        self.state.pan_y = 0.0
        self.state.expanded_layers = {}
        self._pan_last = None
        self.on_change()

    # ---- 点击 ----

    def hit(self, tags: Iterable[str]) -> tuple[str, Any] | None:
        """解析当前项 tag：节点 / 聚合；都不是返回 None。"""
        for tag in tags:
            if tag.startswith("topo_idx:"):
                index = int(tag.split(":", 1)[1])
                return ("node", self.topo_urls.get(index, ""))
            if tag.startswith("aggregate_idx:"):
                index = int(tag.split(":", 1)[1])
                depth = self.aggregate_depths.get(index)
                if depth is not None:
                    return ("aggregate", depth)
        return None

    def expand(self, depth: int) -> None:
        """+N 聚合点击：该层按批展开（每次 +8）。"""
        self.state.expanded_layers[depth] = self.state.expanded_layers.get(depth, 8) + 8
        self.on_change()


__all__ = [
    "ANIM_FRAMES",
    "ANIM_MS",
    "ARM_PX",
    "DROP_PX",
    "MAX_SCALE",
    "MIN_SCALE",
    "TearOffGesture",
    "TopoView",
    "TopoViewState",
    "alpha_at",
    "ease_out",
    "fit_view",
    "interpolate_rect",
]
