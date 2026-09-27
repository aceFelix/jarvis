"""语音识别（STT）—— 让 agent "听懂"用户。

阶段三第二刀。本包按**识别后端**拆分，三个后端彼此没有调用关系：

| 模块 | 后端 | 断句方式 | 传输 |
|---|---|---|---|
| paraformer.py | Paraformer 实时（Recognition） | 客户端 RMS 静音检测 | WS raw bytes |
| qwen.py | Qwen3-ASR（OmniRealtime） | 服务端 VAD | WS base64 |
| funasr.py | FunASR Flash | 客户端 RMS 两段录音 | HTTP POST 整段 WAV |

共享部分（音频常量、RMS 计算、停止标志、PCM→WAV 封装）在 common.py，
工厂函数 create_stt() 留在本文件按 model 名挑选后端。

**对外契约保持不变**：历史上所有调用方都用 `from agent.voice import stt`
再取属性（如 `stt._request_stop()`），因此这里把各子模块的符号原样
re-export，包与拆分前的单文件模块可互换。

核心能力:
1. **listen()**: 录一段话并识别成文字（阻塞）。麦克风录音 → 实时送识别 →
   静音检测自动停止 → 返回识别文本。/listen 命令用。
2. **transcribe_file()**: 识别本地音频文件（备选，一次性识别）。

线程模型: SDK 回调在子线程触发；主线程驱动录音循环 + 静音检测，
用 threading.Event 协调 on_open / on_complete / on_error。

依赖: pip install dashscope pyaudio
key: DASHSCOPE_API_KEY 环境变量（复用已有配置）

@author aceFelix
"""

from __future__ import annotations

from agent.voice.stt.common import (
    _FRAMES_PER_BUFFER,
    _MAX_SECONDS,
    _PCM_CHANNELS,
    _PCM_RATE,
    _PCM_WIDTH,
    _SILENCE_SECONDS,
    _SILENCE_THRESHOLD,
    _import_pyaudio,
    _is_stopped,
    _pcm_to_wav,
    _request_stop,
    _reset_stop,
    _rms,
    _stop_flag,
)
from agent.voice.stt.funasr import FunASRFlashSTT
from agent.voice.stt.paraformer import ParaformerSTT, _RecognitionCallback, _import_stt_deps
from agent.voice.stt.qwen import (
    _QWEN_REALTIME_URL,
    _VAD_LEAD_IN_SECONDS,
    _VAD_THRESHOLD,
    QwenASR,
    _QwenASRCallback,
    _import_qwen_omni,
)

__all__ = [
    "FunASRFlashSTT",
    "ParaformerSTT",
    "QwenASR",
    "create_stt",
]


def create_stt(
    *,
    api_key: str | None = None,
    model: str = "paraformer-realtime-v2",
    language: str = "zh",
) -> "ParaformerSTT | QwenASR | FunASRFlashSTT":
    """根据 model 名创建对应的 STT 后端。

    - model 以 "qwen" 开头 → QwenASR（OmniRealtimeConversation，服务端 VAD，质量高）
    - model 以 "paraformer" 开头 → ParaformerSTT（Recognition，客户端 VAD，轻量快）
    - model 为 "fun-asr-realtime" → ParaformerSTT（同为 Recognition 实时识别后端）
    - model 以 "fun-asr" 开头 → FunASRFlashSTT（HTTP POST 文件上传，非实时）
    - 其他 → 默认 ParaformerSTT

    这样上层只需改 settings.toml 的 stt_model 即可切换后端，代码自动适配。
    """
    m = model.lower()
    if m.startswith("qwen"):
        return QwenASR(api_key=api_key, model=model, language=language)
    if m.startswith("paraformer") or m == "fun-asr-realtime":
        return ParaformerSTT(api_key=api_key, model=model)
    if m.startswith("fun-asr"):
        return FunASRFlashSTT(api_key=api_key, model=model)
    return ParaformerSTT(api_key=api_key, model=model)
