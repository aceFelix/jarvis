"""FunASR Flash 文件上传式语音识别后端（aceFelix）。

与前两个后端的根本差异：不走 WebSocket 流式识别，而是**客户端录完一整段
音频后，封装成 WAV + base64 data URI，一次性 HTTP POST** 给 DashScope 的
multimodal-generation 端点。因此没有服务端 VAD，也没法实时回显中间结果，
延迟高于 Paraformer / Qwen3-ASR，但依赖最轻（不需要 dashscope SDK）。

录音分两段：先等用户开口（避免一上来就送静音），再正式录音到静音结束，
整段送识别。仅标准库 urllib 发请求。

依赖: pip install pyaudio
"""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

from agent.voice.stt.common import (
    _FRAMES_PER_BUFFER,
    _MAX_SECONDS,
    _PCM_CHANNELS,
    _PCM_RATE,
    _SILENCE_SECONDS,
    _SILENCE_THRESHOLD,
    _is_stopped,
    _pcm_to_wav,
    _reset_stop,
    _rms,
)


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
            while not _is_stopped():
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
            if voice_started and not _is_stopped() and not aborted:
                silence_start: float | None = None
                while not _is_stopped():
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
