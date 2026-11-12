"""左侧“爬虫驾驶舱”窗口：URL 清单 + 汇总 + 抽取表 + 分层拓扑。

用户 2026-11-09 决策：爬虫可视化**不再塞进右侧任务驾驶舱**（430px 放不下），
改为在**屏幕最左侧**另开一个独立窗口，左右分工：右边看“做了什么”，左边看
“网络抓了什么”。显示策略：本轮出现第一条抓取才弹出，任务结束保留最终汇总，
点 ✕ 只隐藏，下一轮有新抓取再出现。

数据全部来自 trace 的 ``web_fetch`` / ``web_extract`` / ``crawl_stats``。
2026-11-10 P3.5 增加浏览交互：URL/EXTRACT 鼠标滚轮滚动，拓扑 Ctrl+滚轮缩放、
右键/中键拖动平移、双击右键重置视图，``+N`` 聚合可点击展开；抓取数据仍是只读。
2026-11-10 P3.6：拓扑区**左键按住拖动 = 撕出为独立窗口**（960×720 可拉伸、
适应画布），点 ✕ 以 220ms 动画收回原驾驶舱；视图状态经 ``TopoViewState``
共享，外弹/收回不重置，跨窗口节点↔抽取行高亮联动保持。

@author aceFelix
"""

from __future__ import annotations

from typing import Any, Callable

from agent.ui.cockpit import CockpitState, VisualOptions, _fmt_bytes, _fmt_ms
from agent.ui.cockpit_crawl_data import (
    EXTRACT_ROWS as _EXTRACT_ROWS,
    URL_ROWS as _URL_ROWS,
    clip_url_tail,
    domain_of,
    fetch_window,
    scrollbar_geometry,
    stats_parts,
    summarize,
    unique_fetches,
    visible_window,
)
from agent.ui.cockpit_crawl_topo import build_topology
from agent.ui.cockpit_topo_detach import TearOffGhost, TopoDetachedWindow
from agent.ui.cockpit_topo_view import TearOffGesture, TopoView, TopoViewState
from agent.ui.hud_theme import (
    THEME_CRAWL as _C,
    draw_background,
    draw_frame,
    glow_text,
    FONT_LABEL,
    FONT_MONO,
    FONT_NUM,
    FONT_SMALL,
    FONT_TEXT,
    FONT_TITLE,
    clip,
)

# 左侧窗口尺寸：500×640 容纳 URL 清单 / 抽取表 / 分层拓扑
CRAWL_W = 500
CRAWL_H = 640
CRAWL_PAD = 18

# 拓扑画布区域与统计行（窗口高度固定，坐标集中在这里）
_TOPO_TOP = 438
_TOPO_BOTTOM = 598
_STATS_Y = 616

# 新窗口离屏幕左缘的留白；y 与右侧 HUD 同一“垂直居中偏上”口径
_LEFT_MARGIN = 24


class CrawlWindow:
    """左侧抓取窗口（只在 Tk 线程使用）。

    形态：500×640、无边框、置顶、整面可拖；内容：四格汇总 + 可滚动 URL
    清单 + 可滚动抽取表 + 可缩放/平移拓扑。

    @author aceFelix
    """

    def __init__(self, root: Any, options: VisualOptions, on_close: Callable[[], None],
                 controller: Any) -> None:
        import tkinter as tk

        self._options = options
        self._controller = controller
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.canvas = tk.Canvas(
            self.top,
            width=CRAWL_W,
            height=CRAWL_H,
            bg=_C.bg,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self._drag_offset = (0, 0)
        # 拓扑绘制与视图状态走共享组件；撕出手势只看区域 + 位移（无时间阈值）
        self.view = TopoView(self.canvas, controller.topo_state, on_change=controller.rerender_all)
        self._gesture = TearOffGesture()
        self._press_mode: str | None = None
        # 整面可拖（拓扑区除外：那里左键拖动 = 撕出）；关闭按钮用 tag 命中
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan)
        self.canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self.canvas.bind("<B3-Motion>", self._on_pan)
        self.canvas.bind("<Double-Button-3>", lambda _e: self.view.reset_view())
        self.top.bind("<Escape>", self._on_escape)
        self.canvas.tag_bind("crawl_close", "<Button-1>", lambda _e: on_close())
        # 节点/抽取行/聚合点击：高亮联动或展开；占位区点击收回独立窗
        self.canvas.tag_bind("topo_node", "<Button-1>", self._on_item_click)
        self.canvas.tag_bind("topo_aggregate", "<Button-1>", self._on_item_click)
        self.canvas.tag_bind("extract_row", "<Button-1>", self._on_item_click)
        self.canvas.tag_bind("topo_redock", "<Button-1>", lambda _e: controller.undock())
        self._last_state: CockpitState | None = None
        self._extract_urls: dict[int, str] = {}
        self._url_scroll = 0
        self._extract_scroll: int | None = None

    # 选中态与命中映射都在共享视图组件里（外弹/收回零迁移）
    @property
    def _selected_url(self) -> str | None:
        return self.view.state.selected_url

    @property
    def _topo_urls(self) -> dict[int, str]:
        return self.view.topo_urls

    @property
    def _aggregate_depths(self) -> dict[int, int]:
        return self.view.aggregate_depths

    # ---- 窗口生命周期 ----

    def place_left(self, screen_h: int) -> None:
        """放到屏幕最左侧、垂直居中偏上（与右侧 HUD 对称）。"""
        y = max(24, (screen_h - CRAWL_H) // 2 - 60)
        self.geometry(_LEFT_MARGIN, y)

    def geometry(self, x: int, y: int) -> None:
        """移动到屏幕坐标（无标题栏）。"""
        try:
            if self.top is not None:
                self.top.geometry(f"{CRAWL_W}x{CRAWL_H}+{x}+{y}")
        except Exception:
            pass

    def show(self) -> None:
        """显示/提到最前。"""
        try:
            if self.top is not None:
                self.top.deiconify()
                self.top.lift()
        except Exception:
            pass

    def hide(self) -> None:
        """隐藏但不销毁。"""
        try:
            if self.top is not None:
                self.top.withdraw()
        except Exception:
            pass

    def set_topmost(self, enabled: bool) -> None:
        """切换置顶：终端（Git Bash/cmd）在前台时让位给终端。"""
        try:
            if self.top is not None:
                self.top.attributes("-topmost", bool(enabled))
        except Exception:
            pass

    def reset_interaction(self) -> None:
        """新一轮抓取重置滚动、选择、展开与拓扑视图（共享状态两窗同步）。"""
        self.view.state.reset()
        self._gesture.cancel()
        self._press_mode = None
        self._url_scroll = 0
        self._extract_scroll = None

    def destroy(self) -> None:
        """销毁窗口并断开 Tk 引用（必须在 Tk 线程调用）。

        先置空引用再 destroy：主线程 GC 触碰已释放的 Tcl 解释器会触发
        ``Tcl_AsyncDelete``（与工具卡/代理卡同一约束）。
        """
        top, canvas = self.top, self.canvas
        self.top = None
        self.canvas = None
        try:
            if top is not None:
                top.destroy()
        except Exception:
            pass
        del canvas

    # ---- 拖动与撕出 ----

    def _can_tear_off(self) -> bool:
        """拓扑区可撕出的前提：有层级数据、拓扑开启、尚未外弹。"""
        state = self._last_state
        return (
            self._controller.detached is None
            and self._options.crawl_topology_on()
            and state is not None
            and any(isinstance(item.get("depth"), int) for item in state.fetches)
        )

    def _on_press(self, event: Any) -> None:
        """记录拖动起点；拓扑区内左键按下进入撕出跟踪，区外仍为整窗拖动。"""
        try:
            if self.top is not None:
                self._drag_offset = (
                    event.x_root - self.top.winfo_x(),
                    event.y_root - self.top.winfo_y(),
                )
                self.top.lift()
            y = float(getattr(event, "y", 0))
            if self._can_tear_off() and _TOPO_TOP <= y <= _TOPO_BOTTOM:
                self._press_mode = "topo"
                self._gesture.press(event.x, event.y)
            else:
                self._press_mode = "window"
        except Exception:
            pass

    def _on_drag(self, event: Any) -> None:
        """撕出模式：驱动幽灵预览；否则按位移移动窗口，尺寸不变。"""
        try:
            if self._press_mode == "topo" and self._gesture.active:
                was_armed = self._gesture.armed
                if self._gesture.motion(event.x, event.y):
                    if not was_armed:
                        count = sum(
                            1 for item in (self._last_state.fetches if self._last_state else [])
                            if isinstance(item.get("depth"), int)
                        )
                        self._controller.start_ghost(count)
                    self._controller.move_ghost(event.x_root, event.y_root)
                return
            if self.top is not None:
                x = event.x_root - self._drag_offset[0]
                y = event.y_root - self._drag_offset[1]
                self.top.geometry(f"{CRAWL_W}x{CRAWL_H}+{x}+{y}")
        except Exception:
            pass

    def _on_release(self, event: Any) -> None:
        """松手：落点判定撕出 / 取消；未武装时按原点击处理（tag 已在按下时触发）。"""
        if self._press_mode != "topo":
            return
        self._press_mode = None
        try:
            y = float(getattr(event, "y", 0))
            inside = _TOPO_TOP <= y <= _TOPO_BOTTOM
            result = self._gesture.release(event.x, event.y, inside_topo=inside)
            if result == "detach":
                self._controller.detach_topology(event.x_root, event.y_root)
            else:
                self._controller.cancel_ghost()
        except Exception:
            self._controller.cancel_ghost()

    def _on_escape(self, _event: Any = None) -> None:
        """Esc：取消撕出拖动，无副作用。"""
        self._gesture.cancel()
        self._press_mode = None
        self._controller.cancel_ghost()

    # ---- 滚动与拓扑视图 ----

    def _on_mouse_wheel(self, event: Any) -> None:
        """普通滚轮滚动 URL/EXTRACT；Ctrl+滚轮缩放拓扑。"""
        y = float(getattr(event, "y", 0))
        delta = int(getattr(event, "delta", 0) or 0)
        step = -1 if delta > 0 else 1
        if int(getattr(event, "state", 0)) & 0x0004:  # Ctrl
            if _TOPO_TOP <= y <= _TOPO_BOTTOM:
                self.view.zoom(
                    float(getattr(event, "x", 0)), y, 1.12 if delta > 0 else 1 / 1.12,
                    CRAWL_PAD, _TOPO_TOP, CRAWL_W - 2 * CRAWL_PAD, _TOPO_BOTTOM - _TOPO_TOP,
                )
            return
        if 166 <= y <= 314:
            self._url_scroll = max(0, self._url_scroll + step)
            self._rerender()
        elif 338 <= y <= 412:
            current = self._extract_scroll if self._extract_scroll is not None else 10**9
            self._extract_scroll = max(0, current + step)
            self._rerender()

    def _on_pan_start(self, event: Any) -> None:
        """右键/中键在拓扑区按下：开始平移。"""
        self.view.pan_start(float(getattr(event, "x", 0)), float(getattr(event, "y", 0)),
                            _TOPO_TOP, _TOPO_BOTTOM)

    def _on_pan(self, event: Any) -> None:
        """按鼠标位移平移拓扑。"""
        self.view.pan_to(float(getattr(event, "x", 0)), float(getattr(event, "y", 0)),
                         CRAWL_W - 2 * CRAWL_PAD, _TOPO_BOTTOM - _TOPO_TOP)

    def _rerender(self) -> None:
        """交互状态变化后按最后一份状态重绘。"""
        if self._last_state is not None:
            self.render(self._last_state)

    def _select(self, url: str) -> None:
        """选中 URL 并重绘双方窗口：拓扑节点与抽取行互相高亮；再次点击取消。

        @author aceFelix
        """
        self._controller.select_url(url)

    def _on_item_click(self, _event: Any) -> None:
        """处理拓扑节点/抽取行/+N 聚合点击。

        tag 里只带行号（``topo_idx:N`` / ``extract_idx:N``），URL 存在映射表里，
        避免 URL 中的特殊字符破坏 Tk 标签。

        @author aceFelix
        """
        try:
            current = self.canvas.find_withtag("current")
            if not current:
                return
            tags = self.canvas.gettags(current[0])
            for tag in tags:
                if tag.startswith("extract_idx:"):
                    index = int(tag.split(":", 1)[1])
                    self._select(self._extract_urls.get(index, ""))
                    return
            hit = self.view.hit(tags)
            if hit is None:
                return
            kind, value = hit
            if kind == "aggregate":
                self.view.expand(value)
            elif value:
                self._select(value)
        except Exception:
            pass

    # ---- 绘制 ----

    def render(self, state: CockpitState) -> None:
        """按当前抓取状态整窗重绘（只在有新抓取时调用）。"""
        if self.canvas is None:
            return
        self._last_state = state
        self._extract_urls = {}
        self.canvas.delete("all")
        self._draw_grid()
        self._draw_frame()
        self._draw_header(state)
        self._draw_metrics(state)
        self._draw_urls(state)
        self._draw_extracts(state)
        self._draw_topology(state)

    def _text(
        self,
        x: float,
        y: float,
        content: Any,
        *,
        fill: str = _C.text,
        font: Any = FONT_TEXT,
        anchor: str = "w",
        tag: str | None = None,
        tags: tuple[str, ...] = (),
    ) -> None:
        """放一行文字（tag/tags 用于关闭按钮与节点点击命中）。"""
        merged = tuple(tags) + ((tag,) if tag else ())
        self.canvas.create_text(
            x, y, text=str(content), fill=fill, font=font, anchor=anchor,
            tags=merged,
        )

    def _line(self, x1: float, y1: float, x2: float, y2: float, fill: str, width: int = 1) -> None:
        """画一条线。"""
        self.canvas.create_line(x1, y1, x2, y2, fill=fill, width=width)

    def _section(self, title: str, y: int) -> None:
        """小节标题 + 右侧延长线。"""
        self._text(CRAWL_PAD, y, title, fill=_C.rule, font=FONT_LABEL)
        self._line(CRAWL_PAD + 108, y, CRAWL_W - CRAWL_PAD, y, _C.rule)

    def _draw_scrollbar(self, total: int, visible: int, offset: int, top: float, bottom: float) -> None:
        """在窗口右缘画一条迷你滚动条。"""
        x = CRAWL_W - 9
        self._line(x + 1, top, x + 1, bottom, _C.grid)
        geometry = scrollbar_geometry(total, visible, offset, top=top, bottom=bottom, x=x)
        if geometry is not None:
            self.canvas.create_rectangle(*geometry, fill=_C.accent_dim, outline="")

    def _draw_grid(self) -> None:
        """背景材质：深渊黑 + 扫描线 + 网格 + 颗粒 + 侧刻度（统一在 hud_theme）。"""
        draw_background(self.canvas, _C, CRAWL_W, CRAWL_H)

    def _draw_frame(self) -> None:
        """外框 + 四角 HUD 括号（统一在 hud_theme）。"""
        draw_frame(self.canvas, _C, CRAWL_W, CRAWL_H)

    def _draw_header(self, state: CockpitState) -> None:
        """标题、运行状态点、关闭按钮。"""
        glow_text(self.canvas, CRAWL_PAD, 25, "J.A.R.V.I.S", theme=_C, font=FONT_TITLE)
        self._text(CRAWL_PAD, 46, "CRAWL COCKPIT // 爬虫驾驶舱", fill=_C.dim, font=FONT_SMALL)

        running = state.current is not None
        dot = _C.ok if state.finished else (_C.accent if running else _C.dim)
        self.canvas.create_oval(
            CRAWL_W - 96, 20, CRAWL_W - 88, 28, fill=dot, outline=""
        )
        self._text(
            CRAWL_W - 70, 24, "RUN" if running else "IDLE",
            fill=_C.dim, font=FONT_SMALL, anchor="e",
        )

        # 关闭按钮：只隐藏，下一轮有新抓取自动重现
        self.canvas.create_oval(
            CRAWL_W - 40, 12, CRAWL_W - 14, 38,
            outline=_C.accent_dim, width=1, tags=("crawl_close",),
        )
        self._text(
            CRAWL_W - 27, 25, "✕", fill=_C.dim, font=("Microsoft YaHei UI", 10),
            anchor="center", tag="crawl_close",
        )
        self._line(CRAWL_PAD, 60, CRAWL_W - CRAWL_PAD, 60, _C.rule)

    def _draw_metrics(self, state: CockpitState) -> None:
        """四格汇总：抓取 / 成功 / 失败 / 去重，外加字节与域名分布。"""
        info = summarize(state.fetches, state.stats)
        cells = (
            ("抓取 TOTAL", str(info["total"]), _C.accent),
            ("成功 OK", str(info["ok"]), _C.ok),
            ("失败 FAIL", str(info["failed"]), _C.fail if info["failed"] else _C.ok),
            ("去重 DUP", str(info["duplicates"]), _C.accent),
        )
        cell_w = (CRAWL_W - 2 * CRAWL_PAD - 24) // 4
        for index, (label, value, color) in enumerate(cells):
            x = CRAWL_PAD + index * (cell_w + 8)
            self.canvas.create_rectangle(x, 70, x + cell_w, 126, outline=_C.rule)
            self._text(x + 8, 82, label, fill=_C.dim, font=FONT_LABEL)
            glow_text(
                self.canvas, x + 8, 105, value,
                theme=_C, font=FONT_NUM, fill=color,
            )

        # 第二行：累计字节 + 域名分布（最多 3 个，超出只报总数）
        parts = []
        if info["bytes"]:
            parts.append(f"累计 {_fmt_bytes(info['bytes'])}")
        if info["domains"]:
            top = " · ".join(f"{name}×{count}" for name, count in info["domains"][:3])
            parts.append(f"域名 {len(info['domains'])} 个{(' · ' + top) if top else ''}")
        self._text(
            CRAWL_PAD, 140,
            clip("  |  ".join(parts) if parts else "— 等待抓取", 70),
            fill=_C.dim, font=FONT_SMALL,
        )

    def _draw_urls(self, state: CockpitState) -> None:
        """URL 清单：根页固定，其余滚轮浏览；路径尾部优先保留。"""
        self._section("URL 清单 // URLS", 158)
        rows, self._url_scroll, total, _max_offset = fetch_window(
            state.fetches, self._url_scroll, _URL_ROWS
        )
        if not rows:
            self._text(CRAWL_PAD, 182, "— 暂无抓取", fill=_C.dim, font=FONT_SMALL)
            return
        self._text(
            CRAWL_W - CRAWL_PAD, 158, f"{len(rows)}/{total}  滚轮",
            fill=_C.dim, font=FONT_SMALL, anchor="e",
        )
        y = 180
        for item in rows:
            ok = item.get("status") == "ok"
            self._text(CRAWL_PAD, y, "✓" if ok else "✗", fill=_C.ok if ok else _C.fail, font=FONT_TEXT)
            # 层号：Crawl 提供 depth 时显示 L0/L1…；单页抓取无深度显示 L-
            depth = item.get("depth")
            layer = f"L{depth}" if isinstance(depth, int) else "L-"
            self._text(CRAWL_PAD + 16, y, layer, fill=_C.accent_dim, font=FONT_MONO)
            self._text(
                CRAWL_PAD + 40, y, clip_url_tail(item.get("url"), 44),
                fill=_C.text, font=FONT_MONO,
            )
            meta = " · ".join(
                part for part in (
                    _fmt_ms(item.get("duration_ms")),
                    _fmt_bytes(item.get("bytes")),
                ) if part
            )
            hits = int(item.get("hits") or 1)
            if hits > 1:
                meta = f"×{hits} · {meta}" if meta else f"×{hits}"
            if meta:
                self._text(CRAWL_W - CRAWL_PAD, y, meta, fill=_C.dim, font=FONT_MONO, anchor="e")
            y += 22
        self._draw_scrollbar(total, len(rows), self._url_scroll, 180, 312)

    def _draw_extracts(self, state: CockpitState) -> None:
        """抽取表：可滚轮浏览全部记录；点击行高亮对应拓扑节点。"""
        self._section("抽取 // EXTRACT", 326)
        all_rows = list(state.extracts)
        if not all_rows:
            self._text(
                CRAWL_PAD, 350,
                "— 暂无抽取（用 WebExtract 按 CSS 选择器抽字段）",
                fill=_C.dim, font=FONT_SMALL,
            )
            self._extract_scroll = None
            return
        rows, offset, total = visible_window(all_rows, self._extract_scroll or 0, _EXTRACT_ROWS)
        self._extract_scroll = offset
        self._text(
            CRAWL_W - CRAWL_PAD, 326, f"{len(rows)}/{total}  滚轮",
            fill=_C.dim, font=FONT_SMALL, anchor="e",
        )
        y = 348
        for index, item in enumerate(rows):
            url = str(item.get("url") or "")
            selected = bool(url) and url == self._selected_url
            tags = ("extract_row", f"extract_idx:{index}")
            if selected:
                # 选中行加一层细框：与拓扑节点的高亮环对应
                self.canvas.create_rectangle(
                    CRAWL_PAD - 4, y - 9, CRAWL_W - CRAWL_PAD, y + 9,
                    outline=_C.accent_dim, tags=tags,
                )
            fields = item.get("fields") or {}
            if isinstance(fields, dict) and fields:
                pairs = " · ".join(
                    f"{key}={clip(value, 18)}" for key, value in list(fields.items())[:3]
                )
            else:
                pairs = "（无字段）"
            # 缺失字段只报数量：窗口一行放不下字段名，详情看工具回执
            missing = item.get("missing") or []
            note = f"  ·  缺 {len(missing)}" if missing else ""
            self._text(
                CRAWL_PAD, y,
                f"{clip(domain_of(url), 20)}  {pairs}{note}",
                fill=_C.accent if selected else _C.dim, font=FONT_MONO, tags=tags,
            )
            self._extract_urls[index] = url
            y += 20
        self._draw_scrollbar(total, len(rows), offset, 348, 404)

    def _draw_topology(self, state: CockpitState) -> None:
        """拓扑区：分层节点 + 父→子连线 + 统计；可撕出为独立窗（P3.6）。

        ``crawl_topology = off`` 时只保留统计；单页抓取没有 depth，显示
        “单页抓取，无拓扑”，不画假节点。已外弹时显示占位（点击收回）。
        """
        self._section("拓扑 // TOPOLOGY", 424)
        rows = [item for item in state.fetches if isinstance(item.get("depth"), int)]
        if not rows:
            hint = "— 单页抓取，无拓扑" if state.fetches else "— 等待拓扑数据"
            self._text(CRAWL_PAD, 442, hint, fill=_C.dim, font=FONT_SMALL)
            self._draw_stats(state)
            return
        if not self._options.crawl_topology_on():
            self._text(
                CRAWL_PAD, 442,
                "— 拓扑已关闭（[visualization] crawl_topology = off）",
                fill=_C.dim, font=FONT_SMALL,
            )
            self._draw_stats(state)
            return
        if self._controller.detached is not None:
            self._text(
                CRAWL_PAD, 470, "— 拓扑已外弹至独立窗口查看",
                fill=_C.accent_dim, font=FONT_SMALL,
            )
            self._text(
                CRAWL_PAD, 500, "点击此处收回（或在独立窗点 ✕）",
                fill=_C.accent, font=FONT_SMALL, tags=("topo_redock",),
            )
            self._draw_stats(state)
            return
        layout = build_topology(
            state.fetches,
            width=CRAWL_W - 2 * CRAWL_PAD,
            height=_TOPO_BOTTOM - _TOPO_TOP,
            x0=CRAWL_PAD,
            y0=_TOPO_TOP,
            max_nodes=self._options.crawl_max_nodes,
            layer_limits=dict(self.view.state.expanded_layers) or None,
        )
        self.view.draw(layout, pad=CRAWL_PAD, top=_TOPO_TOP)
        self._text(
            CRAWL_PAD, 604,
            "左键拖出独立窗 · Ctrl+滚轮缩放 · 右键拖动 · 双击右键复位",
            fill=_C.dim, font=FONT_SMALL,
        )
        self._draw_stats(state)

    def _draw_stats(self, state: CockpitState) -> None:
        """拓扑区底部统计行：剩余队列/完成/失败/去重/实达深度/上限/字节/耗时。"""
        parts = stats_parts(state)
        self._text(
            CRAWL_PAD, _STATS_Y, clip(" · ".join(parts) or "—", 72),
            fill=_C.accent_dim, font=FONT_MONO,
        )


class CrawlManager:
    """抓取窗口的显示策略与生命周期（只在 Tk 线程使用）。

    策略（2026-11-09 用户决策）：
    - 本轮出现第一条抓取/抽取事件才创建并弹出；
    - 任务结束保留最终汇总（不隐藏）；
    - 用户点 ✕ 只隐藏，本轮不再自动弹；下一轮（run_id 变化）有抓取再出现；
    - 主 HUD 被关闭时一并隐藏，HUD 重现时同步显示。
    """

    def __init__(self, root: Any, options: VisualOptions) -> None:
        self.root = root
        self.options = options
        self._window: CrawlWindow | None = None
        self._disabled = False
        self._hidden = False
        self._hidden_run_id: str | None = None
        # 最近一次渲染所属轮次（用户点 ✕ 时据此判定“本轮不再弹”；
        # 换轮时用它重置滚动/缩放等交互状态）
        self._run_id = ""
        self._rendered_run_id: str | None = None
        # 当前是否允许置顶（终端在前台时由 zorder 策略置 False）
        self._topmost_allowed = True
        # P3.6：共享拓扑视图状态 + 撕出独立窗 + 拖拽幽灵
        self.topo_state = TopoViewState()
        self._detached: TopoDetachedWindow | None = None
        self._ghost: TearOffGhost | None = None
        self._last_state: CockpitState | None = None

    @property
    def detached(self) -> TopoDetachedWindow | None:
        """当前撕出的独立拓扑窗（未外弹为 None）。"""
        return self._detached

    @staticmethod
    def has_data(state: CockpitState) -> bool:
        """是否已有可展示的抓取数据（决定要不要弹窗）。"""
        return bool(state.fetches or state.extracts or state.stats)

    def sync(self, state: CockpitState, run_id: str) -> None:
        """有新抓取时创建/刷新窗口；本轮用户关过则不再自动弹。"""
        if self._disabled or not self.options.crawl_on():
            return
        if not self.has_data(state):
            return
        self._run_id = run_id
        self._last_state = state
        if self._hidden and self._hidden_run_id == run_id:
            return
        # 新一轮（或从未关过）：解除隐藏标记，后续 show_all 也同步生效
        self._hidden = False
        window = self._ensure_window()
        if window is None:
            return
        if run_id != self._rendered_run_id:
            window.reset_interaction()
            self._rendered_run_id = run_id
        try:
            window.render(state)
            window.show()
            if self._detached is not None:
                self._detached.render(state)
        except Exception as exc:  # noqa: BLE001 - 抓取窗故障不能影响主流程
            # 渲染异常不能静默：否则窗口只剩半截内容（P3 实测拓扑整块空白）。
            # @author aceFelix
            from agent.core.diag import diag_info

            diag_info("ui", f"爬虫驾驶舱渲染失败: {exc}")

    def hide_all(self) -> None:
        """主 HUD 关闭时隐藏（不销毁、不影响下轮重现）；独立窗同步。"""
        self.cancel_ghost()
        if self._detached is not None:
            self._detached.hide()
        if self._window is not None:
            self._window.hide()

    def set_topmost(self, enabled: bool) -> None:
        """切换置顶并记住状态：终端在前台时新弹的抓取窗也应让位。"""
        self._topmost_allowed = bool(enabled)
        if self._detached is not None:
            self._detached.set_topmost(enabled)
        if self._window is not None:
            self._window.set_topmost(enabled)

    def show_all(self, state: CockpitState) -> None:
        """主 HUD 重现时同步显示（有数据才显示）；独立窗一并刷新。"""
        if self._disabled or not self.options.crawl_on():
            return
        if self._window is None or not self.has_data(state):
            return
        self._last_state = state
        try:
            self._window.render(state)
            self._window.show()
            if self._detached is not None:
                self._detached.render(state)
                self._detached.show()
        except Exception as exc:  # noqa: BLE001 - 抓取窗故障不能影响主流程
            from agent.core.diag import diag_info

            diag_info("ui", f"爬虫驾驶舱渲染失败: {exc}")

    def detach(self) -> None:
        """销毁窗口并断开 Tk 根引用（退出时防跨线程 GC 崩溃）。"""
        self.cancel_ghost()
        if self._detached is not None:
            detached, self._detached = self._detached, None
            detached.destroy()
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self.root = None

    # ---- P3.6：撕出 / 收回 / 跨窗联动 ----

    def rerender_all(self) -> None:
        """视图状态变化后重绘驾驶舱与独立窗（render 幂等，不会递归）。"""
        state = self._last_state
        if state is None:
            return
        try:
            if self._window is not None:
                self._window.render(state)
            if self._detached is not None:
                self._detached.render(state)
        except Exception as exc:  # noqa: BLE001 - 渲染故障不能影响主流程
            from agent.core.diag import diag_info

            diag_info("ui", f"爬虫驾驶舱联动重绘失败: {exc}")

    def select_url(self, url: str) -> None:
        """节点↔抽取行选中切换（共享状态，双窗同步高亮）。"""
        if not url:
            return
        state = self.topo_state
        state.selected_url = None if state.selected_url == url else url
        self.rerender_all()

    def start_ghost(self, node_count: int) -> None:
        """撕出武装：创建半透明跟随预览。"""
        if self._ghost is not None or self._window is None or self._window.top is None:
            return
        try:
            self._ghost = TearOffGhost(self._window.top, node_count)
        except Exception:
            self._ghost = None

    def move_ghost(self, x_root: float, y_root: float) -> None:
        """幽灵跟随光标。"""
        if self._ghost is not None:
            self._ghost.move(x_root, y_root)

    def cancel_ghost(self) -> None:
        """销毁幽灵（取消 / 落点 / 隐藏 / 退出）。"""
        if self._ghost is not None:
            ghost, self._ghost = self._ghost, None
            ghost.destroy()

    def detach_topology(self, x_root: float, y_root: float) -> None:
        """松手落点：在光标处生成独立拓扑窗并自动适应画布展开大图。

        先生 2026-11-10 实测反馈：拖出后还要手动点「适应画布」才能铺开，
        直接默认 fit，让撕出的第一帧就是完整大图。
        """
        self.cancel_ghost()
        if self._detached is not None or self._last_state is None:
            return
        try:
            self._detached = TopoDetachedWindow(
                self.root, self.options, self.topo_state, self, x_root, y_root,
            )
            self._detached.set_topmost(self._topmost_allowed)
            self.rerender_all()
            self._detached.fit()
        except Exception as exc:  # noqa: BLE001 - 外弹失败不拖垮驾驶舱
            from agent.core.diag import diag_info

            diag_info("ui", f"拓扑独立窗创建失败: {exc}")
            self._detached = None

    def dock_rect(self) -> tuple[float, float, float, float]:
        """驾驶舱拓扑区的实时屏幕矩形（收回动画终点）。"""
        window = self._window
        try:
            if window is not None and window.top is not None:
                return (
                    float(window.top.winfo_rootx()) + CRAWL_PAD,
                    float(window.top.winfo_rooty()) + _TOPO_TOP,
                    float(CRAWL_W - 2 * CRAWL_PAD),
                    float(_TOPO_BOTTOM - _TOPO_TOP),
                )
        except Exception:
            pass
        return (24.0, 200.0, float(CRAWL_W - 2 * CRAWL_PAD), float(_TOPO_BOTTOM - _TOPO_TOP))

    def undock(self) -> None:
        """收回：销毁独立窗，拓扑在驾驶舱原位置按共享状态重绘。"""
        if self._detached is None:
            return
        detached, self._detached = self._detached, None
        detached.destroy()
        self.rerender_all()

    # ---- 内部 ----

    def _ensure_window(self) -> CrawlWindow | None:
        """懒创建窗口；失败置 disabled，不影响主 HUD。"""
        if self._window is not None:
            return self._window
        try:
            self._window = CrawlWindow(self.root, self.options, self._on_close, controller=self)
            # 新窗口沿用当前置顶策略（终端在前台时不应置顶）
            self._window.set_topmost(self._topmost_allowed)
            try:
                screen_h = int(self.root.winfo_screenheight())
            except Exception:
                screen_h = 1080
            self._window.place_left(screen_h)
            return self._window
        except Exception as exc:  # noqa: BLE001 - 抓取窗故障不能影响主流程
            from agent.core.diag import diag_info

            diag_info("ui", f"抓取窗口创建失败，已禁用: {exc}")
            self._disabled = True
            self._window = None
            return None

    def _on_close(self) -> None:
        """用户点 ✕：只隐藏；记录轮次避免本轮再次弹出。"""
        self._hidden = True
        self._hidden_run_id = self._run_id or None
        if self._window is not None:
            self._window.hide()
