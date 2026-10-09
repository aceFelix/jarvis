"""voice_loop on_turn_end 每轮落库回调测试（半双工语音会话保存修复）。

背景（见 docs/fixlogs/voice-half-duplex-persist-fix.md）：半双工语音轮次
写入 ctx.messages（与工作台引擎 self._messages 同一列表），但整段语音会话
从未触发宿主持久化，重启即丢。voice_loop 新增 on_turn_end 可选回调——本轮
消息有增长即通知宿主（工作台传 _after_turn）。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from agent.voice.voice_events import CollectingVoiceEvents, STATE_EXITED


def _settings() -> SimpleNamespace:
    """最小 settings 假件：仅含语音循环触及的字段。"""
    return SimpleNamespace(
        api_key="test-key",
        verbose=False,
        voice_max_seconds=5,
        stt_silence_seconds=1.0,
        stt_model="fake-stt",
        tts_model="fake-tts",
        tts_voice="fake-voice",
        tts_volume=50,
        tts_speech_rate=1.0,
        tts_pitch_rate=1.0,
    )


class _FakeCtx:
    """最小 ToolContext 假件：消息列表 + abort_event + feeder 挂点。"""

    def __init__(self) -> None:
        self.messages: list = []
        self.abort_event = asyncio.Event()
        self.on_assistant_text = None
        self.tts_ok = True


class _FakeQueryLoop:
    """最小 QueryLoop 假件：voice_loop 仅用思考开关与 _system 属性。"""

    def __init__(self) -> None:
        self._system = ""
        self._thinking = False

    def is_thinking_enabled(self) -> bool:
        return self._thinking

    def set_thinking_enabled(self, on: bool) -> None:
        self._thinking = on


def _run_voice_loop(monkeypatch, fake_round, on_turn_end) -> tuple[list, list]:
    """跑 voice_loop 至退出：monkeypatch 音频与轮次函数，返回 (回调实参, 状态)。"""
    import sys

    import agent.voice as voice_pkg
    import agent.voice.tts as tts_mod
    import agent.voice.voice_state as voice_state
    # 包 __init__ 用同名函数重导出遮蔽了模块属性，需从 sys.modules 取模块本体
    import agent.voice.voice_loop  # noqa: F401

    vl = sys.modules["agent.voice.voice_loop"]

    monkeypatch.setattr(voice_pkg, "create_stt", lambda **kw: SimpleNamespace())
    monkeypatch.setattr(
        tts_mod, "CosyVoiceTTS", lambda **kw: SimpleNamespace(stop=lambda: None)
    )
    # 隔离跨进程语音锁：避免本机恰有 jarvis 语音会话时测试误败
    monkeypatch.setattr(voice_state, "acquire_voice_lock", lambda: (True, ""))
    monkeypatch.setattr(voice_state, "release_voice_lock", lambda: None)
    monkeypatch.setattr(vl, "_voice_loop_round", fake_round)

    calls: list = []
    states: list = []
    events = CollectingVoiceEvents()
    # 事件转储到 states（CollectingVoiceEvents 只存状态码序列）
    ctx = _FakeCtx()
    stop_event = threading.Event()

    async def _run() -> None:
        await vl.voice_loop(
            events, _settings(), _FakeQueryLoop(), ctx,
            stop_event=stop_event, interrupt_event=threading.Event(),
            on_turn_end=on_turn_end,
        )

    asyncio.run(_run())
    return calls, events.states()


def test_on_turn_end_fires_when_round_grows_messages(monkeypatch) -> None:
    """本轮消息有增长：回调触发一次（宿主据此落库）。"""
    calls: list = []

    async def fake_round(events, settings, loop, ctx, tts, stt, intr, stop):
        # 模拟 QueryLoop 写入本轮用户/助手消息
        ctx.messages.append(SimpleNamespace(role="user", get_text=lambda: "问"))
        ctx.messages.append(SimpleNamespace(role="assistant", get_text=lambda: "答"))
        stop.set()  # 一轮后结束会话（不再进待机录音）
        return False

    _calls, states = _run_voice_loop(
        monkeypatch, fake_round, lambda: calls.append(1)
    )
    assert calls == [1]
    assert states[-1] == STATE_EXITED


def test_on_turn_end_skipped_when_no_message_growth(monkeypatch) -> None:
    """本轮无消息增长（纯打断/空轮）：不触发回调，不产生空存盘。"""
    calls: list = []

    async def fake_round(events, settings, loop, ctx, tts, stt, intr, stop):
        stop.set()
        return False

    _calls, _states = _run_voice_loop(
        monkeypatch, fake_round, lambda: calls.append(1)
    )
    assert calls == []


def test_on_turn_end_exception_swallowed(monkeypatch) -> None:
    """回调抛异常：静默吞掉，不中断语音循环（会话仍干净退出）。"""

    async def fake_round(events, settings, loop, ctx, tts, stt, intr, stop):
        ctx.messages.append(SimpleNamespace(role="user", get_text=lambda: "问"))
        stop.set()
        return False

    def _boom() -> None:
        raise RuntimeError("存盘失败")

    _calls, states = _run_voice_loop(monkeypatch, fake_round, _boom)
    assert states[-1] == STATE_EXITED
