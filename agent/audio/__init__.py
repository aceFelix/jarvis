"""音频基础设施 —— /voice 与 /talk 两条链路共用的底层能力。

分层约束（见 docs/plans/voice/talk-voice-layering.md）：
- 本包**禁止** import agent.voice 或 agent.talk，只依赖第三方与标准库；
- 两条链路互不 import，需要共用的一律下沉到这里。

模块职责：
- device: PyAudio 全局单例（Windows 多实例并发会 segfault）
- aec: WebRTC AEC3 回声消除封装
- mic_lock: 麦克风互斥锁（/talk 与 /voice 不能同时开麦）
- providers: 语音厂商注册表（STT/TTS/realtime 共用，未知厂商 fail-closed）
- dsp: 归一化 RMS 等无状态音频计算

@author aceFelix
@since 2026-11-26
"""

from agent.audio.device import get_pyaudio, release_pyaudio
from agent.audio.aec import EchoCanceller, is_available
from agent.audio.mic_lock import acquire_voice_lock, release_voice_lock
from agent.audio.dsp import rms

__all__ = [
    "get_pyaudio",
    "release_pyaudio",
    "EchoCanceller",
    "is_available",
    "acquire_voice_lock",
    "release_voice_lock",
    "rms",
]
