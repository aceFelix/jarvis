"""桌面桥接音频传输 —— RealtimeEngine 的音频适配器（2026-09-28，aceFelix）。

实现引擎的鸭子类型音频接口（``_mic.read(n, blocking)`` / ``_spk.write(pcm)`` /
``stop_stream/start_stream``），数据源/去向是 jarvis-desktop 渲染进程：

- 麦克风：桌面 ``getUserMedia``（**浏览器内置 AEC 回声消除**，这是桌面端能做
  真全双工的关键）+ AudioWorklet 重采样到 16kHz PCM16 → WS ``talk.audio``
  帧 → :class:`BridgeMic` 缓存 → 引擎按 100ms 节拍拉取。断流/欠载自动补静音，
  网络抖动由缓存吸收（背压：超限丢最旧帧）。
- 扬声器：引擎 ``write`` 的 24kHz PCM16 → :class:`BridgeSpk` 逐帧转发 WS
  ``talk_audio`` 事件 → 桌面 AudioContext 播放；打断时 ``stop_stream`` 置
  丢弃标志（远端同步清空播放队列，与引擎的打断代数语义一致）。

引擎侧用法（半双工必须关闭——浏览器 AEC 已负责回声消除）::

    engine = RealtimeEngine(..., half_duplex=False, echo_suppress_with_aec=False)
    mic, spk = BridgeMic(), BridgeSpk(on_chunk=emit_to_desktop)
    engine.attach_audio(mic, spk)
    await engine.run_session(ui)

@author aceFelix
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Callable

# 麦克风缓存上限（秒）：WS 帧到达速率与引擎消费速率的缓冲区间；超限丢最旧，
# 避免桌面卡顿时内存膨胀。16kHz × 2B × 单声道。
_MAX_BUFFER_BYTES = 5 * 16000 * 2
# read() 欠载等待上限（秒）：客户端断流时引擎发送循环不能永久阻塞
_READ_STARVE_SECONDS = 0.25


class BridgeMic:
    """桥接麦克风：线程安全 PCM 缓存，供引擎 ``read(n, blocking)`` 拉取。

    速率天然对齐：桌面以 ~20ms 帧持续推送，引擎每 ~120ms 拉 100ms，
    缓存水位在中位附近波动；欠载（客户端卡顿）返回补零的等长数据，
    保证 DashScope 输入流不中断（协议要求持续发送）。
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._cond = threading.Condition()
        self._closed = False

    def feed(self, data: bytes) -> None:
        """推入一帧客户端麦克风 PCM（serve 侧 WS 线程调用）。"""
        if not data:
            return
        with self._cond:
            if self._closed:
                return
            self._buf.extend(data)
            if len(self._buf) > _MAX_BUFFER_BYTES:
                # 背压：丢最旧（滞后比超前好听——超前会产生可感知的空洞）
                del self._buf[: len(self._buf) - _MAX_BUFFER_BYTES]
            self._cond.notify()

    def read(self, n: int, _exception_on_overflow: bool = False) -> bytes:
        """引擎拉取接口：返回恰好 n 字节；欠载等待后补静音，不永久阻塞。

        必须在引擎的发送协程（经 asyncio.to_thread 调用）中使用。
        """
        with self._cond:
            if self._closed:
                return b"\x00" * n
            # 欠载时短暂等待客户端帧，避免把网络抖动直接变成静音
            if len(self._buf) < n:
                self._cond.wait(timeout=_READ_STARVE_SECONDS)
            if len(self._buf) >= n:
                out = bytes(self._buf[:n])
                del self._buf[:n]
                return out
            # 仍不足：现有数据 + 补静音（等长，协议要求流持续）
            out = bytes(self._buf) + b"\x00" * (n - len(self._buf))
            self._buf.clear()
            return out

    def close(self) -> None:
        """会话结束：唤醒所有等待中的 read 并使其返回静音。"""
        with self._cond:
            self._closed = True
            self._cond.notify_all()


class BridgeSpk:
    """桥接扬声器：引擎 ``write`` 的 PCM 帧经回调转发到 WS 广播。

    ``stop_stream()`` 是"清空已缓冲音频"语义（引擎打断 AI 播报时调用）：
    置丢弃标志（紧接的残余帧直接丢弃）并通过 ``on_flush`` 通知远端清空
    播放队列；随后引擎会 ``start_stream()`` 恢复接收新一轮回复的帧。
    """

    def __init__(
        self,
        on_chunk: Callable[[bytes], None],
        on_flush: Callable[[], None] | None = None,
    ) -> None:
        self._on_chunk = on_chunk
        self._on_flush = on_flush
        self._discard = False
        self._lock = threading.Lock()
        self._closed = False

    def write(self, pcm: bytes) -> None:
        """引擎音频输出回调（engine 的 audio delta 分支调用）。"""
        if not pcm:
            return
        with self._lock:
            if self._closed or self._discard:
                return
        self._on_chunk(pcm)

    def stop_stream(self) -> None:
        """打断：丢弃后续帧并通知远端清空播放队列。"""
        with self._lock:
            if self._closed:
                return
            self._discard = True
        if self._on_flush is not None:
            try:
                self._on_flush()
            except Exception:
                pass

    def start_stream(self) -> None:
        """恢复播放（新一轮 AI 回复开始时由引擎调用）。"""
        with self._lock:
            self._discard = False

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._discard = True
