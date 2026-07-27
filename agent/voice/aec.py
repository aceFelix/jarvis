"""WebRTC AEC3 回声消除封装。

近端（麦克风）与远端参考（扬声器）统一重采样到 16kHz，
按 10ms 帧（160 samples = 320 bytes）送入 WebRTC AudioProcessor。

依赖：pip install aec-audio-processing numpy

@author aceFelix
"""

from __future__ import annotations

import struct
from typing import Optional

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    from aec_audio_processing import AudioProcessor
    _HAS_AEC = True
except ImportError:
    _HAS_AEC = False


# 统一采样率与帧参数（WebRTC APM 要求 10ms 帧对齐）
_RATE = 16000
_FRAME_SAMPLES = 160          # 10ms @ 16kHz
_FRAME_BYTES = _FRAME_SAMPLES * 2  # 320 bytes（16bit mono）

# 扬声器采样率（DashScope realtime 模型输出 24kHz）
_PLAYBACK_RATE = 24000


def _resample_24k_to_16k(data: bytes) -> bytes:
    """将 24kHz 16bit mono PCM 线性插值重采样到 16kHz。

    @author aceFelix
    """
    n = len(data) // 2
    if n == 0:
        return b""
    arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    # 目标样本数 = n * 16000 / 24000 = n * 2/3
    out_n = int(n * _RATE / _PLAYBACK_RATE)
    if out_n == 0:
        return b""
    # 源索引（浮点），用于线性插值
    src_idx = np.arange(out_n, dtype=np.float32) * (_PLAYBACK_RATE / _RATE)
    i0 = src_idx.astype(np.int32)
    frac = src_idx - i0
    i0 = np.clip(i0, 0, n - 1)
    i1 = np.clip(i0 + 1, 0, n - 1)
    out = arr[i0] * (1.0 - frac) + arr[i1] * frac
    return out.astype(np.int16).tobytes()


class EchoCanceller:
    """WebRTC AEC3 回声消除器。

    远端（扬声器）音频通过 feed_reference() 喂入作为回声参考；
    近端（麦克风）音频通过 process_mic() 处理，返回消回声后的干净音频。

    两路信号在内部统一重采样到 16kHz，按 10ms 帧送入 WebRTC APM。
    AEC3 会自适应估计"扬声器→麦克风"的回声路径延迟，无需手动校准。

    @author aceFelix
    """

    def __init__(self) -> None:
        if not _HAS_AEC:
            raise RuntimeError(
                "aec-audio-processing 未安装，请运行: pip install aec-audio-processing"
            )
        if not _HAS_NUMPY:
            raise RuntimeError("numpy 未安装，AEC 重采样需要 numpy")

        # 创建 WebRTC AudioProcessor，启用 AEC + NS（噪声抑制）
        self._apm = AudioProcessor(
            enable_aec=True,
            enable_ns=True,      # 噪声抑制，提升 ASR 识别率
            enable_agc=False,    # 自动增益关闭，避免影响服务端 VAD 灵敏度
            enable_vad=False,    # 不用 WebRTC VAD，服务端已有 smart_turn
        )
        # 近端（麦克风）格式：16kHz mono
        self._apm.set_stream_format(sample_rate_in=_RATE, channel_count_in=1)
        # 远端参考（扬声器）格式：同样 16kHz（内部已重采样）
        self._apm.set_reverse_stream_format(sample_rate_in=_RATE, channel_count_in=1)
        # 初始延迟估计（扬声器→麦克风物理延迟，ms）
        # AEC3 会自适应修正此值。
        # 外放音箱时声学路径较长，适当调大初始值。
        try:
            self._apm.set_stream_delay(160)
        except Exception:
            pass

        # 远端参考信号缓冲区（16kHz，累积到 10ms 帧后送入 APM）
        self._ref_buf = bytearray()
        # 近端缓冲区（用于拼齐 10ms 帧）
        self._mic_buf = bytearray()
        # 统计计数，便于调试
        self._frame_count = 0

    def feed_reference(self, playback_audio: bytes) -> None:
        """喂入扬声器播放的音频（24kHz 16bit mono）作为远端参考。

        内部重采样到 16kHz 并按 10ms 帧送入 APM。
        应在对应时间段的 process_mic 之前调用，保证 AEC 有参考信号。
        @author aceFelix
        """
        if not playback_audio:
            return
        # 24kHz → 16kHz 重采样
        ref_16k = _resample_24k_to_16k(playback_audio)
        self._ref_buf.extend(ref_16k)
        # 按 10ms 帧送入 APM
        while len(self._ref_buf) >= _FRAME_BYTES:
            frame = bytes(self._ref_buf[:_FRAME_BYTES])
            del self._ref_buf[:_FRAME_BYTES]
            try:
                self._apm.process_reverse_stream(frame)
            except Exception:
                pass

    def process_mic(self, mic_data: bytes) -> bytes:
        """处理麦克风音频（16kHz 16bit mono），返回消回声后的音频。

        内部按 10ms 帧送入 APM，输出长度与输入相同（可能跨帧缓冲）。
        若 APM 处理失败，回退返回原始数据，保证链路不中断。
        @author aceFelix
        """
        if not mic_data:
            return b""
        self._mic_buf.extend(mic_data)
        out = bytearray()
        while len(self._mic_buf) >= _FRAME_BYTES:
            frame = bytes(self._mic_buf[:_FRAME_BYTES])
            del self._mic_buf[:_FRAME_BYTES]
            try:
                processed = self._apm.process_stream(frame)
                if processed:
                    out.extend(processed)
                else:
                    out.extend(frame)
                self._frame_count += 1
            except Exception:
                # APM 处理失败时回退原始数据，避免音频中断
                out.extend(frame)
        return bytes(out)

    def has_voice(self) -> bool:
        """查询最近一帧是否检测到人声（需 enable_vad=True 才有效）。"""
        try:
            return self._apm.has_voice()
        except Exception:
            return False


def is_available() -> bool:
    """检查 AEC 依赖是否可用（aec-audio-processing + numpy 均已安装）。"""
    return _HAS_AEC and _HAS_NUMPY
