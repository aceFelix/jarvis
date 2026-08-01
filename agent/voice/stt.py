"""语音识别（STT）—— 让 agent "听懂"用户。

阶段三第二刀。基于阿里 Paraformer 实时语音识别（DashScope），用 pyaudio 录音。

核心能力:
1. **listen()**: 录一段话并识别成文字（阻塞）。麦克风录音 → 实时送识别 →
   静音检测自动停止 → 返回识别文本。/listen 命令用。
2. **transcribe_file()**: 识别本地音频文件（备选，用 SDK 的 call() 一次性识别）。

设计要点:
- **实时流式**: 录音帧边录边送 `send_audio_frame`，服务端边收边出中间结果，
  on_event 回调推送。低延迟，为第三刀流式语音循环打基础。
- **静音检测自动停**: 录音主线程计算每帧 RMS，连续静音超过阈值视为"说完了"，
  主动 stop()。避免用户手动按键，体验接近真正的语音助手。
- **线程模型**: SDK 回调在子线程触发；主线程驱动录音循环 + 静音检测。
  threading.Event 协调 on_open / on_complete / on_error。
- **采样率 16kHz**: Paraformer 实时模型要求 16kHz 单声道 16-bit PCM。

依赖: pip install dashscope pyaudio
API: DashScope WebSocket（wss://dashscope.aliyuncs.com/api-ws/v1/inference）
key: DASHSCOPE_API_KEY 环境变量（复用已有配置）
"""

from __future__ import annotations

import array
import base64
import json
import os
import threading
import time
from typing import Any

from agent.core.result import ToolResult


def _rms(frame: bytes, width: int = 2) -> int:
    """计算 PCM 帧的 RMS（均方根）能量，用于静音检测。

    Python 3.13 移除了标准库 audioop，这里用 array 手动实现等价逻辑。
    """
    if width == 2:
        a = array.array("h")  # 16-bit signed
        a.frombytes(frame)
    elif width == 1:
        a = array.array("b")
        a.frombytes(frame)
    else:
        return 0
    n = len(a)
    if n == 0:
        return 0
    return int((sum(int(x) * int(x) for x in a) / n) ** 0.5)


def _import_stt_deps():
    """延迟导入 dashscope ASR 相关依赖。未装时抛 ImportError。"""
    import dashscope
    from dashscope.audio.asr import (
        Recognition,
        RecognitionCallback,
        RecognitionResult,
    )
    return dashscope, Recognition, RecognitionCallback, RecognitionResult


def _import_pyaudio():
    """延迟导入 pyaudio。"""
    import pyaudio
    return pyaudio


# 录音参数（Paraformer 实时识别要求 16kHz 单声道 16-bit PCM）
_PCM_RATE = 16000
_PCM_CHANNELS = 1
_PCM_WIDTH = 2  # 16-bit = 2 bytes
_FRAMES_PER_BUFFER = 3200  # 200ms @ 16kHz 单声道 16bit（6400 bytes/帧）

# 静音检测默认参数
_SILENCE_THRESHOLD = 500
# Ctrl+C 阻塞机制：pyaudio stream.read() 在 Windows 上无法被 Python 信号中断。
# stop_flag 用于非阻塞轮询（stream.read(..., exception_on_overflow=False) 本身不阻塞）。
# 当主线程调用 stop() 时，置位 stop_flag → 录音循环结束 → 清理资源。
_stop_flag = threading.Event()


def _is_stopped() -> bool:
    """检查是否通过信号收到停止请求。"""
    return _stop_flag.is_set()


def _request_stop() -> None:
    """请求停止当前正在阻塞的 listen()。用于 Ctrl+C 信号处理器。"""
    _stop_flag.set()


def _reset_stop() -> None:
    """重置停止标志（每次 listen() 前调用）。"""
    _stop_flag.clear()  # RMS 阈值，低于此值视为静音（16-bit PCM 量级）
_SILENCE_SECONDS = 1.5  # 连续静音多少秒视为"说完了"
_MAX_SECONDS = 15  # 单次录音最长秒数（防卡死）

# QwenASR 服务端 VAD 能量阈值（0.0~1.0）。
# 0.0 最灵敏——背景噪音 / 扬声器回声尾音都会被判定为"开始说话"，
# 服务端再把噪声转写成含混的"嗯"等文本，造成"没说话却被识别到"的误触发。
# 调高可显著减少误触发；过高会漏掉轻声说话。0.4 是噪音抑制与灵敏度的折中。
_VAD_THRESHOLD = 0.4
# 开启录音后前 N 秒音频"只读不送"（扬声器回声尾音 / 环境噪音通常集中于此窗口），
# 避免把上一轮 TTS 刚播完的尾音当成用户开口。
_VAD_LEAD_IN_SECONDS = 0.4


class _RecognitionCallback:
    """Paraformer 回调实现：收集识别结果，协调线程同步。

    dashscope SDK 的 RecognitionCallback 通过鸭子类型调用（on_open/on_event 等），
    这里不继承基类（基类在 _import_stt_deps 里延迟导入，顶层不可见），
    与 tts.py 的 _PlaybackCallback 保持一致的风格。
    """

    def __init__(self) -> None:
        self._opened = threading.Event()
        self._completed = threading.Event()
        self._error: str | None = None
        self._texts: list[str] = []  # 句子级最终文本（is_sentence_end 时追加）
        self._last_partial: str = ""  # 最近一次中间结果（未结束句）

    # ---- RecognitionCallback 实现（鸭子类型）----

    def on_open(self) -> None:
        """WebSocket 连接建立。通知主线程可以开始送音频了。"""
        self._opened.set()

    def on_event(self, result) -> None:
        """收到识别结果。中间结果 or 句子结束的最终结果。

        注意 dashscope SDK 的 API:
        - get_sentence() 返回 dict（单句）或 list（多句）或 None
        - is_sentence_end(sentence) 是**静态方法**，需传入 sentence 参数，
          不是实例方法（早期版本是实例方法，新版本改静态了，这里按新版本正确用法）
        """
        try:
            sentence = result.get_sentence()
        except Exception:
            return
        if not sentence:
            return
        # 统一成 list 处理（get_sentence 可能返回 dict 或 list）
        sentences = sentence if isinstance(sentence, list) else [sentence]
        for s in sentences:
            if not isinstance(s, dict):
                continue
            text = s.get("text", "")
            if result.is_sentence_end(s):
                # 句子结束，固化为最终文本
                if text:
                    self._texts.append(text)
                self._last_partial = ""
            else:
                # 中间结果（还在说这句），暂存供 UI 实时显示
                self._last_partial = text

    def on_complete(self) -> None:
        """识别完成。通知主线程。"""
        self._completed.set()

    def on_error(self, result) -> None:
        """识别异常。记录错误并通知主线程。"""
        try:
            self._error = result.get("message", "未知识别错误")
        except Exception:
            self._error = "识别错误"
        self._opened.set()  # 避免主线程死等 on_open
        self._completed.set()

    def on_close(self) -> None:
        """WebSocket 连接关闭。"""
        self._completed.set()

    # ---- 外部读取 ----

    def wait_opened(self, timeout: float = 10.0) -> bool:
        return self._opened.wait(timeout=timeout)

    def wait_completed(self, timeout: float = 30.0) -> bool:
        return self._completed.wait(timeout=timeout)

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def final_text(self) -> str:
        """完整识别文本 = 所有结束句拼接 + 未结束的中间句。"""
        parts = list(self._texts)
        if self._last_partial:
            parts.append(self._last_partial)
        return "".join(parts)

    @property
    def partial_text(self) -> str:
        """最近一次中间结果（供 UI 实时回显）。"""
        return self._last_partial


class ParaformerSTT:
    """语音识别器。封装阿里 Paraformer 实时 ASR，提供录音→文字接口。

    用法::

        stt = ParaformerSTT()
        result = stt.listen()  # 阻塞：录音→静音停止→识别
        print(result["text"])  # "你好贾维斯"

    识别本地音频文件::

        result = stt.transcribe_file("speech.wav")
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "paraformer-realtime-v2",
    ) -> None:
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self._model = model

    # ---- 实时录音识别（/listen 命令）----

    def listen(
        self,
        *,
        max_seconds: float = _MAX_SECONDS,
        silence_seconds: float = _SILENCE_SECONDS,
        silence_threshold: int = _SILENCE_THRESHOLD,
        on_partial: Any = None,
        on_open: Any = None,
    ) -> dict[str, Any]:
        """录一段话并识别成文字。阻塞直到识别完成。

        流程:
        1. 创建 Recognition（带 callback）+ start() 建 WebSocket
        2. on_open 后，主线程用 pyaudio 开录音流
        3. 循环: 录一帧 → send_audio_frame → 计算 RMS 做静音检测
        4. 连续静音超过 silence_seconds 或达 max_seconds → stop()
        5. 等 on_complete → 返回 {text, ...}

        Args:
            max_seconds: 单次录音最长秒数（防卡死），默认 15。
            silence_seconds: 连续静音多少秒视为说完，默认 1.5。
            silence_threshold: RMS 静音阈值，默认 500。
            on_partial: 收到中间结果时的回调（参数为中间文本，供 UI 实时回显）。
            on_open: 连接建立/开始录音时的回调。

        Returns:
            {text, duration, error?}
        """
        _reset_stop()  # 每轮 listen() 重置停止标志
        callback = _RecognitionCallback()
        recognizer = self._create_recognizer(callback)
        if recognizer is None:
            return {"text": "", "duration": 0.0, "error": callback.error or "创建识别器失败"}

        # 启动识别会话（建 WebSocket，非阻塞，on_open 在回调线程触发）
        try:
            recognizer.start()
        except Exception as e:
            return {"text": "", "duration": 0.0, "error": f"启动识别失败: {type(e).__name__}: {e}"}

        # 等连接建立
        if not callback.wait_opened(timeout=10.0):
            try:
                recognizer.stop()
            except Exception:
                pass
            return {"text": "", "duration": 0.0, "error": callback.error or "连接识别服务超时"}

        if callback.error:
            return {"text": "", "duration": 0.0, "error": callback.error}
        if on_open:
            try:
                on_open()
            except Exception:
                pass

        # ---- 录音循环 ----
        from agent.voice.audio import get_pyaudio
        pa = get_pyaudio()
        stream = pa.open(
            format=pa.get_format_from_width(2),
            channels=_PCM_CHANNELS,
            rate=_PCM_RATE,
            input=True,
            frames_per_buffer=_FRAMES_PER_BUFFER,
        )

        t0 = time.time()
        silence_start: float | None = None
        aborted = False
        try:
            while not _stop_flag.is_set():
                elapsed = time.time() - t0
                if elapsed >= max_seconds:
                    break
                if callback.error:
                    aborted = True
                    break

                try:
                    frame = stream.read(_FRAMES_PER_BUFFER, exception_on_overflow=False)
                except Exception:
                    break

                # 送识别
                try:
                    recognizer.send_audio_frame(frame)
                except Exception:
                    break

                # 中间结果回调
                if on_partial and callback.partial_text:
                    try:
                        on_partial(callback.partial_text)
                    except Exception:
                        pass

                # 静音检测
                rms = _rms(frame, _PCM_WIDTH)
                if rms < silence_threshold:
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start >= silence_seconds:
                        break  # 说完了
                else:
                    silence_start = None
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

        duration = round(time.time() - t0, 2)

        # 结束识别，等最终结果
        try:
            recognizer.stop()
        except Exception:
            pass
        callback.wait_completed(timeout=10.0)

        result: dict[str, Any] = {"text": callback.final_text, "duration": duration}
        if callback.error:
            result["error"] = callback.error
        return result

    # ---- 音频文件识别（备选）----

    def transcribe_file(self, path: str) -> dict[str, Any]:
        """识别本地音频文件（一次性，非流式）。

        用 SDK 的 call() 直接识别整个文件。
        适合已录好的 wav 文件，不适合实时场景。

        Returns:
            {text, request_id?, error?}
        """
        if not os.path.isfile(path):
            return {"text": "", "error": f"文件不存在: {path}"}

        callback = _RecognitionCallback()
        recognizer = self._create_recognizer(callback)
        if recognizer is None:
            return {"text": "", "error": callback.error or "创建识别器失败"}

        try:
            result = recognizer.call(file=path)
            text = ""
            try:
                text = result.get_sentence().get("text", "") if result else ""
            except Exception:
                pass
            return {"text": text, "request_id": recognizer.get_last_request_id()}
        except Exception as e:
            return {"text": "", "error": f"文件识别失败: {type(e).__name__}: {e}"}

    # ---- 内部 ----

    def _create_recognizer(self, callback: _RecognitionCallback) -> Any:
        """创建 Recognition 实例。失败时设置 callback.error 并返回 None。"""
        try:
            dashscope, Recognition, RecognitionCallback, RecognitionResult = _import_stt_deps()
        except ImportError as e:
            callback._error = f"dashscope 未安装: {e}"
            return None

        if not self._api_key:
            callback._error = "DASHSCOPE_API_KEY 未配置"
            return None

        dashscope.api_key = self._api_key
        dashscope.base_websocket_api_url = (
            "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
        )

        try:
            recognizer = Recognition(
                model=self._model,
                callback=callback,
                format="pcm",
                sample_rate=_PCM_RATE,
            )
            return recognizer
        except Exception as e:
            callback._error = f"创建 Recognition 失败: {type(e).__name__}: {e}"
            return None

    @property
    def error(self) -> str | None:
        return None

    @property
    def model(self) -> str:
        return self._model


# ============================================================================
# Qwen3-ASR 后端（qwen3-asr-flash-realtime）
# 用 OmniRealtimeConversation API（/realtime 端点），与服务端 VAD，比 Paraformer 更准。
# 适配中英混合场景，识别质量 SOTA。
# ============================================================================


def _import_qwen_omni():
    """延迟导入 Qwen Omni Realtime 相关依赖。"""
    import dashscope
    from dashscope.audio.qwen_omni import OmniRealtimeConversation, OmniRealtimeCallback
    from dashscope.audio.qwen_omni.omni_realtime import (
        TranscriptionParams,
        MultiModality,
    )
    return dashscope, OmniRealtimeConversation, OmniRealtimeCallback, TranscriptionParams, MultiModality


# Qwen3-ASR 实时识别 WebSocket 端点（注意与 Paraformer 的 /inference 不同）
_QWEN_REALTIME_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


class _QwenASRCallback:
    """Qwen3-ASR 回调实现：基于 OmniRealtimeCallback 鸭子类型。

    与 Paraformer 回调不同，这里 on_event 收到的是 dict（服务端事件），
    需按 message['type'] 区分事件:
    - session.created / session.updated: 会话生命周期
    - conversation.item.input_audio_transcription.completed: 最终文本（transcript）
    - conversation.item.input_audio_transcription.text: 中间结果（stash）
    - input_audio_buffer.speech_started / speech_stopped: 服务端 VAD 事件
    - session.finished: 会话结束
    """

    def __init__(self) -> None:
        self._opened = threading.Event()
        self._session_updated = threading.Event()
        self._finished = threading.Event()
        self._error: str | None = None
        self._final_texts: list[str] = []
        self._last_partial: str = ""

    # ---- OmniRealtimeCallback 实现（鸭子类型）----

    def on_open(self) -> None:
        """WebSocket 连接建立。"""
        self._opened.set()

    def on_event(self, message) -> None:
        """收到服务端事件。message 是 dict（含 type 字段）。"""
        if isinstance(message, str):
            import json
            try:
                message = json.loads(message)
            except Exception:
                return
        if not isinstance(message, dict):
            return
        evt_type = message.get("type", "")

        if evt_type == "session.created":
            # 会话已创建（connect 后）
            pass
        elif evt_type == "session.updated":
            # 配置已更新（update_session 后），通知主线程可以送音频了
            self._session_updated.set()
        elif evt_type == "conversation.item.input_audio_transcription.completed":
            # 最终文本
            transcript = message.get("transcript", "")
            if transcript:
                self._final_texts.append(transcript)
            self._last_partial = ""
        elif evt_type == "conversation.item.input_audio_transcription.text":
            # 中间结果（stash）
            self._last_partial = message.get("stash", "")
        elif evt_type == "session.finished":
            # 会话结束（end_session 后服务端完成）
            self._finished.set()
        elif evt_type == "error":
            # 错误事件
            err = message.get("error", {})
            self._error = err.get("message", "未知识别错误") if isinstance(err, dict) else "识别错误"
            self._finished.set()

    def on_close(self, close_status_code, close_msg) -> None:
        """WebSocket 连接关闭。"""
        self._finished.set()

    # ---- 外部读取 ----

    def wait_opened(self, timeout: float = 10.0) -> bool:
        return self._opened.wait(timeout=timeout)

    def wait_session_updated(self, timeout: float = 10.0) -> bool:
        return self._session_updated.wait(timeout=timeout)

    def wait_finished(self, timeout: float = 30.0) -> bool:
        return self._finished.wait(timeout=timeout)

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def final_text(self) -> str:
        """完整识别文本 = 所有最终文本拼接。"""
        return "".join(self._final_texts)

    @property
    def partial_text(self) -> str:
        """最近一次中间结果。"""
        return self._last_partial


class QwenASR:
    """Qwen3-ASR 语音识别器。基于 OmniRealtimeConversation，服务端 VAD。

    与 ParaformerSTT 的关键差异:
    - 用 /realtime 端点 + OmniRealtimeConversation（非 /inference + Recognition）
    - 音频需 base64 编码后 append_audio（非 send_audio_frame raw bytes）
    - **服务端 VAD**: 服务端自动检测说话开始/结束，客户端不用手写 RMS 静音检测，
      比客户端检测准得多。客户端只管送音频，靠 max_seconds 兜底 + session.finished 退出。
    - 识别质量更高，尤其中英混合场景。

    用法::

        stt = QwenASR()
        result = stt.listen()  # 阻塞：录音→服务端 VAD 停止→识别
        print(result["text"])
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "qwen3-asr-flash-realtime",
        language: str = "zh",
    ) -> None:
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self._model = model
        self._language = language

    def listen(
        self,
        *,
        max_seconds: float = _MAX_SECONDS,
        silence_seconds: float = _SILENCE_SECONDS,  # QwenASR 用服务端 VAD，此参数仅用于 end_session 时机参考
        silence_threshold: int = _SILENCE_THRESHOLD,  # 未使用（服务端 VAD），保留接口兼容
        on_partial: Any = None,
        on_open: Any = None,
    ) -> dict[str, Any]:
        """录一段话并识别成文字。阻塞直到识别完成。

        流程:
        1. OmniRealtimeConversation(model, callback, url, api_key)
        2. connect() → 等 on_open
        3. update_session(output_modalities=[TEXT], enable_turn_detection=True,
           silence_duration_ms, transcription_params) → 等 session.updated
        4. pyaudio 录音循环: 录一帧 → base64 → append_audio → 检查完成/超时/错误
        5. end_session() → 等 session.finished
        6. close()

        服务端 VAD 自动断句，silence_seconds 映射为 turn_detection_silence_duration_ms。
        """
        import base64

        callback = _QwenASRCallback()
        conv = self._create_conversation(callback)
        if conv is None:
            return {"text": "", "duration": 0.0, "error": callback.error or "创建识别器失败"}

        # 1. 建连接
        try:
            conv.connect()
        except Exception as e:
            return {"text": "", "duration": 0.0, "error": f"连接失败: {type(e).__name__}: {e}"}

        if not callback.wait_opened(timeout=10.0):
            try:
                conv.close()
            except Exception:
                pass
            return {"text": "", "duration": 0.0, "error": callback.error or "连接超时"}
        if callback.error:
            return {"text": "", "duration": 0.0, "error": callback.error}

        # 2. 配置会话（开启服务端 VAD + ASR 转写）
        try:
            dashscope, _, _, TranscriptionParams, MultiModality = _import_qwen_omni()
            conv.update_session(
                output_modalities=[MultiModality.TEXT],
                enable_turn_detection=True,
                turn_detection_type="server_vad",
                # 能量阈值过低会把噪音/回声当开口（误识别出"嗯"），用 _VAD_THRESHOLD 抑制
                turn_detection_threshold=_VAD_THRESHOLD,
                turn_detection_silence_duration_ms=int(silence_seconds * 1000),
                enable_input_audio_transcription=True,
                transcription_params=TranscriptionParams(
                    language=self._language,
                    sample_rate=_PCM_RATE,
                    input_audio_format="pcm",
                ),
            )
        except Exception as e:
            try:
                conv.close()
            except Exception:
                pass
            return {"text": "", "duration": 0.0, "error": f"配置会话失败: {type(e).__name__}: {e}"}

        if not callback.wait_session_updated(timeout=10.0):
            try:
                conv.close()
            except Exception:
                pass
            return {"text": "", "duration": 0.0, "error": callback.error or "配置会话超时"}
        if callback.error:
            return {"text": "", "duration": 0.0, "error": callback.error}
        if on_open:
            try:
                on_open()
            except Exception:
                pass

        # 3. 录音循环
        try:
            pyaudio = _import_pyaudio()
        except ImportError as e:
            try:
                conv.close()
            except Exception:
                pass
            return {"text": "", "duration": 0.0, "error": f"pyaudio 未安装: {e}"}

        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=_PCM_CHANNELS,
            rate=_PCM_RATE,
            input=True,
            frames_per_buffer=_FRAMES_PER_BUFFER,
        )

        t0 = time.time()
        # 首段音频（TTS 回声尾音/环境噪音）只读不送，避免被服务端误判为开口
        lead_in_until = t0 + _VAD_LEAD_IN_SECONDS
        try:
            while not _stop_flag.is_set():
                elapsed = time.time() - t0
                if elapsed >= max_seconds:
                    break
                if callback.error:
                    break
                # 服务端 VAD 检测到说完一句话并返回最终文本后，
                # 多数语音助手场景即视为结束。这里靠 max_seconds 兜底，
                # 同时若已拿到最终文本且无新中间结果，也提前结束。
                if callback.final_text and not callback.partial_text:
                    break

                try:
                    frame = stream.read(_FRAMES_PER_BUFFER, exception_on_overflow=False)
                except Exception:
                    break

                # lead-in 窗口内丢弃（不送服务端），规避上一轮 TTS 尾音
                if time.time() < lead_in_until:
                    continue

                # base64 编码后送音频（Qwen3-ASR 要求 base64，与 Paraformer 的 raw bytes 不同）
                try:
                    audio_b64 = base64.b64encode(frame).decode("ascii")
                    conv.append_audio(audio_b64)
                except Exception:
                    break

                if on_partial and callback.partial_text:
                    try:
                        on_partial(callback.partial_text)
                    except Exception:
                        pass
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

        duration = round(time.time() - t0, 2)

        # 4. 结束会话，等最终结果
        try:
            conv.end_session(timeout=10)
        except Exception:
            pass
        callback.wait_finished(timeout=10.0)
        try:
            conv.close()
        except Exception:
            pass

        result: dict[str, Any] = {"text": callback.final_text, "duration": duration}
        if callback.error:
            result["error"] = callback.error
        return result

    def transcribe_file(self, path: str) -> dict[str, Any]:
        """识别本地音频文件（流式送完整文件）。"""
        import base64
        if not os.path.isfile(path):
            return {"text": "", "error": f"文件不存在: {path}"}

        callback = _QwenASRCallback()
        conv = self._create_conversation(callback)
        if conv is None:
            return {"text": "", "error": callback.error or "创建识别器失败"}

        try:
            conv.connect()
            if not callback.wait_opened(timeout=10.0):
                return {"text": "", "error": "连接超时"}
            dashscope, _, _, TranscriptionParams, MultiModality = _import_qwen_omni()
            conv.update_session(
                output_modalities=[MultiModality.TEXT],
                enable_turn_detection=True,
                turn_detection_silence_duration_ms=int(_SILENCE_SECONDS * 1000),
                enable_input_audio_transcription=True,
                transcription_params=TranscriptionParams(
                    language=self._language,
                    sample_rate=_PCM_RATE,
                    input_audio_format="pcm",
                ),
            )
            if not callback.wait_session_updated(timeout=10.0):
                return {"text": "", "error": "配置会话超时"}

            # 读文件分块送
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(_FRAMES_PER_BUFFER * _PCM_WIDTH)
                    if not chunk:
                        break
                    conv.append_audio(base64.b64encode(chunk).decode("ascii"))

            conv.end_session(timeout=20)
            callback.wait_finished(timeout=20.0)
            return {"text": callback.final_text}
        except Exception as e:
            return {"text": "", "error": f"文件识别失败: {type(e).__name__}: {e}"}
        finally:
            try:
                conv.close()
            except Exception:
                pass

    # ---- 内部 ----

    def _create_conversation(self, callback: _QwenASRCallback) -> Any:
        """创建 OmniRealtimeConversation 实例。"""
        try:
            dashscope, OmniRealtimeConversation, _, _, _ = _import_qwen_omni()
        except ImportError as e:
            callback._error = f"dashscope 未安装: {e}"
            return None

        if not self._api_key:
            callback._error = "DASHSCOPE_API_KEY 未配置"
            return None

        dashscope.api_key = self._api_key
        try:
            conv = OmniRealtimeConversation(
                model=self._model,
                callback=callback,
                url=_QWEN_REALTIME_URL,
                api_key=self._api_key,
            )
            return conv
        except Exception as e:
            callback._error = f"创建会话失败: {type(e).__name__}: {e}"
            return None

    @property
    def error(self) -> str | None:
        return None

    @property
    def model(self) -> str:
        return self._model


# ============================================================================
# 工厂函数：根据 model 名自动选择 STT 后端
# ============================================================================


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


# ============================================================================
# FunASRFlashSTT —— 文件上传式语音识别
# ============================================================================


class FunASRFlashSTT:
    """FunASR Flash 语音识别器。

    基于 DashScope fun-asr-flash-2026-06-15，
    通过 HTTP POST 上传音频文件（WAV base64）获取转写文字。
    客户端做 RMS 静音检测，检测到静音后发送音频数据到服务端。

    用法::

        stt = FunASRFlashSTT(model="fun-asr-flash-2026-06-15")
        result = stt.listen()
        print(result["text"])  # "你好贾维斯"
    """

    API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "fun-asr-flash-2026-06-15",
    ) -> None:
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def listen(
        self,
        *,
        max_seconds: float = _MAX_SECONDS,
        silence_seconds: float = _SILENCE_SECONDS,
        silence_threshold: int = _SILENCE_THRESHOLD,
        on_partial: Any = None,
        on_open: Any = None,
    ) -> dict[str, Any]:
        """录音并识别。阻塞直到识别完成。

        流程分为两段，避免用户还没开口就送空音频给 API：
        1. 预录音等待：最多等待 ``pre_recording_seconds``，直到检测到用户声音；
        2. 正式录音：检测到声音后，按静音阈值结束，再整段送给 FunASR Flash。
        若整段都没有有效语音，直接返回空文本，不会触发 ``ASR_RESPONSE_HAVE_NO_WORDS``。
        """
        _reset_stop()

        from agent.voice.audio import get_pyaudio
        pa = get_pyaudio()
        stream = pa.open(
            format=pa.get_format_from_width(2),
            channels=_PCM_CHANNELS,
            rate=_PCM_RATE,
            input=True,
            frames_per_buffer=_FRAMES_PER_BUFFER,
        )

        if on_open:
            try:
                on_open()
            except Exception:
                pass

        frames: list[bytes] = []
        t0 = time.time()
        aborted = False
        # 预录音等待：给用户一点时间开口，避免还没说话就进入静音检测
        pre_recording_seconds = min(5.0, max_seconds)
        voice_started = False

        try:
            # ---- 阶段 1：等待用户开始说话 ----
            while not _stop_flag.is_set():
                elapsed = time.time() - t0
                if elapsed >= pre_recording_seconds:
                    break

                try:
                    frame = stream.read(_FRAMES_PER_BUFFER, False)
                except Exception:
                    aborted = True
                    break

                frames.append(frame)
                rms = _rms(frame)
                if rms >= silence_threshold:
                    voice_started = True
                    break

                if on_partial:
                    try:
                        on_partial(f"[聆听中 {elapsed:.1f}s]")
                    except Exception:
                        pass

            # ---- 阶段 2：检测到声音后继续录音，直到静音结束或超时 ----
            if voice_started and not _stop_flag.is_set() and not aborted:
                silence_start: float | None = None
                while not _stop_flag.is_set():
                    elapsed = time.time() - t0
                    if elapsed >= max_seconds:
                        break

                    try:
                        frame = stream.read(_FRAMES_PER_BUFFER, False)
                    except Exception:
                        break

                    frames.append(frame)
                    rms = _rms(frame)
                    if rms < silence_threshold:
                        if silence_start is None:
                            silence_start = time.time()
                        elif time.time() - silence_start >= silence_seconds:
                            break
                    else:
                        silence_start = None

                    if on_partial:
                        try:
                            on_partial(f"[录音中 {elapsed:.1f}s]")
                        except Exception:
                            pass

        except KeyboardInterrupt:
            aborted = True
        finally:
            stream.stop_stream()
            stream.close()

        duration = time.time() - t0

        if aborted:
            return {"text": "", "duration": duration, "error": "用户取消"}

        if not voice_started or not frames:
            return {"text": "", "duration": duration, "error": "未检测到语音"}

        # 将 PCM 帧转为 WAV 格式
        raw_pcm = b"".join(frames)
        wav_bytes = _pcm_to_wav(raw_pcm, _PCM_RATE, _PCM_CHANNELS, 16)

        # 转为 base64 data URI
        b64 = base64.b64encode(wav_bytes).decode()
        data_uri = f"data:audio/wav;base64,{b64}"

        # 发送到 FunASR Flash API
        try:
            text = self._call_api(data_uri)
            return {"text": text, "duration": duration}
        except Exception as e:
            return {"text": "", "duration": duration, "error": f"识别失败: {e}"}

    def _call_api(self, audio_data_uri: str) -> str:
        """调 HTTP POST，同步模式下获取转写文字。"""
        import urllib.request
        import urllib.error

        payload = json.dumps({
            "model": self._model,
            "input": {
                "messages": [{
                    "role": "user",
                    "content": [{
                        "type": "input_audio",
                        "input_audio": {
                            "data": audio_data_uri,
                        },
                    }],
                }],
            },
            "parameters": {
                "format": "wav",
                "sample_rate": str(_PCM_RATE),
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            self.API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError(f"API {e.code}: {body[:200]}") from e

        # 解析：output.output.sentence.text 或 output.text
        output = result.get("output", {})
        if isinstance(output, dict):
            inner = output.get("output", {})
            if isinstance(inner, dict):
                sentence = inner.get("sentence", {})
                if isinstance(sentence, dict) and sentence.get("text"):
                    return sentence["text"]
            text = output.get("text", "")
            if text:
                return text

        raise RuntimeError(f"无法解析识别结果: {json.dumps(result, ensure_ascii=False)[:200]}")


# ---- 工具函数 ----


def _pcm_to_wav(pcm_data: bytes, sample_rate: int, channels: int, bits: int) -> bytes:
    """将原始 PCM 数据封装为 WAV 格式（44 字节头 + PCM 数据）。"""
    import struct
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(pcm_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,          # chunk size
        1,           # PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        data_size,
    )
    return header + pcm_data
