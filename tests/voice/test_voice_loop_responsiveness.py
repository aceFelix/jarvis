"""语音循环事件循环饿死修复回归测试（桌面壳按钮无响应 bug）。

背景（见 docs/fixlogs/voice-stop-unresponsive-fix.md）：stt.listen /
StreamTTSPlayer.start / finish 原是同步阻塞 pyaudio / 网络调用，直接在
async 函数里调用会饿死宿主事件循环；serve 宿主下引擎指令循环（消费
voice.stop / voice.interrupt / start_talk 队列）被录音播放卡死，桌面壳
「退出语音 / 文本 / 实时 / 打断」按钮全部无响应。修复统一改
asyncio.to_thread 工作线程跑阻塞 I/O，并用 stop_event 与 stt 停止标志
组合区分「退出意图」与「打断意图」。

覆盖：
- _voice_loop_round 录音期间宿主事件循环仍可调度（心跳协程持续 tick）；
- 停止标志 + stop_event → 抛 KeyboardInterrupt（退出意图，voice.stop / Ctrl+C）；
- 仅停止标志（voice.interrupt）→ 返回 True 继续聆听，不误杀会话；
- voice_loop 全链路：聆听阻塞期间 stop_event 仍能及时终止会话且循环不饿死。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from agent.voice import stt as stt_module
from agent.voice.voice_events import STATE_EXITED, CollectingVoiceEvents


def _settings() -> SimpleNamespace:
    """最小 settings 假件：仅含语音循环触及的字段。"""
    return SimpleNamespace(
        api_key="test-key",
        verbose=False,
        voice_max_seconds=5,
        stt_silence_seconds=1.0,
        stt_silence_threshold=100,
        stt_model="fake-stt",
        tts_model="fake-tts",
        tts_voice="fake-voice",
        tts_volume=50,
        tts_speech_rate=1.0,
        tts_pitch_rate=1.0,
    )


class _BlockingFakeSTT:
    """假 STT：listen 同步阻塞到 stop 标志或超时（模拟 pyaudio 阻塞录音）。

    阻塞发生在调用方线程（修复后为 to_thread 工作线程），期间若宿主事件
    循环未被饿死，其他协程应能正常调度。
    """

    def __init__(self, block_seconds: float = 0.3, text: str = "") -> None:
        self.block_seconds = block_seconds
        self.text = text
        self.listen_calls = 0

    def listen(self, **kwargs):
        self.listen_calls += 1
        deadline = time.time() + self.block_seconds
        while time.time() < deadline and not stt_module._is_stopped():
            time.sleep(0.02)
        return {"text": self.text}


class _FakeQueryLoop:
    """最小 QueryLoop 假件：voice_loop 仅用思考开关与 _system 属性。"""

    def __init__(self) -> None:
        self._system = ""
        self._thinking = False

    def is_thinking_enabled(self) -> bool:
        return self._thinking

    def set_thinking_enabled(self, on: bool) -> None:
        self._thinking = on

    async def run(self, text, ctx, images=None):
        return SimpleNamespace(stopped_reason="end", iterations=1, tool_calls=0)


class _FakeCtx:
    """最小 ToolContext 假件：消息列表 + abort_event + feeder 挂点。"""

    def __init__(self) -> None:
        self.messages = []
        self.abort_event = asyncio.Event()
        self.on_assistant_text = None
        self.tts_ok = True


class TestRoundKeepsLoopFree:
    def test_loop_not_starved_during_listen(self) -> None:
        """录音阻塞 0.3s 期间心跳协程应持续 tick（修复前同步 listen 为 0）。"""
        from agent.voice.voice_loop import _voice_loop_round

        async def _run() -> int:
            ticks = 0

            async def _heartbeat() -> None:
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.05)
                    ticks += 1

            stt_module._reset_stop()
            hb = asyncio.create_task(_heartbeat())
            try:
                await asyncio.wait_for(
                    _voice_loop_round(
                        CollectingVoiceEvents(), _settings(), _FakeQueryLoop(),
                        _FakeCtx(), None, _BlockingFakeSTT(block_seconds=0.3),
                        threading.Event(), threading.Event(),
                    ),
                    timeout=5,
                )
            finally:
                hb.cancel()
                stt_module._reset_stop()
            return ticks

        ticks = asyncio.run(_run())
        assert ticks >= 2


class TestStopVsInterruptIntent:
    """停止标志的两种意图：stop_event 置位=退出，仅标志=打断继续聆听。"""

    @staticmethod
    async def _run_round(stop_set: bool):
        """跑一轮录音被中断的 round；返回 True=抛 KI（退出），None=正常返回（继续）。"""
        from agent.voice.voice_loop import _voice_loop_round
        stt_module._reset_stop()
        stt_module._request_stop()  # 模拟 voice.stop / interrupt 中断了阻塞录音
        stop_event = threading.Event()
        if stop_set:
            stop_event.set()
        try:
            await _voice_loop_round(
                CollectingVoiceEvents(), _settings(), _FakeQueryLoop(),
                _FakeCtx(), None, _BlockingFakeSTT(block_seconds=5),
                threading.Event(), stop_event,
            )
            return None
        except KeyboardInterrupt:
            return True
        finally:
            stt_module._reset_stop()

    def test_stop_event_raises_exit(self) -> None:
        """voice.stop / Ctrl+C（stop_event 置位）：抛 KeyboardInterrupt 退出。"""
        assert asyncio.run(self._run_round(True)) is True

    def test_interrupt_only_continues_listening(self) -> None:
        """voice.interrupt（仅停止标志）：返回 True 继续聆听，不误杀会话。"""
        assert asyncio.run(self._run_round(False)) is None


class TestVoiceLoopStopDuringListen:
    def test_stop_event_ends_session_and_loop_free(self, monkeypatch) -> None:
        """全链路：聆听阻塞期间 stop_event + 停止标志能及时终止会话且循环不饿死。"""
        import agent.voice as voice_pkg
        import agent.voice.tts as tts_mod
        import agent.voice.voice_state as voice_state
        from agent.voice.voice_loop import voice_loop

        stt = _BlockingFakeSTT(block_seconds=30)  # 长录音：只能被停止标志中断
        monkeypatch.setattr(voice_pkg, "create_stt", lambda **kw: stt)
        monkeypatch.setattr(
            tts_mod, "CosyVoiceTTS",
            lambda **kw: SimpleNamespace(stop=lambda: None),
        )
        # 隔离跨进程语音锁：避免本机恰有 jarvis 语音会话时测试误败
        monkeypatch.setattr(voice_state, "acquire_voice_lock", lambda: (True, ""))
        monkeypatch.setattr(voice_state, "release_voice_lock", lambda: None)

        async def _run() -> tuple[int, list]:
            ticks = 0

            async def _heartbeat() -> None:
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.05)
                    ticks += 1

            stt_module._reset_stop()
            events = CollectingVoiceEvents()
            stop_event = threading.Event()
            hb = asyncio.create_task(_heartbeat())
            task = asyncio.create_task(
                voice_loop(
                    events, _settings(), _FakeQueryLoop(), _FakeCtx(),
                    stop_event=stop_event, interrupt_event=threading.Event(),
                )
            )
            await asyncio.sleep(0.2)  # 进入聆听阶段，录音开始阻塞
            # 模拟 engine._handle_stop_voice：置 stop_event + 中断阻塞录音
            stop_event.set()
            stt_module._request_stop()
            try:
                await asyncio.wait_for(task, timeout=3)
            finally:
                hb.cancel()
                stt_module._reset_stop()
            return ticks, events.states()

        ticks, states = asyncio.run(_run())
        assert ticks >= 2  # 聆听期间事件循环未被饿死（指令可被消费）
        assert states[-1] == STATE_EXITED  # 会话干净退出
