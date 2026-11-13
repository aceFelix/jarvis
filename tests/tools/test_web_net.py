"""网络出口策略与国内搜索引擎：让"国外源超时"变成可以解释、可以自愈的事。

起因（2026-11 实测一次语音会话）：查一个人名，5 次工具失败、246 秒。三件事：

1. `_SEARCH_ENGINES` 只有 Bing + DuckDuckGo，国内环境下 DDG 两路必然
   ConnectTimeout，而唯一能通的 Bing 把中文生僻人名拆成单字——
   "梁文锋"被当成汉字"梁"检索，前五条全是字典站；
2. WebFetch 用 requests，**不读 Windows 系统代理**，而 Crawl/Browser* 用
   Chromium、能读——同一个 URL 两套栈两种结果；
3. 同域名失败后每次都重走满三轮退避，60 秒白等。

@author aceFelix
"""

from __future__ import annotations

import time

import pytest

from agent.tools.web import net
from agent.tools.web.search_engine import engines_for, parse_360_html

# 真实 360 结果块（裁自 so.com 一次实际响应）：href 是 so.com 跳转链接，
# 真实地址在 data-mdurl —— 取错字段会把下游抓取引到跳转页上。
_SAMPLE_360 = (
    '<li class="res-list" data-result-type="normal"><h3 class="res-title " > '
    '<a  href="https://www.so.com/link?m=abc123" '
    'data-mdurl="https://www.toutiao.com/article/7604730557896950315/" '
    "target=\"_blank\"><em>梁文锋:DeepSeek创始人</em></a></h3>"
    '<p class="res-desc"><span data-result-summary>他就是DeepSeek创始人梁文锋</span></p></li>'
    '<li class="res-list" data-result-type="normal"><h3 class="res-title"><a '
    'href="https://www.so.com/link?m=def456" '
    'data-mdurl="https://business.sohu.com/a/863373594" target="_blank">'
    "DeepSeek 创始人梁文锋个人履历</a></h3></li>"
)


class TestParse360:
    def test_extracts_real_url_from_data_mdurl(self):
        """必须取 data-mdurl：href 是 so.com 跳转页，不是目标站。"""
        r = parse_360_html(_SAMPLE_360, 5)
        assert len(r) == 2
        assert r[0]["url"] == "https://www.toutiao.com/article/7604730557896950315/"
        assert "so.com/link" not in r[0]["url"]

    def test_strips_em_tags_in_title(self):
        r = parse_360_html(_SAMPLE_360, 5)
        assert "<em>" not in r[0]["title"]
        assert "梁文锋" in r[0]["title"]

    def test_respects_max_results(self):
        assert len(parse_360_html(_SAMPLE_360, 1)) == 1

    def test_survives_garbage(self):
        assert parse_360_html("", 5) == []
        assert parse_360_html("<html>验证码</html>", 5) == []


class TestEngineOrder:
    def test_chinese_query_prefers_domestic(self):
        """中文 query 先走境内引擎：Bing 的分词会把'梁文锋'拆成'梁'。"""
        names = [n for n, _ in engines_for("梁文锋 DeepSeek 创始人")]
        assert names[0] == "360搜索"
        assert "DuckDuckGo" in names  # 仍保留兜底，只是排在后面

    def test_latin_query_prefers_overseas(self):
        names = [n for n, _ in engines_for("Liang Wenfeng DeepSeek founder")]
        assert names[0] == "Bing"
        assert names[-1] == "360搜索"

    def test_default_list_matches_chinese(self):
        """兼容引用 _SEARCH_ENGINES 的老代码：默认应与中文顺序一致。"""
        from agent.tools.web.search_engine import _SEARCH_ENGINES

        assert [n for n, _ in _SEARCH_ENGINES] == [n for n, _ in engines_for("测试")]


class TestEgress:
    def test_host_of(self):
        assert net.host_of("https://zh.wikipedia.org/wiki/X") == "zh.wikipedia.org"
        assert net.host_of("not a url") == ""

    def test_env_proxy_wins(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        p = net.proxies_for("https://example.com")
        assert p and "127.0.0.1:7890" in p["https"]

    def test_no_proxy_short_circuits(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setenv("NO_PROXY", "example.com")
        assert net.proxies_for("https://example.com/x") is None

    def test_system_proxy_disabled_means_direct(self, monkeypatch):
        """ProxyEnable=0 是用户明确选的直连，不许偷偷套代理。"""
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("ALL_PROXY", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.setattr(net, "_system_proxy", lambda: None)
        assert net.proxies_for("https://zh.wikipedia.org/x") is None


class TestCircuitBreaker:
    def test_note_then_skip(self):
        url = "https://breaker-test.invalid/x"
        assert net.is_unreachable(url) is False
        net.note_unreachable(url)
        assert net.is_unreachable(url) is True

    def test_forget_clears(self):
        url = "https://forget-test.invalid/x"
        net.note_unreachable(url)
        net.forget(url)
        assert net.is_unreachable(url) is False

    def test_ttl_expires(self, monkeypatch):
        url = "https://ttl-test.invalid/x"
        net.note_unreachable(url)
        # 把时钟推到 TTL 之后
        real = time.monotonic
        monkeypatch.setattr(net.time, "monotonic", lambda: real() + 10_000)
        assert net.is_unreachable(url) is False

    def test_description_names_the_reason(self):
        url = "https://desc-test.invalid/x"
        net.note_unreachable(url)
        text = net.describe_egress(url)
        assert "不可达" in text
        net.forget(url)

    @pytest.mark.parametrize("bad", ["", "   ", "not-a-url"])
    def test_unparsable_hosts_are_harmless(self, bad):
        net.note_unreachable(bad)
        assert net.is_unreachable(bad) is False
