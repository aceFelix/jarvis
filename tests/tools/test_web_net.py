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


class TestReachabilityRanking:
    """引擎顺序来自**本机实测可达性**，而不是写死的语种表。

    这一层是"治本"的关键：同一份代码在国内、国外、带代理、不带代理下都要选对。
    静态语种表只能覆盖作者当时所在的那种网络。
    """

    @pytest.fixture(autouse=True)
    def _clear_reach(self, monkeypatch):
        from agent.tools.web import search_engine as se

        monkeypatch.setattr(se, "_REACH", {})
        yield

    def _stub(self, monkeypatch, table):
        """把探测结果直接写进缓存，不触网。"""
        from agent.tools.web import search_engine as se

        for name, ok in table.items():
            se._REACH[name] = (ok, 100.0, time.monotonic() + 600)

    def test_unreachable_group_moves_to_back(self, monkeypatch):
        """国内网络：DDG 不可达 → 整组后移，但组内语种序不变。"""
        from agent.tools.web import search_engine as se

        self._stub(monkeypatch, {"360搜索": True, "Bing": True,
                                 "DuckDuckGo": False, "DuckDuckGo-lite": False})
        names = [n for n, _ in asyncio_run(se.ordered_engines("梁文锋"))]
        assert names == ["360搜索", "Bing", "DuckDuckGo", "DuckDuckGo-lite"]

    def test_foreign_network_flips_360_to_last(self, monkeypatch):
        """国外网络：360 不通、DDG 通 → 360 垫底，Bing 顶上。"""
        from agent.tools.web import search_engine as se

        self._stub(monkeypatch, {"360搜索": False, "Bing": True,
                                 "DuckDuckGo": True, "DuckDuckGo-lite": True})
        zh = [n for n, _ in asyncio_run(se.ordered_engines("梁文锋"))]
        en = [n for n, _ in asyncio_run(se.ordered_engines("Liang Wenfeng"))]
        assert zh[-1] == "360搜索"
        assert zh[0] == "Bing"
        assert en[0] == "Bing"

    def test_latin_query_does_not_prefer_faster_chinese_engine(self, monkeypatch):
        """可达性只证明"能连上"，不能证明"适合这种 query"。

        最初实现按纯延迟排，于是 360 最快就永远第一，英文查询也交给它——
        360 是中文索引，英文质量差。组内必须保持语种序。
        """
        from agent.tools.web import search_engine as se

        se._REACH["360搜索"] = (True, 10.0, time.monotonic() + 600)   # 最快
        se._REACH["Bing"] = (True, 900.0, time.monotonic() + 600)     # 慢但英文好
        se._REACH["DuckDuckGo"] = (False, 0.0, time.monotonic() + 600)
        se._REACH["DuckDuckGo-lite"] = (False, 0.0, time.monotonic() + 600)
        en = [n for n, _ in asyncio_run(se.ordered_engines("DeepSeek V3 architecture"))]
        assert en[0] == "Bing"

    def test_all_probes_failed_falls_back_to_language_order(self, monkeypatch):
        """一个都没探通时不能据此判引擎死刑——退回语种静态序。"""
        from agent.tools.web import search_engine as se

        self._stub(monkeypatch, {"360搜索": False, "Bing": False,
                                 "DuckDuckGo": False, "DuckDuckGo-lite": False})
        names = [n for n, _ in asyncio_run(se.ordered_engines("梁文锋"))]
        assert names == [n for n, _ in se.engines_for("梁文锋")]

    def test_probe_cache_is_reused(self, monkeypatch):
        """探测有 600s 缓存，不能每次搜索都付一遍成本。"""
        from agent.tools.web import search_engine as se

        self._stub(monkeypatch, {"360搜索": True})
        calls = []

        def fake_probe(url):
            calls.append(url)
            return True, 50.0

        monkeypatch.setattr(se, "_probe_one", fake_probe)
        asyncio_run(se._ensure_reach(["360搜索", "Bing"]))
        asyncio_run(se._ensure_reach(["360搜索", "Bing"]))
        # 缓存未过期，第二次不应再探
        assert len(calls) <= 2


def asyncio_run(coro):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)
