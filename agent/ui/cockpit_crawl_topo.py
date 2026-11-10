"""爬虫拓扑布局（P3/P3.5）：分层 DAG → 画布坐标（纯函数，可单测）。

输入 ``CockpitState.fetches`` 里由 Crawl 派生的 ``web_fetch``（带
``parent_url`` / ``depth`` / ``status``），输出节点、边、聚合占位与坐标；
Tk 层负责缩放/平移和点击命中，本模块不保存任何视图状态。

布局规则（2026-11-10 实测后调整）：
- 层号 = depth，同层按发现顺序从左到右，根页居中；
- 单层默认最多 ``max_per_layer`` 个节点，超出用可点击的 ``+N`` 聚合占位；
- ``layer_limits`` 可按层展开更多节点（点击 +N 时使用），总预算仍受
  ``max_nodes`` 限制；
- 节点标签取路径尾部语义段，避免一长串相同前缀挤在一起。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlsplit

NODE_RADIUS = 4
LABEL_LIMIT = 16


@dataclass(frozen=True)
class TopoNode:
    """拓扑节点：一个去重后的页面及其画布坐标。

    Attributes:
        url: 页面 URL（去重后的规范键）。
        label: 展示用短标签（path + query，截断）。
        depth: 层号（根页为 0）。
        status: ``ok`` / ``error`` / ``unknown``，决定节点颜色。
        x / y: 画布坐标。
    """

    url: str
    label: str
    depth: int
    status: str
    x: float
    y: float


@dataclass
class TopologyLayout:
    """一次拓扑布局结果。

    ``aggregates`` 每项为 ``{"depth", "count", "x", "y"}``，
    表示该层被折叠的 ``count`` 个节点，UI 画成 ``+N``。
    """

    nodes: list[TopoNode] = field(default_factory=list)
    edges: list[tuple[TopoNode, TopoNode]] = field(default_factory=list)
    aggregates: list[dict[str, Any]] = field(default_factory=list)
    layers: int = 0

    def is_empty(self) -> bool:
        """没有任何可见节点或聚合占位。"""
        return not self.nodes and not self.aggregates


def short_label(url: str, limit: int = LABEL_LIMIT) -> str:
    """取 URL 的路径尾部语义段作为节点标签，超长截断。

    拓扑节点横向排列，若沿用完整 path，多个同目录页面会全部显示成相同
    前缀（实测 books.toscrape.com 一片 ``/catalogu…``）。因此标签优先取
    最后一个有语义的路径段；目录首页 ``index.html`` 不算语义段。

    Args:
        url: 页面 URL。
        limit: 最大字符数。

    Returns:
        根页返回 ``/``；无法解析时返回原串截断。
    """
    text = str(url or "")
    try:
        parsed = urlsplit(text)
    except ValueError:
        return text[:limit]
    if not parsed.scheme and not parsed.netloc:
        return text[:limit] or "/"
    segments = [part for part in (parsed.path or "").split("/") if part]
    if segments and segments[-1].lower() in {"index.html", "index.htm"}:
        segments.pop()
    label = segments[-1] if segments else "/"
    if parsed.query and label != "/":
        label = f"{label}?{parsed.query}"
    if len(label) > limit:
        return label[: max(1, limit - 1)] + "…"
    return label


def build_topology(
    fetches: Iterable[dict[str, Any]],
    *,
    width: float,
    height: float,
    x0: float = 0.0,
    y0: float = 0.0,
    max_nodes: int = 60,
    max_per_layer: int = 8,
    layer_limits: dict[int, int] | None = None,
) -> TopologyLayout:
    """把抓取记录排成分层拓扑（纯函数）。

    只消费带整数 ``depth`` 的记录：单页 WebFetch 没有层级，返回空布局，
    由 UI 显示“单页抓取，无拓扑”。同一 URL 只保留首次出现。

    Args:
        fetches: ``CockpitState.fetches``（Crawl 派生的事件记录）。
        width / height: 可用画布尺寸。
        x0 / y0: 画布左上角偏移。
        max_nodes: 可见节点总上限（含各层可见节点）。
        max_per_layer: 单层可见节点默认上限，超出折叠为 ``+N``。
        layer_limits: 可选的按层覆盖上限；点击 ``+N`` 展开时由 UI 传入。
            它只改变本层可见数，不突破 ``max_nodes`` 总预算。

    Returns:
        ``TopologyLayout``；无有效层级数据时 ``is_empty()`` 为真。
    """
    items = [item for item in fetches if isinstance(item.get("depth"), int)]
    if not items:
        return TopologyLayout()

    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    for item in items:
        url = str(item.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        ordered.append(item)

    layer_count = max(int(item["depth"]) for item in ordered) + 1
    buckets: dict[int, list[dict[str, Any]]] = {}
    parent_of: dict[str, str | None] = {}
    for item in ordered:
        url = str(item["url"])
        buckets.setdefault(int(item["depth"]), []).append(item)
        parent = item.get("parent_url")
        parent_of[url] = str(parent) if parent else None

    row_h = height / max(1, layer_count)
    budget = max(1, int(max_nodes))
    per_layer = max(2, int(max_per_layer))
    limits = layer_limits or {}
    nodes: list[TopoNode] = []
    aggregates: list[dict[str, Any]] = []
    visible_by_url: dict[str, TopoNode] = {}

    for depth in range(layer_count):
        layer = buckets.get(depth, [])
        if not layer:
            continue
        try:
            layer_limit = max(2, int(limits.get(depth, per_layer)))
        except (TypeError, ValueError):
            layer_limit = per_layer
        keep = min(len(layer), layer_limit, budget)
        visible = layer[:keep]
        hidden = len(layer) - keep
        # 多留一个位置给 +N，保证聚合占位不被挤出画布
        slots = len(visible) + (1 if hidden else 0)
        spacing = width / (slots + 1) if slots else width
        y = y0 + row_h * (depth + 0.5)
        for index, item in enumerate(visible):
            url = str(item["url"])
            node = TopoNode(
                url=url,
                label=short_label(url),
                depth=depth,
                status=str(item.get("status") or "unknown"),
                x=x0 + spacing * (index + 1),
                y=y,
            )
            nodes.append(node)
            visible_by_url[url] = node
        if hidden:
            aggregates.append(
                {
                    "depth": depth,
                    "count": hidden,
                    "x": x0 + spacing * (len(visible) + 1),
                    "y": y,
                }
            )
        budget -= keep
        if budget <= 0:
            # 预算用尽：更深层不再画节点，每层各留一个 +N 占位
            for deeper in range(depth + 1, layer_count):
                rest = buckets.get(deeper, [])
                if rest:
                    aggregates.append(
                        {
                            "depth": deeper,
                            "count": len(rest),
                            "x": x0 + width / 2,
                            "y": y0 + row_h * (deeper + 0.5),
                        }
                    )
            break

    edges: list[tuple[TopoNode, TopoNode]] = []
    for node in nodes:
        parent_url = parent_of.get(node.url)
        parent_node = visible_by_url.get(parent_url or "")
        if parent_node is not None:
            edges.append((parent_node, node))
    return TopologyLayout(
        nodes=nodes,
        edges=edges,
        aggregates=aggregates,
        layers=layer_count,
    )


__all__ = [
    "LABEL_LIMIT",
    "NODE_RADIUS",
    "TopoNode",
    "TopologyLayout",
    "build_topology",
    "short_label",
]
