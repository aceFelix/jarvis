"""终端输出流包装 `_TerminalSpeaker` 的测试（2026-11-25）。

正常播放必须与改造前一致（直接 write、不排队）；只有 `abort()` 停流再启，
**绝不 close**（与录音线程并发会 segfault）。这里用假 PyAudio 验证这一约束。

@author aceFelix
"""

from __future__ import annotations

from agent.voice.realtime_talk import _TerminalSpeaker


class FakeStream:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.written: list[bytes] = []
        self.stops = 0
        self.closed = False

    def write(self, pcm: bytes) -> None:
        self.written.append(bytes(pcm))

    def stop_stream(self) -> None:
        self.stops += 1

    def close(self) -> None:
        self.closed = True

    def start_stream(self) -> None:
        pass


class FakePa:
    def __init__(self) -> None:
        self.opened: list[FakeStream] = []

    def open(self, **_kwargs) -> FakeStream:
        stream = FakeStream(f"stream-{len(self.opened)}")
        self.opened.append(stream)
        return stream


class TestTerminalSpeaker:
    def test_write_goes_to_current_stream(self):
        pa = FakePa()
        spk = _TerminalSpeaker(pa)

        spk.write(b"abc")

        assert pa.opened[0].written == [b"abc"]

    def test_abort_stops_then_starts_without_closing(self):
        """打断只能 stop/start，不能 close：close 与录音线程并发会 segfault。"""
        pa = FakePa()
        spk = _TerminalSpeaker(pa)
        stream = pa.opened[0]

        spk.abort()

        assert stream.stops == 1
        assert stream.closed is False
        assert len(pa.opened) == 1  # 没有重开新流

        spk.write(b"still-same")
        assert stream.written == [b"still-same"]
