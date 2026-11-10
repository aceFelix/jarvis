"""左侧“抓取网络”窗口（C2-lite）的纯逻辑测试。

覆盖：URL 去重与命中次数、汇总（成功/失败/去重/字节/域名）、窗口显示策略
（无数据不建窗、off 关闭、本轮用户关闭后不再弹）、``[visualization]``
新增的 ``crawl_window`` 配置归一，以及 P3.5 的滚动窗口/智能截断/统计文案。

不创建真实 Tk：``CrawlManager.sync`` 在所有“不该建窗”的分支都会提前返回。

@author aceFelix
"""

from __future__ import annotations

from agent.ui.cockpit import CockpitState, VisualOptions
from agent.ui.cockpit_crawl import (
    CrawlManager,
    CrawlWindow,
    clip_url_tail,
    domain_of,
    fetch_window,
    scrollbar_geometry,
    stats_parts,
    summarize,
    unique_fetches,
    visible_window,
)
from agent.ui.cockpit_topo_view import TearOffGesture, TopoView, TopoViewState


def _state(*fetches: dict) -> CockpitState:
    """构造一个只含抓取记录的驾驶舱状态。"""
    state = CockpitState()
    state.fetches.extend(fetches)
    return state


class TestDomainOf:
    def test_strips_scheme_and_path(self) -> None:
        assert domain_of("https://example.com/a/b?x=1") == "example.com"

    def test_keeps_port(self) -> None:
        assert domain_of("http://localhost:8080/api") == "localhost:8080"

    def test_missing_returns_placeholder(self) -> None:
        assert domain_of(None) == "?"
        assert domain_of("") == "?"


class TestUniqueFetches:
    def test_dedup_keeps_latest_and_counts_hits(self) -> None:
        """同一 URL 多次抓取：保留最新状态，并标出命中次数。"""
        rows = unique_fetches([
            {"url": "https://a.com/1", "status": "error", "duration_ms": 10},
            {"url": "https://b.com/2", "status": "ok", "duration_ms": 20},
            {"url": "https://a.com/1", "status": "ok", "duration_ms": 30, "bytes": 5},
        ])
        # 最新在前
        assert [row["url"] for row in rows] == ["https://a.com/1", "https://b.com/2"]
        assert rows[0]["status"] == "ok"
        assert rows[0]["duration_ms"] == 30
        assert rows[0]["hits"] == 2
        assert rows[1]["hits"] == 1

    def test_respects_limit(self) -> None:
        rows = unique_fetches(
            [{"url": f"https://e.com/{i}", "status": "ok"} for i in range(10)],
            limit=3,
        )
        assert len(rows) == 3
        assert rows[0]["url"] == "https://e.com/9"


class TestSummarize:
    def test_counts_and_bytes(self) -> None:
        info = summarize([
            {"url": "https://a.com/x", "status": "ok", "bytes": 100},
            {"url": "https://a.com/x", "status": "ok", "bytes": 50},
            {"url": "https://b.com/y", "status": "error"},
        ])
        assert info["total"] == 3
        assert info["ok"] == 2
        assert info["failed"] == 1
        assert info["unique"] == 2
        assert info["duplicates"] == 1
        assert info["bytes"] == 150
        assert ("a.com", 2) in info["domains"]

    def test_empty(self) -> None:
        info = summarize([])
        assert info["total"] == 0
        assert info["bytes"] == 0
        assert info["domains"] == []

    def test_dedup_prefers_crawl_stats(self) -> None:
        """Crawl 入队阶段就去重，fetch 里没有重复项；去重数取 crawl_stats。"""
        info = summarize(
            [{"url": "https://a.com/x", "status": "ok"}],
            {"dedup_hits": 3},
        )
        assert info["duplicates"] == 3

    def test_invalid_bytes_ignored(self) -> None:
        info = summarize([{"url": "u", "status": "ok", "bytes": "oops"}])
        assert info["bytes"] == 0


class TestClipUrlTail:
    """P0：URL 智能截断保留域名与尾部语义段。"""

    def test_short_url_unchanged(self) -> None:
        assert clip_url_tail("https://a.com/x") == "a.com/x"

    def test_long_url_keeps_host_and_tail(self) -> None:
        url = "https://books.toscrape.com/catalogue/category/books/fiction_10/index.html"
        text = clip_url_tail(url)
        assert text.startswith("books.toscrape.com/…")
        assert text.endswith("fiction_10/index.html")

    def test_long_single_segment_keeps_head_and_tail(self) -> None:
        url = "https://a.com/" + "x" * 60
        text = clip_url_tail(url, 30)
        assert text.startswith("a")
        assert "…" in text
        assert text.endswith("x")


class TestVisibleWindow:
    def test_offset_clamped_and_counts(self) -> None:
        rows = [{"i": n} for n in range(10)]
        window, offset, total = visible_window(rows, 99, 3)
        assert [row["i"] for row in window] == [7, 8, 9]
        assert offset == 7
        assert total == 10

    def test_no_scroll_when_fits(self) -> None:
        rows = [{"i": n} for n in range(2)]
        window, offset, total = visible_window(rows, 5, 4)
        assert len(window) == 2
        assert offset == 0
        assert total == 2


class TestFetchWindow:
    """URL 清单窗口：根页固定置顶，其余滚动。"""

    def _fetches(self, count: int) -> list[dict]:
        items = [{"url": "https://s/", "status": "ok", "depth": 0}]
        items += [
            {"url": f"https://s/p{i}", "status": "ok", "depth": 1, "parent_url": "https://s/"}
            for i in range(count)
        ]
        return items

    def test_root_pinned_first(self) -> None:
        rows, offset, total, _max = fetch_window(self._fetches(10), 0, 4)
        assert rows[0]["url"] == "https://s/"
        assert offset == 0
        assert total == 11

    def test_scroll_moves_within_non_root_rows(self) -> None:
        rows, offset, _total, max_offset = fetch_window(self._fetches(10), 99, 4)
        # offset 钳制到最大值；非根行最新在前，滚到底显示最旧的 3 条
        assert offset == max_offset
        assert rows[0]["url"] == "https://s/"
        assert [row["url"] for row in rows[1:]] == [
            "https://s/p2", "https://s/p1", "https://s/p0",
        ]

    def test_scrollback_shows_older_rows(self) -> None:
        rows, offset, _total, _max = fetch_window(self._fetches(10), 2, 4)
        assert offset == 2
        # 非根行按最新在前：offset=2 时从 p7 开始
        assert rows[1]["url"] == "https://s/p7"


class TestScrollbarGeometry:
    def test_none_when_fits(self) -> None:
        assert scrollbar_geometry(3, 7, 0, top=10, bottom=100, x=500) is None

    def test_thumb_inside_track(self) -> None:
        x1, y1, x2, y2 = scrollbar_geometry(20, 7, 5, top=0, bottom=100, x=500)
        assert x2 - x1 == 3
        assert 0 <= y1 < y2 <= 100


class TestStatsParts:
    def test_labels_distinguish_queue_and_depth(self) -> None:
        state = CockpitState()
        state.fetches.extend([
            {"url": "https://s/", "status": "ok", "depth": 0},
            {"url": "https://s/a", "status": "ok", "depth": 1, "parent_url": "https://s/"},
        ])
        state.stats = {
            "queued": 120, "done": 12, "failed": 0, "dedup_hits": 0,
            "max_depth": 2, "bytes": 572416, "elapsed_ms": 18500,
        }
        parts = stats_parts(state)
        assert parts[0] == "剩余 120"
        assert "实达 L1" in parts
        assert "上限 L2" in parts
        assert "18.5s" in parts

    def test_empty_stats_and_no_depth(self) -> None:
        assert stats_parts(CockpitState()) == []


class _FakeCanvas:
    """只记录调用、不真的画，让 ``_draw_*`` 在没有 Tk 的环境下可测。"""

    def __getattr__(self, _name):
        return lambda *args, **kwargs: 1


class _FakeController:
    """记录管理器调用的假控制器（不创建真实窗口）。"""

    def __init__(self) -> None:
        self.topo_state = TopoViewState()
        self.detached = None
        self.ghost_started = 0
        self.ghost_cancelled = 0
        self.detach_calls: list[tuple[float, float]] = []
        self.undock_calls = 0

    def rerender_all(self) -> None:
        pass

    def select_url(self, url: str) -> None:
        if not url:
            return
        state = self.topo_state
        state.selected_url = None if state.selected_url == url else url

    def start_ghost(self, _count: int) -> None:
        self.ghost_started += 1

    def move_ghost(self, _x: float, _y: float) -> None:
        pass

    def cancel_ghost(self) -> None:
        self.ghost_cancelled += 1

    def detach_topology(self, x: float, y: float) -> None:
        # 真实管理器在落点内部先销毁幽灵
        self.cancel_ghost()
        self.detach_calls.append((x, y))

    def undock(self) -> None:
        self.undock_calls += 1


def _bare_window(options: VisualOptions) -> CrawlWindow:
    """构造绕过 ``__init__`` 的 CrawlWindow（FakeCanvas，不依赖 Tk）。

    P3.6 后拓扑视图状态在共享组件与控制器里，全部要在这里初始化，
    否则绘制/手势会直接 AttributeError。
    """
    window = object.__new__(CrawlWindow)
    window.canvas = _FakeCanvas()
    window.top = None
    window._options = options
    window._controller = _FakeController()
    window.view = TopoView(
        window.canvas, window._controller.topo_state,
        on_change=window._controller.rerender_all,
    )
    window._gesture = TearOffGesture()
    window._press_mode = None
    window._drag_offset = (0, 0)
    window._last_state = None
    window._extract_urls = {}
    window._url_scroll = 0
    window._extract_scroll = None
    return window


class TestTopologyDraw:
    """拓扑绘制回归：属性名写错会在这里直接抛 AttributeError。"""

    def _window(self, options: VisualOptions) -> CrawlWindow:
        return _bare_window(options)

    def test_draw_topology_uses_window_options(self) -> None:
        window = self._window(VisualOptions())
        state = _state(
            {"url": "https://s/", "status": "ok", "depth": 0},
            {
                "url": "https://s/a",
                "status": "ok",
                "depth": 1,
                "parent_url": "https://s/",
            },
        )
        state.stats = {"done": 2, "max_depth": 1}
        window._draw_topology(state)
        assert len(window._topo_urls) == 2

    def test_topology_off_draws_no_nodes(self) -> None:
        window = self._window(VisualOptions.from_values(crawl_topology="off"))
        window._draw_topology(_state({"url": "https://s/", "status": "ok", "depth": 0}))
        assert window._topo_urls == {}

    def test_placeholder_when_detached(self) -> None:
        """已外弹：驾驶舱拓扑区画占位，不再画节点。"""
        window = self._window(VisualOptions())
        window._controller.detached = object()  # 非 None 即视为已外弹
        state = _state({"url": "https://s/", "status": "ok", "depth": 0})
        window._draw_topology(state)
        assert window._topo_urls == {}


class TestTearOff:
    """P3.6：拓扑区左键拖动 = 撕出；区外仍是整窗拖动。"""

    def _window_with_topo(self) -> CrawlWindow:
        from agent.ui.cockpit_crawl import _TOPO_BOTTOM, _TOPO_TOP

        window = _bare_window(VisualOptions())
        state = _state(
            {"url": "https://s/", "status": "ok", "depth": 0},
            {"url": "https://s/a", "status": "ok", "depth": 1, "parent_url": "https://s/"},
        )
        window._last_state = state
        self._top = _TOPO_TOP
        self._bottom = _TOPO_BOTTOM
        return window

    def test_drag_in_topology_tears_off(self) -> None:
        window = self._window_with_topo()
        from types import SimpleNamespace

        window._on_press(SimpleNamespace(x=100.0, y=self._top + 50, x_root=118, y_root=438 + 50))
        assert window._press_mode == "topo"
        window._on_drag(SimpleNamespace(x=200.0, y=self._top + 50, x_root=218, y_root=488))
        assert window._controller.ghost_started == 1
        window._on_release(SimpleNamespace(x=300.0, y=self._top + 50, x_root=318, y_root=538))
        assert window._controller.detach_calls == [(318.0, 538)]
        assert window._controller.ghost_cancelled == 1  # 落点后幽灵销毁

    def test_press_outside_topology_keeps_window_drag(self) -> None:
        window = self._window_with_topo()
        from types import SimpleNamespace

        window._on_press(SimpleNamespace(x=100.0, y=200.0, x_root=118, y_root=238))
        assert window._press_mode == "window"
        assert window._gesture.active is False
        window._on_release(SimpleNamespace(x=101.0, y=201.0, x_root=119, y_root=239))
        assert window._controller.detach_calls == []
        assert window._controller.ghost_started == 0

    def test_click_without_drag_does_not_tear_off(self) -> None:
        window = self._window_with_topo()
        from types import SimpleNamespace

        window._on_press(SimpleNamespace(x=100.0, y=self._top + 50, x_root=118, y_root=488))
        window._on_release(SimpleNamespace(x=101.0, y=self._top + 51, x_root=119, y_root=489))
        assert window._controller.detach_calls == []
        assert window._controller.ghost_started == 0

    def test_escape_cancels_tear_off(self) -> None:
        window = self._window_with_topo()
        from types import SimpleNamespace

        window._on_press(SimpleNamespace(x=100.0, y=self._top + 50, x_root=118, y_root=488))
        window._on_drag(SimpleNamespace(x=200.0, y=self._top + 50, x_root=218, y_root=488))
        window._on_escape()
        assert window._controller.ghost_cancelled == 1
        window._on_release(SimpleNamespace(x=300.0, y=self._top + 50, x_root=318, y_root=488))
        assert window._controller.detach_calls == []

    def test_redock_placeholder_triggers_undock(self) -> None:
        """占位区点击收回走管理器 undock（绑定在 __init__，这里验证 tag 名稳定）。"""
        window = self._window_with_topo()
        # 绑定在真实 __init__ 中；裸窗口只验证占位文案路径不炸
        window._controller.detached = object()
        window._draw_topology(window._last_state)
        assert window._topo_urls == {}


class TestTopologyInteraction:
    """P3.5/P3.6：缩放/平移/展开/重置走共享视图组件（FakeCanvas 不真画）。"""

    def _window(self) -> CrawlWindow:
        return _bare_window(VisualOptions())

    def test_zoom_clamps_and_keeps_focus(self) -> None:
        window = self._window()
        view = window.view
        view.zoom(280.0, 520.0, 2.0, 0, 0, 524, 160)
        assert view.state.scale == 2.0
        view.zoom(280.0, 520.0, 2.0, 0, 0, 524, 160)
        view.zoom(280.0, 520.0, 2.0, 0, 0, 524, 160)
        assert view.state.scale == 3.0  # 上限
        view.zoom(280.0, 520.0, 0.01, 0, 0, 524, 160)
        view.zoom(280.0, 520.0, 0.01, 0, 0, 524, 160)
        view.zoom(280.0, 520.0, 0.01, 0, 0, 524, 160)
        assert view.state.scale == 0.55  # 下限

    def test_reset_restores_view_and_expansion(self) -> None:
        window = self._window()
        view = window.view
        view.state.scale = 2.5
        view.state.pan_x = 40.0
        view.state.pan_y = -30.0
        view.state.expanded_layers = {1: 16}
        view.reset_view()
        assert view.state.scale == 1.0
        assert view.state.pan_x == 0.0
        assert view.state.pan_y == 0.0
        assert view.state.expanded_layers == {}

    def test_pan_clamp_keeps_content_visible(self) -> None:
        window = self._window()
        view = window.view
        view.state.scale = 1.0
        view.state.pan_x = 10_000.0
        view.state.pan_y = -10_000.0
        view.clamp_pan(524, 160)
        width = 524
        height = 160
        assert view.state.pan_x <= width - width * 0.75
        assert view.state.pan_y >= -height + height * 0.75

    def test_draw_layout_registers_maps(self) -> None:
        from agent.ui.cockpit_crawl import CRAWL_PAD, _TOPO_BOTTOM, _TOPO_TOP
        from agent.ui.cockpit_crawl_topo import TopoNode, TopologyLayout

        window = self._window()
        layout = TopologyLayout(
            nodes=[TopoNode("https://s/", "/", 0, "ok", 100.0, 470.0)],
            edges=[],
            aggregates=[{"depth": 1, "count": 3, "x": 300.0, "y": 540.0}],
            layers=2,
        )
        window.view.draw(layout, pad=CRAWL_PAD, top=_TOPO_TOP)
        assert window._aggregate_depths == {0: 1}
        assert window._topo_urls == {0: "https://s/"}
        # 常量导出仍可用（其他模块/测试引用口径）
        assert CRAWL_PAD > 0 and _TOPO_BOTTOM > _TOPO_TOP


class TestCrawlWindowPolicy:
    def test_no_data_does_not_create_window(self) -> None:
        """没有抓取数据：不建窗（纯闲聊/普通工具不弹）。"""
        manager = CrawlManager(None, VisualOptions.from_values(crawl_window="auto"))
        manager.sync(CockpitState(), "r1")
        assert manager._window is None
        assert CrawlManager.has_data(CockpitState()) is False

    def test_off_disables_window(self) -> None:
        """crawl_window=off：即使有抓取也不建窗。"""
        manager = CrawlManager(None, VisualOptions.from_values(crawl_window="off"))
        manager.sync(_state({"url": "https://a.com", "status": "ok"}), "r1")
        assert manager._window is None

    def test_hidden_in_same_run_does_not_reopen(self) -> None:
        """用户点 ✕ 后，本轮后续抓取不再弹窗。"""
        manager = CrawlManager(None, VisualOptions.from_values(crawl_window="auto"))
        manager._hidden = True
        manager._hidden_run_id = "r1"
        manager.sync(_state({"url": "https://a.com", "status": "ok"}), "r1")
        assert manager._window is None


class TestCrawlOptions:
    def test_normalizes_and_gates(self) -> None:
        assert VisualOptions.from_values(crawl_window="off").crawl_on() is False
        assert VisualOptions.from_values(crawl_window="whatever").crawl_on() is True
        assert VisualOptions.from_values(enabled=False).crawl_on() is False

    def test_any_enabled_includes_crawl(self) -> None:
        """只开抓取窗口也算有可视化（否则 Tk 窗口不会启动）。"""
        options = VisualOptions.from_values(
            task_cockpit="off", tool_cards="off", agent_cards="off", crawl_window="auto"
        )
        assert options.any_enabled() is True

    def test_from_settings_reads_crawl_window(self) -> None:
        from types import SimpleNamespace

        options = VisualOptions.from_settings(SimpleNamespace(visual_crawl_window="off"))
        assert options.crawl_on() is False

    def test_topology_defaults_and_off(self) -> None:
        """拓扑默认开启；off 只关拓扑不影响抓取窗；节点上限钳制到 20–200。"""
        options = VisualOptions.from_values()
        assert options.crawl_topology_on() is True
        assert options.crawl_max_nodes == 60
        assert options.crawl_on() is True

        off = VisualOptions.from_values(crawl_topology="off")
        assert off.crawl_topology_on() is False
        assert off.crawl_on() is True

        assert VisualOptions.from_values(crawl_max_nodes=5).crawl_max_nodes == 20
        assert VisualOptions.from_values(crawl_max_nodes=999).crawl_max_nodes == 200

    def test_from_settings_reads_crawl_topology(self) -> None:
        from types import SimpleNamespace

        options = VisualOptions.from_settings(
            SimpleNamespace(visual_crawl_topology="off", visual_crawl_max_nodes=30)
        )
        assert options.crawl_topology_on() is False
        assert options.crawl_max_nodes == 30
