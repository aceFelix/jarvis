"""BrowserNavigate file:// 本地页面自查支持测试。

覆盖「交付自查纪律」落地的工具侧改动（browser.py）：
- validate_input: 放行 http/https/file://，拒绝空 url 与裸路径
- check_permissions: file:// 免确认（本地自查），http(s) 仍询问
- is_read_only: file:// 视为本地只读，不算网络操作
- _normalize_file_url: Windows 反斜杠路径统一转正斜杠（Playwright 兼容）

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior


@pytest.fixture
def dummy_ctx() -> ToolContext:
    """构造一个空 ToolContext 用于测试校验和权限方法。"""
    return ToolContext(
        workdir=str(Path.cwd()),
        messages=[],
    )


@pytest.fixture
def navigate_tool():
    """BrowserNavigateTool 实例（不启动真实浏览器，仅测校验/权限层）。"""
    from agent.tools.web.browser import BrowserNavigateTool

    return BrowserNavigateTool()


class TestValidateInput:
    """url 协议校验。"""

    def test_http_ok(self, navigate_tool, dummy_ctx):
        """http:// 放行。"""
        r = navigate_tool.validate_input({"url": "http://localhost:5173"}, dummy_ctx)
        assert r.ok is True

    def test_https_ok(self, navigate_tool, dummy_ctx):
        """https:// 放行。"""
        r = navigate_tool.validate_input({"url": "https://example.com"}, dummy_ctx)
        assert r.ok is True

    def test_file_url_ok(self, navigate_tool, dummy_ctx):
        """file:// 放行（本地静态页面自查场景）。"""
        r = navigate_tool.validate_input(
            {"url": "file:///E:/work/index.html"}, dummy_ctx
        )
        assert r.ok is True

    def test_empty_url_fail(self, navigate_tool, dummy_ctx):
        """空 url 拒绝。"""
        r = navigate_tool.validate_input({"url": ""}, dummy_ctx)
        assert r.ok is False

    def test_bare_path_fail(self, navigate_tool, dummy_ctx):
        """裸 Windows 路径拒绝（需模型自行拼 file:// 前缀）。"""
        r = navigate_tool.validate_input({"url": r"E:\work\index.html"}, dummy_ctx)
        assert r.ok is False
        assert "file://" in r.message


class TestPermissions:
    """file:// 免确认、http(s) 询问。"""

    def test_file_url_auto_allow(self, navigate_tool, dummy_ctx):
        """file:// 本地自查免用户确认，降低自查摩擦。"""
        result = navigate_tool.check_permissions(
            {"url": "file:///E:/work/index.html"}, dummy_ctx
        )
        assert result.behavior == PermissionBehavior.ALLOW

    def test_https_asks(self, navigate_tool, dummy_ctx):
        """https:// 属网络操作，仍需用户确认。"""
        result = navigate_tool.check_permissions({"url": "https://example.com"}, dummy_ctx)
        assert result.behavior == PermissionBehavior.ASK

    def test_file_url_is_read_only(self, navigate_tool):
        """file:// 视为本地只读（不计为网络操作）。"""
        assert navigate_tool.is_read_only({"url": "file:///E:/a.html"}) is True

    def test_https_not_read_only(self, navigate_tool):
        """https:// 不算只读（是网络访问）。"""
        assert navigate_tool.is_read_only({"url": "https://example.com"}) is False


class TestNormalizeFileUrl:
    """Windows 反斜杠路径规范化。"""

    def test_backslash_to_slash(self):
        """file:///E:\\dir\\xx.html → file:///E:/dir/xx.html。"""
        from agent.tools.web.browser import _normalize_file_url

        assert _normalize_file_url("file:///E:\\dir\\xx.html") == "file:///E:/dir/xx.html"

    def test_forward_slash_unchanged(self):
        """已规范的正斜杠形式原样返回。"""
        from agent.tools.web.browser import _normalize_file_url

        assert _normalize_file_url("file:///E:/dir/xx.html") == "file:///E:/dir/xx.html"

    def test_non_file_url_unchanged(self):
        """非 file:// URL 原样返回，不做任何处理。"""
        from agent.tools.web.browser import _normalize_file_url

        assert _normalize_file_url("https://example.com\\x") == "https://example.com\\x"
