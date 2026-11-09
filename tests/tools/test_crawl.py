"""Crawl（爬虫 P2）测试：URL 规范化 / 同域 / 链接 / robots / BFS 主流程。

不触网：``_fetch_page`` 被替换为本地页面字典，robots 也走同一假抓取。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior
from agent.tools.web import crawl as crawl_mod
from agent.tools.web.crawl import (
    CrawlTool,
    extract_links,
    is_same_domain,
    normalize_url,
    parse_robots,
    robots_allows,
)

SITE = {
    "https://site.test/": (
        '<a href="/a">A</a><a href="/b">B</a>'
        '<a href="https://other.test/x">外站</a><a href="#top">锚点</a>'
    ),
    "https://site.test/a": '<a href="/a1">A1</a><a href="/">home</a>',
    "https://site.test/b": '<a href="/b1/">B1</a>',
    "https://site.test/a1": "<h1>A1</h1>",
    "https://site.test/b1": "<h1>B1</h1>",
}


def _fake_fetch(pages: dict[str, str], *, statuses: dict[str, int] | None = None):
    """构造假抓取函数：页面字典命中返回 200，其余 404。"""
    statuses = statuses or {}

    async def fake(url: str, timeout_s: float):
        status = statuses.get(url, 200 if url in pages else 404)
        return pages.get(url, ""), "text/html", status

    return fake


@pytest.fixture
def ctx() -> ToolContext:
    """空 ToolContext（ui=None）。

    ``crawl_use_browser="never"``：本文件的假页面正文都很短，默认 auto 会判定为
    JS 空壳并尝试降级浏览器；测试只验证 HTTP 路径，浏览器降级另见
    :class:`TestBrowserFallback`。
    """
    return ToolContext(
        workdir=str(Path.cwd()),
        messages=[],
        settings=SimpleNamespace(crawl_use_browser="never"),
    )


class TestUrlHelpers:
    """URL 规范化与同域判定。"""

    def test_normalize_strips_fragment_and_normalizes(self) -> None:
        assert (
            normalize_url("HTTPS://Site.Test:443/b/?b=2&a=1#x")
            == "https://site.test/b?a=1&b=2"
        )

    def test_normalize_keeps_query_order_stable(self) -> None:
        assert normalize_url("https://site.test/a?b=2&a=1") == (
            normalize_url("https://site.test/a?a=1&b=2")
        )

    def test_normalize_rejects_non_http(self) -> None:
        assert normalize_url("ftp://site.test/a") == ""
        assert normalize_url("") == ""

    def test_same_domain_includes_subdomain(self) -> None:
        assert is_same_domain("https://cdn.site.test/a", "site.test") is True
        assert is_same_domain("https://other.test/a", "site.test") is False

    def test_extract_links_filters_and_dedups(self) -> None:
        html = (
            '<a href="/a">A</a><a href="/a#x">A2</a>'
            '<a href="mailto:x@y.z">mail</a><a href="javascript:void(0)">js</a>'
        )
        assert extract_links(html, "https://site.test/root") == ["https://site.test/a"]

    def test_extract_links_skips_cloudflare_email_protection(self) -> None:
        """Cloudflare 邮箱混淆链接不是页面，不能入队（否则必得 404）。"""
        html = (
            '<a href="/cdn-cgi/l/email-protection#abc">邮箱</a>'
            '<a href="/a">A</a>'
        )
        assert extract_links(html, "https://site.test/") == ["https://site.test/a"]


class TestRobots:
    """robots.txt 解析与最长匹配。"""

    def test_parse_wildcard_group_only(self) -> None:
        text = "User-agent: bot\nDisallow: /x\nUser-agent: *\nDisallow: /private\nAllow: /private/ok\n"
        rules = parse_robots(text)
        assert rules == [(False, "/private"), (True, "/private/ok")]

    def test_longest_match_wins(self) -> None:
        rules = parse_robots("User-agent: *\nDisallow: /private\nAllow: /private/ok\n")
        assert robots_allows(rules, "/private/secret") is False
        assert robots_allows(rules, "/private/ok/page") is True

    def test_wildcard_and_end_anchor(self) -> None:
        rules = parse_robots("User-agent: *\nDisallow: /*.pdf$\n")
        assert robots_allows(rules, "/doc/a.pdf") is False
        assert robots_allows(rules, "/doc/a.pdf?x=1") is True


class TestValidateInput:
    """工具入参校验（不触网）。"""

    def test_reject_bad_pages(self, ctx: ToolContext) -> None:
        result = CrawlTool().validate_input({"url": "https://site.test/", "max_pages": 0}, ctx)
        assert not result.ok

    def test_reject_bad_depth(self, ctx: ToolContext) -> None:
        result = CrawlTool().validate_input({"url": "https://site.test/", "max_depth": 9}, ctx)
        assert not result.ok

    def test_reject_bad_extract(self, ctx: ToolContext) -> None:
        result = CrawlTool().validate_input(
            {"url": "https://site.test/", "extract": [{"name": "t"}]}, ctx
        )
        assert not result.ok

    def test_gate_by_crawl_enabled(self) -> None:
        """[crawl] enabled=false 时直接 DENY。"""
        ctx = ToolContext(
            workdir=str(Path.cwd()),
            messages=[],
            settings=SimpleNamespace(crawl_enabled=False),
        )
        result = CrawlTool().check_permissions({"url": "https://site.test/"}, ctx)
        assert result.behavior is PermissionBehavior.DENY


class TestCrawlCall:
    """BFS 主流程：层级、同域、去重、失败继续与 trace_meta。"""

    async def test_bfs_pages_depth_and_trace_meta(
        self, ctx: ToolContext, monkeypatch
    ) -> None:
        monkeypatch.setattr(crawl_mod, "_fetch_page", _fake_fetch(SITE))
        result = await CrawlTool().call(
            {
                "url": "https://site.test/",
                "max_pages": 10,
                "max_depth": 2,
                "delay_ms": 0,
                "respect_robots": False,
            },
            ctx,
        )

        assert result.is_error is False
        pages = result.trace_meta["web_fetch"]
        urls = [page["url"] for page in pages]
        assert urls[0] == "https://site.test/"
        assert "https://site.test/a1" in urls
        assert all("other.test" not in url for url in urls)
        assert pages[0]["depth"] == 0
        a_page = next(page for page in pages if page["url"] == "https://site.test/a")
        assert a_page["parent_url"] == "https://site.test/"
        assert a_page["depth"] == 1
        a1_page = next(page for page in pages if page["url"] == "https://site.test/a1")
        assert a1_page["parent_url"] == "https://site.test/a"
        assert a1_page["depth"] == 2

        stats = result.trace_meta["crawl_stats"]
        assert stats["done"] == len(pages)
        assert stats["failed"] == 0
        assert stats["max_depth"] == 2
        assert stats["dedup_hits"] >= 1  # /a 的 home 链接回到根页

    async def test_max_depth_zero_only_root(self, ctx: ToolContext, monkeypatch) -> None:
        monkeypatch.setattr(crawl_mod, "_fetch_page", _fake_fetch(SITE))
        result = await CrawlTool().call(
            {
                "url": "https://site.test/",
                "max_depth": 0,
                "delay_ms": 0,
                "respect_robots": False,
            },
            ctx,
        )
        pages = result.trace_meta["web_fetch"]
        assert [page["url"] for page in pages] == ["https://site.test/"]

    async def test_robots_disallow_skips_links(self, ctx: ToolContext, monkeypatch) -> None:
        pages = dict(SITE)
        pages["https://site.test/robots.txt"] = "User-agent: *\nDisallow: /b\n"
        monkeypatch.setattr(crawl_mod, "_fetch_page", _fake_fetch(pages))
        result = await CrawlTool().call(
            {
                "url": "https://site.test/",
                "max_pages": 10,
                "max_depth": 2,
                "delay_ms": 0,
                "respect_robots": True,
            },
            ctx,
        )
        urls = [page["url"] for page in result.trace_meta["web_fetch"]]
        assert "https://site.test/b" not in urls
        assert "https://site.test/b1" not in urls
        assert "https://site.test/a" in urls

    async def test_start_blocked_by_robots(self, ctx: ToolContext, monkeypatch) -> None:
        pages = {"https://site.test/robots.txt": "User-agent: *\nDisallow: /\n"}
        monkeypatch.setattr(crawl_mod, "_fetch_page", _fake_fetch(pages))
        result = await CrawlTool().call(
            {"url": "https://site.test/", "delay_ms": 0, "respect_robots": True}, ctx
        )
        assert result.is_error is True
        assert "robots" in result.data

    async def test_single_page_failure_continues(self, ctx: ToolContext, monkeypatch) -> None:
        monkeypatch.setattr(
            crawl_mod,
            "_fetch_page",
            _fake_fetch(SITE, statuses={"https://site.test/b": 500}),
        )
        result = await CrawlTool().call(
            {
                "url": "https://site.test/",
                "max_pages": 10,
                "max_depth": 2,
                "delay_ms": 0,
                "respect_robots": False,
            },
            ctx,
        )
        pages = result.trace_meta["web_fetch"]
        failed = [page for page in pages if page["status"] == "error"]
        assert failed and failed[0]["url"] == "https://site.test/b"
        assert "HTTP 500" in failed[0]["error"]
        # 失败页不影响兄弟页面
        assert any(page["url"] == "https://site.test/a1" for page in pages)
        assert result.trace_meta["crawl_stats"]["failed"] == 1

    async def test_extract_fields_per_page(self, ctx: ToolContext, monkeypatch) -> None:
        monkeypatch.setattr(crawl_mod, "_fetch_page", _fake_fetch(SITE))
        result = await CrawlTool().call(
            {
                "url": "https://site.test/a1",
                "delay_ms": 0,
                "respect_robots": False,
                "extract": [{"name": "title", "selector": "h1"}],
            },
            ctx,
        )
        extracted = result.trace_meta["web_extract"]
        assert extracted == [
            {"url": "https://site.test/a1", "fields": {"title": "A1"}, "missing": []}
        ]
        assert "title=A1" in result.data


class TestBrowserFallback:
    """P4 抓取后端策略：auto/always/never、挑战页识别与浏览器降级。

    全部使用假实现：不触网、不启动真实浏览器。
    """

    @staticmethod
    def _auto_ctx() -> ToolContext:
        """不带 crawl_use_browser 的上下文（走配置默认 auto）。"""
        return ToolContext(workdir=str(Path.cwd()), messages=[])

    @staticmethod
    def _patch_browser(monkeypatch, pages: dict[str, str]) -> list[str]:
        """替换浏览器抓取与开关，返回浏览器被调用过的 URL 列表。"""
        calls: list[str] = []

        async def fake_browser(url: str, timeout_s: float):
            calls.append(url)
            return pages.get(url, ""), 200

        async def no_close() -> None:
            return None

        monkeypatch.setattr(crawl_mod, "_fetch_page_browser", fake_browser)
        # False = 假装浏览器未启动，让爬取在收尾调用（也被替换掉的）关闭逻辑
        monkeypatch.setattr(crawl_mod, "_browser_is_active", lambda: False)
        monkeypatch.setattr(crawl_mod, "_close_browser", no_close)
        return calls

    def test_mode_and_challenge_helpers(self) -> None:
        """策略归一、挑战页特征与可见文本长度（纯函数）。"""
        assert crawl_mod.normalize_use_browser("ALWAYS") == "always"
        assert crawl_mod.normalize_use_browser("bogus") == "auto"
        assert crawl_mod.is_challenge_page("<title>Just a moment...</title>") is True
        assert crawl_mod.is_challenge_page("<h1>正常页面</h1>") is False
        assert (
            crawl_mod.visible_text_length(
                "<script>var a=1</script><style>.a{}</style><p>正文</p>"
            )
            == 2
        )

    async def test_invalid_use_browser_rejected(self, ctx: ToolContext) -> None:
        """非法 use_browser 在入参校验阶段就被拒绝。"""
        result = CrawlTool().validate_input(
            {"url": "https://site.test/", "use_browser": "sometimes"}, ctx
        )
        assert not result.ok

    async def test_auto_falls_back_on_shell_page(self, monkeypatch) -> None:
        """auto：HTTP 正文过短（JS 空壳）→ 降级浏览器，kind 记为 browser。"""
        monkeypatch.setattr(
            crawl_mod,
            "_fetch_page",
            _fake_fetch({"https://site.test/": "<div id=app></div>"}),
        )
        long_body = "<p>" + "内容" * 400 + "</p>"
        calls = self._patch_browser(monkeypatch, {"https://site.test/": long_body})
        result = await CrawlTool().call(
            {
                "url": "https://site.test/",
                "delay_ms": 0,
                "respect_robots": False,
                "max_depth": 0,
            },
            self._auto_ctx(),
        )
        page = result.trace_meta["web_fetch"][0]
        assert calls == ["https://site.test/"]
        assert page["kind"] == "browser"
        assert page["status"] == "ok"
        assert "降级" in page["note"]

    async def test_auto_marks_challenge_failure(self, monkeypatch) -> None:
        """HTTP 与浏览器都拿到 Cloudflare 挑战页 → 如实标记失败，不绕过。"""
        challenge = "<title>Just a moment...</title>"
        monkeypatch.setattr(
            crawl_mod, "_fetch_page", _fake_fetch({"https://site.test/": challenge})
        )
        calls = self._patch_browser(monkeypatch, {"https://site.test/": challenge})
        result = await CrawlTool().call(
            {
                "url": "https://site.test/",
                "delay_ms": 0,
                "respect_robots": False,
                "max_depth": 0,
            },
            self._auto_ctx(),
        )
        page = result.trace_meta["web_fetch"][0]
        assert calls == ["https://site.test/"]  # 尝试过浏览器
        assert page["status"] == "error"
        assert page["kind"] == "browser"
        assert "挑战页" in page["error"]
        assert "未绕过" in page["error"]

    async def test_always_skips_http(self, monkeypatch) -> None:
        """always：完全不碰 HTTP，直接浏览器渲染。"""
        async def boom(url: str, timeout_s: float):
            raise AssertionError("use_browser=always 不应走 HTTP")

        monkeypatch.setattr(crawl_mod, "_fetch_page", boom)
        calls = self._patch_browser(monkeypatch, {"https://site.test/": "<h1>hi</h1>"})
        result = await CrawlTool().call(
            {
                "url": "https://site.test/",
                "delay_ms": 0,
                "respect_robots": False,
                "max_depth": 0,
                "use_browser": "always",
            },
            self._auto_ctx(),
        )
        page = result.trace_meta["web_fetch"][0]
        assert calls == ["https://site.test/"]
        assert page["kind"] == "browser"
        assert page["status"] == "ok"
