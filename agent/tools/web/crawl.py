"""Crawl —— 广度优先抓取站点（爬虫能力 P2）。

设计要点（方案见 ``docs/plans/crawler-cockpit.md`` §4）：
- BFS frontier + 规范化 URL 去重，限页数/深度/同域/延迟，默认礼貌爬取；
- 默认读同域 ``robots.txt``，禁止路径不入队；
- 复用 WebFetch 的 ``_fetch_url``（requests/httpx/urllib 自动选择）与 SSRF 校验；
- 每页抓取结果与最终统计写入 ``ToolResult.trace_meta``，由 trace 派生
  ``web_fetch``（带 parent_url/depth/kind）/ ``web_extract`` / ``crawl_stats``
  驱动左侧「爬虫驾驶舱」。工具本身不依赖 trace 与 UI。

浏览器降级（``use_browser``）按计划留到 P4，本阶段只走 HTTP。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from typing import Any
from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

from bs4 import BeautifulSoup

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool
from agent.tools.web.extract import crawl_enabled, extract_fields
from agent.tools.web.web import _fetch_url, _is_safe_host

# 默认参数与硬上限（配置项在 [crawl] 段，工具入参可覆盖单次）
DEFAULT_MAX_PAGES = 10
DEFAULT_MAX_DEPTH = 2
DEFAULT_DELAY_MS = 300
DEFAULT_TIMEOUT_S = 20
DEFAULT_MAX_BYTES = 2_000_000
MAX_PAGES_LIMIT = 100
MAX_DEPTH_LIMIT = 5
# 抓取后端策略（P4）：auto = HTTP 失败/空壳页时降级浏览器；always/never 强制
USE_BROWSER_MODES = ("auto", "always", "never")
DEFAULT_USE_BROWSER = "auto"
# auto 降级阈值：HTTP 正文可见文本少于该字符数，视为 JS 空壳页
MIN_TEXT_CHARS = 500
# 反爬/JS 挑战页特征（小写包含匹配）。识别到只如实标记失败，不做绕过。
_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-challenge",
    "cf_chl_opt",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
    "checking your browser",
    "ddos protection by cloudflare",
)
_MAX_FIELDS = 12
# frontier 上限 = 页数上限 × 该系数，防止大站链接爆炸
_FRONTIER_FACTOR = 10
# 摘要里最多列多少页，避免工具回执被 URL 清单撑爆
_SUMMARY_PAGES = 15
_MAX_ERROR_CHARS = 120

# 目录默认页文件名：归一化时从路径尾剥掉（`/a/index.html` ≡ `/a`）
_INDEX_FILES = frozenset({"index.html", "index.htm", "default.html", "default.htm"})


# ---- 纯函数（可单测，不触网）----


def normalize_url(url: str) -> str:
    """规范化 URL：去 fragment、scheme/host 小写、去默认端口、路径尾斜杠统一、
    query 按键排序（避免 ``?a=1&b=2`` 与 ``?b=2&a=1`` 被当成两页），
    目录默认页归一化（``/index.html`` ≡ ``/``，同页不同 URL 只抓一次）。

    Args:
        url: 原始 URL。

    Returns:
        规范化后的 URL；非 http/https 或无法解析时返回空字符串。
    """
    try:
        parsed = urlsplit(str(url or "").strip())
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        if scheme not in ("http", "https") or not host:
            return ""
        port = parsed.port
    except ValueError:
        return ""
    default_port = 80 if scheme == "http" else 443
    netloc = host if (port is None or port == default_port) else f"{host}:{port}"
    path = parsed.path or "/"
    # 目录默认页归一化：站点常同时给出 `/` 与 `/index.html` 两种链接，
    # 不归一化会把同一页面抓两次（浪费页数额度、驾驶舱多出假节点）。
    segments = [seg for seg in path.split("/") if seg]
    if segments and segments[-1].lower() in _INDEX_FILES:
        segments.pop()
    path = "/" + "/".join(segments)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, ""))


def is_same_domain(url: str, base_host: str) -> bool:
    """判断 URL 是否与起始站点同域（子域算同域，如 ``cdn.site.com``）。

    Args:
        url: 待判断 URL。
        base_host: 起始 URL 的主机名（小写）。

    Returns:
        同域返回 True；URL 无法解析或主机为空返回 False。
    """
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if not host or not base_host:
        return False
    return host == base_host or host.endswith("." + base_host)


def extract_links(html: str, base_url: str) -> list[str]:
    """从 HTML 中提取可抓取的 http(s) 链接（绝对化 + 规范化 + 去重保序）。

    Args:
        html: 页面 HTML。
        base_url: 当前页 URL，用于解析相对链接。

    Returns:
        规范化后的链接列表。
    """
    soup = BeautifulSoup(html or "", "html.parser")
    seen: set[str] = set()
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        # 跳过 Cloudflare 邮箱混淆链接（/cdn-cgi/l/email-protection#…）：
        # 它不是页面，抓了只会得到 404，还会在驾驶舱里标一条红色失败。
        if (urlsplit(absolute).path or "").lower().startswith("/cdn-cgi/"):
            continue
        normalized = normalize_url(absolute)
        if normalized and normalized not in seen:
            seen.add(normalized)
            links.append(normalized)
    return links


def parse_robots(text: str) -> list[tuple[bool, str]]:
    """解析 robots.txt 中 ``User-agent: *`` 组的 Allow/Disallow 规则。

    只实现抓取需要的子集：通配组、``*`` 通配符、``$`` 结尾锚点；
    其他 User-agent 组忽略（我们以浏览器 UA 抓取，按通配组执行）。

    Args:
        text: robots.txt 内容。

    Returns:
        ``[(是否允许, 规则)]``；空列表表示没有限制。
    """
    rules: list[tuple[bool, str]] = []
    active = False
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            active = value == "*"
        elif key in ("allow", "disallow") and active and value:
            rules.append((key == "allow", value))
    return rules


def _compile_rule(pattern: str) -> re.Pattern[str]:
    """把 robots 规则（``*`` 通配 / ``$`` 结尾）编译成正则。"""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    parts = [re.escape(part) for part in body.split("*")]
    return re.compile("^" + ".*".join(parts) + ("$" if anchored else ""))


def robots_allows(rules: list[tuple[bool, str]], path: str) -> bool:
    """按“最长匹配优先、同长 Allow 优先”判断路径是否允许抓取。

    Args:
        rules: :func:`parse_robots` 的结果。
        path: URL 的 path（可含 ``?query``）。

    Returns:
        允许返回 True；没有命中规则也返回 True。
    """
    best_len = -1
    allowed = True
    for is_allow, pattern in rules:
        try:
            if not _compile_rule(pattern).match(path):
                continue
        except re.error:
            continue
        if len(pattern) > best_len or (len(pattern) == best_len and is_allow):
            best_len = len(pattern)
            allowed = is_allow
    return allowed


def path_of(url: str) -> str:
    """取 URL 的 path+query，供 robots 规则匹配。"""
    parsed = urlsplit(url)
    path = parsed.path or "/"
    return f"{path}?{parsed.query}" if parsed.query else path


def normalize_use_browser(value: Any) -> str:
    """归一 ``use_browser`` 取值（auto/always/never），非法值回退 auto。

    @author aceFelix
    """
    text = str(value or "").strip().lower()
    return text if text in USE_BROWSER_MODES else DEFAULT_USE_BROWSER


def is_challenge_page(html: str) -> bool:
    """判断页面是否为 Cloudflare 等 JS/反爬挑战页。

    只做识别、不做绕过：命中后该页按失败标记，避免把挑战页当成正文抽取。

    Args:
        html: 页面 HTML。

    Returns:
        命中任一特征返回 True。
    """
    lowered = (html or "").lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


def visible_text_length(html: str) -> int:
    """统计页面可见文本长度（去掉 script/style/noscript），用于判断空壳页。

    Args:
        html: 页面 HTML。

    Returns:
        可见文本字符数（已压缩空白）。
    """
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return len(soup.get_text(" ", strip=True))


def _fmt_size(n: int) -> str:
    """字节数 → 人读大小（工具回执用，避免依赖 UI 层）。"""
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}MB"
    if n >= 1024:
        return f"{int(n / 1024)}KB"
    return f"{n}B"


def _format_summary(
    start: str,
    pages: list[dict[str, Any]],
    extracted: list[dict[str, Any]],
    stats: dict[str, Any],
) -> str:
    """把爬取结果整理成模型可读的文本（结构化数据另走 ``trace_meta``）。"""
    ok = sum(1 for page in pages if page.get("status") == "ok")
    failed = len(pages) - ok
    lines = [
        f"爬取完成: {start}",
        (
            f"抓取 {len(pages)} 页（成功 {ok} / 失败 {failed} / 去重 {stats['dedup_hits']}）"
            f" | 深度 ≤{stats['max_depth']} | {_fmt_size(int(stats['bytes']))}"
            f" | {stats['elapsed_ms'] / 1000:.1f}s"
        ),
        "",
    ]
    for page in pages[:_SUMMARY_PAGES]:
        depth = page.get("depth")
        layer = f"L{depth}" if isinstance(depth, int) else "L-"
        status = page.get("status")
        mark = "✓" if status == "ok" else "✗"
        # 浏览器降级的页面标一下后端，方便判断「为什么这页慢/为什么能抓到」
        backend = " ·browser" if page.get("kind") == "browser" else ""
        tail = _fmt_size(int(page.get("bytes") or 0)) if page.get("bytes") else ""
        error = page.get("error") or ""
        suffix = f"  {error}" if error else ""
        lines.append(
            f"{mark} [{layer}] {page.get('url')}{backend}  {page.get('duration_ms')}ms {tail}{suffix}"
        )
    if len(pages) > _SUMMARY_PAGES:
        lines.append(f"… 另有 {len(pages) - _SUMMARY_PAGES} 页未列出")
    if extracted:
        lines.append("")
        lines.append("抽取:")
        for item in extracted[:5]:
            pairs = " · ".join(
                f"{key}={value}" for key, value in list((item.get("fields") or {}).items())[:3]
            )
            missing = item.get("missing") or []
            note = f"（缺 {len(missing)}）" if missing else ""
            lines.append(f"- {item.get('url')}: {pairs}{note}")
    return "\n".join(lines)


# ---- 抓取 ----


async def _fetch_page(url: str, timeout_s: float) -> tuple[str, str, int]:
    """在线程池里执行同步抓取（复用 WebFetch 的多后端实现）。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _fetch_url(url, timeout=timeout_s))


async def _fetch_page_browser(url: str, timeout_s: float) -> tuple[str, int]:
    """用共享浏览器渲染单页（P4 降级后端）。

    延迟导入 ``browser`` 模块：crawl 的纯函数/HTTP 路径不应在导入期拉起
    playwright 相关依赖，也避免与 browser 模块形成导入环。

    Args:
        url: 目标 URL。
        timeout_s: 导航超时秒数。

    Returns:
        ``(html, status)``。

    Raises:
        Exception: playwright 未安装或导航失败，由 :func:`_fetch_with_fallback` 兜获。
    """
    from agent.tools.web.browser import fetch_via_browser

    return await fetch_via_browser(url, timeout_s=timeout_s)


def _browser_is_active() -> bool:
    """共享浏览器当前是否已启动（延迟导入，HTTP-only 场景不加载浏览器栈）。"""
    from agent.tools.web.browser import browser_active

    return browser_active()


async def _close_browser() -> None:
    """关闭本次爬取自己拉起的共享浏览器（幂等，失败忽略）。"""
    from agent.tools.web.browser import close_browser

    await close_browser()


async def _fetch_with_fallback(
    url: str,
    timeout_s: float,
    use_browser: str,
) -> tuple[str, int, str, str, str]:
    """按 ``use_browser`` 策略抓取单页，必要时降级到浏览器渲染。

    策略（见方案 §4.3）：
    - ``never``：只走 HTTP（4xx/5xx 与挑战页记失败，正文短本身不算错）；
    - ``always``：只走浏览器；
    - ``auto``：先 HTTP，遇到 403/503、挑战页、正文过短（JS 空壳）或请求异常
      时降级浏览器；浏览器仍拿到挑战页则如实标记失败（不做任何绕过）。

    Args:
        url: 已规范化的 URL。
        timeout_s: 单页超时秒数。
        use_browser: auto / always / never。

    Returns:
        ``(html, status, kind, error, note)``：``error`` 非空表示该页失败；
        ``note`` 记录降级原因（成功降级时用来说明 ``kind=browser`` 的由来）。
    """
    http_error = ""
    html = ""
    status = 0
    http_ok = False

    if use_browser != "always":
        try:
            html, _ctype, status = await _fetch_page(url, timeout_s)
            http_ok = status < 400
        except Exception as exc:  # noqa: BLE001 - 单页异常转成失败原因
            http_error = f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS]
        else:
            if not http_ok:
                http_error = f"HTTP {status}"
            elif is_challenge_page(html):
                http_error = "挑战页（HTTP 返回，未绕过）"
            else:
                text_len = visible_text_length(html)
                if text_len >= MIN_TEXT_CHARS:
                    return html, status, "http", "", ""
                http_error = f"正文仅 {text_len} 字符（疑似 JS 空壳）"

        if use_browser == "never":
            # 没有浏览器兜底：正文短只是事实，2xx 且非挑战页仍按成功返回
            if http_ok and not is_challenge_page(html):
                return html, status, "http", "", ""
            return html, status, "http", http_error, ""
        # auto：真正的内容不存在（404/410）没必要再开浏览器
        if status in (404, 410):
            return html, status, "http", http_error, ""

    note = f"HTTP 未取到正文（{http_error}），已降级浏览器" if http_error else "指定浏览器渲染"
    try:
        browser_html, browser_status = await _fetch_page_browser(url, timeout_s)
    except Exception as exc:  # noqa: BLE001 - 浏览器不可用/超时都按失败处理
        reason = f"浏览器渲染失败: {type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS]
        return "", 0, "browser", reason, note
    if browser_status >= 400:
        return browser_html, browser_status, "browser", f"HTTP {browser_status}", note
    if is_challenge_page(browser_html):
        return browser_html, browser_status, "browser", "挑战页（浏览器仍未通过，未绕过）", note
    return browser_html, browser_status, "browser", "", note


class CrawlTool(Tool):
    """广度优先抓取站点，可选按 CSS 选择器逐页抽取字段。

    全部参数都有 ``[crawl]`` 默认值，单次调用可覆盖；只读、可并发。
    爬取结束后通过 ``trace_meta`` 一次性上报页面、抽取与统计（P2 不做
    逐页实时上报，驾驶舱在工具结束后刷新）。

    @author aceFelix
    """

    name = "Crawl"
    description = (
        "广度优先抓取站点：限页数/深度/同域/延迟，默认遵守 robots.txt。"
        "可传 extract 定义，每页按 CSS 选择器抽取字段。"
        "单页失败不终止整次爬取；不绕过 Cloudflare 等反爬挑战。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "起点 URL（http/https）"},
            "max_pages": {
                "type": "integer",
                "description": f"最多抓取页数（1–{MAX_PAGES_LIMIT}，默认取 [crawl] 配置）",
            },
            "max_depth": {
                "type": "integer",
                "description": f"最大深度（0–{MAX_DEPTH_LIMIT}，起点为 L0，默认取配置）",
            },
            "same_domain": {
                "type": "boolean",
                "description": "只跟同域链接（子域算同域），默认 true",
            },
            "delay_ms": {
                "type": "integer",
                "description": "每次请求间隔毫秒（礼貌爬取，0–5000）",
            },
            "respect_robots": {
                "type": "boolean",
                "description": "是否读取同域 robots.txt 并跳过禁止路径，默认 true",
            },
            "timeout_s": {
                "type": "integer",
                "description": "单页抓取超时秒数（1–120）",
            },
            "use_browser": {
                "type": "string",
                "enum": list(USE_BROWSER_MODES),
                "description": (
                    "抓取后端：auto（默认，HTTP 失败/空壳页自动降级浏览器渲染）"
                    "/always（全程浏览器）/never（只用 HTTP）"
                ),
            },
            "extract": {
                "type": "array",
                "description": f"可选：逐页抽取字段定义，最多 {_MAX_FIELDS} 个",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "字段名"},
                        "selector": {"type": "string", "description": "CSS 选择器"},
                        "attr": {
                            "type": "string",
                            "description": "text（默认）/html/href/src，或任意属性名",
                        },
                    },
                    "required": ["name", "selector"],
                },
            },
        },
        "required": ["url"],
    }
    max_result_chars = 20_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """``[crawl] enabled=false`` 时直接拒绝，避免模型反复重试。"""
        if not crawl_enabled(getattr(ctx, "settings", None)):
            return PermissionResult.deny("爬虫已关闭（[crawl] enabled = false）")
        return PermissionResult.allow("只读网络访问")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        """校验 URL 与抓取参数；错误直接回给模型便于自我修正。"""
        url = str(args.get("url") or "").strip()
        if not url:
            return ValidationResult.fail("url 不能为空")
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            return ValidationResult.fail(f"仅支持 http/https，收到: {parsed.scheme}")
        if not parsed.netloc:
            return ValidationResult.fail("URL 缺少主机名")
        if not _is_safe_host(parsed.hostname or ""):
            return ValidationResult.fail(f"安全策略: 禁止访问内网/本地地址: {parsed.hostname}")

        max_pages = _as_int(args.get("max_pages"), DEFAULT_MAX_PAGES)
        if not 1 <= max_pages <= MAX_PAGES_LIMIT:
            return ValidationResult.fail(f"max_pages 必须在 1–{MAX_PAGES_LIMIT}")
        max_depth = _as_int(args.get("max_depth"), DEFAULT_MAX_DEPTH)
        if not 0 <= max_depth <= MAX_DEPTH_LIMIT:
            return ValidationResult.fail(f"max_depth 必须在 0–{MAX_DEPTH_LIMIT}")
        delay_ms = _as_int(args.get("delay_ms"), DEFAULT_DELAY_MS)
        if not 0 <= delay_ms <= 5000:
            return ValidationResult.fail("delay_ms 必须在 0–5000")
        timeout_s = _as_int(args.get("timeout_s"), DEFAULT_TIMEOUT_S)
        if not 1 <= timeout_s <= 120:
            return ValidationResult.fail("timeout_s 必须在 1–120")
        mode = str(args.get("use_browser") or "").strip().lower()
        if mode and mode not in USE_BROWSER_MODES:
            return ValidationResult.fail("use_browser 只能是 auto / always / never")

        extract_defs = args.get("extract")
        if extract_defs is not None:
            if not isinstance(extract_defs, list) or len(extract_defs) > _MAX_FIELDS:
                return ValidationResult.fail(f"extract 必须是最多 {_MAX_FIELDS} 项的数组")
            for index, spec in enumerate(extract_defs, 1):
                if not isinstance(spec, dict):
                    return ValidationResult.fail(f"extract[{index}] 必须是对象")
                if not str(spec.get("name") or "").strip() or not str(
                    spec.get("selector") or ""
                ).strip():
                    return ValidationResult.fail(f"extract[{index}] 缺少 name 或 selector")
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """执行 BFS 抓取，返回文本摘要并在 ``trace_meta`` 里带上完整结构化数据。"""
        settings = getattr(ctx, "settings", None)
        start = normalize_url(str(args["url"]))
        if not start:
            return ToolResult(data="起始 URL 无法规范化，请检查格式", is_error=True)

        max_pages = _as_int(
            args.get("max_pages"), _as_int(getattr(settings, "crawl_max_pages", DEFAULT_MAX_PAGES), DEFAULT_MAX_PAGES)
        )
        max_depth = _as_int(
            args.get("max_depth"), _as_int(getattr(settings, "crawl_max_depth", DEFAULT_MAX_DEPTH), DEFAULT_MAX_DEPTH)
        )
        same_domain = _as_bool(
            args.get("same_domain"), _as_bool(getattr(settings, "crawl_same_domain", True), True)
        )
        delay_ms = _as_int(
            args.get("delay_ms"), _as_int(getattr(settings, "crawl_delay_ms", DEFAULT_DELAY_MS), DEFAULT_DELAY_MS)
        )
        respect_robots = _as_bool(
            args.get("respect_robots"),
            _as_bool(getattr(settings, "crawl_respect_robots", True), True),
        )
        timeout_s = float(
            _as_int(
                args.get("timeout_s"),
                _as_int(getattr(settings, "crawl_timeout_s", DEFAULT_TIMEOUT_S), DEFAULT_TIMEOUT_S),
            )
        )
        max_bytes = _as_int(
            getattr(settings, "crawl_max_bytes_per_page", DEFAULT_MAX_BYTES),
            DEFAULT_MAX_BYTES,
        )
        use_browser = normalize_use_browser(
            args.get("use_browser")
            or getattr(settings, "crawl_use_browser", DEFAULT_USE_BROWSER)
        )
        extract_defs = [dict(item) for item in (args.get("extract") or []) if isinstance(item, dict)]

        if ctx.ui:
            ctx.ui.info(f"爬取中: {start}（≤{max_pages} 页 / 深度 {max_depth}）")

        base_host = (urlsplit(start).hostname or "").lower()
        frontier: deque[tuple[str, str | None, int]] = deque([(start, None, 0)])
        visited: set[str] = set()
        pages: list[dict[str, Any]] = []
        extracted: list[dict[str, Any]] = []
        dedup_hits = 0
        total_bytes = 0
        robots_cache: dict[str, list[tuple[bool, str]]] = {}
        started = time.monotonic()
        # 只有本次爬取自己拉起浏览器时才在收尾关闭，避免关掉用户/模型正在用的页面
        browser_opened_here = use_browser != "never" and not _browser_is_active()

        async def robots_ok(url: str) -> bool:
            """读同域 robots.txt 并判断路径是否允许（每域缓存一次）。"""
            if not respect_robots:
                return True
            parsed = urlsplit(url)
            key = f"{parsed.scheme}://{parsed.netloc.lower()}"
            if key not in robots_cache:
                try:
                    text, _ctype, status = await _fetch_page(key + "/robots.txt", timeout_s)
                    robots_cache[key] = parse_robots(text) if status < 400 else []
                except Exception:  # noqa: BLE001 - robots 拉不到按无限制处理，避免整站不可用
                    robots_cache[key] = []
            return robots_allows(robots_cache[key], path_of(url))

        if not await robots_ok(start):
            return ToolResult(
                data=f"起始 URL 被 robots.txt 禁止抓取: {start}",
                is_error=True,
            )

        try:
            while frontier and len(pages) < max_pages:
                url, parent, depth = frontier.popleft()
                normalized = normalize_url(url)
                if not normalized or normalized in visited:
                    dedup_hits += 1
                    continue
                visited.add(normalized)
                if pages and delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000)

                fetch_started = time.monotonic()
                html, _status, kind, error, note = await _fetch_with_fallback(
                    normalized, timeout_s, use_browser
                )
                duration_ms = int((time.monotonic() - fetch_started) * 1000)

                if error:
                    failed: dict[str, Any] = {
                        "url": normalized,
                        "status": "error",
                        "parent_url": parent,
                        "depth": depth,
                        "kind": kind,
                        "duration_ms": duration_ms,
                        "error": error,
                    }
                    if note:
                        failed["note"] = note
                    pages.append(failed)
                    continue

                body = html if len(html) <= max_bytes else html[:max_bytes]
                page_bytes = len(body.encode("utf-8", "replace"))
                total_bytes += page_bytes
                page: dict[str, Any] = {
                    "url": normalized,
                    "status": "ok",
                    "parent_url": parent,
                    "depth": depth,
                    "kind": kind,
                    "duration_ms": duration_ms,
                    "bytes": page_bytes,
                }
                if note:
                    page["note"] = note
                pages.append(page)

                if extract_defs:
                    values, missing = extract_fields(body, extract_defs)
                    extracted.append({"url": normalized, "fields": values, "missing": missing})

                if depth >= max_depth:
                    continue
                for link in extract_links(body, normalized):
                    if same_domain and not is_same_domain(link, base_host):
                        continue
                    if not await robots_ok(link):
                        continue
                    if len(frontier) >= max_pages * _FRONTIER_FACTOR:
                        break
                    frontier.append((link, normalized, depth + 1))
        finally:
            # 本次爬取自己拉起的浏览器在收尾释放（提前 return/异常也会走到）
            if browser_opened_here:
                await _close_browser()

        elapsed_ms = int((time.monotonic() - started) * 1000)
        done = sum(1 for page in pages if page.get("status") == "ok")
        stats = {
            "queued": len(frontier),
            "done": done,
            "failed": len(pages) - done,
            "dedup_hits": dedup_hits,
            "bytes": total_bytes,
            "elapsed_ms": elapsed_ms,
            "max_pages": max_pages,
            "max_depth": max_depth,
        }
        trace_meta = {
            # 每页一条，trace 逐条派生 web_fetch（带 parent_url/depth/kind）
            "web_fetch": [dict(page) for page in pages],
            # 逐页抽取结果，trace 逐条派生 web_extract
            "web_extract": [dict(item) for item in extracted],
            "crawl_stats": dict(stats),
        }
        summary = _format_summary(start, pages, extracted, stats)
        return ToolResult(data=summary, is_error=done == 0, trace_meta=trace_meta)


def _as_int(value: Any, default: int) -> int:
    """宽容转 int；缺失/非法回默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    """宽容转 bool；字符串 "false"/"0"/"no" 视为 False。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("false", "0", "no", "off"):
        return False
    if text in ("true", "1", "yes", "on"):
        return True
    return default


__all__ = [
    "CrawlTool",
    "extract_links",
    "is_same_domain",
    "normalize_url",
    "parse_robots",
    "robots_allows",
]
