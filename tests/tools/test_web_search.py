"""WebSearch 多引擎 fallback + 浏览器兜底测试。

覆盖 web 搜索链路改造（search_engine.py / web.py / browser.py）：
- 解析层: Bing / DDG HTML / DDG lite 三种结果页 HTML 的纯函数解析
- 轮询层: 引擎异常与 0 结果都降级下一个引擎，成功结果带来源格式化
- 兜底层: HTTP 引擎全挂 → 本地无头浏览器临时页面搜索 → 全挂时回执
  引导模型改用 MCP 搜索工具
- search_via_browser: 元素提取、去重、临时页与自启浏览器的生命周期清理

全部测试不触网（引擎函数被 monkeypatch / HTML 用本地样例）。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior


@pytest.fixture
def dummy_ctx() -> ToolContext:
    """空 ToolContext（ui=None 走无界面分支）。"""
    return ToolContext(workdir=str(Path.cwd()), messages=[])


# ---------------------------------------------------------------------------
# 结果页 HTML 样例（构造为真实引擎页面结构的精简版）
# ---------------------------------------------------------------------------

BING_HTML = """<html><body>
<li class="b_algo"><h2><a href="https://a.example/">标题<b>A</b></a></h2>
<p class="b_lineclamp2">摘要一</p></li>
<li class="b_algo"><h2><a href="/internal">忽略我</a></h2></li>
<li class="b_algo"><h2><a href="https://b.example/">标题B</a></h2></li>
</body></html>"""

DDG_HTML = """<html><body>
<div class="result results_links">
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fddg.example%2F&rut=x">DDG标题</a>
<a class="result__snippet">DDG摘要</a>
</div>
</body></html>"""

DDG_LITE_HTML = """<html><body><table>
<tr><td><a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Flite.example%2F"
class='result-link'>Lite标题</a></td></tr>
<tr><td class="result-snippet">Lite摘要</td></tr>
</table></body></html>"""


class TestParseEngines:
    """三个引擎 HTML 解析纯函数。"""

    def test_parse_bing_html(self):
        """b_algo 块解析出标题/URL/摘要；非 http 链接与无 h2 块被跳过。"""
        from agent.tools.web.search_engine import parse_bing_html

        r = parse_bing_html(BING_HTML, 8)
        assert len(r) == 2
        assert r[0]["title"] == "标题A"  # 内联标签被剥掉
        assert r[0]["url"] == "https://a.example/"
        assert r[0]["snippet"] == "摘要一"
        assert r[1]["snippet"] == ""  # 无摘要段落不阻塞结果

    def test_parse_ddg_html_decodes_redirect(self):
        """html 端点结果块解析，uddg 重定向链接还原为真实 URL。"""
        from agent.tools.web.search_engine import parse_ddg_html

        r = parse_ddg_html(DDG_HTML, 8)
        assert len(r) == 1
        assert r[0]["title"] == "DDG标题"
        assert r[0]["url"] == "https://ddg.example/"
        assert r[0]["snippet"] == "DDG摘要"

    def test_parse_ddg_lite_html(self):
        """lite 端点按 result-link 切块，href 在 class 之前的顺序也能解析。"""
        from agent.tools.web.search_engine import parse_ddg_lite_html

        r = parse_ddg_lite_html(DDG_LITE_HTML, 8)
        assert len(r) == 1
        assert r[0]["title"] == "Lite标题"
        assert r[0]["url"] == "https://lite.example/"
        assert r[0]["snippet"] == "Lite摘要"

    def test_max_results_cap(self):
        """解析条数不超过 max_results。"""
        from agent.tools.web.search_engine import parse_bing_html

        assert len(parse_bing_html(BING_HTML, 1)) == 1

    def test_engine_order_bing_first(self):
        """引擎顺序表按国内可达性排：Bing 在最前，DDG 两端点兜底。"""
        from agent.tools.web.search_engine import _SEARCH_ENGINES

        names = [n for n, _ in _SEARCH_ENGINES]
        assert names == ["Bing", "DuckDuckGo", "DuckDuckGo-lite"]


class TestToolMetadata:
    """工具元数据保持既有口径（只读、并发安全、免确认）。"""

    def test_read_only_and_concurrent(self, dummy_ctx):
        from agent.tools.web.web import WebSearchTool

        t = WebSearchTool()
        assert t.is_read_only({"query": "x"}) is True
        assert t.is_concurrency_safe({"query": "x"}) is True
        assert t.check_permissions({"query": "x"}, dummy_ctx).behavior \
            == PermissionBehavior.ALLOW

    def test_empty_query_fails_validation(self, dummy_ctx):
        from agent.tools.web.web import WebSearchTool

        t = WebSearchTool()
        assert t.validate_input({"query": "  "}, dummy_ctx).ok is False


class TestEngineFallbackChain:
    """WebSearchTool.call 的引擎轮询与降级链（引擎全 monkeypatch，不触网）。"""

    def test_first_engine_exception_falls_to_next(self, dummy_ctx, monkeypatch):
        """Bing 抛异常 → DuckDuckGo 成功返回，结果正常格式化。"""
        from agent.tools.web import web as web_mod

        async def broken(query, max_results):
            raise TimeoutError("connect timeout")

        async def good(query, max_results):
            return [{"title": "T", "url": "https://u", "snippet": "S"}]

        monkeypatch.setattr(
            web_mod, "_SEARCH_ENGINES", [("Bing", broken), ("DuckDuckGo", good)]
        )
        r = asyncio.run(web_mod.WebSearchTool().call({"query": "q"}, dummy_ctx))
        assert r.is_error is False
        assert "T" in r.data and "https://u" in r.data

    def test_zero_results_also_falls_through(self, dummy_ctx, monkeypatch):
        """引擎请求成功但 0 结果也记为失效并降级下一引擎。"""
        from agent.tools.web import web as web_mod

        async def empty(query, max_results):
            return []

        async def good(query, max_results):
            return [{"title": "second", "url": "https://2", "snippet": ""}]

        monkeypatch.setattr(
            web_mod, "_SEARCH_ENGINES", [("Bing", empty), ("DuckDuckGo", good)]
        )
        results, failures = asyncio.run(
            web_mod._run_search_engines("q", 8, dummy_ctx)
        )
        assert results[0]["title"] == "second"
        assert failures and failures[0][0] == "Bing"
        assert "0 条" in failures[0][1]

    def test_all_fail_then_browser_fallback(self, dummy_ctx, monkeypatch):
        """HTTP 引擎全挂 → 浏览器兜底成功，输出标注兜底通道。"""
        from agent.tools.web import web as web_mod

        async def broken(query, max_results):
            raise OSError("unreachable")

        monkeypatch.setattr(web_mod, "_SEARCH_ENGINES", [("Bing", broken)])

        async def fake_browser(manager, query, max_results, **kw):
            return [{"title": "B排结果", "url": "https://b", "snippet": ""}]

        monkeypatch.setattr(
            "agent.tools.web.browser.search_via_browser", fake_browser
        )
        r = asyncio.run(web_mod.WebSearchTool().call({"query": "q"}, dummy_ctx))
        assert r.is_error is False
        assert "兜底通道" in r.data and "B排结果" in r.data

    def test_everything_fails_guides_to_mcp(self, dummy_ctx, monkeypatch):
        """引擎与浏览器兜底全挂 → 失败回执引导模型改用 MCP 搜索工具。"""
        from agent.tools.web import web as web_mod

        async def broken(query, max_results):
            raise OSError("unreachable")

        monkeypatch.setattr(
            web_mod, "_SEARCH_ENGINES",
            [("Bing", broken), ("DuckDuckGo", broken)],
        )

        async def broken_browser(manager, query, max_results, **kw):
            raise RuntimeError("chromium launch failed")

        monkeypatch.setattr(
            "agent.tools.web.browser.search_via_browser", broken_browser
        )
        r = asyncio.run(web_mod.WebSearchTool().call({"query": "q"}, dummy_ctx))
        assert r.is_error is True
        assert "MCP" in r.data
        assert "DuckDuckGo" in r.data  # 失败清单列出每个引擎
        assert "chromium launch failed" in r.data  # 兜底失败原因也展示


class _FakeLink:
    """伪 h2 a 元素。"""

    def __init__(self, title: str, url: str):
        self._t, self._u = title, url

    async def inner_text(self):
        return self._t

    async def get_attribute(self, name):
        return self._u if name == "href" else None


class _FakeEl:
    """伪 li.b_algo 结果块。"""

    def __init__(self, title: str, url: str, snippet: str = "摘要"):
        self._link = _FakeLink(title, url)
        self._snippet = snippet

    async def query_selector(self, sel):
        if sel == "h2 a":
            return self._link
        if sel == "p":
            return _FakeText(self._snippet)
        return None


class _FakeText:
    def __init__(self, t: str):
        self._t = t

    async def inner_text(self):
        return self._t


class _FakeTmpPage:
    """伪临时搜索结果页。"""

    def __init__(self, els):
        self._els = els
        self.closed = False
        self.goto_url = ""

    async def goto(self, url, **kw):
        self.goto_url = url

    async def query_selector_all(self, sel):
        return list(self._els)

    async def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self, page):
        self._page = page

    async def new_page(self):
        return self._page


class _FakeMainPage:
    def __init__(self, context):
        self.context = context
        self.navigated = False


class _FakeManager:
    """伪 BrowserManager：可配置初始共享 page 与 is_active 状态。"""

    def __init__(self, *, active: bool, shared_page):
        self._active = active
        self._page = shared_page
        self.closed_calls = 0

    @property
    def is_active(self):
        return self._active

    async def get_page(self, *, headless: bool = True):
        return self._page

    async def close(self):
        self.closed_calls += 1


class TestSearchViaBrowser:
    """browser.py 兜底搜索的提取与生命周期逻辑。"""

    def test_extracts_dedups_and_closes_temp_page(self):
        """正常提取结果；非 http/重复链接跳过；临时页用完即关。"""
        from agent.tools.web.browser import search_via_browser

        els = [
            _FakeEl("第一条", "https://1.example/"),
            _FakeEl("内部链接", "/internal"),       # 非 http，跳过
            _FakeEl("重复", "https://1.example/"),  # 重复 URL，跳过
            _FakeEl("第二条", "https://2.example/"),
        ]
        tmp = _FakeTmpPage(els)
        shared = _FakeMainPage(_FakeContext(tmp))
        mgr = _FakeManager(active=True, shared_page=shared)
        r = asyncio.run(search_via_browser(mgr, "天气", 8))
        assert [x["title"] for x in r] == ["第一条", "第二条"]
        assert r[0]["snippet"] == "摘要"
        assert tmp.closed is True
        assert "weather" not in r[0]["url"]
        assert mgr.closed_calls == 0  # 浏览器原本开着，不该被兜底关掉

    def test_closes_self_started_browser(self):
        """浏览器由本次兜底懒启动时，用完释放整个实例。"""
        from agent.tools.web.browser import search_via_browser

        tmp = _FakeTmpPage([_FakeEl("t", "https://x.example/")])
        shared = _FakeMainPage(_FakeContext(tmp))
        mgr = _FakeManager(active=False, shared_page=shared)
        r = asyncio.run(search_via_browser(mgr, "q", 8))
        assert len(r) == 1
        assert mgr.closed_calls == 1

    def test_max_results_respected(self):
        """结果数达到 max_results 即停止。"""
        from agent.tools.web.browser import search_via_browser

        els = [_FakeEl(f"t{i}", f"https://{i}.example/") for i in range(10)]
        tmp = _FakeTmpPage(els)
        shared = _FakeMainPage(_FakeContext(tmp))
        mgr = _FakeManager(active=True, shared_page=shared)
        r = asyncio.run(search_via_browser(mgr, "q", 3))
        assert len(r) == 3

    def test_shared_page_not_navigated(self):
        """兜底只在临时页导航，共享 page 不被 goto 污染。"""
        from agent.tools.web.browser import search_via_browser

        tmp = _FakeTmpPage([])
        shared = _FakeMainPage(_FakeContext(tmp))
        mgr = _FakeManager(active=True, shared_page=shared)
        asyncio.run(search_via_browser(mgr, "q", 8))
        assert shared.navigated is False
        assert tmp.goto_url.startswith("https://www.bing.com/search?q=")
