"""回归测试：TTS 播放排空（修复"贾维斯话没说完，STT 已开录"）。

背景：``on_complete`` 只表示服务端把音频发完了，扬声器缓冲里仍有未播完的
音频。旧实现 ``finish()`` 一收到完成信号就返回、``on_close`` 一关 WS 就
``stop_stream()``，导致尾音被截断且下一轮 STT 立刻开录，录到贾维斯自己的声音。

覆盖 agent/voice/tts.py：
- ``_PlaybackCallback.remaining_play_seconds``：按累计字节折算剩余播放时长
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
    """pyaudio 输出流替身：只记录 stop/close，不触碰真实声卡。"""

    def __init__(self) -> None:
        self.stopped = False
        self.closed = False

    def write(self, data: bytes) -> None:
        pass

    def stop_stream(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def _primed_callback(*, seconds: float = 1.0) -> _PlaybackCallback:
    """构造一个"已开始播放 seconds 秒音频"的回调替身。"""
    cb = _PlaybackCallback()
    cb._stream = _FakeStream()
    cb._first_data_fired = True
    cb._play_start = time.time()
    cb._total_bytes = int(
        seconds * tts_mod._PCM_RATE * tts_mod._PCM_CHANNELS * tts_mod._PCM_WIDTH
    )
    return cb


class TestRemainingPlaySeconds:
    """剩余播放时长估算。"""

    def test_full_second_remaining(self):
        cb = _primed_callback(seconds=1.0)
        rem = cb.remaining_play_seconds()
        assert 0.5 < rem <= 1.0

    def test_zero_without_audio(self):
        cb = _PlaybackCallback()
        assert cb.remaining_play_seconds() == 0.0

    def test_zero_after_playback_window(self):
        cb = _primed_callback(seconds=1.0)
        cb._play_start = time.time() - 5.0
        assert cb.remaining_play_seconds() == 0.0


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
