"""/voice 解耦（二期）测试：事件协议 + 双适配器 + serve 协议契约。

覆盖：
- ``VoiceSessionEvents`` 协议与 ``CollectingVoiceEvents`` 收集顺序；
- ``ServeVoiceAdapter``：voice_loop 事件 → ``voice_*`` WS 事件外抛序列；
- ``RichCLIVoiceAdapter``：事件 → RichCLI 输出 / 内部渲染状态映射（REPL 保底）；
- 解耦签名回归：voice_loop / _voice_loop_round 不再接收 RichCLI，改收
  events + interrupt_event；
- serve 协议契约：voice.start/stop/interrupt 指令与 voice_* 事件常量就位，
  且 engine 字面量与 protocol 常量一致（防漂移）。

@author aceFelix
"""

from __future__ import annotations

import inspect
import io
import sys
import threading

import pytest

from agent.voice.voice_events import (
    LEVEL_WARN,
    STATE_LISTENING,
    STATE_SPEAKING,
    STATE_THINKING,
    CollectingVoiceEvents,
    VoiceSessionEvents,
)


class _FakeEmitter:
    """收集 emit(name, payload) 的假事件发射器（断言外抛序列）。"""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, object]] = []

    def emit(self, name: str, payload: object = "") -> None:
        self.emitted.append((name, payload))

    def names(self) -> list[str]:
        return [n for n, _ in self.emitted]


class _FakeUI:
    """收集 info/warn/error 与内部渲染状态的假 RichCLI。"""

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warns: list[str] = []
        self.errors: list[str] = []
        self._voice_mode = False
        self._voice_tts_feed = None
        self._thinking_started = False
        self._thinking_live = None
        self._thinking_buf = ""

    def info(self, msg: str) -> None:
        self.infos.append(msg)

    def warn(self, msg: str) -> None:
        self.warns.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)


# ---- 协议与收集实现 ----

class TestVoiceEventsProtocol:
    def test_collecting_records_order(self) -> None:
        """CollectingVoiceEvents 应按调用顺序记录各类事件。"""
        ev = CollectingVoiceEvents()
        assert isinstance(ev, VoiceSessionEvents)
        ev.on_state(STATE_LISTENING)
        ev.on_user_transcript("你好")
        ev.on_ai_text_delta("你")
        ev.on_ai_text("你好呀")
        ev.on_info("提示")
        ev.on_error("出错")
        kinds = [k for k, _ in ev.records]
        assert kinds == [
            "state", "user_transcript", "ai_text_delta",
            "ai_text", "info", "error",
        ]
        assert ev.states() == [STATE_LISTENING]

    def test_base_default_noop(self) -> None:
        """VoiceEventsBase 默认实现静默不抛异常。"""
        from agent.voice.voice_events import VoiceEventsBase
        base = VoiceEventsBase()
        base.on_state("x")
        base.on_user_partial("p")
        base.on_info("i", LEVEL_WARN)
        base.on_error("e")


# ---- serve 适配器 ----

class TestServeVoiceAdapter:
    def _adapter(self) -> tuple[object, _FakeEmitter]:
        from agent.ui.workbench.engine import ServeVoiceAdapter
        em = _FakeEmitter()
        return ServeVoiceAdapter(em), em

    def test_state_and_text_events(self) -> None:
        """状态/转录/流式文本应外抛为 voice_* 事件。"""
        ad, em = self._adapter()
        ad.on_state(STATE_THINKING)
        ad.on_user_transcript("提醒我喝水")
        ad.on_ai_text_delta("好的")
        ad.on_ai_text("好的，已提醒")
        assert em.emitted == [
            ("voice_state", STATE_THINKING),
            ("voice_user_transcript", "提醒我喝水"),
            ("voice_ai_text_delta", "好的"),
            ("voice_ai_text", "好的，已提醒"),
        ]

    def test_info_warn_error_channels(self) -> None:
        """info/warn 复用 info/warn 通道，error 复用 error 通道。"""
        ad, em = self._adapter()
        ad.on_info("普通提示")
        ad.on_info("告警", LEVEL_WARN)
        ad.on_error("失败")
        assert em.emitted == [
            ("info", "普通提示"),
            ("warn", "告警"),
            ("error", "失败"),
        ]

    def test_full_round_sequence(self) -> None:
        """一轮 听→想→说 的状态迁移序列应完整外抛。"""
        ad, em = self._adapter()
        for st in (STATE_LISTENING, STATE_THINKING, STATE_SPEAKING):
            ad.on_state(st)
        assert em.names() == ["voice_state", "voice_state", "voice_state"]
        assert [p for _, p in em.emitted] == [
            STATE_LISTENING, STATE_THINKING, STATE_SPEAKING,
        ]


# ---- REPL 适配器（保底） ----

class TestRichCLIVoiceAdapter:
    def _adapter(self) -> tuple[object, _FakeUI]:
        from agent.voice.repl_adapter import RichCLIVoiceAdapter
        ui = _FakeUI()
        return RichCLIVoiceAdapter(ui), ui

    def test_thinking_state_sets_voice_mode(self) -> None:
        """thinking 状态应开启 RichCLI _voice_mode，其余状态关闭。"""
        ad, ui = self._adapter()
        ad.on_state(STATE_THINKING)
        assert ui._voice_mode is True
        ad.on_state(STATE_SPEAKING)
        assert ui._voice_mode is False

    def test_info_warn_error_mapping(self) -> None:
        """info/warn/error 应映射到 RichCLI 对应方法。"""
        ad, ui = self._adapter()
        ad.on_info("提示")
        ad.on_info("告警", LEVEL_WARN)
        ad.on_error("错误")
        assert ui.infos == ["提示"]
        assert ui.warns == ["告警"]
        assert ui.errors == ["错误"]

    def test_transcript_prints_and_partial_clears(self, monkeypatch) -> None:
        """识别全文应打印；空 partial 应清进度行（写 stdout）。"""
        ad, ui = self._adapter()
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        ad.on_user_partial("你")
        ad.on_user_partial("")  # 清行
        ad.on_user_transcript("你好")
        out = buf.getvalue()
        assert "识别中: 你" in out
        assert "🧑 你说: 你好" in ui.infos

    def test_key_watcher_unavailable_returns_false(self, monkeypatch) -> None:
        """keyboard 不可用时 start_key_watcher 返回 False 且不启动监听。"""
        import agent.voice.repl_adapter as ra

        class _FakeWatcher:
            available = False

            def __init__(self, cb) -> None:
                self.cb = cb

            def start(self) -> None:
                raise AssertionError("unavailable watcher must not start")

            def stop(self) -> None:
                pass

        monkeypatch.setattr(ra, "_KeyBargeInWatcher", _FakeWatcher)
        ad, _ = self._adapter()
        assert ad.start_key_watcher(threading.Event()) is False
        ad.stop_key_watcher()  # 未启动时 stop 也应安全

    def test_key_watcher_callback_sets_interrupt(self, monkeypatch) -> None:
        """ESC 回调应置位 interrupt_event 并中断阻塞的 stt.listen。"""
        import agent.voice.repl_adapter as ra
        from agent.voice import stt as stt_mod

        captured: dict = {}

        class _FakeWatcher:
            available = True

            def __init__(self, cb) -> None:
                captured["cb"] = cb
                self.started = False

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                pass

        monkeypatch.setattr(ra, "_KeyBargeInWatcher", _FakeWatcher)
        ad, _ = self._adapter()
        ev = threading.Event()
        try:
            assert ad.start_key_watcher(ev) is True
            assert not ev.is_set()
            captured["cb"]()  # 模拟按下 ESC
            assert ev.is_set()
            assert stt_mod._is_stopped()  # 回调应中断 stt
        finally:
            stt_mod._reset_stop()
            ad.stop_key_watcher()


# ---- 解耦签名回归 ----

class TestDecoupledSignature:
    def test_voice_loop_has_no_ui_param(self) -> None:
        """voice_loop 签名应含 events/interrupt_event 且不再含 ui。"""
        from agent.voice.voice_loop import voice_loop
        params = inspect.signature(voice_loop).parameters
        assert "events" in params
        assert "interrupt_event" in params
        assert "stop_event" in params
        assert "ui" not in params

    def test_round_has_interrupt_event(self) -> None:
        """_voice_loop_round 应接收 interrupt_event 且不再含 ui。"""
        from agent.voice.voice_loop import _voice_loop_round
        params = inspect.signature(_voice_loop_round).parameters
        assert "interrupt_event" in params
        assert "events" in params
        assert "ui" not in params

    def test_voice_loop_no_richcli_import(self) -> None:
        """voice_loop 模块不应再 import RichCLI / 键盘 watcher（解耦彻底性）。

        只查 import 语句（docstring 提及 RichCLI 属正常说明，不算耦合）。
        """
        import agent.voice.voice_loop as mod
        src = inspect.getsource(mod)
        assert "from agent.ui.cli import" not in src
        assert "from agent.voice.barge_in import" not in src
        assert "_KeyBargeInWatcher" not in src


# ---- serve 协议契约 ----

class TestServeProtocolVoice:
    def test_voice_commands_registered(self) -> None:
        """voice.start/stop/interrupt 应进入桌面指令集合。"""
        from agent.serve import protocol
        assert protocol.CMD_VOICE_START == "voice.start"
        assert protocol.CMD_VOICE_STOP == "voice.stop"
        assert protocol.CMD_VOICE_INTERRUPT == "voice.interrupt"
        for cmd in ("voice.start", "voice.stop", "voice.interrupt"):
            assert cmd in protocol.DESKTOP_COMMANDS

    def test_voice_event_constants(self) -> None:
        """voice_* 事件常量应就位。"""
        from agent.serve import protocol
        assert protocol.EVT_VOICE_STARTED == "voice_started"
        assert protocol.EVT_VOICE_STOPPED == "voice_stopped"
        assert protocol.EVT_VOICE_STATE == "voice_state"
        assert protocol.EVT_VOICE_USER_TRANSCRIPT == "voice_user_transcript"
        assert protocol.EVT_VOICE_AI_TEXT_DELTA == "voice_ai_text_delta"
        assert protocol.EVT_VOICE_AI_TEXT == "voice_ai_text"

    def test_engine_literals_match_protocol(self) -> None:
        """engine 的字面量事件名应与 protocol 常量一致（防漂移）。"""
        from agent.serve import protocol
        from agent.ui.workbench import engine
        assert engine._EVT_VOICE_STARTED == protocol.EVT_VOICE_STARTED
        assert engine._EVT_VOICE_STOPPED == protocol.EVT_VOICE_STOPPED
        assert engine._EVT_VOICE_STATE == protocol.EVT_VOICE_STATE
        assert engine._EVT_VOICE_USER_TRANSCRIPT == protocol.EVT_VOICE_USER_TRANSCRIPT
        assert engine._EVT_VOICE_AI_TEXT_DELTA == protocol.EVT_VOICE_AI_TEXT_DELTA
        assert engine._EVT_VOICE_AI_TEXT == protocol.EVT_VOICE_AI_TEXT

    def test_api_exposes_voice_methods(self) -> None:
        """WorkbenchAPI 应暴露 start_voice/stop_voice/interrupt_voice。"""
        from agent.ui.workbench.api import WorkbenchAPI
        for name in ("start_voice", "stop_voice", "interrupt_voice"):
            assert callable(getattr(WorkbenchAPI, name))


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
