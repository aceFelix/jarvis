"""语音打断监听器 —— 在 TTS 播报 / LLM 推理期间检测用户打断信号。

从 voice_loop 拆出。含三类监听器:
- _BargeInWatcher: 麦克风能量（RMS）监听，超阈值持续一段时间视为开口
- _KeyBargeInWatcher: 键盘 ESC 轮询（当前主循环实际使用的）
- _VoiceBargeInDetector: 短 STT 录音 + 打断词检测
"""

from __future__ import annotations

import threading
import time
from typing import Any

# 复用 stt 模块的 RMS 计算（Python 3.13 无 audioop）
from agent.voice.stt import _rms
from agent.voice.voice_config import _INTERRUPT_WORDS, _contains_any

_BARGE_IN_THRESHOLD = 2500     # RMS 阈值，远高于 STT 的 500，避免 TTS 自回声触发
_BARGE_IN_MIN_SPEAK = 0.4      # 持续发声多少秒视为用户开口（防瞬时噪音误触发）
_BARGE_IN_RATE = 16000         # 监听采样率
_BARGE_IN_FRAMES = 1600        # 100ms/帧


class _BargeInWatcher:
    """后台麦克风能量监听器：检测用户说话，触发打断。

    在 TTS 播报阶段运行（与 STT 录音阶段错开，不冲突）。daemon 线程读麦克风
    帧 → 算 RMS → 持续超阈值 min_speak 秒 → 调 on_barge_in。
    """

    def __init__(self, on_barge_in, *, threshold: int = _BARGE_IN_THRESHOLD,
                 min_speak: float = _BARGE_IN_MIN_SPEAK) -> None:
        self._on_barge = on_barge_in
        self._threshold = threshold
        self._min_speak = min_speak
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pa = None
        self._stream = None
        self._triggered = False

    def start(self) -> None:
        self._stop.clear()
        self._triggered = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._stream is not None:
                self._stream.close()
        except Exception:
            pass
        try:
            if self._pa is not None:
                self._pa.terminate()
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    @property
    def triggered(self) -> bool:
        return self._triggered

    def _run(self) -> None:
        try:
            import pyaudio
            self._pa = pyaudio.PyAudio()
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=_BARGE_IN_RATE,
                input=True,
                frames_per_buffer=_BARGE_IN_FRAMES,
            )
            speaking_since: float | None = None
            while not self._stop.is_set():
                try:
                    frame = self._stream.read(_BARGE_IN_FRAMES, exception_on_overflow=False)
                except Exception:
                    break
                rms = _rms(frame, 2)
                if rms >= self._threshold:
                    if speaking_since is None:
                        speaking_since = time.time()
                    elif time.time() - speaking_since >= self._min_speak:
                        self._triggered = True
                        self._stop.set()
                        try:
                            self._on_barge()
                        except Exception:
                            pass
                        break
                else:
                    speaking_since = None
        except Exception:
            # 麦克风监听失败静默处理，打断功能不可用但不影响对话
            pass


class _KeyBargeInWatcher:
    """键盘 ESC 监听器：全阶段通用退出/打断信号。

    - 对话阶段（聆听/思考/说话）：停止当前播报 → 返回聆听
    - 待机阶段（等唤醒词）：退出语音模式 → 回到文本 REPL

    用 keyboard.is_pressed() 轮询，不依赖全局钩子（Windows 上全局钩子需管理员权限）。
    后台线程每 100ms 检查一次，响应延迟 < 200ms。
    """

    def __init__(self, on_barge_in, *, key: str = "esc") -> None:
        self._on_barge = on_barge_in
        self._key = key
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._triggered = False
        self._available = False
        try:
            import keyboard  # noqa: F401
            self._available = True
        except Exception:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def start(self) -> None:
        if not self._available:
            return
        self._triggered = False
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def _poll(self) -> None:
        import keyboard
        import time
        while not self._stop.is_set():
            try:
                if keyboard.is_pressed(self._key):
                    self._triggered = True
                    self._on_barge()
                    break
            except Exception:
                pass
            time.sleep(0.1)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None

    @property
    def triggered(self) -> bool:
        return self._triggered


class _VoiceBargeInDetector:
    """语音打断检测器：TTS 播报期间开短 STT 录音，检测中断词。

    为避免 Windows PyAudio 双实例 segfault，每次短录后立即释放 PyAudio。
    若 PyAudio 冲突则静默放弃当次检测，连续无冲突检测到中断词则触发打断。
    """

    def __init__(self, stt: Any, on_barge) -> None:
        self._stt = stt
        self._on_barge = on_barge
        self._stop = threading.Event()

    def run(self) -> None:
        """在后台线程中循环短录，检测到中断词则回调。"""
        import time
        self._stop.clear()
        # 初始延迟：避免和 TTS WS 同时建立连接触发 DashScope RST
        time.sleep(1.0)
        while not self._stop.is_set():
            try:
                result = self._stt.listen(
                    max_seconds=1.5,
                    silence_seconds=0.5,
                    silence_threshold=500,
                    on_partial=lambda t: None,
                    on_open=lambda: None,
                )
                text = (result.get("text") or "").strip()
                if text and _contains_any(text, _INTERRUPT_WORDS):
                    self._on_barge()
                    break
            except Exception:
                pass
            # 节流：每 3 秒最多一次 STT 短录，防止 WS 连接风暴
            if not self._stop.is_set():
                time.sleep(3.0)

    def stop(self) -> None:
        self._stop.set()
