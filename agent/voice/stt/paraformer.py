"""Paraformer 实时语音识别后端（aceFelix）。

阿里 DashScope Paraformer（Recognition API，wss /inference 端点）。
特点：轻量、延迟低，**客户端自己做 RMS 静音检测**——录音帧边录边送，
主线程算每帧能量，连续静音超阈值即主动 stop()。

原属 agent/voice/stt.py 的第一段实现，与 Qwen3-ASR、FunASR Flash
两个后端彼此没有调用关系，各自独立成模块。

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
    _SILENCE_THRESHOLD,
    _is_stopped,
    _reset_stop,
    _rms,
)


def _import_stt_deps():
    """延迟导入 dashscope ASR 相关依赖。未装时抛 ImportError。"""
    import dashscope
    from dashscope.audio.asr import (
        Recognition,
        RecognitionCallback,
        RecognitionResult,
    )
    return dashscope, Recognition, RecognitionCallback, RecognitionResult


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
            while not _is_stopped():
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
