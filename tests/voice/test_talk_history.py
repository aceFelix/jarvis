"""终端 ``/talk`` 转写落库适配器与命令接线测试。

背景（2026-11-09 用户要求）：实时双工对话发生在 DashScope 实时通道内，
不经 ``QueryLoop``，此前终端 ``/talk`` 的内容退出即丢；现在由
``TalkHistoryRecorder`` 按轮配对后写入会话历史，并复用 ``advance_session``
（与文本、``/voice`` 共用保存 + 15 字标题口径）。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent.voice.talk_history import TalkHistoryRecorder


class StubUI:
    """最小 UI 替身：记录转写/增量调用，其余输出忽略。"""

    def __init__(self) -> None:
        self.user_lines: list[str] = []
        self.ai_lines: list[str] = []
        self.deltas: list[str] = []

    def on_user_speaking(self, speaking: bool) -> None:
        self.user_lines.append("speaking" if speaking else "silent")

    def on_user_transcript(self, text: str) -> None:
        self.user_lines.append(text)

    def on_ai_transcript(self, text: str) -> None:
        self.ai_lines.append(text)

    def on_ai_transcript_delta(self, text: str) -> None:
        self.deltas.append(text)

    def info(self, text: str) -> None:  # 透传验证用
        self.user_lines.append(f"info:{text}")


def _recorder(ui: StubUI | None = None) -> tuple[TalkHistoryRecorder, StubUI, list]:
    """构造适配器：返回 (recorder, ui, 落库调用列表)。"""
    stub = ui or StubUI()
    commits: list[tuple[str | None, str | None]] = []
    recorder = TalkHistoryRecorder(stub, lambda u, a: commits.append((u, a)))
    return recorder, stub, commits


class TestTalkHistoryRecorder:
    """转写配对规则（与桌面 WorkbenchRealtimeUI 同口径）。"""

    def test_normal_order_pairs_question_and_answer(self) -> None:
        """用户转写先到 → AI 转写到达即配对 [问, 答]。"""
        recorder, ui, commits = _recorder()
        recorder.on_user_speaking(True)
        recorder.on_user_transcript("帮我查一下天气")
        recorder.on_ai_transcript("今天晴，26 度")
        assert commits == [("帮我查一下天气", "今天晴，26 度")]
        # 同时原样上屏
        assert "帮我查一下天气" in ui.user_lines
        assert ui.ai_lines == ["今天晴，26 度"]

    def test_lagging_transcript_pairs_in_order(self) -> None:
        """转写滞后：回答先到（用户已开口）→ 暂存，等用户转写再按 [问, 答] 落库。"""
        recorder, _ui, commits = _recorder()
        recorder.on_user_speaking(True)
        recorder.on_ai_transcript("好的")
        assert commits == []
        recorder.on_user_transcript("打开记事本")
        assert commits == [("打开记事本", "好的")]

    def test_greeting_without_user_speech_is_persisted_alone(self) -> None:
        """用户从未开口：开场白/主动播报单独落库。"""
        recorder, _ui, commits = _recorder()
        recorder.on_ai_transcript("我在，先生")
        assert commits == [(None, "我在，先生")]

    def test_echo_turn_is_dropped(self) -> None:
        """转写与 AI 刚播内容重合（外放回声）→ 丢弃，不落库。"""
        recorder, _ui, commits = _recorder()
        recorder.on_user_speaking(True)
        recorder.on_user_transcript("收到")
        recorder.on_ai_transcript("好的，已经帮你记下了")
        assert len(commits) == 1
        # 回声轮：与上一条 AI 播报完全一致
        recorder.on_user_transcript("好的，已经帮你记下了")
        assert len(commits) == 1

    def test_interrupted_question_is_persisted_before_new_one(self) -> None:
        """上一问未获回答又开口：旧问句先单独落库，再配对新一轮。"""
        recorder, _ui, commits = _recorder()
        recorder.on_user_speaking(True)
        recorder.on_user_transcript("第一问")
        recorder.on_user_transcript("第二问")
        assert commits == [("第一问", None)]
        recorder.on_ai_transcript("回答第二问")
        assert commits == [("第一问", None), ("第二问", "回答第二问")]

    def test_flush_writes_pending_turn(self) -> None:
        """退出时把仍在缓冲里的残轮写入（否则永久丢失）。"""
        recorder, _ui, commits = _recorder()
        recorder.on_user_speaking(True)
        recorder.on_user_transcript("还没回答的一问")
        recorder.flush_pending_transcripts()
        assert commits == [("还没回答的一问", None)]
        # 已清空：再次 flush 不重复落库
        recorder.flush_pending_transcripts()
        assert len(commits) == 1

    def test_passthrough_and_delta(self) -> None:
        """未覆盖的属性透传；UI 缺少 on_ai_transcript_delta 时不报错。"""
        recorder, ui, _commits = _recorder()
        recorder.info("hi")
        assert ui.user_lines[-1] == "info:hi"
        recorder.on_ai_transcript_delta("流式")
        assert ui.deltas == ["流式"]

        class _NoDeltaUI(StubUI):
            on_ai_transcript_delta = None  # 模拟旧 UI 无此方法

        recorder2 = TalkHistoryRecorder(_NoDeltaUI(), lambda _u, _a: None)
        recorder2.on_ai_transcript_delta("x")  # 不应抛异常


class TestRealtimeTalkPersistence:
    """``/talk`` 命令接线：转写写进 messages 并触发统一收尾。"""

    def test_talk_turns_are_appended_and_persisted(self, monkeypatch) -> None:
        import agent.commands.handlers.voice_commands as vc
        import agent.config.voice_key as vk
        import agent.voice.realtime_talk as rt_mod

        monkeypatch.setattr(vk, "resolve", lambda _settings: "key")

        class _FakeTalk:
            def __init__(self, **_kwargs) -> None:
                pass

            async def run(self, ui) -> None:
                # 模拟一轮正常问答（服务端事件顺序）
                ui.on_user_speaking(True)
                ui.on_user_transcript("你好贾维斯")
                ui.on_ai_transcript("在的，先生")

        monkeypatch.setattr(rt_mod, "RealtimeTalk", _FakeTalk)

        async def _fake_advance(ui, messages, **kwargs):
            # 不落盘、不调 LLM：只验证接线与轮数推进
            return kwargs["session_name"], kwargs["dialog_count"] + 1, kwargs["title_generated"]

        import agent.session_manager as sm

        monkeypatch.setattr(sm, "advance_session", _fake_advance)

        settings = SimpleNamespace(workdir="/wd", provider="p", model="m")
        messages: list = []
        cmd_ctx = SimpleNamespace(
            ui=StubUI(),
            settings=settings,
            messages=messages,
            session_name="session-old",
            dialog_count=0,
            title_generated=False,
            provider=None,
            model="m",
            ctx=SimpleNamespace(extra={}),
        )
        asyncio.run(vc._realtime_talk(cmd_ctx.ui, settings, cmd_ctx=cmd_ctx))

        assert [m.role for m in messages] == ["user", "assistant"]
        assert messages[0].get_text() == "你好贾维斯"
        assert messages[1].get_text() == "在的，先生"
        assert cmd_ctx.dialog_count == 1

    def test_no_cmd_ctx_keeps_plain_ui(self, monkeypatch) -> None:
        """未传 cmd_ctx（旧调用点）：UI 原样传入，不落库、不报错。"""
        import agent.commands.handlers.voice_commands as vc
        import agent.config.voice_key as vk
        import agent.voice.realtime_talk as rt_mod

        monkeypatch.setattr(vk, "resolve", lambda _settings: "key")
        seen: dict = {}

        class _FakeTalk:
            def __init__(self, **_kwargs) -> None:
                pass

            async def run(self, ui) -> None:
                seen["ui"] = ui
                ui.on_user_transcript("随口一句")

        monkeypatch.setattr(rt_mod, "RealtimeTalk", _FakeTalk)

        ui = StubUI()
        asyncio.run(vc._realtime_talk(ui, SimpleNamespace(workdir="/wd")))
        assert seen["ui"] is ui
