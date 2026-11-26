"""无状态音频计算（DSP）—— 两条语音链路共用。

当前只有归一化 RMS：原先 /talk 的 realtime_audio 与 /voice 的 voice_barge 各用一份，
分层后统一从这里取，避免 voice → talk 的反向依赖。

注意：`agent/voice/stt/common.py` 里另有一个 `_rms(frame, width) -> int`，契约不同
（返回整数能量、支持 8/16bit），服务于 STT 静音检测，**不与本函数合并**。

@author aceFelix
@since 2026-11-26
"""

from __future__ import annotations

import math
import struct


def rms(data: bytes) -> float:
    """计算 16bit mono PCM 的 RMS 音量，返回 0.0 ~ 1.0。

    Args:
        data: 16bit 单声道 PCM 字节；空数据返回 0.0。

    Returns:
        归一化音量（除以 32768 并封顶 1.0），可直接与 0.0x 量级门槛比较。

    @author aceFelix
    """
    count = len(data) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"{count}h", data[: count * 2])
    mean_square = sum(s * s for s in samples) / count
    return min(1.0, math.sqrt(mean_square) / 32768.0)


__all__ = ["rms"]
