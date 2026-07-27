"""P1-3 实时语音稳定性 — 单元测试。

覆盖 ClientVAD 的核心逻辑。

@author aceFelix
"""

from __future__ import annotations

import struct

import pytest

from agent.voice.client_vad import ClientVAD, _rms


# ---- _rms 测试 ----


class TestRMS:
    """测试 RMS 音量计算。"""

    def test_empty_data(self) -> None:
        """空数据返回 0.0。"""
        assert _rms(b"") == 0.0

    def test_silence(self) -> None:
        """全零数据返回 0.0。"""
        data = struct.pack(f"{10}h", *([0] * 10))
        assert _rms(data) == 0.0

    def test_full_volume(self) -> None:
        """满幅数据返回接近 1.0。"""
        data = struct.pack(f"{10}h", *([32767] * 10))
        rms = _rms(data)
        assert 0.99 <= rms <= 1.0

    def test_half_volume(self) -> None:
        """半幅数据返回约 0.5。"""
        data = struct.pack(f"{10}h", *([16384] * 10))
        rms = _rms(data)
        assert 0.45 <= rms <= 0.55


# ---- ClientVAD 测试 ----


class TestClientVAD:
    """测试客户端 VAD。"""

    def test_disabled_does_not_trigger(self) -> None:
        """禁用时不触发。"""
        triggered = []
        vad = ClientVAD(
            threshold=0.01,
            trigger_frames=2,
            on_barge_in=lambda: triggered.append(True),
        )
        vad.enabled = False
        vad.set_ai_speaking(True)
        # 喂入高音量数据
        loud = struct.pack(f"{100}h", *([32767] * 100))
        for _ in range(10):
            vad.feed(loud)
        assert len(triggered) == 0

    def test_not_triggered_when_ai_silent(self) -> None:
        """AI 不说话时不触发。"""
        triggered = []
        vad = ClientVAD(
            threshold=0.01,
            trigger_frames=2,
            on_barge_in=lambda: triggered.append(True),
        )
        vad.set_ai_speaking(False)
        loud = struct.pack(f"{100}h", *([32767] * 100))
        for _ in range(10):
            vad.feed(loud)
        assert len(triggered) == 0

    def test_triggers_on_continuous_loud(self) -> None:
        """连续高音量帧触发打断。"""
        triggered = []
        vad = ClientVAD(
            threshold=0.01,
            trigger_frames=3,
            on_barge_in=lambda: triggered.append(True),
        )
        vad.set_ai_speaking(True)
        loud = struct.pack(f"{100}h", *([32767] * 100))
        vad.feed(loud)  # frame 1
        vad.feed(loud)  # frame 2
        assert len(triggered) == 0
        vad.feed(loud)  # frame 3 → trigger
        assert len(triggered) == 1

    def test_silence_resets_counter(self) -> None:
        """超过容忍上限的静音帧才重置连续计数。"""
        triggered = []
        vad = ClientVAD(
            threshold=0.01,
            trigger_frames=3,
            max_silence_frames=2,
            on_barge_in=lambda: triggered.append(True),
        )
        vad.set_ai_speaking(True)
        loud = struct.pack(f"{100}h", *([32767] * 100))
        silent = struct.pack(f"{100}h", *([0] * 100))
        vad.feed(loud)   # frame 1
        vad.feed(loud)   # frame 2
        # 2 帧静音在容忍范围内，不重置
        vad.feed(silent)
        vad.feed(silent)
        vad.feed(loud)   # frame 3 → trigger
        assert len(triggered) == 1

    def test_silence_beyond_tolerance_resets(self) -> None:
        """超过容忍上限的连续静音帧重置 loud 计数。"""
        triggered = []
        vad = ClientVAD(
            threshold=0.01,
            trigger_frames=3,
            max_silence_frames=2,
            on_barge_in=lambda: triggered.append(True),
        )
        vad.set_ai_speaking(True)
        loud = struct.pack(f"{100}h", *([32767] * 100))
        silent = struct.pack(f"{100}h", *([0] * 100))
        vad.feed(loud)   # frame 1
        vad.feed(loud)   # frame 2
        # 3 帧静音超过容忍上限 → 重置
        vad.feed(silent)
        vad.feed(silent)
        vad.feed(silent)
        vad.feed(loud)   # frame 1 again
        vad.feed(loud)   # frame 2 again
        assert len(triggered) == 0  # 未达到 3 帧

    def test_cooldown_prevents_repeat(self) -> None:
        """冷却期防止重复触发。"""
        triggered = []
        vad = ClientVAD(
            threshold=0.01,
            trigger_frames=2,
            cooldown_frames=5,
            on_barge_in=lambda: triggered.append(True),
        )
        vad.set_ai_speaking(True)
        loud = struct.pack(f"{100}h", *([32767] * 100))
        # 触发一次
        vad.feed(loud)
        vad.feed(loud)
        assert len(triggered) == 1
        # 冷却期内不触发（5 帧冷却）
        for _ in range(5):
            vad.feed(loud)
        assert len(triggered) == 1
        # 冷却结束后需重新积累 trigger_frames 帧才触发
        vad.feed(loud)  # frame 1
        vad.feed(loud)  # frame 2 → trigger
        assert len(triggered) == 2

    def test_reset(self) -> None:
        """reset 清空所有状态。"""
        vad = ClientVAD(threshold=0.5, trigger_frames=5)
        vad.set_ai_speaking(True)
        vad._consecutive_active = 3
        vad._consecutive_silence = 2
        vad._cooldown_remaining = 2
        vad.reset()
        assert vad._consecutive_active == 0
        assert vad._consecutive_silence == 0
        assert vad._cooldown_remaining == 0
        assert vad._ai_speaking is False

    def test_set_ai_speaking_resets(self) -> None:
        """设置 AI 不说话时重置计数。"""
        vad = ClientVAD(threshold=0.5, trigger_frames=3)
        vad.set_ai_speaking(True)
        vad._consecutive_active = 5
        vad.set_ai_speaking(False)
        assert vad._consecutive_active == 0

    def test_delay_frames_prevents_early_trigger(self) -> None:
        """AI 开口后的 delay_frames 期间不触发打断。"""
        triggered = []
        vad = ClientVAD(
            threshold=0.01,
            trigger_frames=2,
            delay_frames=3,
            on_barge_in=lambda: triggered.append(True),
        )
        vad.set_ai_speaking(True)
        loud = struct.pack(f"{100}h", *([32767] * 100))
        # 前 3 帧处于 delay 期，不触发
        vad.feed(loud)
        vad.feed(loud)
        vad.feed(loud)
        assert len(triggered) == 0
        # delay 结束后还需连续 trigger_frames 帧才触发
        vad.feed(loud)  # frame 1
        vad.feed(loud)  # frame 2 → trigger
        assert len(triggered) == 1

    def test_delay_frames_initializes_on_speaking_start(self) -> None:
        """每次 AI 开始说话时重置 delay_remaining。"""
        vad = ClientVAD(threshold=0.5, trigger_frames=3, delay_frames=3)
        vad.set_ai_speaking(True)
        assert vad._delay_remaining == 3
        vad.set_ai_speaking(False)
        assert vad._delay_remaining == 0
