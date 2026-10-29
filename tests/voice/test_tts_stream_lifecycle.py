"""流式 TTS 会话生命周期回归测试（aceFelix）。

锁定的用户现象（2026-10）：
「文字都出了，但是语音会断，然后继续之后的说话过程中会夹杂上次断的语音继续说。」

查出三个独立缺陷，都会造成上述表现：

1. **打断后输出流/WS 连接只置标志、从不释放**（`_PlaybackCallback.stop()` 与
   `CosyVoiceTTS.stop()`）。`finish()` 因 stop 提前返回并把 `_synthesizer` 置
   None，此后再没对象能关它 → 每打断一次泄漏一个 pyaudio 输出流 + 一条
   DashScope WS。下一轮在同一 PyAudio 上再开一个流，两个流并存就是"新旧语音
   混在一起"；连接攒够后 `start_stream()` 被拒，表现为"用得越久越容易没声音"。

2. **收尾时长估算被 LLM 停顿污染**（`remaining_play_seconds`，见
   `test_tts_playback_drain.py::test_long_gap_does_not_underestimate`）。

3. **WS 断开后音频被静默丢弃**（`on_data` 直接 return，不记错、不降级）。
   文字照常上屏，语音彻底没了，用户无从判断。

覆盖：
- `stop()` 幂等释放流；`on_close()` 再调一次不出错
- 两轮之间旧流必须先释放（不允许并发输出流）
- 流释放后仍有音频进来 → 记 error（可被上层感知）
- `CosyVoiceTTS.stop()` / `finish()` 关闭合成器（含 `streaming_cancel`）
- `StreamTTSPlayer` 依据 `tts.error` 降级，不再静默吞句

@author aceFelix
"""

from __future__ import annotations

import threading

import pytest

from agent.voice import tts as tts_mod
from agent.voice.stream_tts import StreamTTSPlayer
from agent.voice.tts import CosyVoiceTTS, _PlaybackCallback


class _FakeStream:
    """pyaudio 输出流替身。"""

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


class _FakeSynth:
    """SpeechSynthesizer 替身：记录 cancel / close。"""

    def __init__(self, *, fail_on_call: bool = False) -> None:
        self.cancelled = False
        self.closed = False
        self.calls: list[str] = []
        self._fail_on_call = fail_on_call

    def streaming_call(self, text: str) -> None:
        if self._fail_on_call:
            raise RuntimeError("ws 已断开")
        self.calls.append(text)

    def streaming_cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.closed = True

    def streaming_complete(self) -> None:
        pass

    def get_first_package_delay(self) -> int:
        return 0


# ------------------------------------------------- 1. 打断必须释放输出流 ----

class TestBargeInReleasesStream:
    """打断 = 立刻闭嘴 + 不留悬空的输出流。"""

    @staticmethod
    def test_stop_releases_stream_immediately() -> None:
        """`stop()` 必须真的关掉流，而不是只置标志。"""
        cb = _PlaybackCallback()
        stream = _FakeStream()
        cb._stream = stream

        cb.stop()

        assert stream.stopped and stream.closed, "打断后输出流没关 = 泄漏"
        assert cb._stream is None

    @staticmethod
    def test_stop_is_idempotent_with_on_close() -> None:
        """stop() 之后再走 on_close()（SDK 迟到回调）不能二次关闭或抛错。"""
        cb = _PlaybackCallback()
        stream = _FakeStream()
        cb._stream = stream

        cb.stop()
        cb.on_close()          # 幂等，不应抛异常

        assert stream.stopped and stream.closed
        assert cb._closed.is_set()

    @staticmethod
    def test_two_rounds_do_not_overlap_output_streams(
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """两轮不能同时持有输出流——并存就会把新旧语音混在一起。"""
        opened: list[_FakeStream] = []

        class _PA:
            def open(self, **_kw):  # noqa: ANN003, ANN201
                s = _FakeStream()
                opened.append(s)
                return s

            def get_format_from_width(self, _w: int) -> int:  # noqa: ARG004
                return 8

        # on_open 内部是 `from agent.voice.audio import get_pyaudio`，patch 模块属性
        monkeypatch.setattr("agent.voice.audio.get_pyaudio", lambda: _PA())

        round1 = _PlaybackCallback()
        round1.on_open()
        round1.on_data(b"\x00\x00" * 100)
        round1.stop()                     # 用户打断第 1 轮

        round2 = _PlaybackCallback()
        round2.on_open()                  # 第 2 轮开新流

        assert opened[0].closed, "第 1 轮的流还开着 → 会与新流同时出声"
        assert len(opened) == 2
        assert not opened[1].closed       # 第 2 轮正常持有

    @staticmethod
    def test_audio_after_release_is_recorded_as_error() -> None:
        """**非打断**场景下流没了却还在收音频，必须留下原因而不是静默丢弃。

        静默丢弃 = 上层完全看不出“音频被丢了”，只剩“文字有、语音没”。
        注意区分：打断（stop）后丢包是我们主动的，不该报错。
        """
        cb = _PlaybackCallback()
        cb._stream = _FakeStream()
        cb._release_stream()      # 流被释放，但没置 stop_flag（不是用户打断）

        cb.on_data(b"\x00\x00" * 1000)

        assert cb.error, "音频被丢弃却没有留下任何原因"
        assert "播放流已关闭" in cb.error

    @staticmethod
    def test_barge_in_drop_is_not_an_error() -> None:
        """打断后丢包属预期行为，不应该污染 error。"""
        cb = _PlaybackCallback()
        cb._stream = _FakeStream()

        cb.stop()
        cb.on_data(b"\x00\x00" * 1000)

        assert cb.error is None


# ---------------------------------------- 2. 合成器必须被关闭（WS 不泄漏）----

class TestSynthesizerClosed:
    """`CosyVoiceTTS` 的收尾路径必须关闭 WS。"""

    @staticmethod
    def test_stop_cancels_and_closes() -> None:
        """打断：先 cancel 告诉服务端作废，再 close 断开。"""
        tts = CosyVoiceTTS(api_key="sk-test")
        synth = _FakeSynth()
        tts._synthesizer = synth
        tts._callback = _PlaybackCallback()

        tts.stop()

        assert synth.cancelled, "打断未 streaming_cancel，服务端还会继续推音频"
        assert synth.closed, "打断未 close，WS 连接泄漏"
        assert tts._synthesizer is None

    @staticmethod
    def test_stop_twice_is_safe() -> None:
        """重复 stop（ESC 连按 / 多路打断）不能抛错。"""
        tts = CosyVoiceTTS(api_key="sk-test")
        tts._synthesizer = _FakeSynth()
        tts._callback = _PlaybackCallback()

        tts.stop()
        tts.stop()          # 二次调用不应异常

    @staticmethod
    def test_finish_closes_synth(monkeypatch: pytest.MonkeyPatch) -> None:
        """正常收尾也要关 WS，否则连接会一直累积。"""
        tts = CosyVoiceTTS(api_key="sk-test")
        synth = _FakeSynth()
        cb = _PlaybackCallback()
        cb._completed.set()          # 模拟服务端已发完
        tts._callback = cb
        tts._synthesizer = synth
        # 不真的等排空
        monkeypatch.setattr(cb, "remaining_play_seconds", lambda: 0.0)

        tts.finish()

        assert synth.closed, "finish() 之后 WS 没关，连接泄漏"

    @staticmethod
    def test_finish_keeps_first_package_delay(monkeypatch: pytest.MonkeyPatch) -> None:
        """首包延迟要在 close 之前取到，不能因收尾而丢统计。"""
        tts = CosyVoiceTTS(api_key="sk-test")
        cb = _PlaybackCallback()
        cb._completed.set()
        tts._callback = cb
        tts._synthesizer = _FakeSynth()
        monkeypatch.setattr(cb, "remaining_play_seconds", lambda: 0.0)

        stats = tts.finish()

        assert stats.get("first_package_delay_ms") == 0


# ------------------------------------------ 3. 音频被丢要能降级（不只静默）----

class TestSilentDropDegrades:
    """`StreamTTSPlayer` 必须把「音频被丢」识别为失败并降级。"""

    @staticmethod
    def _player() -> StreamTTSPlayer:
        player = StreamTTSPlayer(api_key="sk-test")
        player._started = True
        return player

    @staticmethod
    def test_direct_send_failure_degrades() -> None:
        """`tts.feed` 直接抛异常 → 降级，剩余文本改整段播。"""
        player = TestSilentDropDegrades._player()
        player._tts._synthesizer = _FakeSynth(fail_on_call=True)

        player._send_sentence("第一句。")

        assert player.is_degraded
        assert "第一句。" in player._fallback_buf

    @staticmethod
    def test_silent_drop_degrades_via_error_flag() -> None:
        """不抛异常、只是把音频丢了 —— 也必须降级。

        这是最阴的一条：WS 已断时 `streaming_call` 往往不报错，音频静默进
        黑洞，文字却照常上屏。旧实现只捕异常，永远发现不了。
        """
        player = TestSilentDropDegrades._player()
        synth = _FakeSynth()
        player._tts._synthesizer = synth
        # 模拟"流已释放、音频被丢"：feed 不抛，但 error 有值
        player._tts._callback = _PlaybackCallback()
        player._tts._callback._stream = None
        player._tts._callback.on_data(b"\x00\x00" * 10)   # 触发 error 记录

        player._send_sentence("第二句。")

        assert player._tts.error
        assert player.is_degraded, "音频被静默丢弃却未降级 → 用户只听到断掉的语音"
        assert "第二句。" in player._fallback_buf

    @staticmethod
    def test_degraded_does_not_increment_sentence_count() -> None:
        """降级的句子不计入句数（统计别虚报）。"""
        player = TestSilentDropDegrades._player()
        player._tts._synthesizer = _FakeSynth(fail_on_call=True)

        player._send_sentence("一句。")

        assert player._sentence_count == 0


class TestThreadSafety:
    """stop 与回调线程并发时不能炸（`_stream` 置 None 的竞态）。"""

    @staticmethod
    def test_release_during_write_does_not_raise() -> None:
        cb = _PlaybackCallback()

        class _SlowStream(_FakeStream):
            def write(self, data: bytes) -> None:  # noqa: ARG002
                raise ValueError("I/O operation on closed pipe")

        cb._stream = _SlowStream()
        cb.on_data(b"\x00\x00" * 100)      # 写入失败
        assert cb.error and "写入扬声器失败" in cb.error

        # 释放后并发再写也不能抛
        stopper = threading.Thread(target=cb.stop)
        stopper.start()
        cb.on_data(b"\x00\x00" * 100)
        stopper.join(timeout=2)
        assert cb._stream is None
