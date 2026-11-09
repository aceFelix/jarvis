"""WebExtract（爬虫 P1）测试：选择器抽取纯函数 + 工具回执 + trace 旁路数据。

不触网：抓取函数被 monkeypatch，HTML 使用本地样例。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior
from agent.tools.web import extract as extract_mod
from agent.tools.web.extract import WebExtractTool, extract_fields

HTML = """<html><body>
<article class="post">
  <h1 class="title">React <b>19</b> 新特性</h1>
  <span class="author">张三</span>
  <a class="more" href="/react">阅读</a>
  <div class="summary"></div>
</article>
<footer><span class="author">页脚作者</span></footer>
</body></html>"""


@pytest.fixture
def ctx() -> ToolContext:
    """空 ToolContext（ui=None 走无界面分支）。"""
    return ToolContext(workdir=str(Path.cwd()), messages=[])


class TestExtractFields:
    """纯函数：字段抽取与缺失语义。"""

    def test_text_with_scope(self) -> None:
        """作用范围限定在 article 内，不误取 footer 的同名节点。"""
        values, missing = extract_fields(
            HTML,
            [
                {"name": "title", "selector": "h1.title"},
                {"name": "author", "selector": ".author"},
            ],
            selector="article",
        )
        assert values["title"] == "React 19 新特性"
        assert values["author"] == "张三"
        assert missing == []

    def test_empty_value_goes_to_missing(self) -> None:
        """抽到空节点不写空字符串，记入 missing。"""
        values, missing = extract_fields(
            HTML, [{"name": "summary", "selector": ".summary"}]
        )
        assert values == {}
        assert missing == ["summary"]

    def test_attr_href_and_html(self) -> None:
        """attr=href 取属性；attr=html 取内部 HTML（保留标签）。"""
        values, missing = extract_fields(
            HTML,
            [
                {"name": "link", "selector": "a.more", "attr": "href"},
                {"name": "raw", "selector": "h1.title", "attr": "html"},
            ],
        )
        assert values["link"] == "/react"
        assert "<b>19</b>" in values["raw"]
        assert missing == []

    def test_scope_not_found_marks_all_missing(self) -> None:
        """作用范围没命中时全部缺失，不退回整页抽取。"""
        values, missing = extract_fields(
            HTML, [{"name": "title", "selector": "h1.title"}], selector=".missing"
        )
        assert values == {}
        assert missing == ["title"]

    def test_invalid_selector_is_missing_not_crash(self) -> None:
        """选择器语法错误按“没抽到”处理，不让整页抽取中断。"""
        values, missing = extract_fields(HTML, [{"name": "x", "selector": "div[["}])
        assert values == {}
        assert missing == ["x"]


class TestValidateInput:
    """工具入参校验：错误必须在触网前返回。"""

    def test_reject_empty_url(self, ctx: ToolContext) -> None:
        result = WebExtractTool().validate_input({"url": "", "fields": []}, ctx)
        assert not result.ok

    def test_reject_non_http_scheme(self, ctx: ToolContext) -> None:
        result = WebExtractTool().validate_input(
            {"url": "ftp://example.com", "fields": [{"name": "a", "selector": "h1"}]}, ctx
        )
        assert not result.ok

    def test_reject_missing_fields(self, ctx: ToolContext) -> None:
        result = WebExtractTool().validate_input({"url": "https://example.com"}, ctx)
        assert not result.ok

    def test_reject_field_without_selector(self, ctx: ToolContext) -> None:
        result = WebExtractTool().validate_input(
            {"url": "https://example.com", "fields": [{"name": "title"}]}, ctx
        )
        assert not result.ok

    def test_gate_by_crawl_enabled(self) -> None:
        """[crawl] enabled=false 时，抽取随爬虫总开关一起拒绝。"""
        ctx = ToolContext(
            workdir=str(Path.cwd()),
            messages=[],
            settings=SimpleNamespace(crawl_enabled=False),
        )
        result = WebExtractTool().check_permissions({"url": "https://example.com"}, ctx)
        assert result.behavior is PermissionBehavior.DENY

    def test_accept_valid_input(self, ctx: ToolContext, monkeypatch) -> None:
        """合法入参通过；SSRF 校验打桩，避免测试依赖 DNS。"""
        monkeypatch.setattr(extract_mod, "_is_safe_host", lambda _host: True)
        result = WebExtractTool().validate_input(
            {
                "url": "https://example.com/post",
                "fields": [{"name": "title", "selector": "h1"}],
            },
            ctx,
        )
        assert result.ok


class TestToolCall:
    """工具执行：回执文本 + trace_meta 结构化数据。"""

    async def test_call_returns_fields_and_trace_meta(
        self, ctx: ToolContext, monkeypatch
    ) -> None:
        async def fake_fetch(url: str):
            return HTML, "text/html", 200

        monkeypatch.setattr(extract_mod, "_fetch_url_async", fake_fetch)
        result = await WebExtractTool().call(
            {
                "url": "https://example.com/post",
                "selector": "article",
                "fields": [
                    {"name": "title", "selector": "h1.title"},
                    {"name": "author", "selector": ".author"},
                    {"name": "summary", "selector": ".summary"},
                ],
            },
            ctx,
        )

        assert result.is_error is False
        assert "抽取 2/3 个字段" in result.data
        assert "缺失字段: summary" in result.data

        meta = result.trace_meta["web_extract"]
        assert meta["url"] == "https://example.com/post"
        assert meta["fields"] == {"title": "React 19 新特性", "author": "张三"}
        assert meta["missing"] == ["summary"]
        assert meta["selector"] == "article"

    async def test_http_error_returns_failure_without_trace_meta(
        self, ctx: ToolContext, monkeypatch
    ) -> None:
        async def fake_fetch(url: str):
            return "", "text/html", 403

        monkeypatch.setattr(extract_mod, "_fetch_url_async", fake_fetch)
        result = await WebExtractTool().call(
            {"url": "https://example.com/x", "fields": [{"name": "t", "selector": "h1"}]},
            ctx,
        )
        assert result.is_error is True
        assert "403" in result.data
        assert result.trace_meta == {}
