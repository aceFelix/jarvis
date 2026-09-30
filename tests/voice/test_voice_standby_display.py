"""<standby/> 退下标记不再泄漏到上屏文本的回归测试。

背景：语音 system prompt 要求模型在用户表达"退下"时在回复结尾输出
``<standby/>`` 控制标记。TTS 音频路径一直在朗读前剥除该标记（用户听不到），
但**显示路径**（on_ai_text / on_ai_text_delta → 桌面壳聊天气泡 / 终端）曾把
含标记的原文直接上屏，导致气泡里出现字面 ``<standby/>``。修复：voice_loop 在
外抛显示流（全量收尾 + 流式 delta）前统一剥除该标记；退下检测仍读原始消息，
不受影响。

覆盖：
- 流式 delta：on_ai_text_delta 收到的每段均不含 <standby/>；
- 全量收尾：on_ai_text 收到的整段文本不含 <standby/>；
- 退下检测仍生效：含标记的回复使本轮返回 False（进入待机）。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import agent.voice.stream_tts as stream_tts_module
from agent.voice.voice_events import STATE_STANDBY, CollectingVoiceEvents


def _settings() -> SimpleNamespace:
    """最小 settings 假件：仅含语音一轮触及的字段。"""
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


class _FakeMsg:
    """assistant 消息假件：get_text/get_thinking 供 voice_loop 提取回复。"""

    def __init__(self, role: str, text: str) -> None:
        self.role = role
        self._text = text

    def get_text(self) -> str:
        return self._text

    def get_thinking(self) -> str:
        return ""


class _QuickSTT:
    """假 STT：立即返回固定识别文本（不阻塞、不查停止标志）。"""

    def listen(self, **kwargs):
        return {"text": "退下吧"}


class _StandbyQueryLoop:
    """假 QueryLoop：run 时把"告别语 + <standby/>"经 feeder 流式吐出，并把
    含标记的整条 assistant 消息追加进 ctx.messages（模拟真实回复落库）。"""

    def __init__(self) -> None:
        self._thinking = False

    def is_thinking_enabled(self) -> bool:
        return self._thinking

    def set_thinking_enabled(self, on: bool) -> None:
        self._thinking = on

    async def run(self, text, ctx, images=None):
        # 分两段吐字：第二段整体含 <standby/>（覆盖 delta 单帧剥标）
        ctx.on_assistant_text("好的，再见")
        ctx.on_assistant_text("，有事叫我<standby/>")
        ctx.messages.append(_FakeMsg("assistant", "好的，再见，有事叫我<standby/>"))
        return SimpleNamespace(stopped_reason="end", iterations=1, tool_calls=0)


class _FakeStreamPlayer:
    """假流式 TTS 播放器：start 成功、feed/stop 空实现、finish 返回空统计。"""

    def __init__(self, **kwargs) -> None:
        pass

    def start(self) -> bool:
        return True

    def feed(self, text: str) -> None:
        pass

    def stop(self) -> None:
        pass

    def finish(self) -> dict:
        return {}


class _FakeCtx:
    """最小 ToolContext 假件：消息列表 + abort_event + feeder 挂点。"""

    def __init__(self) -> None:
        self.messages: list = []
        self.abort_event = asyncio.Event()
        self.on_assistant_text = None
        self.tts_ok = True


def _run_round(monkeypatch) -> CollectingVoiceEvents:
    """在剥标修复下跑一整轮语音回复，返回收集到的事件。"""
    from agent.voice.voice_loop import _voice_loop_round

    monkeypatch.setattr(stream_tts_module, "StreamTTSPlayer", _FakeStreamPlayer)
    events = CollectingVoiceEvents()
    ctx = _FakeCtx()

    async def _go() -> bool:
        return await _voice_loop_round(
            events, _settings(), _StandbyQueryLoop(), ctx, None, _QuickSTT(),
            threading.Event(), threading.Event(),
        )

    continue_round = asyncio.run(_go())
    # 含 <standby/> 的回复应触发退下检测 → 本轮不继续（进入待机）
    assert continue_round is False
    return events


def test_ai_text_delta_strips_standby(monkeypatch) -> None:
    """流式 delta 上屏：每段都不含 <standby/> 标记。"""
    events = _run_round(monkeypatch)
    deltas = [p for k, p in events.records if k == "ai_text_delta"]
    assert deltas == ["好的，再见", "，有事叫我"]
    assert all("<standby" not in d for d in deltas)


def test_ai_text_full_strips_standby(monkeypatch) -> None:
    """全量收尾上屏：整段文本已剥标，气泡不再残留 <standby/>。"""
    events = _run_round(monkeypatch)
    full = [p for k, p in events.records if k == "ai_text"]
    assert full == ["好的，再见，有事叫我"]


def test_standby_state_still_emitted(monkeypatch) -> None:
    """剥标不影响退下检测：仍迁移到 standby 状态。"""
    events = _run_round(monkeypatch)
    assert STATE_STANDBY in events.states()
