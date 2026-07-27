"""客户端 VAD（语音活动检测）兜底模块。

在实时双工语音对话中，服务端 VAD 可能因网络延迟或模型漏检而未及时触发
``input_audio_buffer.speech_started`` 事件。本模块通过轻量级 RMS 能量检测，
在 AI 播报期间持续监控麦克风输入，当检测到用户持续说话时主动触发打断。

仅作为服务端 VAD 的补充，不替代服务端 VAD 的 speech_started/speech_stopped 事件。

借鉴 openclaw ``RealtimeMulawSpeechStartDetector`` 的设计：
- 不仅计数连续 loud frames 触发说话，还计数连续 quiet frames 重置状态
- 允许短暂静音间隔（词间停顿），不因单帧静音就重置计数

@author aceFelix
"""

from __future__ import annotations

import math
import struct
from typing import Callable


def _rms(data: bytes) -> float:
    """计算 16bit PCM 音频数据的 RMS 音量，返回 0.0 ~ 1.0。

    Args:
        data: PCM 16bit mono 音频字节。

    Returns:
        RMS 音量值，范围 0.0 ~ 1.0。
    """
    count = len(data) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"{count}h", data[: count * 2])
    mean_square = sum(s * s for s in samples) / count
    return min(1.0, math.sqrt(mean_square) / 32768.0)


class ClientVAD:
    """客户端 RMS 能量 VAD，作为服务端 VAD 的兜底。

    工作原理：
    1. 仅在 AI 说话期间（``ai_speaking=True``）启用检测
    2. 对每个音频帧计算 RMS 音量
    3. 连续 ``trigger_frames`` 帧音量超过 ``threshold`` → 触发打断回调
    4. 允许 ``max_silence_frames`` 帧静音不重置计数（容忍词间停顿）
    5. 超过 ``max_silence_frames`` 连续静音才重置 loud 计数
    6. 触发后进入冷却期（``cooldown_frames``），防止重复触发

    Attributes:
        threshold: RMS 能量阈值，超过视为"有声音"。
        trigger_frames: 连续 N 帧超阈值才触发，防误检。
        max_silence_frames: 允许的最大连续静音帧数，超过才重置 loud 计数。
        cooldown_frames: 触发后冷却帧数，防止重复触发。
    """

    def __init__(
        self,
        *,
        threshold: float = 0.10,
        trigger_frames: int = 5,
        max_silence_frames: int = 4,
        cooldown_frames: int = 20,
        delay_frames: int = 0,
        on_barge_in: Callable[[], None] | None = None,
    ) -> None:
        self._threshold = threshold
        self._trigger_frames = trigger_frames
        self._max_silence_frames = max_silence_frames
        self._cooldown_frames = cooldown_frames
        self._delay_frames = max(0, delay_frames)
        self._on_barge_in = on_barge_in

        self._ai_speaking = False
        self._consecutive_active = 0
        self._consecutive_silence = 0
        self._cooldown_remaining = 0
        self._delay_remaining = 0
        self._enabled = True

    @property
    def enabled(self) -> bool:
        """是否启用客户端 VAD。"""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
        if not value:
            self._consecutive_active = 0
            self._consecutive_silence = 0
            self._cooldown_remaining = 0
            self._delay_remaining = 0

    def set_ai_speaking(self, speaking: bool) -> None:
        """设置 AI 是否正在说话。

        仅在 AI 说话期间检测用户打断。
        AI 刚开始说话时启动 delay_frames 倒计时，避免自身声音回授被误判。

        Args:
            speaking: True 表示 AI 开始说话，False 表示 AI 说话结束。
        """
        self._ai_speaking = speaking
        if speaking:
            self._delay_remaining = self._delay_frames
        else:
            self._delay_remaining = 0
        self._consecutive_active = 0
        self._consecutive_silence = 0
        self._cooldown_remaining = 0

    def feed(self, audio_chunk: bytes) -> bool:
        """喂入一帧音频数据，返回是否触发了打断。

        Args:
            audio_chunk: PCM 16bit mono 音频字节。

        Returns:
            True 表示本帧触发了打断回调。
        """
        if not self._enabled or not self._ai_speaking:
            return False

        # AI 刚开口的延迟期内不检测，避免回授尖峰误触发
        if self._delay_remaining > 0:
            self._delay_remaining -= 1
            return False

        # 冷却期计数
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            self._consecutive_active = 0
            self._consecutive_silence = 0
            return False

        volume = _rms(audio_chunk)
        if volume >= self._threshold:
            self._consecutive_active += 1
            self._consecutive_silence = 0
            if self._consecutive_active >= self._trigger_frames:
                # 触发打断
                self._consecutive_active = 0
                self._cooldown_remaining = self._cooldown_frames
                if self._on_barge_in:
                    try:
                        self._on_barge_in()
                    except Exception:
                        pass
                return True
        else:
            # 静音帧 → 累计静音计数，超过容忍上限才重置 loud 计数
            self._consecutive_silence += 1
            if self._consecutive_silence > self._max_silence_frames:
                self._consecutive_active = 0

        return False

    def reset(self) -> None:
        """重置状态（用于重连后清空历史）。"""
        self._consecutive_active = 0
        self._consecutive_silence = 0
        self._cooldown_remaining = 0
        self._delay_remaining = 0
        self._ai_speaking = False
