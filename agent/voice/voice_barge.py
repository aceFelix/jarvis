"""/voice 半双工的语音 barge-in（开口即停），复用 /talk 的 AEC+本地 VAD 思路。

与 /talk 的区别：/voice 的 TTS 是独立 CosyVoice WebSocket，没有服务端 VAD，
所以播报期间由本模块在本地听麦克风：TTS 实际播放的 PCM 作为 AEC 参考，
麦克风经 AEC 消回声后算 RMS，连续超阈即判定用户开口 → 回调打断。

安全约束（Windows PortAudio 非线程安全）：
- 麦克风输入流从 ``audio.get_pyaudio()`` 全局实例开（TTS 输出已用它），
  **绝不新建 PyAudio()、绝不 terminate()**；单实例双流已由 /talk 验证。
- 只在 STT 录音结束后的「推理+播报」窗口运行（voice_loop 的轮询线程生命周期），
  不与 stt.listen() 的录音并发。

@author aceFelix
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from agent.audio.device import get_pyaudio
from agent.audio.dsp import rms as _rms

try:
    import pyaudio
except ImportError:
    pyaudio = None

# PyAudio 的 paInt16 常量（枚举值 8）。CI 等无 pyaudio 环境里模块为 None，
# 但单测用假设备构造仍需要格式值；运行期真实开流时 pyaudio 必然存在。
_PA_INT16 = getattr(pyaudio, "paInt16", 8)

_RATE = 16000
_FRAMES = 1600          # 100ms/帧
_THRESHOLD = 0.08       # AEC 后 RMS 门槛（-22dBFS），高于残余回声
_MIN_FRAMES = 2         # 连续超阈帧数（~200ms），防瞬时噪音
_WARMUP = 0.3           # 启动收敛窗（秒），AEC 对齐前不判

_active_lock = threading.Lock()
_active: "VoiceBargeWatcher | None" = None


def feed_playback(pcm: bytes) -> None:
    """TTS 播放回调调用：把实际播放的 PCM 喂给当前 barge-in 的 AEC 参考。

    无活跃 watcher 时为空操作；异常吞掉，绝不影响播放。
    """
    with _active_lock:
        watcher = _active
    if watcher is not None:
        watcher.feed_reference(pcm)


class VoiceBargeWatcher:
    """播报/推理期间的麦克风 barge-in 监听器（单实例 PyAudio + AEC）。

    Args:
        on_barge: 判定用户开口后的回调（voice_loop 置 interrupt_event + 停 TTS）。
        threshold / min_frames / warmup: 判定参数，默认见模块常量。

    @author aceFelix
    """

    def __init__(
        self,
        on_barge: Callable[[], None],
        *,
        threshold: float = _THRESHOLD,
        min_frames: int = _MIN_FRAMES,
        warmup: float = _WARMUP,
    ) -> None:
        self._on_barge = on_barge
        self._threshold = threshold
        self._min_frames = min_frames
        self._warmup = warmup
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream: Any = None
        self._aec: Any = None
        self.triggered = False

    # ---- 生命周期 ----

    def start(self) -> None:
        global _active
        self._stop.clear()
        self.triggered = False
        with _active_lock:
            _active = self
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        global _active
        self._stop.set()
        with _active_lock:
            if _active is self:
                _active = None
        # 只关流，不 terminate（全局实例由 audio.release_pyaudio 统一释放）
        try:
            if self._stream is not None:
                self._stream.close()
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def feed_reference(self, pcm: bytes) -> None:
        """TTS 播放参考信号（24kHz PCM），交给 AEC 消回声。"""
        if self._aec is not None and pcm:
            try:
                self._aec.feed_reference(pcm)
            except Exception:
                pass

    # ---- 监听线程 ----

    def _run(self) -> None:
        try:
            pa = get_pyaudio()
            self._stream = pa.open(
                format=_PA_INT16,
                channels=1,
                rate=_RATE,
                input=True,
                frames_per_buffer=_FRAMES,
            )
        except Exception:
            # 麦克风不可用：barge-in 静默降级，手动打断仍可用
            return
        try:
            from agent.audio.aec import EchoCanceller, is_available
            if is_available():
                self._aec = EchoCanceller()
        except Exception:
            self._aec = None

        started = time.time()
        hits = 0
        while not self._stop.is_set():
            try:
                frame = self._stream.read(_FRAMES, exception_on_overflow=False)
            except Exception:
                break
            data = frame
            if self._aec is not None:
                try:
                    data = self._aec.process_mic(frame) or frame
                except Exception:
                    data = frame
            if time.time() - started < self._warmup:
                hits = 0
                continue
            if _rms(data) >= self._threshold:
                hits += 1
                if hits >= self._min_frames:
                    self.triggered = True
                    try:
                        self._on_barge()
                    except Exception:
                        pass
                    break
            else:
                hits = 0
        try:
            if self._stream is not None:
                self._stream.close()
        except Exception:
            pass
        self._stream = None


__all__ = ["VoiceBargeWatcher", "feed_playback"]
