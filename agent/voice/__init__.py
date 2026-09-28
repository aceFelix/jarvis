"""语音模块 —— 让 agent 能听会说。

阶段三「实时语音」的实现。分两个方向:
- tts: 文字转语音（TTS），基于阿里 CosyVoice，让 agent "开口说话"。
- stt: 语音转文字（STT），单一后端 QwenASR（Qwen3-ASR，OmniRealtime 服务端 VAD，
  中英混合强）；create_stt() 工厂只做参数透传，不再按 model 名分派。

依赖: dashscope SDK + pyaudio（播放/录音）。
"""

from agent.voice.stt import QwenASR, create_stt
from agent.voice.stream_tts import StreamTTSPlayer
from agent.voice.tts import CosyVoiceTTS
from agent.voice.voice_loop import voice_loop

__all__ = ["CosyVoiceTTS", "QwenASR", "StreamTTSPlayer", "create_stt", "voice_loop"]
