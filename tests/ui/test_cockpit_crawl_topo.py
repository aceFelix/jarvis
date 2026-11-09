"""爬虫拓扑布局（P3）测试：分层、坐标、边、聚合与空态。

纯函数，不依赖 Tk。

@author aceFelix
"""

from __future__ import annotations

from agent.ui.cockpit_crawl_topo import build_topology, short_label


def _node(url: str, depth: int, parent: str | None = None, status: str = "ok") -> dict:
    """构造一条 Crawl 派生的 web_fetch 记录。"""
    item = {"url": url, "status": status, "depth": depth}
    if parent:
        item["parent_url"] = parent
    return item


class TestShortLabel:
    def test_root_and_path(self) -> None:
        assert short_label("https://site.test/") == "/"
        assert short_label("https://site.test/a/b") == "/a/b"

    def test_query_and_truncate(self) -> None:
        assert short_label("https://site.test/a?b=1") == "/a?b=1"
        label = short_label("https://site.test/" + "x" * 40)
        assert len(label) == 16
        assert label.endswith("…")

    def test_non_url_falls_back(self) -> None:
        assert short_label("") == "/"


class TestBuildTopology:
    def test_layers_coords_and_edges(self) -> None:
        layout = build_topology(
            [
                _node("https://s/", 0),
                _node("https://s/a", 1, "https://s/"),
                _node("https://s/b", 1, "https://s/", status="error"),
                _node("https://s/a1", 2, "https://s/a"),
            ],
            width=400,
            height=120,
            x0=20,
            y0=40,
        )
        assert layout.layers == 3
        assert len(layout.nodes) == 4
        assert len(layout.edges) == 3
        root = layout.nodes[0]
        assert root.depth == 0
        # 根页在画布水平居中
        assert abs(root.x - (20 + 400 / 2)) < 0.01
        assert layout.nodes[1].y > root.y
        assert layout.nodes[3].y > layout.nodes[1].y
        # 失败节点保留状态，交给 UI 标红
        assert any(node.status == "error" for node in layout.nodes)
        # 每条边都是父层 → 子层
        for parent, child in layout.edges:
            assert parent.depth < child.depth

    def test_single_page_without_depth_is_empty(self) -> None:
        """单页 WebFetch 没有 depth：返回空布局，UI 显示“单页抓取，无拓扑”。"""
        layout = build_topology(
            [{"url": "https://s/", "status": "ok"}], width=400, height=120
        )
        assert layout.is_empty() is True

    def test_duplicate_urls_are_ignored(self) -> None:
        layout = build_topology(
            [_node("https://s/", 0), _node("https://s/", 0)], width=400, height=120
        )
        assert len(layout.nodes) == 1

    def test_per_layer_aggregation(self) -> None:
        """单层超宽时折叠为 +N，避免标签重叠。"""
        fetches = [_node("https://s/", 0)]
        fetches += [
            _node(f"https://s/{index}", 1, "https://s/") for index in range(6)
        ]
        layout = build_topology(
            fetches, width=400, height=120, max_per_layer=4
        )
        layer1 = [node for node in layout.nodes if node.depth == 1]
        assert len(layer1) == 4
        aggregates = [item for item in layout.aggregates if item["depth"] == 1]
        assert aggregates and aggregates[0]["count"] == 2

    def test_total_node_budget_aggregates_deeper_layers(self) -> None:
        """总节点超限后，更深层不再画节点，各层留 +N。"""
        layout = build_topology(
            [
                _node("https://s/0", 0),
                _node("https://s/1", 1, "https://s/0"),
                _node("https://s/2", 2, "https://s/1"),
                _node("https://s/3", 3, "https://s/2"),
                _node("https://s/4", 4, "https://s/3"),
            ],
            width=400,
            height=120,
            max_nodes=3,
        )
        assert len(layout.nodes) == 3
        aggregate_depths = {item["depth"] for item in layout.aggregates}
        assert aggregate_depths == {3, 4}
        assert all(item["count"] == 1 for item in layout.aggregates)
