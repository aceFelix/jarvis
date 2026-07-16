"""实时双工语音对话模块 —— /talk 命令。

基于 DashScope qwen-audio-3.0-realtime-flash，
通过 WebSocket 全双工连接实现实时语音对话。

对标 OpenClaw Talk mode：
- 全双工：同时听说，无需等待
- 服务端 VAD：自动检测说话开始/结束
- 语音打断：用户开口时自动停止 AI 播报
- 实时转录：显示对话文本

用法: /talk 启动，ESC 退出
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from typing import Any

try:
    import pyaudio
except ImportError:
    pyaudio = None

try:
    import websockets
except ImportError:
    websockets = None

# ---- 默认配置（可被 settings.toml [realtime_talk] 覆盖） ----
# DashScope 实时语音/多模态公共 WebSocket 端点。
# 如需业务空间专属域名，在 settings.toml [realtime_talk] 中覆盖 ws_url。
DEFAULT_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_VOICE = "longanqian"
DEFAULT_SILENCE_MS = 500
DEFAULT_VAD_THRESHOLD = 0.5

# ---- 音频参数 ----
INPUT_RATE = 16000    # 麦克风：16kHz
OUTPUT_RATE = 24000   # 扬声器：24kHz
CHUNK_BYTES = 3200    # 每次读取 3200 字节（~100ms @ 16kHz mono 16bit）
SEND_INTERVAL = 0.02  # 发送间隔 20ms


class RealtimeTalk:
    """实时双工语音对话。"""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "qwen-audio-3.0-realtime-flash",
        voice: str = DEFAULT_VOICE,
        instructions: str = "",
        ws_url: str = DEFAULT_WS_URL,
        silence_duration_ms: int = DEFAULT_SILENCE_MS,
        vad_threshold: float = DEFAULT_VAD_THRESHOLD,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._instructions = instructions or (
            "你是贾维斯，先生的全能管家。用简洁自然的口语回复，"
            "不要输出思考过程。保持对话流畅自然。"
        )
        self._ws_url = ws_url
        self._silence_ms = silence_duration_ms
        self._vad_threshold = vad_threshold

        self._pya: Any = None
        self._mic: Any = None
        self._spk: Any = None
        self._running = False

    async def run(self, ui) -> None:
        """启动实时对话。ESC 退出。"""
        if websockets is None:
            ui.error("缺少 websockets 库，请运行: pip install websockets")
            return
        if pyaudio is None:
            ui.error("缺少 pyaudio 库，请运行: pip install pyaudio（Windows 上可能需要从 https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio 下载 whl 安装）")
            return

        # 初始化 PyAudio
        self._pya = pyaudio.PyAudio()
        try:
            self._mic = self._pya.open(
                format=pyaudio.paInt16, channels=1, rate=INPUT_RATE, input=True
            )
            self._spk = self._pya.open(
                format=pyaudio.paInt16, channels=1, rate=OUTPUT_RATE, output=True
            )
        except Exception as e:
            ui.error(f"音频设备初始化失败: {e}")
            self._cleanup()
            return

        # WebSocket URL
        url = f"{self._ws_url}?model={self._model}"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        ui.info("=" * 56)
        ui.info("🎙️  实时双工语音对话已开启")
        ui.info(f"   模型: {self._model}  ·  音色: {self._voice}")
        ui.info("   全双工模式 · 说话即可打断 AI · ESC 退出")
        ui.info("=" * 56)

        try:
            async with websockets.connect(url, additional_headers=headers) as ws:
                # 发送 session.update
                await ws.send(json.dumps({
                    "type": "session.update",
                    "session": {
                        "modalities": ["text", "audio"],
                        "voice": self._voice,
                        "instructions": self._instructions,
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": self._vad_threshold,
                            "silence_duration_ms": self._silence_ms,
                        },
                    },
                }))

                self._running = True

                # 并发：发音频 + 收事件 + ESC 监听
                await asyncio.gather(
                    self._send_audio(ws),
                    self._recv_events(ws, ui),
                    self._esc_watcher(ui),
                )

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            msg = str(e)
            if "401" in msg or "403" in msg:
                ui.error(
                    "实时对话鉴权失败（HTTP 401/403）。"
                    "请检查：1) DASHSCOPE_API_KEY 是否配置且有效；"
                    "2) 是否已开通 DashScope 实时语音/多模态服务；"
                    "3) [realtime_talk] 中的 ws_url 是否与你的业务空间一致。"
                )
            else:
                ui.error(f"实时对话异常: {e}")
        finally:
            self._running = False
            self._cleanup()
            ui.info("\n已退出实时语音对话")

    async def _send_audio(self, ws) -> None:
        """持续读取麦克风并发送音频。"""
        while self._running:
            try:
                data = await asyncio.to_thread(self._mic.read, CHUNK_BYTES, False)
                b64 = base64.b64encode(data).decode()
                await ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": b64,
                }))
                await asyncio.sleep(SEND_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.05)

    async def _recv_events(self, ws, ui) -> None:
        """接收并处理服务器事件。"""
        async for msg in ws:
            if not self._running:
                break
            try:
                event = json.loads(msg)
            except json.JSONDecodeError:
                continue

            t = event.get("type", "")

            if t == "response.audio.delta":
                # AI 语音 → 直接播放（与官方示例一致）
                delta = event.get("delta", "")
                if delta:
                    audio = base64.b64decode(delta)
                    await asyncio.to_thread(self._spk.write, audio)

            elif t == "input_audio_buffer.speech_started":
                # 用户开始说话 → 清空扬声器缓冲区
                if self._spk:
                    try:
                        self._spk.stop_stream()
                        self._spk.start_stream()
                    except Exception:
                        pass

            elif t == "conversation.item.input_audio_transcription.completed":
                transcript = event.get("transcript", "")
                if transcript:
                    ui.info(f"\n🧑 你: {transcript}")

            elif t == "response.audio_transcript.done":
                transcript = event.get("transcript", "")
                if transcript:
                    ui.info(f"\n🤖 贾维斯: {transcript}")

            elif t == "error":
                err = event.get("error", {})
                ui.warn(f"\n⚠ {err.get('message', str(event))}")

    async def _esc_watcher(self, ui) -> None:
        """ESC 键退出。"""
        try:
            import keyboard
            while self._running:
                if keyboard.is_pressed("esc"):
                    ui.info("\nESC 退出...")
                    self._running = False
                    return
                await asyncio.sleep(0.15)
        except ImportError:
            # keyboard 库不可用 → 静默等待（靠 Ctrl+C 退出）
            while self._running:
                await asyncio.sleep(0.5)

    def _cleanup(self) -> None:
        for dev in ("_mic", "_spk"):
            obj = getattr(self, dev, None)
            if obj:
                try:
                    obj.stop_stream()
                    obj.close()
                except Exception:
                    pass
        if self._pya:
            try:
                self._pya.terminate()
            except Exception:
                pass
