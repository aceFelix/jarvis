"""语音识别（STT）—— 让 agent "听懂"用户。

阶段三第二刀。原 stt.py 单文件 1087 行超出「单文件 ≤ 800 行」上限，
按识别后端拆成包；随后按「一个模型、一个后端、不留无用实现」的决策
精简为单一后端：

| 模块 | 内容 |
|---|---|
| common.py | 音频常量、`_rms()`、停止标志、pyaudio 延迟导入 |
| qwen.py | `QwenASR`（DashScope OmniRealtime，服务端 VAD，中英混合强） |
| __init__.py | `create_stt()` 工厂 + 兼容符号 re-export |

已下线的后端（留记录以免误以为还存在）：
- `ParaformerSTT`：Recognition WebSocket + 客户端 RMS 静音检测
- `FunASRFlashSTT`：HTTP POST 整段 WAV，非实时，不适用于 /voice 循环

**对外契约**：调用方都用 `from agent.voice import stt` 再取属性
（如 `stt._request_stop()`、`stt._rms()`），因此本包把实际存在的子模块
符号原样 re-export，包内符号与调用方保持一致。

核心能力:
1. **listen()**: 录一段话并识别成文字（阻塞）。麦克风录音 → 实时送识别 →
   服务端 VAD 断句 → 返回识别文本。/listen 与 /voice 均用。

线程模型: SDK 回调在子线程触发；主线程驱动录音循环，
用 threading.Event 协调 on_open / 会话结束 / on_error。

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
    _import_pyaudio,
    _is_stopped,
    _request_stop,
    _reset_stop,
    _rms,
    _stop_flag,
)
from agent.voice.stt.qwen import (
    _QWEN_REALTIME_URL,
    _VAD_LEAD_IN_SECONDS,
    _VAD_THRESHOLD,
    QwenASR,
    _QwenASRCallback,
    _import_qwen_omni,
)

__all__ = ["QwenASR", "create_stt"]


def create_stt(
    *,
    api_key: str | None = None,
    model: str = "qwen3-asr-flash-realtime",
    language: str = "zh",
) -> "QwenASR":
    """创建 STT 后端实例。

    现在只有一个后端实现，因此不再按 model 名前缀分派：model 原样透传给
    QwenASR，需填 DashScope 的 qwen 实时识别模型名（如
    qwen3-asr-flash-realtime）。若填 paraformer / fun-asr 之类，会在建连
    阶段被服务端拒绝，从而及早暴露配置错误。

    Args:
        api_key: DashScope API Key，留空则回退到 DASHSCOPE_API_KEY 环境变量。
        model: 实时识别模型名（透传）。
        language: 识别语言，透传给服务端 transcription_params。

    Returns:
        QwenASR 实例，提供阻塞式 listen() 录音识别。

    @author aceFelix
    """
    return QwenASR(api_key=api_key, model=model, language=language)
