"""左侧“抓取网络”窗口（C2-lite）的纯逻辑测试。

覆盖：URL 去重与命中次数、汇总（成功/失败/去重/字节/域名）、窗口显示策略
（无数据不建窗、off 关闭、本轮用户关闭后不再弹）以及 ``[visualization]``
新增的 ``crawl_window`` 配置归一。

不创建真实 Tk：``CrawlManager.sync`` 在所有“不该建窗”的分支都会提前返回。

@author aceFelix
"""

from __future__ import annotations

from agent.ui.cockpit import CockpitState, VisualOptions
from agent.ui.cockpit_crawl import (
    CrawlManager,
    CrawlWindow,
    domain_of,
    summarize,
    unique_fetches,
)


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


class _FakeCanvas:
    """只记录调用、不真的画，让 ``_draw_*`` 在没有 Tk 的环境下可测。"""

    def __getattr__(self, _name):
        return lambda *args, **kwargs: 1


class TestTopologyDraw:
    """拓扑绘制回归：属性名写错会在这里直接抛 AttributeError。"""

    def _window(self, options: VisualOptions) -> CrawlWindow:
        window = object.__new__(CrawlWindow)
        window.canvas = _FakeCanvas()
        window._options = options
        window._selected_url = None
        window._topo_urls = {}
        window._extract_urls = {}
        return window

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
