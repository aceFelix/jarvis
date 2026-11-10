"""拓扑撕出（P3.6）：独立查看窗、拖拽幽灵预览与收回动画。

- ``TopoDetachedWindow``：960×720 可自由拉伸的独立拓扑窗，保留缩放/平移/
  复位/+N 展开/点击高亮，新增「适应画布」；点 ✕ 以 220ms 动画飞回驾驶舱。
- ``TearOffGhost``：撕出拖动中跟随光标的半透明预览（不抢焦点）。

窗口只依赖 controller（CrawlManager）提供的四个接口：``dock_rect()``、
``undock()``、``rerender_all()``、``select_url(url)``；共享 ``TopoViewState``
保证外弹/收回落位后视图状态不重置。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.ui.cockpit_crawl_topo import build_topology
from agent.ui.cockpit_topo_view import (
    ANIM_FRAMES,
    ANIM_MS,
    TopoView,
    TopoViewState,
    alpha_at,
    fit_view,
    interpolate_rect,
)
from agent.ui.hud_theme import (
    BG,
    CYAN,
    CYAN_DIM,
    DIM,
    FONT_LABEL,
    FONT_SMALL,
    FONT_TITLE,
)

DETACH_W = 960
DETACH_H = 720
_PAD = 24
_TOP = 86
_BOTTOM = 56


class TearOffGhost:
    """撕出拖动中的半透明跟随预览（无边框、不抢焦点）。"""

    _W = 220
    _H = 120

    def __init__(self, parent: Any, node_count: int) -> None:
        import tkinter as tk

        self.top = tk.Toplevel(parent)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.attributes("-alpha", 0.6)
        canvas = tk.Canvas(
            self.top, width=self._W, height=self._H, bg=BG,
            highlightthickness=1, highlightbackground=CYAN_DIM, bd=0,
        )
        canvas.pack()
        canvas.create_text(
            self._W // 2, 42, text="拓扑 // TOPOLOGY",
            fill=CYAN, font=FONT_TITLE, anchor="center",
        )
        canvas.create_text(
            self._W // 2, 78, text=f"{node_count} 节点 · 松手生成独立窗口",
            fill=DIM, font=FONT_SMALL, anchor="center",
        )

    def move(self, x_root: float, y_root: float) -> None:
        """光标位于预览中心。"""
        try:
            if self.top is not None:
                self.top.geometry(
                    f"{self._W}x{self._H}+{int(x_root - self._W / 2)}+{int(y_root - self._H / 2)}"
                )
        except Exception:
            pass

    def destroy(self) -> None:
        """销毁并断开引用（Tk 线程内调用）。"""
        top, self.top = self.top, None
        try:
            if top is not None:
                top.destroy()
        except Exception:
            pass


class TopoDetachedWindow:
    """独立拓扑查看窗（只在 Tk 线程使用）。

    生命周期由 CrawlManager 持有；✕ 收回时先播动画再 ``controller.undock()``。
    """

    def __init__(
        self,
        root: Any,
        options: Any,
        state: TopoViewState,
        controller: Any,
        x_root: float,
        y_root: float,
    ) -> None:
        import tkinter as tk

        self._options = options
        self._controller = controller
        self._last_state: Any = None
        self._last_layout: Any = None
        self._animating = False
        self._drag_offset = (0, 0)
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        # 落在松手光标处（中心对齐），并钳制到屏幕内
        screen_w = int(self.top.winfo_screenwidth())
        screen_h = int(self.top.winfo_screenheight())
        x = max(0, min(int(x_root - DETACH_W / 2), screen_w - DETACH_W))
        y = max(0, min(int(y_root - DETACH_H / 2), screen_h - DETACH_H))
        self.top.geometry(f"{DETACH_W}x{DETACH_H}+{x}+{y}")
        self.canvas = tk.Canvas(
            self.top, width=DETACH_W, height=DETACH_H, bg=BG,
            highlightthickness=0, bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.view = TopoView(self.canvas, state, on_change=controller.rerender_all)
        # 事件：缩放/平移/复位/点击；标题带拖动移窗；Esc 等同 ✕
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan)
        self.canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self.canvas.bind("<B3-Motion>", self._on_pan)
        self.canvas.bind("<Double-Button-3>", lambda _e: self.view.reset_view())
        self.canvas.tag_bind("topo_node", "<Button-1>", self._on_item_click)
        self.canvas.tag_bind("topo_aggregate", "<Button-1>", self._on_item_click)
        self.canvas.tag_bind("detach_close", "<Button-1>", lambda _e: self.request_close())
        self.canvas.tag_bind("detach_fit", "<Button-1>", lambda _e: self.fit())
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.top.bind("<Escape>", lambda _e: self.request_close())
        self.top.bind("<Configure>", self._on_resize)
        self._size = (DETACH_W, DETACH_H)

    # ---- 绘制 ----

    def _size_now(self) -> tuple[int, int]:
        """当前实际窗口尺寸；未映射时（创建瞬间）回退到默认尺寸。

        创建后立即渲染时窗口尚未映射，``winfo_width()`` 返回 1，若按它
        布局会把整个拓扑画成左上角一小块（2026-11-10 实测暴露）；未映射
        时必须用设计尺寸 960×720。
        """
        try:
            if self.top is not None and self.top.winfo_ismapped():
                return max(200, int(self.top.winfo_width())), max(160, int(self.top.winfo_height()))
        except Exception:
            pass
        return DETACH_W, DETACH_H

    def render(self, state: Any) -> None:
        """整窗重绘：标题/控件 + 拓扑 + 提示行。"""
        if self.canvas is None or self._animating:
            return
        self._last_state = state
        self.canvas.delete("all")
        w, h = self._size_now()
        self._size = (w, h)
        bottom = h - _BOTTOM
        self._text(_PAD, 26, "CRAWL TOPOLOGY", fill=CYAN, font=FONT_TITLE)
        self._text(_PAD, 50, "看完点 ✕ 收回驾驶舱（视图状态保留）", fill=DIM, font=FONT_SMALL)
        self._text(w - 96, 26, "✕ 收回", fill=CYAN_DIM, font=FONT_LABEL,
                 anchor="e", tags=("detach_close",))
        self._text(w - 24, 50, "适应画布", fill=CYAN_DIM, font=FONT_SMALL,
                 anchor="e", tags=("detach_fit",))
        self._line(_PAD, 66, w - _PAD, 66, CYAN_DIM)
        self.canvas.create_rectangle(6, 6, w - 6, h - 6, outline=CYAN_DIM)
        layout = build_topology(
            state.fetches,
            width=w - 2 * _PAD,
            height=bottom - _TOP,
            x0=_PAD,
            y0=_TOP,
            max_nodes=self._options.crawl_max_nodes,
            layer_limits=dict(self.view.state.expanded_layers) or None,
        )
        self._last_layout = layout
        self.view.draw(layout, pad=_PAD, top=_TOP)
        self._text(
            _PAD, h - 30,
            "Ctrl+滚轮缩放 · 右键拖动 · 双击右键复位 · 点+N展开 · 点节点联动抽取表",
            fill=DIM, font=FONT_SMALL,
        )

    def _text(self, x: float, y: float, content: Any, *, fill: str, font: Any,
              anchor: str = "w", tags: tuple[str, ...] = ()) -> None:
        self.canvas.create_text(x, y, text=str(content), fill=fill, font=font,
                                anchor=anchor, tags=tags)

    def _line(self, x1: float, y1: float, x2: float, y2: float, fill: str) -> None:
        self.canvas.create_line(x1, y1, x2, y2, fill=fill, width=1)

    # ---- 交互 ----

    def _on_resize(self, _event: Any) -> None:
        size = self._size_now()
        if size != self._size:
            self._size = size
            if self._last_state is not None:
                self.render(self._last_state)

    def _on_wheel(self, event: Any) -> None:
        if not (int(getattr(event, "state", 0)) & 0x0004):
            return
        delta = int(getattr(event, "delta", 0) or 0)
        w, h = self._size_now()
        self.view.zoom(
            float(event.x), float(event.y), 1.12 if delta > 0 else 1 / 1.12,
            _PAD, _TOP, w - 2 * _PAD, h - _BOTTOM - _TOP,
        )

    def _on_pan_start(self, event: Any) -> None:
        self.view.pan_start(float(event.x), float(event.y), _TOP, self._size_now()[1] - _BOTTOM)

    def _on_pan(self, event: Any) -> None:
        w, h = self._size_now()
        self.view.pan_to(float(event.x), float(event.y), w - 2 * _PAD, h - _BOTTOM - _TOP)

    def _on_item_click(self, _event: Any) -> None:
        try:
            current = self.canvas.find_withtag("current")
            if not current:
                return
            hit = self.view.hit(self.canvas.gettags(current[0]))
            if hit is None:
                return
            kind, value = hit
            if kind == "aggregate":
                self.view.expand(value)
            elif value:
                self._controller.select_url(value)
        except Exception:
            pass

    def _on_press(self, event: Any) -> None:
        """标题带左键拖动移动独立窗（拓扑区不撕出，只在此窗内交互）。"""
        try:
            if event.y <= 66:
                self._drag_offset = (
                    event.x_root - self.top.winfo_x(),
                    event.y_root - self.top.winfo_y(),
                )
            else:
                self._drag_offset = (0, 0)
        except Exception:
            pass

    def _on_drag(self, event: Any) -> None:
        try:
            if self._drag_offset != (0, 0) and not self._animating:
                self.top.geometry(
                    f"+{event.x_root - self._drag_offset[0]}+{event.y_root - self._drag_offset[1]}"
                )
        except Exception:
            pass

    # ---- 适应画布 ----

    def fit(self) -> None:
        """把全部可见节点与 +N 聚合一次性铺满当前画布。"""
        layout = self._last_layout
        if layout is None:
            return
        points = [(n.x - _PAD, n.y - _TOP) for n in layout.nodes]
        points += [(a["x"] - _PAD, a["y"] - _TOP) for a in layout.aggregates]
        w, h = self._size_now()
        result = fit_view(points, w - 2 * _PAD, h - _BOTTOM - _TOP)
        if result is None:
            return
        self.view.state.scale, self.view.state.pan_x, self.view.state.pan_y = result
        self._controller.rerender_all()

    # ---- 收回动画 ----

    def request_close(self) -> None:
        """✕ / Esc：220ms 飞回驾驶舱拓扑区并渐隐，末帧 undock。"""
        if self._animating or self.top is None:
            return
        self._animating = True
        try:
            r0 = (
                float(self.top.winfo_rootx()), float(self.top.winfo_rooty()),
                float(self.top.winfo_width()), float(self.top.winfo_height()),
            )
        except Exception:
            self._finish_close()
            return
        r1 = self._controller.dock_rect()
        frame_ms = max(8, ANIM_MS // ANIM_FRAMES)

        def step(index: int) -> None:
            if self.top is None:
                return
            t = index / ANIM_FRAMES
            try:
                x, y, w, h = interpolate_rect(r0, r1, t)
                self.top.geometry(f"{max(1, int(w))}x{max(1, int(h))}+{int(x)}+{int(y)}")
                self.top.attributes("-alpha", alpha_at(t))
            except Exception:
                self._finish_close()
                return
            if index < ANIM_FRAMES:
                self.top.after(frame_ms, lambda: step(index + 1))
            else:
                self._finish_close()

        step(1)

    def _finish_close(self) -> None:
        """动画末帧（或异常兜底）：通知管理器收回。"""
        self._animating = False
        self._controller.undock()

    def set_topmost(self, enabled: bool) -> None:
        """终端前台时与驾驶舱一起让位置顶。"""
        try:
            if self.top is not None:
                self.top.attributes("-topmost", bool(enabled))
        except Exception:
            pass

    def hide(self) -> None:
        try:
            if self.top is not None:
                self.top.withdraw()
        except Exception:
            pass

    def show(self) -> None:
        try:
            if self.top is not None:
                self.top.deiconify()
                self.top.lift()
        except Exception:
            pass

    def destroy(self) -> None:
        """销毁并断开 Tk 引用（先置空再 destroy，防跨线程 GC）。"""
        top, canvas = self.top, self.canvas
        self.top = None
        self.canvas = None
        try:
            if top is not None:
                top.destroy()
        except Exception:
            pass
        del canvas


__all__ = ["DETACH_H", "DETACH_W", "TearOffGhost", "TopoDetachedWindow"]
