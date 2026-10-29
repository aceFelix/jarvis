"""Qwen3-ASR 实时语音识别后端（aceFelix）。

模型 qwen3-asr-flash-realtime，走 OmniRealtimeConversation API
（wss /realtime 端点，注意与 Paraformer 的 /inference 不同）。

与 Paraformer 的关键差异：
- **服务端 VAD**：由服务端判断说话的开始/结束，客户端不用手写 RMS 检测，
  断句准确率明显更高，中英混合场景表现更好。
- 音频要 base64 编码后 append_audio（Paraformer 是 raw bytes 的
  send_audio_frame）。

依赖: pip install dashscope pyaudio
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from agent.voice.stt.common import (
    _FRAMES_PER_BUFFER,
    _MAX_SECONDS,
    _PCM_CHANNELS,
    _PCM_RATE,
    _PCM_WIDTH,
    _SILENCE_SECONDS,
    _import_pyaudio,
    _is_stopped,
)

# QwenASR 服务端 VAD 能量阈值（0.0~1.0）。
# 0.0 最灵敏——背景噪音 / 扬声器回声尾音都会被判定为"开始说话"，
# 服务端再把噪声转写成含混的"嗯"等文本，造成"没说话却被识别到"的误触发。
# 调高可显著减少误触发；过高会漏掉轻声说话。0.4 是噪音抑制与灵敏度的折中。
_VAD_THRESHOLD = 0.4
# 开启录音后前 N 秒音频"只读不送"（扬声器回声尾音 / 环境噪音通常集中于此窗口），
# 避免把上一轮 TTS 刚播完的尾音当成用户开口。
_VAD_LEAD_IN_SECONDS = 0.4

# 配置确认窗口（秒）。connect() 之后服务端会主动下发 session.created，
# 自带一份完整的默认会话配置（modalities / input_audio_format / sample_rate /
# input_audio_transcription / turn_detection 全在），也就是说**不发 update 也已可用**。
# 而 update_session 之后的 session.updated 确认并非所有模型都会回：
# qwen3-asr-flash-realtime 实测从不返回该事件（dashscope SDK 的事件分发里也没有
# session.updated 分支，只有 session.created / session.finished / error / response.*）。
# 所以这里只在一个短窗口里等「错误事件」，没等到确认不判失败——
# 若死等 10 秒，每一轮都会以「配置会话超时」收场，从未进入录音循环。
_SESSION_CONFIRM_TIMEOUT_S = 2.0

# 服务端静默窗口（秒）。录音期间若这么久一个服务端事件都没收到（连服务端 VAD 的
# input_audio_buffer.speech_started 都没有），说明链路上根本没在识别——提前收尾并
# 如实报错，而不是让用户干等满 max_seconds（/voice 下是 300 秒）。
# 注意判据是「零事件」而非「没识别出文字」：用户说了话必然先有 VAD 事件，
# 因此正常路径不会被这个窗口误伤。
_SERVER_SILENCE_TIMEOUT_S = 10.0

# Qwen3-ASR 实时识别 WebSocket 端点（注意与 Paraformer 的 /inference 不同）
_QWEN_REALTIME_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


def _import_qwen_omni():
    """延迟导入 Qwen Omni Realtime 相关依赖。"""
    import dashscope
    from dashscope.audio.qwen_omni import OmniRealtimeConversation, OmniRealtimeCallback
    from dashscope.audio.qwen_omni.omni_realtime import (
        TranscriptionParams,
        MultiModality,
    )
    return dashscope, OmniRealtimeConversation, OmniRealtimeCallback, TranscriptionParams, MultiModality


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
        # 已收到的服务端事件总数。调用方用它区分「链路上根本没在识别」
        # （计数不动）与「在识别但没听到人说话」（计数会涨）。
        self._event_count = 0

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
        self._event_count += 1
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
    def event_count(self) -> int:
        """已收到的服务端事件总数（含 session.created）。

        录音前后对比该值即可判断链路是否真的在工作：完全不变意味着服务端
        一句响应都没给（账户额度不足 / 模型未开通 / 服务异常），
        而正常说话时至少会有 speech_started 等 VAD 事件。
        """
        return self._event_count

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

    实现要点（与已下线的 Paraformer 后端不同）:
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
        hotwords: str = "",
    ) -> None:
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self._model = model
        self._language = language
        # 热词（上下文增强）：随 session.update 一起发，让识别模型一开始就倾向于
        # 输出这些词，而不是先听成近音字。实测 qwen3-asr-flash-realtime 接受
        # input_audio_transcription.corpus.text（2026-10）。@author aceFelix
        self._hotwords = (hotwords or "").strip()

    def listen(
        self,
        *,
        max_seconds: float = _MAX_SECONDS,
        silence_seconds: float = _SILENCE_SECONDS,  # QwenASR 用服务端 VAD，此参数仅用于 end_session 时机参考
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
            params = TranscriptionParams(
                language=self._language,
                sample_rate=_PCM_RATE,
                input_audio_format="pcm",
            )
            # 热词走 corpus_text：SDK 会把它包成 input_audio_transcription.corpus
            # 的 {"text": ...} 再随 session.update 下发（见 omni_realtime.py）。
            # 留空则不设该字段，行为与改造前完全一致。@author aceFelix
            if self._hotwords:
                params.corpus_text = self._hotwords
            conv.update_session(
                output_modalities=[MultiModality.TEXT],
                enable_turn_detection=True,
                turn_detection_type="server_vad",
                # 能量阈值过低会把噪音/回声当开口（误识别出"嗯"），用 _VAD_THRESHOLD 抑制
                turn_detection_threshold=_VAD_THRESHOLD,
                turn_detection_silence_duration_ms=int(silence_seconds * 1000),
                enable_input_audio_transcription=True,
                transcription_params=params,
            )
        except Exception as e:
            try:
                conv.close()
            except Exception:
                pass
            return {"text": "", "duration": 0.0, "error": f"配置会话失败: {type(e).__name__}: {e}"}

        # 配置确认只是可选信号：服务端不一定回 session.updated（见
        # _SESSION_CONFIRM_TIMEOUT_S 注释），因此只等一个短窗口，且**不以超时作为
        # 失败判据**。真正需要提前报错的只有服务端明确下发的 error 事件。
        callback.wait_session_updated(timeout=_SESSION_CONFIRM_TIMEOUT_S)
        if callback.error:
            try:
                conv.close()
            except Exception:
                pass
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
        # 服务端静默监控基线：录音开始时的已收事件数与最近一次收到事件的时刻。
        # 两者一起用才能区分「在识别但没人说话」与「服务端完全没响应」。
        events_baseline = callback.event_count
        last_event_ts = t0
        server_silent = False
        try:
            while not _is_stopped():
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

                # 服务端静默监测：超过窗口仍一个事件都没来，说明这条链路根本没在识别。
                # 提前收尾，避免用户对着一个死连接干等满 max_seconds。
                if callback.event_count != events_baseline:
                    events_baseline = callback.event_count
                    last_event_ts = time.time()
                elif time.time() - last_event_ts >= _SERVER_SILENCE_TIMEOUT_S:
                    server_silent = True
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
        # 只在确认过链路在工作（收到过服务端事件）时才等最终结果。
        # 全程静默时服务端也不会回 session.finished，再等满 10 秒只是把失败耗时翻倍
        # （静默窗口 + 这一等 = 双倍等待，用户体感就是「卡死」）。
        if not server_silent:
            callback.wait_finished(timeout=10.0)
        try:
            conv.close()
        except Exception:
            pass

        result: dict[str, Any] = {"text": callback.final_text, "duration": duration}
        if callback.error:
            result["error"] = callback.error
        elif server_silent and not callback.final_text:
            # 录音期间服务端一个事件都没给（连 VAD 的 speech_started 都没有）。
            # 这不是「没听清」，而是链路上根本没在做识别，必须如实说明，
            # 否则会被当成偶发失败而反复重试（历史上就报成了「配置会话超时，再说一次」）。
            result["error"] = (
                "服务端无响应：录音期间未收到任何识别事件，"
                "请检查 DashScope 该模型的额度与开通状态（cosyvoice / qwen3-asr 分属单独计费）"
            )
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
            # 同 listen()：session.updated 只是可选确认，不作为失败判据
            callback.wait_session_updated(timeout=_SESSION_CONFIRM_TIMEOUT_S)
            if callback.error:
                return {"text": "", "error": callback.error}

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
