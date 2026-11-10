"""爬虫驾驶舱的纯逻辑层：URL 截断、滚动窗口、汇总与统计文案。

不依赖 Tk，可单独单测；``cockpit_crawl`` 只负责绘制与事件，把这些函数
再导出以保持既有导入路径（``from agent.ui.cockpit_crawl import summarize``）。

2026-11-10 拆出：``cockpit_crawl.py`` 涨到 1070 行触发 900 行门禁
（``tests/test_engine_split_guards.py``），按"纯逻辑 / Tk 视图"分层拆包。

@author aceFelix
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from agent.ui.cockpit import FETCH_MAX, CockpitState, _fmt_bytes
from agent.ui.hud_theme import display_width

# 清单最多展示多少条去重 URL、抽取表最多几行（窗口高度固定）
URL_ROWS = 7
EXTRACT_ROWS = 4


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


def _strip_scheme(url: Any) -> str:
    """去掉 http/https 协议前缀，供清单紧凑显示。"""
    text = str(url or "")
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _tail_to_width(text: str, limit: int) -> str:
    """按显示宽度保留字符串尾部。"""
    out: list[str] = []
    used = 0
    for ch in reversed(str(text)):
        width = 2 if ord(ch) > 0x2E7F else 1
        if used + width > limit:
            break
        out.append(ch)
        used += width
    return "".join(reversed(out))


def clip_url_tail(url: Any, limit: int = 44) -> str:
    """URL 智能截断：保留域名和路径尾部，避免同类页面全显示成相同前缀。

    Args:
        url: 原始 URL。
        limit: 最大显示宽度。

    Returns:
        类似 ``books.toscrape.com/…/fiction_10/index.html`` 的紧凑文本。
    """
    text = _strip_scheme(url)
    if display_width(text) <= limit:
        return text
    host, sep, path = text.partition("/")
    prefix_width = display_width(host) + (2 if sep else 1)  # '/' + '…'
    if sep and prefix_width < limit - 6:
        parts = [part for part in path.split("/") if part]
        # index.html 本身没有辨识度，连同上一级目录一起保留
        tail_parts = parts[-2:] if parts and parts[-1].lower() in {"index.html", "index.htm"} else parts[-1:]
        tail = "/".join(tail_parts)
        tail = _tail_to_width(tail, limit - prefix_width)
        return f"{host}/…{tail}"
    head_limit = max(4, limit // 2 - 1)
    return f"{text[:head_limit]}…{_tail_to_width(text, limit - head_limit - 1)}"


def _clamp(value: int, low: int, high: int) -> int:
    """把整数钳制到闭区间。"""
    return max(low, min(high, value))


def visible_window(
    items: Iterable[dict[str, Any]], offset: int, limit: int
) -> tuple[list[dict[str, Any]], int, int]:
    """按 offset 取一个可见窗口，并返回 ``(窗口, 钳制后 offset, 总条数)``。"""
    rows = list(items)
    max_offset = max(0, len(rows) - max(1, limit))
    start = _clamp(int(offset), 0, max_offset)
    return rows[start:start + max(1, limit)], start, len(rows)


def fetch_window(
    fetches: Iterable[dict[str, Any]],
    offset: int,
    limit: int = URL_ROWS,
) -> tuple[list[dict[str, Any]], int, int, int]:
    """URL 清单窗口：根页固定置顶，其余按最新在前滚动。

    Returns:
        ``(可见行, 钳制后 offset, 总条数, 最大 offset)``；offset 只作用于根页以外。
    """
    rows = unique_fetches(fetches, FETCH_MAX)
    root_index = next(
        (i for i, row in enumerate(rows)
         if isinstance(row.get("depth"), int) and row["depth"] == 0),
        None,
    )
    pinned: list[dict[str, Any]] = []
    if root_index is not None:
        pinned.append(rows.pop(root_index))
    capacity = max(1, limit - len(pinned))
    max_offset = max(0, len(rows) - capacity)
    start = _clamp(int(offset), 0, max_offset)
    return pinned + rows[start:start + capacity], start, len(rows) + len(pinned), max_offset


def scrollbar_geometry(
    total: int,
    visible: int,
    offset: int,
    *,
    top: float,
    bottom: float,
    x: float,
) -> tuple[float, float, float, float] | None:
    """计算迷你滚动条滑块；无需滚动时返回 None。"""
    if total <= visible or total <= 0 or bottom <= top:
        return None
    height = bottom - top
    thumb_h = max(16.0, height * min(1.0, visible / total))
    max_offset = max(1, total - visible)
    y = top + (height - thumb_h) * (_clamp(offset, 0, max_offset) / max_offset)
    return (x, y, x + 3, y + thumb_h)


def unique_fetches(fetches: Iterable[dict[str, Any]], limit: int = URL_ROWS) -> list[dict[str, Any]]:
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


def stats_parts(state: CockpitState) -> list[str]:
    """底部统计文案：明确区分剩余队列、配置上限与实际到达深度。"""
    stats = state.stats or {}
    parts: list[str] = []
    for label, key in (
        ("剩余", "queued"), ("完成", "done"), ("失败", "failed"), ("去重", "dedup_hits")
    ):
        if stats.get(key) is not None:
            parts.append(f"{label} {stats.get(key)}")
    actual_depth = max(
        (int(item["depth"]) for item in state.fetches if isinstance(item.get("depth"), int)),
        default=None,
    )
    if actual_depth is not None:
        parts.append(f"实达 L{actual_depth}")
    if stats.get("max_depth") is not None:
        parts.append(f"上限 L{stats['max_depth']}")
    if stats.get("bytes") is not None:
        parts.append(f"字节 {_fmt_bytes(stats.get('bytes'))}")
    if stats.get("elapsed_ms") is not None:
        parts.append(f"{float(stats['elapsed_ms']) / 1000:.1f}s")
    return parts


__all__ = [
    "EXTRACT_ROWS",
    "URL_ROWS",
    "clip_url_tail",
    "domain_of",
    "fetch_window",
    "scrollbar_geometry",
    "stats_parts",
    "summarize",
    "unique_fetches",
    "visible_window",
]
