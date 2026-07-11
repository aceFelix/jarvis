"""语音模块 —— 让 agent 能听会说。

阶段三「实时语音」的实现。分两个方向:
- tts: 文字转语音（TTS），基于阿里 CosyVoice，让 agent "开口说话"。
- stt: 语音转文字（STT），支持双后端:
  - ParaformerSTT: 阿里 Paraformer 实时识别（轻量快，客户端 VAD）
  - QwenASR: Qwen3-ASR（质量高，服务端 VAD，中英混合强）
  - create_stt() 工厂函数按 model 名自动选择后端

依赖: dashscope SDK + pyaudio（播放/录音）。
"""

from agent.voice.stt import ParaformerSTT, QwenASR, create_stt
from agent.voice.tts import CosyVoiceTTS
from agent.voice.voice_loop import voice_loop

__all__ = ["CosyVoiceTTS", "ParaformerSTT", "QwenASR", "create_stt", "voice_loop"]
