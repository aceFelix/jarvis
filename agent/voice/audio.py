"""PyAudio 全局单例。

Windows 上 PortAudio 非线程安全，多个 PyAudio() 实例并发会 segfault。
此模块确保整个进程只有一个 PyAudio 实例，TTS（输出流）和 STT（输入流）
通过同一实例 open() 各自的 stream，互不冲突。

用法:
    from agent.voice.audio import get_pyaudio, release_pyaudio

    pa = get_pyaudio()
    stream = pa.open(..., output=True)   # TTS 播放流
    # 或
    stream = pa.open(..., input=True)    # STT 录音流

    stream.stop_stream()
    stream.close()
    # 不调 pa.terminate()！由 release_pyaudio() 统一释放
"""

from __future__ import annotations

import threading
from typing import Any

_pyaudio: Any = None
_lock = threading.Lock()


def get_pyaudio() -> Any:
    """获取全局 PyAudio 单例。首次调用初始化。"""
    global _pyaudio
    if _pyaudio is None:
        with _lock:
            if _pyaudio is None:
                import pyaudio
                _pyaudio = pyaudio.PyAudio()
    return _pyaudio


def release_pyaudio() -> None:
    """释放全局 PyAudio 实例（进程退出时调用）。"""
    global _pyaudio
    with _lock:
        if _pyaudio is not None:
            try:
                _pyaudio.terminate()
            except Exception:
                pass
            _pyaudio = None
