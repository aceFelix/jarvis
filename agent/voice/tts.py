"""语音合成（TTS）—— 让 agent "开口说话"。

阶段三第一刀。基于阿里 CosyVoice（DashScope），用 pyaudio 实时播放。

核心能力:
1. **speak()**: 非流式整段合成播放。适合短文本、/say 命令验证。
2. **stream_speak()**: 流式合成播放。配合 LLM 文本流（第三刀），
   边生成边合成边播放，实现"贾维斯式"低延迟语音。

设计要点:
- **回调驱动**: CosyVoice SDK 用 ResultCallback 回调推送音频数据（on_data），
  我们在回调里用 pyaudio 实时写扬声器，实现"收到一块播一块"。
- **线程模型**: SDK 的回调在子线程触发，pyaudio 播放也在该线程。
  speak()/stream_speak() 阻塞主线程直到合成完成（streaming_complete 阻塞）。
- **中断支持**: 提供 stop() 方法，设置中断标志，回调里检查后停止播放。
  （第三刀打断功能的基础。）

依赖: pip install dashscope pyaudio
API: DashScope WebSocket（wss://dashscope.aliyuncs.com/api-ws/v1/inference）
key: DASHSCOPE_API_KEY 环境变量（复用已有配置）
"""

from __future__ import annotations

import os
import threading
from typing import Any, Iterator

from agent.core.result import ToolResult


def _import_tts_deps():
    """延迟导入 dashscope TTS 相关依赖。未装时抛 ImportError。"""
    import dashscope
    from dashscope.audio.tts_v2 import (
        AudioFormat,
        ResultCallback,
        SpeechSynthesizer,
    )
    return dashscope, AudioFormat, ResultCallback, SpeechSynthesizer


def _import_pyaudio():
    """延迟导入 pyaudio。"""
    import pyaudio
    return pyaudio


# PCM 播放参数（与 AudioFormat.PCM_22050HZ_MONO_16BIT 对应）
_PCM_RATE = 22050
_PCM_CHANNELS = 1
_PCM_WIDTH = 2  # 16-bit = 2 bytes


class _PlaybackCallback:
    """CosyVoice 回调实现：收到音频数据用 pyaudio 实时播放。

    dashscope SDK 的 ResultCallback 是抽象基类，子类需实现 6 个方法。
    音频数据通过 on_data 回调分块推送，这里直接写 pyaudio 流实现实时播放。
    """

    def __init__(self, *, on_first_data=None) -> None:
        self._stream = None
        self._stop_flag = threading.Event()
        self._on_first_data = on_first_data
        self._first_data_fired = False
        self._error: str | None = None
        self._data_chunks: int = 0
        self._total_bytes: int = 0
        self._closed = threading.Event()       # WS closed (normal or RST)
        self._completed = threading.Event()    # server confirmed synthesis complete

    # ---- ResultCallback 实现 ----

    def on_open(self) -> None:
        """WebSocket 连接建立。初始化 pyaudio 播放流。"""
        try:
            from agent.voice.audio import get_pyaudio
            pa = get_pyaudio()
        except ImportError as e:
            self._error = f"pyaudio 未安装: {e}"
            return
        self._stream = pa.open(
            format=pa.get_format_from_width(2),  # paInt16
            channels=_PCM_CHANNELS,
            rate=_PCM_RATE,
            output=True,
        )

    def on_event(self, message: str) -> None:
        """合成事件回调（句子开始/合成中/结束）。当前仅记录，不处理。"""
        # 可扩展: 用于显示"正在说第几句"等 UI 反馈
        pass

    def on_data(self, data: bytes) -> None:
        """收到一块音频数据。用 pyaudio 实时播放。"""
        if self._stop_flag.is_set() or self._stream is None:
            return
        if not self._first_data_fired:
            self._first_data_fired = True
            if self._on_first_data:
                try:
                    self._on_first_data()
                except Exception:
                    pass
        self._stream.write(data)
        self._data_chunks += 1
        self._total_bytes += len(data)

    def on_complete(self) -> None:
        """所有文本合成完成且音频全部返回。"""
        self._completed.set()

    def on_error(self, message: str) -> None:
        """合成异常。记录错误，连接将自动关闭。"""
        self._error = message

    def on_close(self) -> None:
        """WebSocket 连接关闭。释放播放流（不 terminate PyAudio——全局单例）。"""
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._closed.set()

    # ---- 外部控制 ----

    def stop(self) -> None:
        """请求中断播放。回调里检查后停止写入。"""
        self._stop_flag.set()

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def is_closed(self) -> bool:
        return self._closed.is_set()

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "data_chunks": self._data_chunks,
            "total_bytes": self._total_bytes,
            "total_seconds": round(self._total_bytes / (_PCM_RATE * _PCM_CHANNELS * _PCM_WIDTH), 2),
        }


class CosyVoiceTTS:
    """语音合成器。封装阿里 CosyVoice，提供同步播放接口。

    用法::

        tts = CosyVoiceTTS(voice="longanlang_v3")
        tts.speak("你好，我是贾维斯。")  # 阻塞播放，播完返回

    流式（配合 LLM 文本流，第三刀用）::

        async for chunk in llm_stream():
            tts.feed(chunk)  # 分片喂文本
        tts.finish()  # 结束，阻塞到合成播完
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "cosyvoice-v3-flash",
        voice: str = "longanlang_v3",
        volume: int = 50,
        speech_rate: float = 1.0,
        pitch_rate: float = 1.0,
    ) -> None:
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self._model = model
        self._voice = voice
        self._volume = volume
        self._speech_rate = speech_rate
        self._pitch_rate = pitch_rate
        self._callback: _PlaybackCallback | None = None
        self._synthesizer: Any = None

    # ---- 同步整段播放（/say 命令、短文本）----

    def speak(
        self,
        text: str,
        *,
        on_first_data=None,
    ) -> dict[str, Any]:
        """合成并播放一段文本。阻塞直到播放结束。

        Args:
            text: 要朗读的文本（≤20000 字符）。
            on_first_data: 收到首块音频时的回调（可用于 UI 显示"开始说话"）。

        Returns:
            统计字典: {data_chunks, total_bytes, total_seconds, first_package_delay_ms, error?}

        Note:
            CosyVoice SDK 设置 callback 时 call() 不阻塞（走流式模式），
            所以这里用 streaming_call + streaming_complete 实现整段阻塞播放。
            streaming_complete() 阻塞直到所有文本合成播放完毕。
        """
        if not text.strip():
            return {"error": "文本为空", "data_chunks": 0, "total_bytes": 0, "total_seconds": 0}

        callback = _PlaybackCallback(on_first_data=on_first_data)
        self._callback = callback
        synth = self._create_synthesizer(callback)
        if synth is None:
            return {**callback.stats, "error": callback.error or "创建合成器失败"}

        try:
            synth.streaming_call(text)
            # streaming_complete() 可能因 WS RST 永久阻塞。
            # 双信号并发等待：_completed（正常完成）或 _closed（WS异常关闭）
            # 任一触发即解除阻塞，最多等 30 秒。
            import threading as _th
            sc_error: list[str] = []
            def _do_complete():
                try:
                    synth.streaming_complete()
                except Exception as e:
                    sc_error.append(str(e))
            t = _th.Thread(target=_do_complete, daemon=True)
            t.start()

            # 并发等两个事件，谁先到谁解除
            done = _th.Event()
            def _wait_evt(evt):
                evt.wait()
                done.set()
            _th.Thread(target=_wait_evt, args=(callback._completed,), daemon=True).start()
            _th.Thread(target=_wait_evt, args=(callback._closed,), daemon=True).start()

            ok = done.wait(timeout=30)
            if not ok:
                sc_error.append("TTS 合成超时 30s")
            t.join(timeout=3)
        except Exception as e:
            return {**callback.stats, "error": f"合成失败: {type(e).__name__}: {e}"}

        result = {
            **callback.stats,
            "first_package_delay_ms": self._safe_get_delay(synth),
        }
        if callback.error:
            result["error"] = callback.error
        return result

    # ---- 流式播放（配合 LLM 文本流，第三刀用）----

    def start_stream(self, *, on_first_data=None) -> bool:
        """开始一次流式合成会话。后续用 feed() 分片喂文本。

        Returns: True 成功，False 失败（见 error 属性）。
        """
        callback = _PlaybackCallback(on_first_data=on_first_data)
        self._callback = callback
        synth = self._create_synthesizer(callback)
        if synth is None:
            self._last_error = callback.error or "创建合成器失败"
            return False
        self._synthesizer = synth
        self._last_error = None
        return True

    def feed(self, text: str) -> None:
        """流式喂入一段文本（LLM 吐的一个 chunk）。

        可多次调用。服务端自动分句合成。
        """
        if self._synthesizer is None or not text:
            return
        self._synthesizer.streaming_call(text)

    def finish(self) -> dict[str, Any]:
        """结束流式合成，阻塞直到所有文本合成播放完毕。

        Returns: 统计字典。
        """
        if self._synthesizer is None:
            return {"error": "未开始流式合成"}
        try:
            self._synthesizer.streaming_complete()
        except Exception as e:
            return {**self._callback.stats, "error": f"结束合成失败: {type(e).__name__}: {e}"}

        result = {
            **self._callback.stats,
            "first_package_delay_ms": self._safe_get_delay(self._synthesizer),
        }
        if self._callback.error:
            result["error"] = self._callback.error
        self._synthesizer = None
        return result

    def stop(self) -> None:
        """中断当前播放/合成。"""
        if self._callback:
            self._callback.stop()

    # ---- 内部 ----

    def _create_synthesizer(self, callback: _PlaybackCallback) -> Any:
        """创建 SpeechSynthesizer 实例。失败时设置 callback.error 并返回 None。"""
        try:
            dashscope, AudioFormat, ResultCallback, SpeechSynthesizer = _import_tts_deps()
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
            synth = SpeechSynthesizer(
                model=self._model,
                voice=self._voice,
                format=AudioFormat.PCM_22050HZ_MONO_16BIT,
                volume=self._volume,
                speech_rate=self._speech_rate,
                pitch_rate=self._pitch_rate,
                callback=callback,
            )
            return synth
        except Exception as e:
            callback._error = f"创建 SpeechSynthesizer 失败: {type(e).__name__}: {e}"
            return None

    @staticmethod
    def _safe_get_delay(synth: Any) -> int | None:
        """安全获取首包延迟（可能抛异常）。"""
        try:
            return synth.get_first_package_delay()
        except Exception:
            return None

    @property
    def error(self) -> str | None:
        return getattr(self, "_last_error", None) or (self._callback.error if self._callback else None)

    @property
    def voice(self) -> str:
        return self._voice

    @property
    def model(self) -> str:
        return self._model
