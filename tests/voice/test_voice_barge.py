"""/voice 语音 barge-in（voice_barge）单元测试。

覆盖：连续超阈触发、单帧不触发、warmup 不触发、feed_playback 转发、
stop 清理活跃 watcher。麦克风/AEC 用替身，不依赖真实音频设备。

@author aceFelix
"""

from __future__ import annotations

import time

import pytest

from agent.voice import voice_barge


class _FakeStream:
    def __init__(self, frames):
        self._frames = list(frames)
        self.closed = False

    def read(self, n, exception_on_overflow=False):
        if self._frames:
            return self._frames.pop(0)
        time.sleep(0.01)
        return b"\x00\x00" * n

    def close(self):
        self.closed = True


class _FakePa:
    def __init__(self, stream):
        self._stream = stream

    def open(self, **kwargs):
        return self._stream


_LOW = b"\x00\x00" * 1600
_HIGH = b"\xff\x7f" * 1600


@pytest.fixture
def no_aec(monkeypatch):
    import agent.audio.aec as aec_mod
    monkeypatch.setattr(aec_mod, "is_available", lambda: False)


def _watcher(frames, monkeypatch, **kw):
    stream = _FakeStream(frames)
    monkeypatch.setattr(voice_barge, "get_pyaudio", lambda: _FakePa(stream))
    hits = []
    w = voice_barge.VoiceBargeWatcher(
        lambda: hits.append(1), warmup=0.0, min_frames=2, threshold=0.5, **kw
    )
    return w, hits, stream


class TestVoiceBarge:
    def test_two_consecutive_loud_frames_trigger(self, monkeypatch, no_aec):
        w, hits, stream = _watcher([_LOW, _HIGH, _HIGH], monkeypatch)
        w.start()
        for _ in range(100):
            if hits:
                break
            time.sleep(0.01)
        w.stop()
        assert hits == [1]
        assert w.triggered is True

    def test_single_loud_frame_does_not_trigger(self, monkeypatch, no_aec):
        w, hits, stream = _watcher([_HIGH, _LOW, _LOW, _LOW], monkeypatch)
        w.start()
        time.sleep(0.1)
        w.stop()
        assert hits == []

    def test_warmup_suppresses_trigger(self, monkeypatch, no_aec):
        stream = _FakeStream([_HIGH, _HIGH, _HIGH])
        monkeypatch.setattr(voice_barge, "get_pyaudio", lambda: _FakePa(stream))
        hits = []
        w = voice_barge.VoiceBargeWatcher(
            lambda: hits.append(1), warmup=5.0, min_frames=2, threshold=0.5
        )
        w.start()
        time.sleep(0.1)
        w.stop()
        assert hits == []

    def test_feed_playback_forwards_to_active(self, monkeypatch):
        received = []

        class _Sink:
            def feed_reference(self, pcm):
                received.append(pcm)

        monkeypatch.setattr(voice_barge, "_active", _Sink())
        voice_barge.feed_playback(b"abc")
        assert received == [b"abc"]

    def test_stop_clears_active(self, monkeypatch, no_aec):
        w, hits, stream = _watcher([_LOW], monkeypatch)
        w.start()
        assert voice_barge._active is w
        w.stop()
        assert voice_barge._active is None

    def test_runs_without_pyaudio_module(self, monkeypatch, no_aec):
        """CI runner 未装 pyaudio：模块导入不能依赖真实 pyaudio 常量。"""
        import importlib
        import sys

        monkeypatch.setitem(sys.modules, "pyaudio", None)
        vb = importlib.reload(voice_barge)
        assert vb._PA_INT16 == 8

        stream = _FakeStream([_HIGH, _HIGH])
        monkeypatch.setattr(vb, "get_pyaudio", lambda: _FakePa(stream))
        hits = []
        w = vb.VoiceBargeWatcher(
            lambda: hits.append(1), warmup=0.0, min_frames=2, threshold=0.5
        )
        w.start()
        for _ in range(100):
            if hits:
                break
            time.sleep(0.01)
        w.stop()
        assert hits == [1]
