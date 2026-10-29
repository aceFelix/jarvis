"""QwenASR 会话配置与失败回执的行为回归测试（aceFelix）。

锁定 2026-10-28 修复的两个缺陷（真机现象：/voice 每轮报「识别失败: 配置会话超时」，
麦克风一句话都没被识别过）：

1. **死等 session.updated**：`qwen3-asr-flash-realtime` 实测在 update_session 之后
   从不返回 `session.updated`（dashscope SDK 的事件分发里也没有该分支，只有
   session.created / session.finished / error / response.*）。旧实现硬等 10 秒后判
   「配置会话超时」，导致从未进入录音循环。
2. **回执掩盖真相**：服务端整轮零事件（额度不足 / 模型未开通）被报成
   「配置会话超时，再说一次」，与「偶发识别失败」无法区分，用户会反复重试。

对应用例：
- 缺确认但服务端正常工作 → 必须照常录音并返回文本
- 服务端整轮零事件 → 必须如实报「服务端无响应」并提前收尾
- 服务端下发 error 事件 → 回原始错误，不被「无响应」覆盖

@author aceFelix
"""

from __future__ import annotations

import time

import pytest

from agent.voice.stt import qwen as qwen_mod

# ---------------------------------------------------------------- 假 SDK ----


class _FakeDashscope:
    """dashscope 模块替身：listen() 只用到 api_key 赋值。"""

    api_key = ""


class _FakeTranscriptionParams:
    """TranscriptionParams 替身：只记录入参，不做校验。

    ``corpus_text`` / ``corpus`` 与真实 dataclass 一致，默认空串——
    被测代码只在非空时赋值，所以这里必须给出默认值才能断言「未配即不发」。
    """

    corpus_text: str = ""
    corpus: dict | None = None

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class _FakeMultiModality:
    """MultiModality 替身：listen() 只用 TEXT。"""

    TEXT = "text"


class _FakeConversation:
    """OmniRealtimeConversation 替身，按类属性脚本回放服务端事件。

    服务端真实行为用三个开关模拟：
    - ``emit_session_updated``：是否在 update_session 后回 session.updated
    - ``append_events``：每次 append_audio 时下发的事件（只下发一次）
    - ``emit_error``：update_session 后回 error 事件
    """

    emit_session_updated = False
    append_events: list[dict] = []
    emit_error: dict | None = None
    emit_session_finished = True
    instances: list["_FakeConversation"] = []

    def __init__(self, *, model, callback, url, api_key) -> None:
        self.callback = callback
        self.model = model
        self.update_kwargs: dict = {}
        self.consumed_append_script = False
        _FakeConversation.instances.append(self)

    # -- 服务端 → 客户端 --
    def _emit(self, event: dict) -> None:
        self.callback.on_event(event)

    # -- SDK 对外接口 --
    def connect(self) -> None:
        self.callback.on_open()
        # connect 后服务端先下发 session.created（自带完整默认配置）
        self._emit({"type": "session.created", "session": {"id": "sess_fake"}})

    def update_session(self, **kwargs) -> None:
        self.update_kwargs = kwargs
        if _FakeConversation.emit_error is not None:
            self._emit(_FakeConversation.emit_error)
            return
        if _FakeConversation.emit_session_updated:
            self._emit({"type": "session.updated", "session": {"id": "sess_fake"}})

    def append_audio(self, audio_b64: str) -> None:
        if self.consumed_append_script:
            return
        self.consumed_append_script = True
        for event in _FakeConversation.append_events:
            self._emit(event)

    def end_session(self, timeout: int = 20) -> None:
        # 正常服务端在 session.finish 后回 session.finished；链路已死时（
        # emit_session_finished=False）什么都不回，边界与实测一致。
        if _FakeConversation.emit_session_finished:
            self._emit({"type": "session.finished"})

    def close(self) -> None:
        pass


class _FakeStream:
    """pyaudio 输入流替身：返回全零帧，read 立即返回。"""

    def __init__(self, frames_per_buffer: int) -> None:
        self._frames = frames_per_buffer

    def read(self, num_frames, exception_on_overflow=False) -> bytes:  # noqa: ARG002
        return b"\x00\x00" * self._frames

    def stop_stream(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakePyAudioModule:
    """pyaudio 模块替身。"""

    paInt16 = 8

    class PyAudio:  # noqa: N801 - 模拟 pyaudio 的工厂函数命名
        def open(self, **kwargs):  # noqa: ANN003, ANN201
            return _FakeStream(kwargs.get("frames_per_buffer", 3200))


@pytest.fixture(autouse=True)
def _fast_constants(monkeypatch):
    """把三处时间常量压到毫秒级，让行为测试在秒内跑完。"""
    monkeypatch.setattr(qwen_mod, "_VAD_LEAD_IN_SECONDS", 0.01)
    monkeypatch.setattr(qwen_mod, "_SESSION_CONFIRM_TIMEOUT_S", 0.05)
    monkeypatch.setattr(qwen_mod, "_SERVER_SILENCE_TIMEOUT_S", 0.4)


@pytest.fixture(autouse=True)
def _fake_sdk(monkeypatch):
    """把重型依赖换成替身，并把脚本状态重置为「服务端健康」。"""
    _FakeConversation.instances = []
    _FakeConversation.emit_session_updated = False
    _FakeConversation.append_events = []
    _FakeConversation.emit_error = None
    _FakeConversation.emit_session_finished = True

    monkeypatch.setattr(
        qwen_mod,
        "_import_qwen_omni",
        lambda: (
            _FakeDashscope,
            _FakeConversation,
            None,
            _FakeTranscriptionParams,
            _FakeMultiModality,
        ),
    )
    monkeypatch.setattr(qwen_mod, "_import_pyaudio", lambda: _FakePyAudioModule)
    monkeypatch.setattr(qwen_mod, "_is_stopped", lambda: False)
    return _FakeConversation


def _make_stt() -> qwen_mod.QwenASR:
    return qwen_mod.QwenASR(api_key="sk-test", model="qwen3-asr-flash-realtime")


# ------------------------------------------------------------------ 用例 ----


class TestSessionConfigureIsNotFatal:
    """session.updated 缺失不再判失败。"""

    def test_missing_session_updated_still_records_and_returns_text(self, _fake_sdk) -> None:
        """服务端不回 session.updated，但 VAD 与转写正常 —— 必须照常返回文本。

        这正是旧实现失败的场景：它把「没等到确认」当成配置失败，
        在进入录音前就 return 了。
        """
        _fake_sdk.emit_session_updated = False  # 服务端就是不回该事件
        _fake_sdk.append_events = [
            {"type": "input_audio_buffer.speech_started"},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "你好贾维斯",
            },
        ]

        result = _make_stt().listen(max_seconds=2.0)

        assert result.get("error") is None, f"不应再报配置失败: {result.get('error')}"
        assert result["text"] == "你好贾维斯"

    def test_transcribe_file_no_longer_requires_session_updated(self, _fake_sdk, tmp_path) -> None:
        """transcribe_file 同样不因缺确认而失败（旧实现会返回「配置会话超时」）。"""
        _fake_sdk.emit_session_updated = False
        audio = tmp_path / "a.pcm"
        audio.write_bytes(b"\x00\x00" * 1600)

        result = _make_stt().transcribe_file(str(audio))

        assert result.get("error") != "配置会话超时", "仍在校验 session.updated"
        assert "error" not in result or "超时" not in str(result["error"])


class TestServerSilenceIsReported:
    """服务端整轮零事件必须被如实报出。"""

    def test_zero_events_reports_unresponsive(self, _fake_sdk) -> None:
        """录音期间服务端一个事件都没给 —— 报「服务端无响应」而不是「再说一次」。"""
        _fake_sdk.emit_session_updated = False
        _fake_sdk.append_events = []  # 连 speech_started 都没有

        result = _make_stt().listen(max_seconds=5.0)

        assert result["text"] == ""
        error = result.get("error") or ""
        assert "服务端无响应" in error, f"回执未说明真实原因: {error!r}"
        # 必须能指向额度/开通这个可排查方向，否则用户仍会当成偶发失败
        assert "额度" in error or "开通" in error

    def test_silence_ends_before_max_seconds(self, _fake_sdk) -> None:
        """零事件时提前收尾，不让用户对着死连接干等满 max_seconds。"""
        _fake_sdk.emit_session_updated = False
        _fake_sdk.append_events = []

        started = time.time()
        _make_stt().listen(max_seconds=30.0)  # /voice 下真实值是 300 秒
        elapsed = time.time() - started

        assert elapsed < 5.0, f"未提前收尾，耗时 {elapsed:.1f}s"

    def test_error_event_takes_precedence_over_silence(self, _fake_sdk) -> None:
        """服务端明确下发 error 时回原始错误，不被「服务端无响应」覆盖。"""
        _fake_sdk.emit_error = {
            "type": "error",
            "error": {"code": "Arrearage", "message": "Access denied"},
        }

        result = _make_stt().listen(max_seconds=2.0)

        assert result["text"] == ""
        assert "Access denied" in (result.get("error") or "")


class TestCallbackEventCounter:
    """回调事件计数是「链路是否在工作」的判据基础。"""

    def test_event_count_ignores_non_dict(self) -> None:
        """非 dict 事件不计入，避免污染静默判断。"""
        cb = qwen_mod._QwenASRCallback()
        assert cb.event_count == 0
        cb.on_event({"type": "session.created"})
        assert cb.event_count == 1
        cb.on_event(None)  # type: ignore[arg-type]
        assert cb.event_count == 1


class TestHotwordsReachServer:
    """唤醒词热词必须真的随 session.update 下发（上下文增强）。

    背景（2026-10）：用户抱怨「喊贾维斯没反应」。除了做拼音同音兜底，
    更治本的是把唤醒词当热词告诉识别模型，让它一开始就倾向于输出「贾维斯」
    而不是近音字。SDK 侧走 ``TranscriptionParams.corpus_text``，
    最终拼成 ``input_audio_transcription.corpus.text``。
    """

    def test_hotwords_passed_as_corpus_text(self, _fake_sdk) -> None:
        """配了热词 → corpus_text 必须被赋上（SDK 会包成 corpus.text 下发）。"""
        qwen_mod.QwenASR(
            api_key="sk-test", hotwords="贾维斯",
        ).listen(max_seconds=0.5)

        params = _fake_sdk.instances[-1].update_kwargs["transcription_params"]
        assert params.corpus_text == "贾维斯"

    def test_no_hotwords_keeps_payload_unchanged(self, _fake_sdk) -> None:
        """未配热词 → 不下发该字段，报文与改造前完全一致。"""
        qwen_mod.QwenASR(api_key="sk-test").listen(max_seconds=0.5)

        params = _fake_sdk.instances[-1].update_kwargs["transcription_params"]
        assert params.corpus_text == ""

    def test_blank_hotwords_treated_as_absent(self, _fake_sdk) -> None:
        """只有空白的热词串等于没配，不能发出空语料。"""
        qwen_mod.QwenASR(api_key="sk-test", hotwords="   ").listen(max_seconds=0.5)

        params = _fake_sdk.instances[-1].update_kwargs["transcription_params"]
        assert params.corpus_text == ""

    def test_create_stt_forwards_hotwords(self) -> None:
        """工厂函数不能把 hotwords 吃掉（voice_loop 就靠它下发）。"""
        from agent.voice.stt import create_stt

        assert create_stt(api_key="k", hotwords="贾维斯")._hotwords == "贾维斯"
