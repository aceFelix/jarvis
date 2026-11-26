"""语音打断监听器 —— 在 TTS 播报 / LLM 推理期间检测用户打断信号。

从 voice_loop 拆出。含两类监听器:
- _BargeInWatcher: 麦克风能量（RMS）监听（旧实现，现由 voice_barge 取代）
- _KeyBargeInWatcher: 键盘空格轮询（当前主循环实际使用的）
"""

from __future__ import annotations

import threading
import time
from typing import Any

# 复用 stt 模块的 RMS 计算（Python 3.13 无 audioop）
from agent.voice.stt import _rms

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
        # 先 join 再让监听线程自己关流：read() 期间从外部 close 在 Windows
        # PortAudio 上会直接访问违例（0xC0000005），进程静默退出（2026-11-26）。
        # 不 terminate：全局 PyAudio 实例由 audio.release_pyaudio 统一释放
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    @property
    def triggered(self) -> bool:
        return self._triggered

    def _run(self) -> None:
        stream = None
        try:
            import pyaudio
            from agent.audio.device import get_pyaudio
            self._pa = get_pyaudio()
            stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=_BARGE_IN_RATE,
                input=True,
                frames_per_buffer=_BARGE_IN_FRAMES,
            )
            self._stream = stream
            speaking_since: float | None = None
            while not self._stop.is_set():
                try:
                    frame = stream.read(_BARGE_IN_FRAMES, exception_on_overflow=False)
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
        finally:
            # 流只由本线程关闭，避免 read/close 并发导致 PortAudio 崩溃
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
            if self._stream is stream:
                self._stream = None


class _KeyBargeInWatcher:
    """键盘空格监听器：全阶段通用退出/打断信号。

    - 对话阶段（聆听/思考/说话）：停止当前播报 → 返回聆听
    - 待机阶段（等唤醒词）：退出语音模式 → 回到文本 REPL

    用 keyboard.is_pressed() 轮询，不依赖全局钩子（Windows 上全局钩子需管理员权限）。
    后台线程每 100ms 检查一次，响应延迟 < 200ms。
    键名可通过 ``[voice] barge_in_hotkey`` 配置（默认 space，2026-11-09 用户要求
    由 ESC 改为空格）。
    """

    def __init__(self, on_barge_in, *, key: str = "space") -> None:
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
