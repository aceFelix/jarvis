"""回归测试：TTS 播放排空（修复"贾维斯话没说完，STT 已开录"）。

背景：``on_complete`` 只表示服务端把音频发完了，扬声器缓冲里仍有未播完的
音频。旧实现 ``finish()`` 一收到完成信号就返回、``on_close`` 一关 WS 就
``stop_stream()``，导致尾音被截断且下一轮 STT 立刻开录，录到贾维斯自己的声音。

覆盖 agent/voice/tts.py：
- ``_PlaybackCallback.remaining_play_seconds``：只看“最后一次写入之后”的残余
  （2026-10 修正：旧口径把 LLM 吐字停顿也算作已播，长停顿后重算得 0，尾音被截断）
- ``on_close``：关闭 WS 前排空扬声器缓冲，打断（barge-in）时跳过
- ``CosyVoiceTTS._wait_for_playback``：完成信号到达后仍排空

@author aceFelix
"""

from __future__ import annotations

import threading
import time

from agent.voice import tts as tts_mod
from agent.voice.tts import CosyVoiceTTS, _PlaybackCallback


class _FakeStream:
    """pyaudio 输出流替身：只记录 stop/close/write，不触碰真实声卡。"""

    def __init__(self) -> None:
        self.stopped = False
        self.closed = False
        self.written = 0

    def write(self, data: bytes) -> None:
        self.written += len(data)

    def stop_stream(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def _primed_callback(*, seconds: float = 1.0) -> _PlaybackCallback:
    """构造一个“刚写完 seconds 秒音频”的回调替身。

    收尾排空现在只看“最后一次写入之后”（见 ``remaining_play_seconds``），
    所以必须同时给出 ``_last_data_time`` / ``_last_chunk_bytes``；
    只设 ``_play_start`` / ``_total_bytes`` 是旧口径，已不适配。
    """
    cb = _PlaybackCallback()
    cb._stream = _FakeStream()
    cb._first_data_fired = True
    cb._play_start = time.time()
    cb._total_bytes = int(seconds * tts_mod._BYTES_PER_SEC)
    cb._last_data_time = time.time()
    cb._last_chunk_bytes = cb._total_bytes
    return cb


class TestRemainingPlaySeconds:
    """剩余播放时长估算。"""

    def test_full_second_remaining(self):
        cb = _primed_callback(seconds=1.0)
        rem = cb.remaining_play_seconds()
        assert 0.5 < rem <= 1.2

    def test_zero_without_audio(self):
        cb = _PlaybackCallback()
        assert cb.remaining_play_seconds() == 0.0

    def test_zero_after_drain(self):
        """最后一块音频是很久以前写的 → 已经播完，不该再排空。"""
        cb = _primed_callback(seconds=1.0)
        cb._last_data_time = time.time() - 5.0
        assert cb.remaining_play_seconds() == 0.0

    def test_long_gap_does_not_underestimate(self):
        """LLM 停顿也不能把估算拉到 0（2026-10 修的真 bug）。

        旧口径 ``total_bytes/每秒字节 - (now - 首块到达)`` 把“停顿”当成了
        “正在播”。实测：累计 3s 音频 + 中间停 6s → 估 **0.00s**，
        真实还有 **1.00s**，于是 on_close 立刻 stop_stream，尾音被截断。
        """
        cb = _PlaybackCallback()
        cb._stream = _FakeStream()
        one_sec = tts_mod._BYTES_PER_SEC
        cb.on_data(b"\x00\x00" * (one_sec // 2))     # 句1：1 秒音频
        cb._play_start -= 6.0                        # 句1 之后停了 6 秒
        cb.on_data(b"\x00\x00" * (one_sec // 2))     # 句2：刚写入 1 秒

        rem = cb.remaining_play_seconds()
        assert rem > 0.9, f"停 6 秒后估算被拉到 {rem:.2f}s，句2 会被截断"


class TestOnCloseDrain:
    """WS 关闭时的排空行为。"""

    def test_drains_before_closing(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(tts_mod.time, "sleep", lambda s: slept.append(s))
        cb = _primed_callback(seconds=1.0)
        cb.on_close()
        assert slept and slept[0] > 0.5    # 排空等待发生
        assert cb._stream is None          # 播放流已释放
        assert cb._closed.is_set()

    def test_no_drain_when_barged_in(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(tts_mod.time, "sleep", lambda s: slept.append(s))
        cb = _primed_callback(seconds=1.0)
        cb.stop()                          # barge-in：ESC / 外部分断
        cb.on_close()
        assert slept == []                 # 打断不排空，保证即时响应
        assert cb._closed.is_set()


class TestWaitForPlayback:
    """合成完成后的排空行为。"""

    def test_drains_after_completion(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(tts_mod.time, "sleep", lambda s: slept.append(s))
        tts = CosyVoiceTTS(api_key="sk-test")
        tts._callback = _primed_callback(seconds=1.0)
        done = threading.Event()
        done.set()
        sc: list[str] = []
        tts._wait_for_playback(done, sc)
        assert sc == []
        assert slept and slept[0] > 0.5

    def test_no_drain_when_barged_in(self, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(tts_mod.time, "sleep", lambda s: slept.append(s))
        tts = CosyVoiceTTS(api_key="sk-test")
        cb = _primed_callback(seconds=1.0)
        cb.stop()
        tts._callback = cb
        done = threading.Event()
        done.set()
        tts._wait_for_playback(done, [])
        assert slept == []
