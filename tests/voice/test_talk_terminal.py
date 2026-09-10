"""/talk 纯终端回归测试。

2026-09 起 /talk 下线 pywebview 独立窗口路径，一律直接走终端
RealtimeTalk 全双工对话；realtime_window 独立窗口包已作为孤儿代码删除。
本文件锁定该行为，防止窗口路径被误恢复；同时覆盖 DashScope API Key
防呆 fail-fast（非 dashscope 厂商不借用其它厂商 key）回归。

@author aceFelix
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.commands.handlers.voice_commands import _realtime_talk


class _FakeUI:
    """最小 UI 桩：记录 error/warn 调用，不产生终端输出。"""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warns: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warns.append(msg)


class _FakeRealtimeTalk:
    """RealtimeTalk 桩：记录构造配置与 run 调用，不真连 DashScope WS。"""

    last_config: dict | None = None
    run_calls: list = []

    def __init__(self, **config) -> None:
        type(self).last_config = config

    async def run(self, ui) -> None:
        type(self).run_calls.append(ui)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """清空环境中的真实 API Key，避免测试机配置干扰断言。"""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _FakeRealtimeTalk.last_config = None
    _FakeRealtimeTalk.run_calls.clear()


async def test_talk_runs_in_terminal(monkeypatch):
    """/talk 直接在终端运行 RealtimeTalk，配置正确透传，不再触碰窗口类。"""
    monkeypatch.setattr("agent.voice.realtime_talk.RealtimeTalk", _FakeRealtimeTalk)
    ui = _FakeUI()
    settings = SimpleNamespace(dashscope_api_key="sk-test", api_key="")

    await _realtime_talk(ui, settings)

    assert _FakeRealtimeTalk.run_calls == [ui]
    assert _FakeRealtimeTalk.last_config is not None
    assert _FakeRealtimeTalk.last_config["api_key"] == "sk-test"


async def test_talk_missing_key_errors(monkeypatch):
    """无任何 DashScope Key 时报错退出，不启动实时引擎。"""
    monkeypatch.setattr("agent.voice.realtime_talk.RealtimeTalk", _FakeRealtimeTalk)
    ui = _FakeUI()
    settings = SimpleNamespace(dashscope_api_key="", api_key="")

    await _realtime_talk(ui, settings)

    assert ui.errors and "DashScope" in ui.errors[0]
    assert _FakeRealtimeTalk.run_calls == []


async def test_talk_never_borrows_other_vendor_key(monkeypatch):
    """防呆 fail-fast：provider 非 dashscope 时，即使 settings.api_key 有值
    （如 deepseek key）也不得借去连 DashScope，应报错给出中文配置指引。

    背景：旧回退链会拿别家 key 硬连，被服务端 1007 Access denied 秒拒，
    用户只能看到英文报错（2026-09-10 实测踩坑）。
    """
    monkeypatch.setattr("agent.voice.realtime_talk.RealtimeTalk", _FakeRealtimeTalk)
    ui = _FakeUI()
    settings = SimpleNamespace(
        dashscope_api_key="", api_key="sk-deepseek-vendor", provider="deepseek",
    )

    await _realtime_talk(ui, settings)

    assert ui.errors and "DashScope" in ui.errors[0]
    # 不得用别家 key 启动会话
    assert _FakeRealtimeTalk.run_calls == []


async def test_talk_uses_api_key_when_provider_is_dashscope(monkeypatch):
    """provider 就是 dashscope 时，settings.api_key 本身即 DashScope key，可直用。"""
    monkeypatch.setattr("agent.voice.realtime_talk.RealtimeTalk", _FakeRealtimeTalk)
    ui = _FakeUI()
    settings = SimpleNamespace(
        dashscope_api_key="", api_key="sk-dashscope-native", provider="dashscope",
    )

    await _realtime_talk(ui, settings)

    assert ui.errors == []
    assert _FakeRealtimeTalk.run_calls == [ui]
    assert _FakeRealtimeTalk.last_config["api_key"] == "sk-dashscope-native"


def test_realtime_window_package_removed():
    """独立窗口包已删除：不允许残留导入路径（防窗口路线复活）。"""
    with pytest.raises(ModuleNotFoundError):
        import agent.ui.realtime_window  # noqa: F401
