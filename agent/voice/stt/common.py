"""STT 后端共享基础设施（aceFelix）。

从原 agent/voice/stt.py 抽出，只放**与具体后端无关**的部分：
- 音频常量（采样率 / 声道 / 位宽 / 帧大小）与录音时长默认值
- pyaudio 延迟导入这类纯工具
- 进程级「停止录音」标志：所有后端共用同一个 threading.Event

（历史上还有 Paraformer 客户端 VAD 用的 _rms()、_SILENCE_THRESHOLD，
以及 FunASR Flash 上传用的 _pcm_to_wav()，随这两个后端一起下线。）

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

# 录音参数（DashScope 实时识别要求 16kHz 单声道 16-bit PCM）
_PCM_RATE = 16000
_PCM_CHANNELS = 1
_PCM_WIDTH = 2  # 16-bit = 2 bytes
_FRAMES_PER_BUFFER = 3200  # 200ms @ 16kHz 单声道 16bit（6400 bytes/帧）

# 录音时长默认值（QwenASR 走服务端 VAD，_SILENCE_SECONDS 作为断句时长参考）
_SILENCE_SECONDS = 1.5  # 连续静音多少秒视为"说完了"
_MAX_SECONDS = 15  # 单次录音最长秒数（防卡死）

# Ctrl+C 阻塞机制：pyaudio stream.read() 在 Windows 上无法被 Python 信号中断。
# stop_flag 用于非阻塞轮询（stream.read(..., exception_on_overflow=False) 本身不阻塞）。
# 当主线程调用 stop() 时，置位 stop_flag → 录音循环结束 → 清理资源。
_stop_flag = threading.Event()


def _rms(frame: bytes, width: int = 2) -> int:
    """计算 PCM 帧的 RMS（均方根）能量，用于音量判断与静音检测。

    Python 3.13 移除了标准库 audioop，这里用 array 手动实现等价逻辑。
    当前唯一的调用方是 barge_in._BargeInWatcher（判断用户是否开口）。
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
