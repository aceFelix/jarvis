"""左侧“抓取网络”窗口（C2-lite）：URL 清单 + 汇总 + 抽取表。

用户 2026-11-09 决策：爬虫可视化**不再塞进右侧任务驾驶舱**（430px 放不下），
改为在**屏幕最左侧**另开一个独立窗口，左右分工：右边看“做了什么”，左边看
“网络抓了什么”。显示策略：本轮出现第一条抓取才弹出，任务结束保留最终汇总，
点 ✕ 只隐藏，下一轮有新抓取再出现。

数据全部来自 trace 的 ``web_fetch`` / ``web_extract`` / ``crawl_stats``：

- ``web_fetch`` 由 ``TraceRecorder`` 从 WebFetch / BrowserNavigate 派生（工具零改动），
  Crawl 的页面事件额外带 ``parent_url`` / ``depth`` / ``kind``；
- ``web_extract`` 由 WebExtract / Crawl 的 ``ToolResult.trace_meta`` 派生（P1/P2，已接入）；
- ``crawl_stats`` 由 Crawl 结束时上报的 ``trace_meta`` 派生（P2，已接入）；
- **拓扑区（P3）**：分层节点 + 父→子连线，节点与抽取行点击互相高亮；
  单页抓取没有层级时显示“单页抓取，无拓扑”。

@author aceFelix
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Iterable

from agent.ui.cockpit import CockpitState, VisualOptions, _fmt_bytes, _fmt_ms
from agent.ui.cockpit_crawl_topo import TopologyLayout, build_topology
from agent.ui.hud_theme import (
    BG,
    CYAN,
    CYAN_DIM,
    DIM,
    FONT_LABEL,
    FONT_MONO,
    FONT_NUM,
    FONT_SMALL,
    FONT_TEXT,
    FONT_TITLE,
    GREEN,
    GRID,
    RED,
    TEXT,
    clip,
)

# 左侧窗口尺寸：560×640 容纳 URL 清单 / 抽取表 / 分层拓扑
CRAWL_W = 560
CRAWL_H = 640
CRAWL_PAD = 18

# 拓扑画布区域与统计行（窗口高度固定，坐标集中在这里）
_TOPO_TOP = 438
_TOPO_BOTTOM = 598
_STATS_Y = 616

# 清单最多展示多少条去重 URL、抽取表最多几行（窗口高度固定）
_URL_ROWS = 7
_EXTRACT_ROWS = 4

# 新窗口离屏幕左缘的留白；y 与右侧 HUD 同一“垂直居中偏上”口径
_LEFT_MARGIN = 24


# ---- 纯逻辑（可单测，不依赖 Tk）----


def domain_of(url: Any) -> str:
    """从 URL 提取主机名；无法解析时返回 ``?``。

    Args:
        url: 原始 URL（可能为空/非字符串）。

    Returns:
        去掉协议与路径的主机名，如 ``example.com``。
    """
    text = str(url or "").strip()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    host = text.split("/", 1)[0].split("?", 1)[0].strip()
    return host or "?"


def unique_fetches(fetches: Iterable[dict[str, Any]], limit: int = _URL_ROWS) -> list[dict[str, Any]]:
    """按 URL 去重，返回最近的若干条（最新在前），并带上命中次数。

    Args:
        fetches: ``CockpitState.fetches``（按发生顺序，旧→新）。
        limit: 最多返回多少条。

    Returns:
        每项为原 fetch 字段的副本，额外含 ``hits``（同一 URL 在本批出现的次数）；
        按“最后一次被抓取的时间”倒序（最新在前）。
    """
    counts: Counter[str] = Counter()
    latest: dict[str, dict[str, Any]] = {}
    last_index: dict[str, int] = {}
    for index, item in enumerate(fetches):
        url = str(item.get("url") or "")
        counts[url] += 1
        # 同一个 URL 反复抓取：保留最新一条的状态/耗时/字节
        latest[url] = item
        last_index[url] = index
    ordered = []
    for url in sorted(last_index, key=lambda key: last_index[key], reverse=True):
        row = dict(latest[url])
        row["hits"] = counts[url]
        ordered.append(row)
        if len(ordered) >= limit:
            break
    return ordered


def summarize(
    fetches: Iterable[dict[str, Any]],
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """汇总本批抓取：总数 / 成功 / 失败 / 去重 / 字节 / 域名分布。

    Args:
        fetches: ``CockpitState.fetches``。
        stats: 可选：``CrawlState.stats``（来自 ``crawl_stats``）。提供
            ``dedup_hits`` 时以它为准：Crawl 在入队/出队阶段就去重，重复 URL
            不会产生第二条 fetch，单看 fetches 会把去重数算成 0。

    Returns:
        汇总字典；``domains`` 为 ``[(域名, 次数)]``，按次数倒序。
    """
    items = list(fetches)
    total = len(items)
    ok = sum(1 for item in items if item.get("status") == "ok")
    total_bytes = 0
    for item in items:
        try:
            total_bytes += int(item.get("bytes") or 0)
        except (TypeError, ValueError):
            continue
    domains = Counter(domain_of(item.get("url")) for item in items)
    unique = len({str(item.get("url") or "") for item in items})
    dedup = total - unique
    if isinstance(stats, dict) and stats.get("dedup_hits") is not None:
        try:
            dedup = int(stats["dedup_hits"])
        except (TypeError, ValueError):
            pass
    return {
        "total": total,
        "ok": ok,
        "failed": total - ok,
        "unique": unique,
        "duplicates": dedup,
        "bytes": total_bytes,
        "domains": domains.most_common(),
    }


class CrawlWindow:
    """左侧抓取窗口（只在 Tk 线程使用）。

    形态：520×520、无边框、置顶、整面可拖；青色 HUD 造型，与右侧驾驶舱同一
    视觉语言。内容：四格汇总 + URL 清单 + 抽取表 + 拓扑占位。

    @author aceFelix
    """

    def __init__(self, root: Any, options: VisualOptions, on_close: Callable[[], None]) -> None:
        import tkinter as tk

        self._options = options
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.canvas = tk.Canvas(
            self.top,
            width=CRAWL_W,
            height=CRAWL_H,
            bg=BG,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self._drag_offset = (0, 0)
        # 整面可拖（与驾驶舱一致）；关闭按钮用 tag 命中，不影响拖动
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.tag_bind("crawl_close", "<Button-1>", lambda _e: on_close())
        # 节点/抽取行点击：高亮联动（P3，不阻止整窗拖动）
        self.canvas.tag_bind("topo_node", "<Button-1>", self._on_item_click)
        self.canvas.tag_bind("extract_row", "<Button-1>", self._on_item_click)
        self._selected_url: str | None = None
        self._last_state: CockpitState | None = None
        self._topo_urls: dict[int, str] = {}
        self._extract_urls: dict[int, str] = {}

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

    # ---- 拖动 ----

    def _on_press(self, event: Any) -> None:
        """记录拖动起点。"""
        try:
            if self.top is not None:
                self._drag_offset = (
                    event.x_root - self.top.winfo_x(),
                    event.y_root - self.top.winfo_y(),
                )
                self.top.lift()
        except Exception:
            pass

    def _on_drag(self, event: Any) -> None:
        """按位移移动窗口，尺寸不变。"""
        try:
            if self.top is not None:
                x = event.x_root - self._drag_offset[0]
                y = event.y_root - self._drag_offset[1]
                self.top.geometry(f"{CRAWL_W}x{CRAWL_H}+{x}+{y}")
        except Exception:
            pass

    def _select(self, url: str) -> None:
        """选中 URL 并重绘：拓扑节点与抽取行互相高亮；再次点击取消。

        @author aceFelix
        """
        if not url:
            return
        self._selected_url = None if self._selected_url == url else url
        if self._last_state is not None:
            self.render(self._last_state)

    def _on_item_click(self, _event: Any) -> None:
        """处理拓扑节点/抽取行点击。

        tag 里只带行号（``topo_idx:N`` / ``extract_idx:N``），URL 存在映射表里，
        避免 URL 中的特殊字符破坏 Tk 标签。

        @author aceFelix
        """
        try:
            current = self.canvas.find_withtag("current")
            if not current:
                return
            for tag in self.canvas.gettags(current[0]):
                if tag.startswith("topo_idx:"):
                    index = int(tag.split(":", 1)[1])
                    self._select(self._topo_urls.get(index, ""))
                    return
                if tag.startswith("extract_idx:"):
                    index = int(tag.split(":", 1)[1])
                    self._select(self._extract_urls.get(index, ""))
                    return
        except Exception:
            pass

    # ---- 绘制 ----

    def render(self, state: CockpitState) -> None:
        """按当前抓取状态整窗重绘（只在有新抓取时调用）。"""
        if self.canvas is None:
            return
        self._last_state = state
        self._topo_urls = {}
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
        fill: str = TEXT,
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
        self._text(CRAWL_PAD, y, title, fill=CYAN_DIM, font=FONT_LABEL)
        self._line(CRAWL_PAD + 108, y, CRAWL_W - CRAWL_PAD, y, GRID)

    def _draw_grid(self) -> None:
        """背景网格：与驾驶舱同款低亮度底噪。"""
        for x in range(0, CRAWL_W, 28):
            self._line(x, 0, x, CRAWL_H, GRID)
        for y in range(0, CRAWL_H, 28):
            self._line(0, y, CRAWL_W, y, GRID)

    def _draw_frame(self) -> None:
        """外框 + 四角角标。"""
        self.canvas.create_rectangle(
            6, 6, CRAWL_W - 6, CRAWL_H - 6, outline=CYAN_DIM
        )
        corner = 16
        for x, y, dx, dy in (
            (6, 6, 1, 1),
            (CRAWL_W - 6, 6, -1, 1),
            (6, CRAWL_H - 6, 1, -1),
            (CRAWL_W - 6, CRAWL_H - 6, -1, -1),
        ):
            self._line(x, y, x + dx * corner, y, CYAN, 2)
            self._line(x, y, x, y + dy * corner, CYAN, 2)

    def _draw_header(self, state: CockpitState) -> None:
        """标题、运行状态点、关闭按钮。"""
        self._text(CRAWL_PAD, 26, "J.A.R.V.I.S", fill=CYAN_DIM, font=FONT_TITLE)
        self._text(CRAWL_PAD + 1, 25, "J.A.R.V.I.S", fill=CYAN, font=FONT_TITLE)
        self._text(CRAWL_PAD, 46, "CRAWL // 爬虫驾驶舱", fill=DIM, font=FONT_SMALL)

        running = state.current is not None
        dot = GREEN if state.finished else (CYAN if running else DIM)
        self.canvas.create_oval(
            CRAWL_W - 96, 20, CRAWL_W - 88, 28, fill=dot, outline=""
        )
        self._text(
            CRAWL_W - 70, 24, "RUN" if running else "IDLE",
            fill=DIM, font=FONT_SMALL, anchor="e",
        )

        # 关闭按钮：只隐藏，下一轮有新抓取自动重现
        self.canvas.create_oval(
            CRAWL_W - 40, 12, CRAWL_W - 14, 38,
            outline=CYAN_DIM, width=1, tags=("crawl_close",),
        )
        self._text(
            CRAWL_W - 27, 25, "✕", fill=DIM, font=("Microsoft YaHei UI", 10),
            anchor="center", tag="crawl_close",
        )
        self._line(CRAWL_PAD, 60, CRAWL_W - CRAWL_PAD, 60, CYAN_DIM)

    def _draw_metrics(self, state: CockpitState) -> None:
        """四格汇总：抓取 / 成功 / 失败 / 去重，外加字节与域名分布。"""
        info = summarize(state.fetches, state.stats)
        cells = (
            ("抓取 TOTAL", str(info["total"]), CYAN),
            ("成功 OK", str(info["ok"]), GREEN),
            ("失败 FAIL", str(info["failed"]), RED if info["failed"] else GREEN),
            ("去重 DUP", str(info["duplicates"]), CYAN),
        )
        cell_w = (CRAWL_W - 2 * CRAWL_PAD - 24) // 4
        for index, (label, value, color) in enumerate(cells):
            x = CRAWL_PAD + index * (cell_w + 8)
            self.canvas.create_rectangle(x, 70, x + cell_w, 126, outline=GRID)
            self._text(x + 8, 82, label, fill=DIM, font=FONT_LABEL)
            self._text(x + 9, 106, value, fill=CYAN_DIM, font=FONT_NUM)
            self._text(x + 8, 105, value, fill=color, font=FONT_NUM)

        # 第二行：累计字节 + 域名分布（最多 3 个，超出只报总数）
        parts = []
        if info["bytes"]:
            parts.append(f"累计 {_fmt_bytes(info['bytes'])}")
        if info["domains"]:
            top = " · ".join(f"{name}×{count}" for name, count in info["domains"][:3])
            parts.append(f"域名 {len(info['domains'])} 个{(' · ' + top) if top else ''}")
        self._text(
            CRAWL_PAD, 140,
            clip("  |  ".join(parts) if parts else "— 等待抓取", 84),
            fill=DIM, font=FONT_SMALL,
        )

    def _draw_urls(self, state: CockpitState) -> None:
        """URL 清单：去重后最新在前，带状态、层号、耗时、字节与重复次数。"""
        self._section("URL 清单 // URLS", 158)
        rows = unique_fetches(state.fetches, _URL_ROWS)
        if not rows:
            self._text(CRAWL_PAD, 182, "— 暂无抓取", fill=DIM, font=FONT_SMALL)
            return
        y = 180
        for item in rows:
            ok = item.get("status") == "ok"
            self._text(CRAWL_PAD, y, "✓" if ok else "✗", fill=GREEN if ok else RED, font=FONT_TEXT)
            # 层号：Crawl 提供 depth 时显示 L0/L1…；单页抓取无深度显示 L-
            depth = item.get("depth")
            layer = f"L{depth}" if isinstance(depth, int) else "L-"
            self._text(CRAWL_PAD + 16, y, layer, fill=CYAN_DIM, font=FONT_MONO)
            # URL 去掉协议前缀后按显示宽度截断；域名单独一列便于快速扫读
            url = str(item.get("url") or "")
            for prefix in ("https://", "http://"):
                if url.startswith(prefix):
                    url = url[len(prefix):]
                    break
            self._text(CRAWL_PAD + 40, y, clip(url, 38), fill=TEXT, font=FONT_MONO)
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
                self._text(CRAWL_W - CRAWL_PAD, y, meta, fill=DIM, font=FONT_MONO, anchor="e")
            y += 22

    def _draw_extracts(self, state: CockpitState) -> None:
        """抽取表：最近几页抽取出的结构化字段；点击行高亮对应拓扑节点。"""
        self._section("抽取 // EXTRACT", 326)
        rows = list(state.extracts)[-_EXTRACT_ROWS:]
        if not rows:
            self._text(
                CRAWL_PAD, 350,
                "— 暂无抽取（用 WebExtract 按 CSS 选择器抽字段）",
                fill=DIM, font=FONT_SMALL,
            )
            return
        y = 348
        for index, item in enumerate(rows):
            url = str(item.get("url") or "")
            selected = bool(url) and url == self._selected_url
            tags = ("extract_row", f"extract_idx:{index}")
            if selected:
                # 选中行加一层细框：与拓扑节点的高亮环对应
                self.canvas.create_rectangle(
                    CRAWL_PAD - 4, y - 9, CRAWL_W - CRAWL_PAD, y + 9,
                    outline=CYAN_DIM, tags=tags,
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
                fill=CYAN if selected else DIM, font=FONT_MONO, tags=tags,
            )
            self._extract_urls[index] = url
            y += 20

    def _draw_topology(self, state: CockpitState) -> None:
        """拓扑区（P3）：分层节点 + 父→子连线 + 队列统计，节点可点击联动。

        ``crawl_topology = off`` 时只保留统计；单页抓取没有 depth，显示
        “单页抓取，无拓扑”，不画假节点。
        """
        self._section("拓扑 // TOPOLOGY", 424)
        rows = [item for item in state.fetches if isinstance(item.get("depth"), int)]
        if not rows:
            hint = "— 单页抓取，无拓扑" if state.fetches else "— 等待拓扑数据"
            self._text(CRAWL_PAD, 442, hint, fill=DIM, font=FONT_SMALL)
            self._draw_stats(state)
            return
        if self._options.crawl_topology_on():
            layout = build_topology(
                state.fetches,
                width=CRAWL_W - 2 * CRAWL_PAD,
                height=_TOPO_BOTTOM - _TOPO_TOP,
                x0=CRAWL_PAD,
                y0=_TOPO_TOP,
                max_nodes=self._options.crawl_max_nodes,
            )
            self._draw_topo_layout(layout)
        else:
            self._text(
                CRAWL_PAD, 442,
                "— 拓扑已关闭（[visualization] crawl_topology = off）",
                fill=DIM, font=FONT_SMALL,
            )
        self._draw_stats(state)

    def _draw_topo_layout(self, layout: TopologyLayout) -> None:
        """按布局画边、节点与 +N 聚合；选中节点加亮环。"""
        if layout.is_empty():
            self._text(CRAWL_PAD, 442, "— 无拓扑数据", fill=DIM, font=FONT_SMALL)
            return
        # 边先画，节点覆盖在线条上方
        for parent, child in layout.edges:
            self._line(parent.x, parent.y, child.x, child.y, CYAN_DIM)
        for index, node in enumerate(layout.nodes):
            color = GREEN if node.status == "ok" else (RED if node.status == "error" else DIM)
            tags = ("topo_node", f"topo_idx:{index}")
            selected = node.url == self._selected_url
            if selected:
                self.canvas.create_oval(
                    node.x - 7, node.y - 7, node.x + 7, node.y + 7,
                    outline=CYAN, width=1, tags=tags,
                )
            self.canvas.create_oval(
                node.x - 4, node.y - 4, node.x + 4, node.y + 4,
                fill=color, outline="", tags=tags,
            )
            self._text(
                node.x, node.y + 12, clip(node.label, 10),
                fill=CYAN if selected else DIM, font=FONT_SMALL, anchor="n", tags=tags,
            )
            self._topo_urls[index] = node.url
        for item in layout.aggregates:
            self._text(
                item["x"], item["y"], f"+{item['count']}",
                fill=DIM, font=FONT_SMALL, anchor="center",
            )

    def _draw_stats(self, state: CockpitState) -> None:
        """拓扑区底部统计行：队列/完成/失败/去重/深度/字节/耗时。"""
        stats = state.stats or {}
        if not stats:
            return
        parts = []
        for label, key in (
            ("队列", "queued"), ("完成", "done"), ("失败", "failed"), ("去重", "dedup_hits")
        ):
            if stats.get(key) is not None:
                parts.append(f"{label} {stats.get(key)}")
        if stats.get("max_depth") is not None:
            parts.append(f"深度 {stats.get('max_depth')}")
        if stats.get("bytes") is not None:
            parts.append(f"字节 {_fmt_bytes(stats.get('bytes'))}")
        if stats.get("elapsed_ms") is not None:
            parts.append(f"{float(stats['elapsed_ms']) / 1000:.1f}s")
        self._text(
            CRAWL_PAD, _STATS_Y, clip(" · ".join(parts) or "—", 88),
            fill=CYAN_DIM, font=FONT_MONO,
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
        # 最近一次渲染所属轮次（用户点 ✕ 时据此判定“本轮不再弹”）
        self._run_id = ""
        # 当前是否允许置顶（终端在前台时由 zorder 策略置 False）
        self._topmost_allowed = True

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
        if self._hidden and self._hidden_run_id == run_id:
            return
        # 新一轮（或从未关过）：解除隐藏标记，后续 show_all 也同步生效
        self._hidden = False
        window = self._ensure_window()
        if window is None:
            return
        try:
            window.render(state)
            window.show()
        except Exception as exc:  # noqa: BLE001 - 抓取窗故障不能影响主流程
            # 渲染异常不能静默：否则窗口只剩半截内容（P3 实测拓扑整块空白）。
            # @author aceFelix
            from agent.core.diag import diag_info

            diag_info("ui", f"爬虫驾驶舱渲染失败: {exc}")

    def hide_all(self) -> None:
        """主 HUD 关闭时隐藏（不销毁、不影响下轮重现）。"""
        if self._window is not None:
            self._window.hide()

    def set_topmost(self, enabled: bool) -> None:
        """切换置顶并记住状态：终端在前台时新弹的抓取窗也应让位。"""
        self._topmost_allowed = bool(enabled)
        if self._window is not None:
            self._window.set_topmost(enabled)

    def show_all(self, state: CockpitState) -> None:
        """主 HUD 重现时同步显示（有数据才显示）。"""
        if self._disabled or not self.options.crawl_on():
            return
        if self._window is None or not self.has_data(state):
            return
        try:
            self._window.render(state)
            self._window.show()
        except Exception as exc:  # noqa: BLE001 - 抓取窗故障不能影响主流程
            from agent.core.diag import diag_info

            diag_info("ui", f"爬虫驾驶舱渲染失败: {exc}")

    def detach(self) -> None:
        """销毁窗口并断开 Tk 根引用（退出时防跨线程 GC 崩溃）。"""
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self.root = None

    # ---- 内部 ----

    def _ensure_window(self) -> CrawlWindow | None:
        """懒创建窗口；失败置 disabled，不影响主 HUD。"""
        if self._window is not None:
            return self._window
        try:
            self._window = CrawlWindow(self.root, self.options, self._on_close)
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
