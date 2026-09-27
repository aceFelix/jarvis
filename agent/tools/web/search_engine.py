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
from typing import Any
from urllib.parse import quote_plus, unquote

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
    """POST 表单并返回响应文本。requests 优先、urllib 兜底。"""
    headers = {
        "User-Agent": _UA,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        import requests  # type: ignore
        resp = requests.post(url, data=body, headers=headers, timeout=timeout)
        return resp.text
    except ImportError:
        pass
    import urllib.request
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _get_url(url: str, *, timeout: float = _ENGINE_TIMEOUT) -> str:
    """GET 并返回响应文本。requests 优先、urllib 兜底。"""
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
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max_results}"
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


# 引擎顺序表: (显示名, 实现函数)。WebSearchTool 按此顺序轮询，
# 抛异常或返回空结果都降级到下一个；全部失效后走浏览器/引导兜底。
_SEARCH_ENGINES: list[tuple[str, Any]] = [
    ("Bing", search_bing),
    ("DuckDuckGo", search_duckduckgo),
    ("DuckDuckGo-lite", search_duckduckgo_lite),
]
