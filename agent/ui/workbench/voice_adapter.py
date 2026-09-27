"""工作台 / 桌面壳的语音事件适配器（自 engine.py 拆出，控引擎文件行数）。

``ServeVoiceAdapter`` 把解耦后的 voice_loop 事件转成 ``voice_*`` WS 事件
外抛给 serve / 桌面壳（jarvis-desktop）渲染；提示 / 错误复用 info / warn /
error 通道。音频（STT 录音 / TTS 播放）留在本进程本地 pyaudio，不向 GUI
传音频流。与 REPL 的 ``RichCLIVoiceAdapter`` 共用同一 voice_loop
（协议见 agent/voice/voice_events.py）。

@author aceFelix
"""

from __future__ import annotations

from agent.ui.workbench.bridge import _EventEmitter
from agent.voice.voice_events import VoiceEventsBase

# 半双工语音 WS 事件名（与 agent/serve/protocol.py 的 EVT_VOICE_* 字面量对齐；
# 此处不 import serve 包，避免 workbench ↔ serve 循环依赖）。
_EVT_VOICE_STARTED = "voice_started"
_EVT_VOICE_STOPPED = "voice_stopped"
_EVT_VOICE_STATE = "voice_state"
_EVT_VOICE_USER_TRANSCRIPT = "voice_user_transcript"
_EVT_VOICE_AI_TEXT_DELTA = "voice_ai_text_delta"
_EVT_VOICE_AI_TEXT = "voice_ai_text"


class ServeVoiceAdapter(VoiceEventsBase):
    """serve / 桌面壳宿主适配器：voice_loop 事件 → WS 事件外抛。

    把解耦 voice_loop 的状态 / 转录 / 流式文本转成 ``voice_*`` 事件经
    event_queue 推给桌面壳（jarvis-desktop）渲染；提示 / 错误复用现有
    info / warn / error 通道。音频（STT 录音 / TTS 播放）留在本进程本地
    pyaudio，不向 GUI 传音频流。

    @author aceFelix
    """

    def __init__(self, emitter: _EventEmitter) -> None:
        self._emitter = emitter

    def on_state(self, state: str) -> None:
        """状态迁移 → voice_state 事件。"""
        self._emitter.emit(_EVT_VOICE_STATE, state)

    def on_user_transcript(self, text: str) -> None:
        """用户识别全文 → voice_user_transcript 事件（上屏用户气泡）。"""
        self._emitter.emit(_EVT_VOICE_USER_TRANSCRIPT, text)

    def on_ai_text_delta(self, text: str) -> None:
        """AI 流式增量 → voice_ai_text_delta 事件（流式上屏）。"""
        self._emitter.emit(_EVT_VOICE_AI_TEXT_DELTA, text)

    def on_ai_text(self, text: str) -> None:
        """AI 回复全文 → voice_ai_text 事件（流式收尾校验）。"""
        self._emitter.emit(_EVT_VOICE_AI_TEXT, text)

    def on_info(self, msg: str, level: str = "info") -> None:
        """提示 / 告警 → 复用 info / warn 通道。"""
        self._emitter.emit(level if level in ("info", "warn") else "info", msg)

    def on_error(self, msg: str) -> None:
        """错误 → 复用 error 通道。"""
        self._emitter.emit("error", msg)
