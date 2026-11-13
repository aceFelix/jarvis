"""WebSearch 搜索引擎实现注册表 —— 多引擎 fallback 的底层能力。

设计要点（@author aceFelix）:
1. 每个引擎是一个 `async f(query, max_results) -> list[dict]`，失败抛异常、
   无结果返回空列表，由 WebSearchTool 按 _SEARCH_ENGINES 顺序轮询降级。
2. 引擎顺序按国内可达性排: Bing（境内直连可用）优先，DuckDuckGo HTML /
   lite 两个端点兜底（需代理，但页面结构极简、解析稳定）。
3. HTTP 层与 web.py 的 WebFetch 同策略: 优先 requests，未安装降级标准库
   urllib；请求头伪装浏览器 UA；请求走 run_in_executor 不阻塞事件循环。
4. 纯解析函数（parse_* / decode_ddg_redirect）与网络层分离，便于单测。
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import quote_plus, unquote

from agent.tools.web import net

# 单引擎 HTTP 请求超时（秒）。比 WebFetch 的 15s 略短：
# 后面还排着其他引擎和浏览器兜底，单引擎不要耗太久。
_ENGINE_TIMEOUT = 10.0

# 伪装浏览器 UA，与 web.py 保持一致，避免被引擎侧拒绝
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# HTTP 底层（requests 优先，ImportError 降级 urllib，同步函数进 executor 调）
# ---------------------------------------------------------------------------


def _post_form(url: str, body: bytes, *, timeout: float = _ENGINE_TIMEOUT) -> str:
    """POST 表单并返回响应文本。requests 优先、urllib 兜底。

    出口与 WebFetch 同源（见 web/net.py）：环境变量 → 系统代理 → 直连。
    近期被判不可达的主机直接跳过，不再白等一轮超时。
    """
    if net.is_unreachable(url):
        raise ConnectionError(f"{net.host_of(url)} 近期不可达，已跳过")
    headers = {
        "User-Agent": _UA,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        import requests  # type: ignore
        resp = requests.post(
            url, data=body, headers=headers, timeout=timeout,
            proxies=net.proxies_for(url),
        )
        net.forget(url)
        return resp.text
    except ImportError:
        pass
    import urllib.request
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _get_url(url: str, *, timeout: float = _ENGINE_TIMEOUT) -> str:
    """GET 并返回响应文本。requests 优先、urllib 兜底。出口策略同 _post_form。"""
    if net.is_unreachable(url):
        raise ConnectionError(f"{net.host_of(url)} 近期不可达，已跳过")
    headers = {"User-Agent": _UA}
    try:
        import requests  # type: ignore
        resp = requests.get(url, headers=headers, timeout=timeout)
        return resp.text
    except ImportError:
        pass
    import urllib.request
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# HTML 解析（纯函数，单测覆盖）
# ---------------------------------------------------------------------------


def decode_ddg_redirect(raw_url: str) -> str:
    """把 DuckDuckGo 的重定向链接还原为真实 URL。

    DDG 结果页链接形如 //duckduckgo.com/l/?uddg=<encoded>&rut=...，
    需解出 uddg 参数；非重定向链接原样返回。
    """
    m = re.search(r"uddg=([^&]+)", raw_url)
    return unquote(m.group(1)) if m else raw_url


def _strip_tags(html_fragment: str) -> str:
    """去掉 HTML 标签，返回纯文本。"""
    return re.sub(r"<[^>]+>", "", html_fragment).strip()


def parse_bing_html(html: str, max_results: int) -> list[dict[str, str]]:
    """解析 Bing 搜索结果页 HTML。

    Bing 的 GET 端点（cn.bing.com/search）返回静态 HTML，结果块为
    `<li class="b_algo">`，标题链接在块内第一个 <h2><a href>，
    摘要在 <p class="b_lineclamp...">（class 名多形态，前缀匹配即可）。
    """
    results: list[dict[str, str]] = []
    blocks = re.split(r'<li class="b_algo"', html)[1:]
    for block in blocks:
        try:
            m = re.search(
                r"<h2[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
                block,
                re.DOTALL,
            )
            if not m:
                continue
            url = m.group(1).strip()
            title = _strip_tags(m.group(2))
            if not url.startswith("http") or not title:
                continue
            snippet = ""
            sm = re.search(
                r'<p class="b_lineclamp[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL
            )
            if sm:
                snippet = _strip_tags(sm.group(1))
            results.append({"title": title, "url": url, "snippet": snippet})
            if len(results) >= max_results:
                break
        except Exception:
            continue
    return results


def parse_ddg_html(html: str, max_results: int) -> list[dict[str, str]]:
    """解析 DuckDuckGo HTML 端点（html.duckduckgo.com/html/）的结果页。

    结果块为 `<div class="result ...">`，标题链接 class="result__a"、
    摘要 class="result__snippet"，链接需经 decode_ddg_redirect 还原。
    """
    results: list[dict[str, str]] = []
    blocks = re.split(r'<div class="result ', html)[1:]
    for block in blocks:
        try:
            m = re.search(
                r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                block,
                re.DOTALL,
            )
            if not m:
                continue
            url = decode_ddg_redirect(m.group(1))
            title = _strip_tags(m.group(2))
            snippet = ""
            sm = re.search(
                r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                block,
                re.DOTALL,
            )
            if sm:
                snippet = _strip_tags(sm.group(1))
            if title and url:
                results.append({"title": title, "url": url, "snippet": snippet})
            if len(results) >= max_results:
                break
        except Exception:
            continue
    return results


def parse_ddg_lite_html(html: str, max_results: int) -> list[dict[str, str]]:
    """解析 DuckDuckGo lite 端点（lite.duckduckgo.com/lite/）的结果页。

    lite 页无 div 结果块结构：结果链接为 `<a rel="nofollow" href="..."
    class='result-link'>标题</a>`（href 在 class 之前），摘要在紧随其后的
    td.result-snippet。按 result-link 切块，块内完整匹配链接 + 摘要。
    """
    results: list[dict[str, str]] = []
    # 每个结果从 result-link 开始切块，块范围到下一个 result-link 之前
    parts = re.split(r"(?=<a[^>]*class=['\"]result-link['\"])", html)[1:]
    for part in parts:
        try:
            m = re.search(
                r"<a[^>]*href=\"([^\"]+)\"[^>]*class=['\"]result-link['\"]"
                r"[^>]*>(.*?)</a>",
                part,
                re.DOTALL,
            )
            if not m:
                continue
            url = decode_ddg_redirect(m.group(1))
            title = _strip_tags(m.group(2))
            snippet = ""
            sm = re.search(
                r'class="result-snippet"[^>]*>(.*?)</td>', part, re.DOTALL
            )
            if sm:
                snippet = _strip_tags(sm.group(1))
            if title and url:
                results.append({
                    "title": title, "url": url, "snippet": snippet,
                })
            if len(results) >= max_results:
                break
        except Exception:
            continue
    return results


# ---------------------------------------------------------------------------
# 引擎入口（async，供 WebSearchTool 轮询）
# ---------------------------------------------------------------------------


async def search_bing(query: str, max_results: int) -> list[dict[str, str]]:
    """Bing HTML 搜索。GET 请求，国内可直连（cn.bing.com），免 API key。"""
    loop = asyncio.get_event_loop()
    # mkt/setlang 决定分词与召回语种：中文 query 不带这两个参数时，
    # Bing 会把生僻人名拆成单字（"梁文锋"→"梁"，2026-11 实测）。
    url = (
        f"https://www.bing.com/search?q={quote_plus(query)}"
        f"&count={max_results}&mkt=zh-CN&setlang=zh-CN"
    )
    html = await loop.run_in_executor(None, _get_url, url)
    return parse_bing_html(html, max_results)


async def search_duckduckgo(query: str, max_results: int) -> list[dict[str, str]]:
    """DuckDuckGo HTML 端点搜索（POST，免 key，国内通常需代理）。"""
    loop = asyncio.get_event_loop()
    url = "https://html.duckduckgo.com/html/"
    body = f"q={quote_plus(query)}&b=&kl=".encode("utf-8")
    html = await loop.run_in_executor(None, _post_form, url, body)
    return parse_ddg_html(html, max_results)


async def search_duckduckgo_lite(
    query: str, max_results: int
) -> list[dict[str, str]]:
    """DuckDuckGo lite 端点搜索（POST，页面更简，HTML 端点改版时的备胎）。"""
    loop = asyncio.get_event_loop()
    url = "https://lite.duckduckgo.com/lite/"
    body = f"q={quote_plus(query)}".encode("utf-8")
    html = await loop.run_in_executor(None, _post_form, url, body)
    return parse_ddg_lite_html(html, max_results)


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def parse_360_html(html: str, max_results: int) -> list[dict[str, str]]:
    """解析 360 搜索（so.com）结果页。

    结果块为 `<li class="res-list ...">`，标题在 `<h3 class="res-title"><a ...>`。
    注意 `href` 是 so.com 的跳转链接，**真实地址在 `data-mdurl`**；只取 href
    会把下游抓取引到跳转页上。
    """
    results: list[dict[str, str]] = []
    for block in html.split('<li class="res-list')[1:]:
        try:
            m = re.search(
                r"<h3[^>]*>\s*<a[^>]*?href=\"[^\"]*\"([^>]*)>(.*?)</a>",
                block,
                re.DOTALL,
            )
            if not m:
                continue
            attrs, inner = m.group(1), m.group(2)
            title = _strip_tags(inner)
            md = re.search(r'data-mdurl="([^"]+)"', attrs)
            url = md.group(1).strip() if md else ""
            if not url.startswith("http") or not title:
                continue
            snippet = ""
            sm = re.search(r"<span data-result-summary>(.*?)</span>", block, re.DOTALL)
            if not sm:
                sm = re.search(r'<p class="res-desc"[^>]*>(.*?)</p>', block, re.DOTALL)
            if sm:
                snippet = _strip_tags(sm.group(1))
            results.append({"title": title, "url": url, "snippet": snippet})
            if len(results) >= max_results:
                break
        except Exception:
            continue
    return results


async def search_360(query: str, max_results: int) -> list[dict[str, str]]:
    """360 搜索 HTML 端点（境内直连，服务端渲染）。"""
    loop = asyncio.get_event_loop()
    url = f"https://www.so.com/s?q={quote_plus(query)}"
    html = await loop.run_in_executor(None, _get_url, url)
    return parse_360_html(html, max_results)


# 引擎顺序表: (显示名, 实现函数)。WebSearchTool 按 engines_for() 的顺序轮询，
# 抛异常或返回空结果都降级到下一个；全部失效后走浏览器/引导兜底。
#
# 为什么要分语种（2026-11 修）：原来这张表只有 Bing + DuckDuckGo 两路，
# 国内环境下 DDG 两个端点必然 ConnectTimeout（15s/10s 各一轮），而唯一能通的
# Bing 对中文生僻人名分词很差——"梁文锋"被当成汉字"梁"检索，前五条全是字典。
# 一次"查某人底细"就这样烧掉 5 次失败、246 秒。现在中文 query 先走境内引擎。
_SEARCH_ENGINES_ZH: list[tuple[str, Any]] = [
    ("360搜索", search_360),
    ("Bing", search_bing),
    ("DuckDuckGo", search_duckduckgo),
    ("DuckDuckGo-lite", search_duckduckgo_lite),
]

_SEARCH_ENGINES_EN: list[tuple[str, Any]] = [
    ("Bing", search_bing),
    ("DuckDuckGo", search_duckduckgo),
    ("DuckDuckGo-lite", search_duckduckgo_lite),
    ("360搜索", search_360),
]

# ---------------------------------------------------------------------------
# 可达性探测：按本机网络排引擎，而不是按语种猜
# ---------------------------------------------------------------------------
#
# 为什么需要（2026-11）：`engines_for()` 只看 query 有没有汉字，这在境内网络
# 碰巧对，但换环境必错——外国用户机器上 360 可能不可达却排第一；用户人在国外
# 时同一份静态表仍然先试境内源；Bing 某天被污染，静态表毫无察觉。
#
# 排序规则：**可达优先，按实测延迟升序；语种只作平手时的次序**。
# 探测与真实调用走同一条出口策略（含代理），否则结论与实际行为不一致。

# 探测缓存：引擎名 -> (可达, 延迟毫秒, 过期时间戳)
_REACH_TTL_S = 600.0
_REACH: dict[str, tuple[bool, float, float]] = {}

# 每个引擎的轻量探测地址：只读首块就断开，不下载整页。
_PROBE_URLS: dict[str, str] = {
    "360搜索": "https://www.so.com/",
    "Bing": "https://www.bing.com/",
    "DuckDuckGo": "https://html.duckduckgo.com/html/",
    "DuckDuckGo-lite": "https://lite.duckduckgo.com/lite/",
}

_PROBE_TIMEOUT = 1.5


def _probe_one(url: str) -> tuple[bool, float]:
    """探测单个地址是否可达。实现统一在 net.probe，这里只固定超时。"""
    return net.probe(url, timeout=_PROBE_TIMEOUT)


async def _ensure_reach(names: list[str]) -> None:
    """并发补齐这些引擎的可达性缓存（过期的重探）。"""
    now = time.monotonic()
    todo = [
        n
        for n in names
        if n in _PROBE_URLS and (n not in _REACH or _REACH[n][2] <= now)
    ]
    if not todo:
        return
    loop = asyncio.get_event_loop()
    results = await asyncio.gather(
        *(
            loop.run_in_executor(None, _probe_one, _PROBE_URLS[n])
            for n in todo
        ),
        return_exceptions=True,
    )
    stamp = time.monotonic() + _REACH_TTL_S
    for name, res in zip(todo, results):
        if isinstance(res, BaseException):
            _REACH[name] = (False, 0.0, stamp)
        else:
            ok, ms = res
            _REACH[name] = (ok, ms, stamp)


async def ordered_engines(query: str) -> list[tuple[str, Any]]:
    """按本机实测可达性给引擎顺序。

    **可达性是过滤器，语种是排序器**——这一点最初写错了：按纯延迟排的话，
    360 最快就永远排第一，连英文查询也交给它（360 是中文索引，英文质量差）。
    正确做法是：可达的那一组保持语种序，不可达的整组移到后面。

    - 国内 + 中文 → 360, Bing（语种序本来就是境内优先）
    - 国内 + 英文 → Bing, 360（DDG 不可达被移后，Bing 本就排第一）
    - 国外 + 中文 → Bing, DDG, 360（360 不可达被移后）
    - 国外 + 英文 → Bing, DDG, 360

    探测全失败时退回语种静态序——宁可慢，不要全空。
    """
    chain = engines_for(query)
    names = [n for n, _ in chain]
    try:
        await _ensure_reach(names)
    except Exception:
        return chain

    reach = {n: _REACH.get(n) for n in names}
    if not any(v and v[0] for v in reach.values()):
        # 一个都没探通：可能是探测地址被拦，别据此判引擎死刑
        return chain

    ok = [(n, e) for n, e in chain if reach.get(n) and reach[n][0]]
    bad = [(n, e) for n, e in chain if not (reach.get(n) and reach[n][0])]
    # 组内保持语种序，不按延迟重排：延迟只证明"能连上"，不能证明"这个引擎
    # 适合这种 query"。英文查询即使 360 更快也该先走 Bing。
    return ok + bad


# 兼容旧引用：默认按中文排序（本项目的用户与查询以中文为主）。
_SEARCH_ENGINES: list[tuple[str, Any]] = _SEARCH_ENGINES_ZH


def engines_for(query: str) -> list[tuple[str, Any]]:
    """按 query 语种给出引擎顺序。

    含汉字就走境内优先（360→Bing→DDG）；纯拉丁查询走海外优先
    （Bing→DDG→360）。顺序不是"哪个更好"，是"哪个在这台机器的网络里能通"。

    `_SEARCH_ENGINES` 一旦被外部替换（测试打桩、插件注册），就以替换后的顺序
    为准，不再按语种改写——否则“换了引擎表却不生效”会变成新的排查陷阱。
    """
    if _SEARCH_ENGINES is not _SEARCH_ENGINES_ZH:
        return list(_SEARCH_ENGINES)
    return _SEARCH_ENGINES_ZH if _CJK_RE.search(query) else _SEARCH_ENGINES_EN
