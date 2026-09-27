"""STT 后端共享基础设施（aceFelix）。

从原 agent/voice/stt.py 抽出，只放**跨后端共用**的部分：
- 音频常量（采样率 / 声道 / 位宽 / 帧大小）与静音检测阈值
- RMS 能量计算（Windows 3.13 起标准库移除 audioop，此处手写等价逻辑）
- PCM → WAV 封装、pyaudio 延迟导入这类与具体后端无关的纯工具
- 进程级「停止录音」标志：所有后端共用同一个 threading.Event

关于停止标志为什么必须在这里而不是各后端各有一份：
pyaudio 的 stream.read() 是 C 扩展阻塞调用，收不到 Python 的信号与
asyncio 取消。上层（voice_loop 的 SIGINT 处理、REPL 的 ESC 打断、
workbench 引擎的停止按钮）统一调用 agent.voice.stt 暴露的
_request_stop()，而录音循环在 stt.listen() 里轮询同一个 Event——
所以这个标志是**跨模块的通信契约**，只能有一个实例。
"""

from __future__ import annotations

import array
import threading

# 录音参数（Paraformer 实时识别要求 16kHz 单声道 16-bit PCM，各后端沿用同一规格）
_PCM_RATE = 16000
_PCM_CHANNELS = 1
_PCM_WIDTH = 2  # 16-bit = 2 bytes
_FRAMES_PER_BUFFER = 3200  # 200ms @ 16kHz 单声道 16bit（6400 bytes/帧）

# 静音检测默认参数
_SILENCE_THRESHOLD = 500  # RMS 阈值，低于此值视为静音（16-bit PCM 量级）
_SILENCE_SECONDS = 1.5  # 连续静音多少秒视为"说完了"
_MAX_SECONDS = 15  # 单次录音最长秒数（防卡死）

# Ctrl+C 阻塞机制：pyaudio stream.read() 在 Windows 上无法被 Python 信号中断。
# stop_flag 用于非阻塞轮询（stream.read(..., exception_on_overflow=False) 本身不阻塞）。
# 当主线程调用 stop() 时，置位 stop_flag → 录音循环结束 → 清理资源。
_stop_flag = threading.Event()


def _rms(frame: bytes, width: int = 2) -> int:
    """计算 PCM 帧的 RMS（均方根）能量，用于静音检测。

    Python 3.13 移除了标准库 audioop，这里用 array 手动实现等价逻辑。
    """
    if width == 2:
        a = array.array("h")  # 16-bit signed
        a.frombytes(frame)
    elif width == 1:
        a = array.array("b")
        a.frombytes(frame)
    else:
        return 0
    n = len(a)
    if n == 0:
        return 0
    return int((sum(int(x) * int(x) for x in a) / n) ** 0.5)


def _is_stopped() -> bool:
    """检查是否通过信号收到停止请求。"""
    return _stop_flag.is_set()


def _request_stop() -> None:
    """请求停止当前正在阻塞的 listen()。用于 Ctrl+C 信号处理器。"""
    _stop_flag.set()


def _reset_stop() -> None:
    """重置停止标志（每次 listen() 前调用）。"""
    _stop_flag.clear()


def _import_pyaudio():
    """延迟导入 pyaudio。"""
    import pyaudio
    return pyaudio


def _pcm_to_wav(pcm_data: bytes, sample_rate: int, channels: int, bits: int) -> bytes:
    """将原始 PCM 数据封装为 WAV 格式（44 字节头 + PCM 数据）。"""
    import struct
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(pcm_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,          # chunk size
        1,           # PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        data_size,
    )
    return header + pcm_data
