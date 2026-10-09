"""实时语音转写落库测试。

覆盖两条链路：
- WorkbenchRealtimeUI 转写配对缓冲：正常顺序 / 转写滞后乱序 / 开场白 /
  回声轮过滤 / 打断轮 / 停止收尾 flush（owner 用记录桩，纯同步验证）
- ChatEngine.commit_voice_turn：消息追加 + _auto_save 触发（全程
  monkeypatch，不触真实用户目录与 LLM）

背景：桌面实时语音对话发生在 DashScope 实时通道内、不经 QueryLoop，
此前语音内容从不进会话历史（恢复会话只剩文本轮，2026-10 修复）。

@author aceFelix
"""

from __future__ import annotations

import queue

import pytest

from agent.config.settings import Settings
from agent.ui.workbench.bridge import WorkbenchRealtimeUI, _EventEmitter
from agent.ui.workbench.engine import ChatEngine


class _OwnerSpy:
    """记录 commit_voice_turn 调用的桩 owner（user, ai) 列表。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None]] = []

    def commit_voice_turn(self, user: str | None, ai: str | None) -> None:
        self.calls.append((user, ai))


def _make_ui(owner: _OwnerSpy | None = None) -> tuple[WorkbenchRealtimeUI, queue.Queue, _OwnerSpy]:
    """构造带桩 owner 的适配器与事件队列，便于同时断言事件透传。"""
    q: queue.Queue = queue.Queue()
    spy = owner or _OwnerSpy()
    return WorkbenchRealtimeUI(_EventEmitter(q), owner=spy), q, spy


# ---- 配对：正常顺序 ----

def test_normal_order_pairs_question_and_answer():
    """用户转写先到、回答后到：配对成 (问, 答) 单次落库。"""
    ui, _q, spy = _make_ui()
    ui.on_user_transcript("在吗？")
    assert spy.calls == []  # 回答未到，先不落库
    ui.on_ai_transcript("在的，先生。")
    assert spy.calls == [("在吗？", "在的，先生。")]


# ---- 配对：转写滞后（输入转写晚于回复转写） ----

def test_lagged_transcript_keeps_question_before_answer():
    """回答先到（用户开过口）→ 暂存；用户转写一到按 (问, 答) 顺序落库。"""
    ui, _q, spy = _make_ui()
    ui.on_user_speaking(True)  # 服务端 speech_started：用户真实开口
    ui.on_ai_transcript("我在呢～")  # 回答转写先到
    assert spy.calls == []
    ui.on_user_transcript("你好")  # 输入转写滞后到达
    assert spy.calls == [("你好", "我在呢～")]


def test_lagged_without_speech_signal_is_treated_as_proactive():
    """回答先到但用户从未开口：视为主动播报，立即单独落库（不悬等配对）。"""
    ui, _q, spy = _make_ui()
    ui.on_ai_transcript("定时提醒：该喝水了")
    assert spy.calls == [(None, "定时提醒：该喝水了")]


# ---- 开场白 ----

def test_greeting_persists_standalone_and_next_turn_pairs_normally():
    """开场白单独落库；其后第一轮真实对话仍正常配对（问在上答在下）。"""
    ui, _q, spy = _make_ui()
    ui.on_ai_transcript("晚上好，先生。")
    assert spy.calls == [(None, "晚上好，先生。")]
    ui.on_user_transcript("今天天气如何？")
    ui.on_ai_transcript("晴，26 度。")
    assert spy.calls[-1] == ("今天天气如何？", "晴，26 度。")


# ---- 回声轮 ----

def test_echo_turn_is_filtered_out():
    """用户转写与 AI 刚播内容重合（外放回声被误转写）：丢弃不落库。"""
    ui, _q, spy = _make_ui()
    ui.on_ai_transcript("晚上好，先生。系统一切正常。")
    ui.on_user_transcript("晚上好，先生。系统一切正常")  # 回声伪轮
    assert spy.calls == [(None, "晚上好，先生。系统一切正常。")]
    ui.flush_pending_transcripts()  # 回声不应留在缓冲里
    assert len(spy.calls) == 1


# ---- 打断轮 ----

def test_interrupted_question_persisted_before_new_turn():
    """前一问未获回复（被打断）又开新口：旧问句先单独落库，新轮正常配对。"""
    ui, _q, spy = _make_ui()
    ui.on_user_transcript("帮我打开——")
    ui.on_user_transcript("算了，现在几点？")
    ui.on_ai_transcript("现在是 21 点。")
    assert spy.calls == [("帮我打开——", None), ("算了，现在几点？", "现在是 21 点。")]


# ---- 收尾 flush ----

def test_flush_persists_unpaired_question_at_stop():
    """停止语音时缓冲里还挂着一问（回答没来）：flush 单独落库，不重复。"""
    ui, _q, spy = _make_ui()
    ui.on_user_transcript("最后一句")
    assert spy.calls == []
    ui.flush_pending_transcripts()
    assert spy.calls == [("最后一句", None)]
    ui.flush_pending_transcripts()  # 二次 flush 幂等
    assert spy.calls == [("最后一句", None)]


def test_flush_persists_staged_lagged_answer():
    """滞后场景下立即停止：flush 把 (问, 答) 补齐落库。"""
    ui, _q, spy = _make_ui()
    ui.on_user_speaking(True)
    ui.on_ai_transcript("答")
    ui.on_user_transcript("问")
    assert spy.calls == [("问", "答")]  # 用户转写一到即配对，flush 无残留
    ui.flush_pending_transcripts()
    assert spy.calls == [("问", "答")]


# ---- 事件透传（无 owner） ----

def test_no_owner_still_emits_display_events():
    """无 owner（独立窗口/测试）：显示事件照发，只是不落库。"""
    q: queue.Queue = queue.Queue()
    ui = WorkbenchRealtimeUI(_EventEmitter(q), owner=None)
    ui.on_user_transcript("你好")
    ui.on_ai_transcript("你好呀")
    types = []
    while not q.empty():
        types.append(q.get_nowait()["type"])
    assert "user_transcript" in types and "ai_transcript" in types


# ---- ChatEngine.commit_voice_turn（集成：消息追加 + 存盘触发） ----

@pytest.fixture()
def _capture_auto_save(monkeypatch) -> list[dict]:
    """拦截 session_manager._auto_save，记录调用（不触真实用户目录）。"""
    import agent.session_manager as sm

    calls: list[dict] = []

    def _rec(*args, **kwargs):
        calls.append({"messages": kwargs.get("messages") or (args[1] if len(args) > 1 else [])})

    monkeypatch.setattr(sm, "_auto_save", _rec)
    return calls


def _make_engine() -> ChatEngine:
    """不启动线程的引擎实例，预置会话态（_ensure_session 的最小替代）。"""
    engine = ChatEngine(Settings(), queue.Queue(), queue.Queue())
    engine._session_ready = True
    engine._session_name = "session-test"
    engine._messages = []
    engine._dialog_count = 0
    engine._title_generated = True  # 跳过标题生成任务（不触 LLM）
    engine._model = "test-model"  # _after_turn 存盘参数需要（正常由装配设置）
    return engine


def test_commit_voice_turn_appends_pair_and_saves(_capture_auto_save):
    """(问, 答) 追加为 user/assistant 两条消息并触发一次自动保存。"""
    engine = _make_engine()
    engine.commit_voice_turn("在吗？", "在的，先生。")
    assert [m.role for m in engine._messages] == ["user", "assistant"]
    assert engine._messages[0].content[0].text == "在吗？"
    assert engine._messages[1].content[0].text == "在的，先生。"
    assert len(_capture_auto_save) == 1
    assert engine._dialog_count == 1


def test_commit_voice_turn_single_side_variants(_capture_auto_save):
    """单侧落库：开场白（仅 ai）与打断轮（仅 user）各成一条消息。"""
    engine = _make_engine()
    engine.commit_voice_turn(None, "晚上好，先生。")
    engine.commit_voice_turn("没听清，再说一遍？", None)
    assert [m.role for m in engine._messages] == ["assistant", "user"]
    assert len(_capture_auto_save) == 2


def test_commit_voice_turn_noop_guards(_capture_auto_save):
    """双侧为空不动作；会话未装配不动作（不追加、不存盘）。"""
    engine = _make_engine()
    engine.commit_voice_turn(None, None)
    assert engine._messages == []
    engine._session_ready = False
    engine.commit_voice_turn("问", "答")
    assert engine._messages == []
    assert _capture_auto_save == []
