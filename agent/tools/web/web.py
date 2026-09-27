"""Web 工具 —— WebFetch + WebSearch。

设计要点:
1. WebFetch: 抓取 URL 内容，转 Markdown 给 LLM
2. WebSearch: 多引擎 fallback 搜索（Bing → DuckDuckGo HTML → DDG lite，
   全部失效再降级本地浏览器兜底/引导 MCP 搜索工具，免 API key）
3. SSRF 防护: 禁止访问内网 IP / localhost / 私有网段
4. 超时: WebFetch 默认 15 秒，搜索单引擎 10 秒
5. 内容长度限制: 默认 8000 字符（避免 token 爆炸）
6. User-Agent: 伪装为浏览器，避免被某些站点拒绝
7. 依赖: 仅用标准库 urllib + re，避免引入 requests/httpx 等新依赖
   （如果环境装了 requests/httpx，优先使用）
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool
from agent.tools.web.search_engine import _SEARCH_ENGINES


# ---- 通用工具 ----

_DEFAULT_TIMEOUT = 15.0
_MAX_CONTENT_CHARS = 8000
_MAX_SEARCH_RESULTS = 8

# 内网 IP 网段（SSRF 防护）
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_safe_host(host: str) -> bool:
    """检查主机名是否安全（非内网/非 localhost）。

    解析主机名为 IP 后检查是否在内网网段。
    DNS 解析失败时返回 False（保守起见）。
    """
    if not host:
        return False
    host = host.lower()
    if host in ("localhost", "localhost.localdomain"):
        return False
    try:
        # 解析所有 A/AAAA 记录
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip_str = info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
                for net in _PRIVATE_NETWORKS:
                    if ip in net:
                        return False
            except ValueError:
                continue
        return True
    except Exception:
        return False  # 解析失败保守拒绝


def _fetch_url(url: str, *, timeout: float = _DEFAULT_TIMEOUT) -> tuple[str, str, int]:
    """抓取 URL，返回 (content, content_type, status_code)。

    优先用 requests/httpx（如已安装），否则用 urllib。
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    # 优先 requests
    try:
        import requests  # type: ignore
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        return resp.text, resp.headers.get("Content-Type", ""), resp.status_code
    except ImportError:
        pass

    # 其次 httpx
    try:
        import httpx  # type: ignore
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            return resp.text, resp.headers.get("Content-Type", ""), resp.status_code
    except ImportError:
        pass

    # 回退到 urllib
    import urllib.request
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read().decode("utf-8", errors="replace")
        return content, resp.headers.get("Content-Type", ""), resp.status


def _html_to_markdown(html: str) -> str:
    """简易 HTML → 文本转换。

    不引入 BeautifulSoup 等依赖，用正则做基础清理:
    - 移除 script/style/nav/footer/aside 标签内容
    - 移除所有 HTML 标签
    - 解码常见 HTML 实体
    - 压缩空白
    """
    # 移除无关标签内容
    for tag in ("script", "style", "nav", "footer", "aside", "noscript", "iframe"):
        html = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
    # 移除 HTML 注释
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    # <br> / <p> / <div> 换行
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</(p|div|h[1-6]|li|tr)>", "\n", html, flags=re.IGNORECASE)
    # 标题转 Markdown
    for i in range(1, 7):
        html = re.sub(
            rf"<h{i}\b[^>]*>(.*?)</h{i}>",
            lambda m: "#" * i + " " + m.group(1).strip() + "\n",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
    # 链接: <a href="...">text</a> → text (href)
    html = re.sub(
        r'<a\s+[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        lambda m: f"[{m.group(2).strip()}]({m.group(1)})",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # 代码块
    html = re.sub(
        r"<pre\b[^>]*>(.*?)</pre>",
        lambda m: "\n```\n" + m.group(1) + "\n```\n",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # 移除其余所有标签
    text = re.sub(r"<[^>]+>", "", html)
    # 解码常见 HTML 实体
    entities = {
        "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&quot;": '"', "&#39;": "'", "&apos;": "'",
    }
    for ent, char in entities.items():
        text = text.replace(ent, char)
    # 数字实体
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    # 压缩多余空白
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _truncate(text: str, max_chars: int = _MAX_CONTENT_CHARS) -> str:
    """截断文本到指定字符数，加省略提示。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n... [截断，原文共 {len(text)} 字符]"


# ---- WebFetch 工具 ----

class WebFetchTool(Tool):
    name = "WebFetch"
    description = (
        "抓取指定 URL 的网页内容，转为 Markdown 文本返回。"
        "用于查阅在线文档、API 参考、博客文章等。"
        "内置 SSRF 防护，禁止访问内网地址。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抓取的 URL（http/https）"},
            "max_chars": {
                "type": "integer",
                "description": "返回内容最大字符数（默认 8000）",
            },
        },
        "required": ["url"],
    }
    max_result_chars = 12_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读网络访问")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        url = args.get("url", "").strip()
        if not url:
            return ValidationResult.fail("url 不能为空")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ValidationResult.fail(f"仅支持 http/https，收到: {parsed.scheme}")
        if not parsed.netloc:
            return ValidationResult.fail("URL 缺少主机名")
        # SSRF 防护
        if not _is_safe_host(parsed.hostname or ""):
            return ValidationResult.fail(
                f"安全策略: 禁止访问内网/本地地址: {parsed.hostname}"
            )
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = args["url"].strip()
        max_chars = int(args.get("max_chars", _MAX_CONTENT_CHARS))
        if ctx.ui:
            ctx.ui.info(f"抓取中: {url}")

        try:
            content, content_type, status = await _fetch_url_async(url)
        except Exception as e:
            return ToolResult(data=f"抓取失败: {type(e).__name__}: {e}", is_error=True)

        if status >= 400:
            return ToolResult(
                data=f"HTTP {status}: 抓取失败（URL 可能无效或服务器拒绝访问）",
                is_error=True,
            )

        # HTML 转 Markdown
        if "html" in content_type.lower() or "<html" in content[:500].lower():
            text = _html_to_markdown(content)
        else:
            text = content

        text = _truncate(text, max_chars)
        header = f"URL: {url}\nHTTP {status} | Content-Type: {content_type}\n\n"
        return ToolResult(data=header + text)


async def _fetch_url_async(url: str):
    """异步包装: 在线程池中执行同步 fetch。"""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_url, url)


# ---- WebSearch 工具 ----

class WebSearchTool(Tool):
    name = "WebSearch"
    description = (
        "用搜索引擎查询关键词，返回前 N 条结果（标题+URL+摘要）。"
        "多引擎 fallback：Bing → DuckDuckGo HTML → DDG lite，"
        "全部失效再尝试本地无头浏览器兜底（无需 API key）。"
        "适合查询最新信息、技术文档、新闻等。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {
                "type": "integer",
                "description": "返回结果数（默认 8，最多 20）",
            },
        },
        "required": ["query"],
    }
    max_result_chars = 8_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读网络访问")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        q = args.get("query", "").strip()
        if not q:
            return ValidationResult.fail("query 不能为空")
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = args["query"].strip()
        max_results = min(int(args.get("max_results", _MAX_SEARCH_RESULTS)), 20)
        if ctx.ui:
            ctx.ui.info(f"搜索: {query}")

        results, failures = await _run_search_engines(query, max_results, ctx)

        if results:
            return ToolResult(data=_format_search_results(query, results))

        # 所有 HTTP 引擎都失效 → 本地浏览器兜底（Playwright 临时页面，
        # 不占用 Browser* 工具的共享 page）
        br_results, br_note = await _browser_search_fallback(query, max_results, ctx)
        if br_results:
            return ToolResult(data=_format_search_results(
                query, br_results, source_note=br_note))

        # 浏览器兜底也不行 → 失败回执里引导模型改用 MCP 搜索工具（若有）
        fail_lines = "\n".join(f"  - {name}: {err}" for name, err in failures)
        return ToolResult(
            data=(
                f"搜索失败，已尝试全部通道:\n{fail_lines}\n"
                f"浏览器兜底: {br_note}\n"
                "建议: 1) 若已注册带搜索能力的 MCP 工具（如 Exa/Tavily/Bocha 等），"
                "请改调用该工具搜索；2) 否则提示用户检查网络/代理后重试。"
            ),
            is_error=True,
        )


async def _run_search_engines(
    query: str, max_results: int, ctx: ToolContext
) -> tuple[list[dict[str, str]], list[tuple[str, str]]]:
    """按 _SEARCH_ENGINES 顺序轮询搜索引擎。

    Returns:
        (results, failures): 首个非空引擎结果，以及失败引擎的
        (名称, 错误摘要) 列表（异常与「0 结果」都记为失效）。
    """
    failures: list[tuple[str, str]] = []
    for name, engine in _SEARCH_ENGINES:
        try:
            results = await engine(query, max_results)
        except Exception as e:
            failures.append((name, f"{type(e).__name__}: {e}"[:200]))
            if ctx.ui:
                ctx.ui.info(f"搜索引擎 {name} 失效，降级下一个引擎...")
            continue
        if results:
            return results, failures
        failures.append((name, "请求成功但解析到 0 条结果（页面结构可能改版）"))
    return [], failures


async def _browser_search_fallback(
    query: str, max_results: int, ctx: ToolContext
) -> tuple[list[dict[str, str]], str]:
    """本地浏览器兜底搜索（第二道兜底，免 API key）。

    仅当所有 HTTP 引擎失效时调用：复用 browser.py 的 BrowserManager 懒启动
    无头 Chromium，但开临时 page 搜索、用完即关，不污染 Browser* 工具的
    共享页面。playwright 未安装时直接返回说明，不抛异常。

    Returns:
        (results, note): 结果列表与兜底通道状态说明（供失败回执展示）。
    """
    try:
        # 延迟导入：browser.py 模块本身可在无 playwright 时导入，
        # playwright 只在 get_page 内部 import
        from agent.tools.web.browser import _manager, search_via_browser
    except ImportError as e:
        return [], f"不可用（浏览器模块未安装依赖: {e}）"
    if ctx.ui:
        ctx.ui.info("HTTP 搜索引擎全部失效，启动本地无头浏览器兜底搜索...")
    try:
        results = await search_via_browser(_manager, query, max_results)
        return results, f"已执行，解析到 {len(results)} 条结果"
    except Exception as e:
        return [], f"失败: {type(e).__name__}: {e}"[:200]


def _format_search_results(
    query: str,
    results: list[dict[str, str]],
    *,
    source_note: str | None = None,
) -> str:
    """把搜索结果列表格式化为 Markdown（工具统一输出格式）。

    Args:
        query: 搜索关键词。
        results: {title, url, snippet} 字典列表。
        source_note: 结果来源说明（浏览器兜底时标注，让模型知道走了降级通道）。
    """
    lines = [f"搜索「{query}」结果（{len(results)} 条）:\n"]
    if source_note:
        lines.insert(0, f"[兜底通道] {source_note}\n")
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**")
        lines.append(f"   URL: {r['url']}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
        lines.append("")
    return "\n".join(lines)
