"""`/net` 诊断命令：输出要能一眼分清「墙 / 代理没开 / 引擎挂了」。

起因（2026-11）：一次查人名的会话烧掉 5 次失败、246 秒。修完之后用户仍需要
一个入口，把"到底是哪一层不通"直接摆出来，而不是每次重新推理。

这里钉三件事：
1. 三处登记都在（router / help 文案 / 补全清单）——漏一处就是"命令存在但
   用户不知道，或者敲了没反应"；
2. 输出分段正确、✓/✗ 与实际探测结果一致；
3. 探测用打桩，测试不触网。

@author aceFelix
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from agent.commands.handlers import net_commands


class FakeUI:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, msg: str = "") -> None:
        self.lines.append(str(msg))

    def warn(self, msg: str = "") -> None:
        self.lines.append(f"[warn] {msg}")

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture()
def ui_ctx():
    ui = FakeUI()
    return SimpleNamespace(ui=ui), ui


class TestRegistration:
    """三处登记点缺一不可。"""

    def test_registered_in_router(self):
        from agent.commands.router import COMMAND_HANDLERS

        assert "/net" in COMMAND_HANDLERS

    def test_listed_in_help_text(self):
        """/help 是第三处登记点：命令能用但 help 里没有，等于用户不知道它存在。

        直接查源码而不是渲染 help——渲染要整台 RichCLI，为一个字符串断言搭它不值。
        """
        from agent.commands.handlers import core_commands

        source = open(core_commands.__file__, encoding="utf-8").read()
        assert '"/net' in source or "  /net" in source

    def test_listed_in_slash_completer(self):
        from agent.ui import slash_completer

        source = open(slash_completer.__file__, encoding="utf-8").read()
        assert '"/net"' in source


class TestOutput:
    def test_reports_egress_engines_and_sites(self, ui_ctx, monkeypatch):
        ctx, ui = ui_ctx
        # 打桩探测：360/Bing 通、DDG 不通；baidu 通、github/wiki 不通
        def fake_probe(url, *, timeout=1.5):
            ok = any(k in url for k in ("so.com", "bing.com", "baidu.com", "deepseek.com"))
            return (True, 123.0) if ok else (False, 0.0)

        monkeypatch.setattr(net_commands.net, "probe", fake_probe)
        monkeypatch.setattr(
            net_commands.net, "describe_egress", lambda url="": "直连（测试桩）"
        )

        assert net_commands.handle_net(ctx, "/net") is True
        text = ui.text
        assert "出口：直连（测试桩）" in text
        assert "引擎可达性" in text and "站点可达性" in text
        assert "✓ 360搜索" in text
        assert "✗ DuckDuckGo" in text
        assert "✓ baidu.com" in text
        assert "✗ zh.wikipedia.org" in text
        # 说明里必须给出"该怎么办"，否则它只是一张报表
        assert "系统代理" in text

    def test_marks_failures_as_unreachable(self, ui_ctx, monkeypatch):
        ctx, ui = ui_ctx
        monkeypatch.setattr(
            net_commands.net, "probe", lambda url, **kw: (False, 0.0)
        )
        monkeypatch.setattr(net_commands.net, "describe_egress", lambda url="": "x")
        net_commands.handle_net(ctx, "/net")
        assert "不可达" in ui.text
        assert "✓" not in ui.text.replace("✓", "", 0) or "✗" in ui.text

    def test_latency_rendered_as_ms(self, ui_ctx, monkeypatch):
        ctx, ui = ui_ctx
        monkeypatch.setattr(net_commands.net, "probe", lambda url, **kw: (True, 258.4))
        monkeypatch.setattr(net_commands.net, "describe_egress", lambda url="": "x")
        net_commands.handle_net(ctx, "/net")
        assert "258ms" in ui.text


class TestProbeImplementation:
    """探测实现只有一份，在 net.probe；search_engine 只固定超时。"""

    def test_search_engine_delegates_to_net(self, monkeypatch):
        from agent.tools.web import net, search_engine

        called = {}

        def fake(url, *, timeout=1.5):
            called["url"] = url
            called["timeout"] = timeout
            return True, 42.0

        monkeypatch.setattr(net, "probe", fake)
        ok, ms = search_engine._probe_one("https://example.test/")
        assert (ok, ms) == (True, 42.0)
        assert called["url"] == "https://example.test/"
        assert called["timeout"] == search_engine._PROBE_TIMEOUT
