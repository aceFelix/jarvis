"""语音模式静默待机与空格打断测试（2026-11-09）。

覆盖：
- 10 秒静默阈值默认值与钳制；
- 静默看门狗到点中断阻塞录音，轮次返回 False 进入待机；
- 累计静默已超阈值时不再启动录音；
- 键盘打断键默认 space（可配置回 esc）。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from agent.config.settings import Settings
from agent.voice.barge_in import _KeyBargeInWatcher
from agent.voice.voice_events import CollectingVoiceEvents
from agent.voice.voice_loop import (
    _IdleStandbyTimer,
    _idle_standby_seconds,
    _voice_loop_round,
)


def test_idle_standby_seconds_default_and_clamp() -> None:
    """默认 10 秒；0/负数关闭；超大值钳到 120 秒。"""
    assert _idle_standby_seconds(Settings()) == 10.0
    assert _idle_standby_seconds(SimpleNamespace(voice_idle_standby_seconds=0)) == 0.0
    assert _idle_standby_seconds(SimpleNamespace(voice_idle_standby_seconds=-1)) == 0.0
    assert _idle_standby_seconds(SimpleNamespace(voice_idle_standby_seconds=999)) == 120.0


def test_idle_timer_fires_and_marks_flag() -> None:
    """看门狗到点必须置 fired 并执行回调（voice_loop 靠它判断待机）。"""
    fired: list[bool] = []
    timer = _IdleStandbyTimer(0.05, lambda: fired.append(True))
    timer.start()
    time.sleep(0.2)
    assert timer.fired.is_set()
    assert fired == [True]


def test_round_enters_standby_before_starting_recording() -> None:
    """累计静默已超过阈值时，本轮直接返回 False，不再启动录音。"""
    events = CollectingVoiceEvents()
    state = {"last_speech": time.time() - 11}
    ok = asyncio.run(
        _voice_loop_round(
            events,
            SimpleNamespace(voice_idle_standby_seconds=10.0),
            None,
            None,
            None,
            None,
            threading.Event(),
            threading.Event(),
            state,
        )
    )
    assert ok is False
    assert any(
        "进入待机" in payload[0]
        for kind, payload in events.records
        if kind == "info"
    )


def test_round_watchdog_cuts_silent_recording() -> None:
    """录音一直没人说话：看门狗提前中断，轮次返回 False 进入待机。"""

    class _SlowEmptySTT:
        def listen(self, **kwargs):
            # 模拟阻塞录音：真实 stt.listen 会一直等到 max_seconds
            time.sleep(0.35)
            return {"text": ""}

    events = CollectingVoiceEvents()
    settings = SimpleNamespace(
        voice_idle_standby_seconds=0.15,
        voice_max_seconds=300,
        stt_silence_seconds=1.5,
    )
    ok = asyncio.run(
        _voice_loop_round(
            events,
            settings,
            None,
            None,
            None,
            _SlowEmptySTT(),
            threading.Event(),
            threading.Event(),
            {"last_speech": time.time()},
        )
    )
    assert ok is False
    assert any(
        "进入待机" in payload[0]
        for kind, payload in events.records
        if kind == "info"
    )


def test_key_barge_in_defaults_to_space() -> None:
    """键盘打断默认空格（用户要求；仍可配置回 esc）。"""
    watcher = _KeyBargeInWatcher(lambda: None)
    assert watcher._key == "space"


def test_dialog_drops_hallucinated_text_without_vad() -> None:
    """对话聆听：服务端没检测到 VAD 语音却返回「贾维斯」→ 当成没听清。"""

    class _FailLoop:
        async def run(self, text, ctx):
            raise AssertionError("幻觉文本不应上交给 LLM")

    class _HallucinatingSTT:
        def listen(self, **kwargs):
            return {"text": "贾维斯", "speech_detected": False}

    events = CollectingVoiceEvents()
    settings = SimpleNamespace(
        voice_idle_standby_seconds=0,  # 关掉看门狗，只验证 VAD 门禁
        voice_max_seconds=300,
        stt_silence_seconds=1.5,
    )
    ok = asyncio.run(
        _voice_loop_round(
            events,
            settings,
            _FailLoop(),
            SimpleNamespace(messages=[], abort_event=None, on_assistant_text=None),
            None,
            _HallucinatingSTT(),
            threading.Event(),
            threading.Event(),
            {"last_speech": time.time()},
        )
    )

    assert ok is True
    assert not [r for r in events.records if r[0] == "user_transcript"]
    assert any(
        "没听清" in payload[0]
        for kind, payload in events.records
        if kind == "info"
    )


def test_silence_timer_restarts_after_reply(monkeypatch) -> None:
    """长回答不能被算成用户沉默：计时应在 JARVIS 说完后重新开始。"""

    class _Msg:
        role = "assistant"

        def __init__(self, text: str) -> None:
            self._text = text

        def get_text(self) -> str:
            return self._text

        def get_thinking(self) -> str:
            return ""

    class _Ctx:
        def __init__(self) -> None:
            self.messages: list[_Msg] = []
            self.abort_event = None
            self.on_assistant_text = None
            self.tts_ok = True

    class _SlowLoop:
        async def run(self, text, ctx):
            # 模拟 LLM 思考 + 工具：耗时超过静默阈值
            await asyncio.sleep(0.18)
            ctx.messages.append(_Msg("好的"))
            return SimpleNamespace(iterations=1, tool_calls=0)

    class _Player:
        def start(self) -> bool:
            return True

        def feed(self, text: str) -> None:
            pass

        def finish(self) -> dict:
            time.sleep(0.05)
            return {}

        def stop(self) -> None:
            pass

    from agent.voice import stream_tts as st_module

    monkeypatch.setattr(st_module, "StreamTTSPlayer", lambda **kwargs: _Player())

    class _QuickSTT:
        def listen(self, **kwargs):
            return {"text": "你好"}

    events = CollectingVoiceEvents()
    ctx = _Ctx()
    state = {"last_speech": time.time()}
    settings = SimpleNamespace(
        voice_idle_standby_seconds=0.1,
        voice_max_seconds=300,
        stt_silence_seconds=1.5,
        tts_model="fake",
        tts_voice="fake",
        tts_volume=50,
        tts_speech_rate=1.0,
        tts_pitch_rate=1.0,
        verbose=False,
    )

    ok = asyncio.run(
        _voice_loop_round(
            events,
            settings,
            _SlowLoop(),
            ctx,
            None,
            _QuickSTT(),
            threading.Event(),
            threading.Event(),
            state,
        )
    )

    assert ok is True
    # 若把 LLM/TTS 时间算进沉默，这里会是 ~0.23s > 0.1；修复后应在说完时重置
    assert time.time() - state["last_speech"] < 0.1
