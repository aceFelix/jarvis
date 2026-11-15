"""提炼空闲触发（profile_idle）单元测试。

用假 Timer 钉住：调度延迟、去重替换、解绑取消，以及到点后的三条复核条件
（会话仍匹配 / 真空闲 / 增量足够）与异常静默。

@author aceFelix
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent.config.settings import Settings
from agent.core.memory import profile_idle as I
from agent.core.memory import profile_ledger as L
from agent.core.memory import profile_refiner as R
from agent.core.message import Message, TextContent


class _FakeTimer:
    """替身计时器：只记录参数，不真正起线程。"""

    instances: list["_FakeTimer"] = []

    def __init__(self, delay: float, fn) -> None:
        self.delay = delay
        self.fn = fn
        self.daemon = False
        self.name = ""
        self.cancelled = False
        self.started = False
        _FakeTimer.instances.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


def _msg(text: str) -> Message:
    return Message(role="user", content=[TextContent(text=text)])


def _dialog(n: int) -> list[Message]:
    return [_msg(f"m{i}") for i in range(n)]


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch):
    L.configure_ledger_path(tmp_path / "memory" / "refine_ledger.json")
    monkeypatch.setattr(I, "_timer_factory", _FakeTimer)
    _FakeTimer.instances = []
    I.reset_for_tests()
    yield
    I.reset_for_tests()
    L.reset_for_tests()


def _settings(**kw) -> Settings:
    base = {"profile_enabled": True, "profile_refine_min_delta": 6,
            "profile_refine_idle_minutes": 60}
    base.update(kw)
    return Settings(**base)


def _bind(settings, messages=None, current="s1") -> None:
    I.bind(
        settings,
        messages_provider=lambda: messages if messages is not None else [],
        session_provider=lambda: current,
    )


class TestScheduling:
    def test_delay_counts_from_last_message(self) -> None:
        _bind(_settings())
        # 30 分钟前有消息 → 还差 30 分钟到 60 分钟空闲线
        I.schedule_idle_check("s1", last_msg_ts=time.time() - 1800)
        timer = _FakeTimer.instances[-1]
        assert timer.started is True
        assert 1795 <= timer.delay <= 1805

    def test_disabled_when_idle_zero(self) -> None:
        _bind(_settings(profile_refine_idle_minutes=0))
        assert I.schedule_idle_check("s1") is False
        assert _FakeTimer.instances == []

    def test_unbound_returns_false(self) -> None:
        assert I.schedule_idle_check("s1") is False

    def test_reschedule_cancels_previous(self) -> None:
        _bind(_settings())
        I.schedule_idle_check("s1")
        first = _FakeTimer.instances[-1]
        I.schedule_idle_check("s1")
        second = _FakeTimer.instances[-1]
        assert first.cancelled is True and second.cancelled is False
        assert I.pending_sessions() == ["s1"]
        assert len(_FakeTimer.instances) == 2

    def test_unbind_cancels_all(self) -> None:
        _bind(_settings())
        I.schedule_idle_check("s1")
        I.schedule_idle_check("s2")
        timers = list(_FakeTimer.instances)
        I.unbind()
        assert all(t.cancelled for t in timers)
        assert I.pending_sessions() == []


class TestFireConditions:
    def test_skips_when_session_switched(self, monkeypatch) -> None:
        called: list = []
        monkeypatch.setattr(R, "maybe_refine_async", lambda *a, **k: called.append(1) or True)
        L.touch("s1", 10, now=1000.0)
        _bind(_settings(), messages=_dialog(10), current="s2")
        I._fire("s1", now=9999.0)
        assert called == []

    def test_skips_when_not_idle_enough(self, monkeypatch) -> None:
        called: list = []
        monkeypatch.setattr(R, "maybe_refine_async", lambda *a, **k: called.append(1) or True)
        L.touch("s1", 10, now=1000.0)
        _bind(_settings(), messages=_dialog(10))
        I._fire("s1", now=1000.0 + 60)   # 只过了 1 分钟
        assert called == []

    def test_skips_when_no_new_messages(self, monkeypatch) -> None:
        called: list = []
        monkeypatch.setattr(R, "maybe_refine_async", lambda *a, **k: called.append(1) or True)
        L.touch("s1", 10, now=1000.0)
        L.advance("s1", 10, now=1001.0)   # 水位追平，无增量
        _bind(_settings(), messages=_dialog(10))
        I._fire("s1", now=1000.0 + 3600)
        assert called == []

    def test_triggers_with_single_message_tail(self, monkeypatch) -> None:
        """空闲触发门槛是 delta≥1：聊两三句走开也要落库（2026-11-15 实测修正）。"""
        called: list = []
        monkeypatch.setattr(R, "maybe_refine_async", lambda *a, **k: called.append(1) or True)
        L.touch("s1", 10, now=1000.0)
        L.advance("s1", 9, now=1001.0)   # 只剩 1 条增量
        _bind(_settings(), messages=_dialog(10))
        I._fire("s1", now=1000.0 + 3600)
        assert called == [1]

    def test_triggers_force_when_all_conditions_met(self, monkeypatch) -> None:
        captured: dict = {}

        def _fake(messages, sid, settings, *, force=False):
            captured.update(messages=messages, sid=sid, force=force)
            return True

        monkeypatch.setattr(R, "maybe_refine_async", _fake)
        L.touch("s1", 10, now=1000.0)
        L.advance("s1", 4, now=1001.0)   # 6 条增量，刚够
        _bind(_settings(), messages=_dialog(10))
        I._fire("s1", now=1000.0 + 3601)

        assert captured["sid"] == "s1"
        assert captured["force"] is True
        assert len(captured["messages"]) == 10

    def test_provider_exception_is_swallowed(self, monkeypatch) -> None:
        def _boom() -> list:
            raise RuntimeError("provider died")

        I.bind(_settings(), messages_provider=_boom, session_provider=lambda: "s1")
        L.touch("s1", 10, now=1000.0)
        I._fire("s1", now=1000.0 + 3601)   # 不抛即通过
