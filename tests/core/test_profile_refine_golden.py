"""画像提炼编排的 golden 用例（P0 / P4）。

用假 `refine_session` 钉住编排层：只喂增量、三触发语义、幂等、失败不推进水位。
真提炼逻辑（prompt/解析/落盘）由 `test_profile_memory.py` 覆盖，这里不重复。

@author aceFelix
"""

from __future__ import annotations

import json
import time
import threading
from pathlib import Path

import pytest

from agent.config.settings import Settings
from agent.core.memory import profile_ledger as L
from agent.core.memory import profile_refiner as R
from agent.core.message import Message, TextContent


def _msg(role: str, text: str) -> Message:
    return Message(role=role, content=[TextContent(text=text)])


def _dialog(n: int, *, start: int = 0, prefix: str = "m") -> list[Message]:
    """构造 n 条 user/assistant 交替的消息（每条内容唯一）。"""
    out: list[Message] = []
    for i in range(start, start + n):
        out.append(_msg("user" if i % 2 == 0 else "assistant", f"{prefix}-{i}"))
    return out


def _wait_until(pred, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    L.configure_ledger_path(tmp_path / "memory" / "refine_ledger.json")
    # 事件轨同样隔离：episodic 用例不得写进真实 ~/.jarvis
    from agent.core.memory import profile_events as EV

    EV.configure_events_path(tmp_path / "memory" / "events.jsonl")
    yield
    _drain_refiner()
    L.reset_for_tests()
    EV.reset_for_tests()


def _drain_refiner(timeout: float = 5.0) -> None:
    """等后台提炼线程退出后再还原台账路径。

    否则 worker 可能在 fixture teardown 之后才写 `advance()`，而那时路径已还原成
    真实 `~/.jarvis/memory/refine_ledger.json`——实测因此把 `s1` 写进了用户文件。
    """
    for t in list(threading.enumerate()):
        if t.name == "profile-refiner" and t.is_alive():
            t.join(timeout=timeout)


@pytest.fixture
def settings() -> Settings:
    # interval=0：让阈值触发不被节流挡住；各用例自行覆盖
    return Settings(profile_enabled=True, profile_refine_interval=0,
                    profile_refine_min_messages=6, profile_refine_min_delta=6)


class _Recorder:
    """替身 refine_session：记录被喂了哪些消息，返回预设 report。"""

    def __init__(self, report: R.RefineReport | None = None) -> None:
        self.calls: list[list[Message]] = []
        self._report = report or R.RefineReport(advanceable=True)

    def __call__(self, messages, session_id, settings, store=None):  # noqa: ARG002
        self.calls.append(list(messages))
        return self._report

    @property
    def fed_texts(self) -> list[str]:
        return [m.get_text() for call in self.calls for m in call]


class TestDeltaGate:
    def test_delta_below_min_delta_no_call(self, settings, monkeypatch) -> None:
        rec = _Recorder()
        monkeypatch.setattr(R, "refine_session", rec)
        L.advance("s1", 1, now=0.0)          # 已提炼 1 条
        assert R.maybe_refine_async(_dialog(6), "s1", settings) is False
        assert rec.calls == []

    def test_delta_meets_min_delta_feeds_only_increment(self, settings, monkeypatch) -> None:
        rec = _Recorder()
        monkeypatch.setattr(R, "refine_session", rec)
        L.advance("s1", 10, now=0.0)         # 前 10 条已提炼
        msgs = _dialog(16)
        assert R.maybe_refine_async(msgs, "s1", settings) is True
        assert _wait_until(lambda: len(rec.calls) == 1)
        assert [m.get_text() for m in rec.calls[0]] == [m.get_text() for m in msgs[10:16]]
        # 成功后水位推进到 16
        assert _wait_until(lambda: L.load_state("s1").refined_until == 16)

    def test_small_tail_schedules_idle(self, settings, monkeypatch) -> None:
        """增量不够 min_delta 时不提炼，但必须排空闲兜底。

        2026-11-15 实测：聊两三句就走开，这几条既没提炼也没排空闲，直接丢了。
        """
        scheduled: list[tuple[str, float]] = []
        monkeypatch.setattr(R, "_schedule_idle", lambda sid, ts: scheduled.append((sid, ts)))
        rec = _Recorder()
        monkeypatch.setattr(R, "refine_session", rec)
        L.advance("s1", 10, now=0.0)

        assert R.maybe_refine_async(_dialog(12), "s1", settings) is False  # 2 < 6
        assert rec.calls == []
        assert scheduled and scheduled[0][0] == "s1"

    def test_three_rounds_feed_each_message_once(self, settings, monkeypatch) -> None:
        """连续三轮：每轮只喂新增量，任何一条消息都不会被重复喂。"""
        rec = _Recorder()
        monkeypatch.setattr(R, "refine_session", rec)
        all_msgs = _dialog(18)
        for end in (6, 12, 18):
            assert R.maybe_refine_async(all_msgs[:end], "s1", settings) is True
            assert _wait_until(lambda e=end: L.load_state("s1").refined_until == e)

        assert [len(c) for c in rec.calls] == [6, 6, 6]
        fed = rec.fed_texts
        assert len(fed) == 18 and len(set(fed)) == 18


class TestForceTrigger:
    def test_force_ignores_interval_with_one_new_message(self, monkeypatch) -> None:
        s = Settings(profile_enabled=True, profile_refine_interval=99999,
                     profile_refine_min_messages=6, profile_refine_min_delta=6)
        rec = _Recorder()
        monkeypatch.setattr(R, "refine_session", rec)
        L.advance("s1", 15, now=time.time())      # 刚提炼过，节流生效中
        assert R.maybe_refine_async(_dialog(16), "s1", s, force=True) is True
        assert _wait_until(lambda: len(rec.calls) == 1)
        assert len(rec.calls[0]) == 1

    def test_force_with_zero_delta_no_call(self, monkeypatch) -> None:
        s = Settings(profile_enabled=True, profile_refine_interval=0)
        rec = _Recorder()
        monkeypatch.setattr(R, "refine_session", rec)
        L.advance("s1", 16, now=0.0)
        assert R.maybe_refine_async(_dialog(16), "s1", s, force=True) is False
        assert rec.calls == []

    def test_min_messages_still_absolute_floor(self, monkeypatch) -> None:
        s = Settings(profile_enabled=True, profile_refine_interval=0,
                     profile_refine_min_messages=6)
        rec = _Recorder()
        monkeypatch.setattr(R, "refine_session", rec)
        assert R.maybe_refine_async(_dialog(2), "s1", s, force=True) is False
        assert rec.calls == []


class TestAdvancePolicy:
    def test_error_does_not_advance(self, settings, monkeypatch) -> None:
        rec = _Recorder(R.RefineReport(error="ProviderError: boom"))
        monkeypatch.setattr(R, "refine_session", rec)
        L.advance("s1", 10, now=0.0)
        assert R.maybe_refine_async(_dialog(16), "s1", settings) is True
        assert _wait_until(lambda: "boom" in L.load_state("s1").last_error)
        st = L.load_state("s1")
        assert st.refined_until == 10          # 失败不推进，下次重试同一段

    def test_skipped_without_llm_does_not_advance(self, settings, monkeypatch) -> None:
        rec = _Recorder(R.RefineReport(skipped=True, advanceable=False,
                                       reason="无可用 LLM（mock 且未配置独立提炼模型）"))
        monkeypatch.setattr(R, "refine_session", rec)
        L.advance("s1", 10, now=0.0)
        assert R.maybe_refine_async(_dialog(16), "s1", settings) is True
        assert _wait_until(lambda: len(rec.calls) == 1)
        time.sleep(0.05)
        assert L.load_state("s1").refined_until == 10

    def test_skipped_without_content_advances(self, settings, monkeypatch) -> None:
        rec = _Recorder(R.RefineReport(skipped=True, advanceable=True,
                                       reason="无可提炼对话内容"))
        monkeypatch.setattr(R, "refine_session", rec)
        L.advance("s1", 10, now=0.0)
        assert R.maybe_refine_async(_dialog(16), "s1", settings) is True
        assert _wait_until(lambda: L.load_state("s1").refined_until == 16)


class TestReportTelemetry:
    """P1d：上轮提炼计数随水位一起落台账（/memory 展示的数据源）。"""

    def test_report_written_with_advance(self, settings, monkeypatch) -> None:
        report = R.RefineReport(
            advanceable=True, extracted=6, added=2, merged=1, noop=3, rejected=1,
        )
        monkeypatch.setattr(R, "refine_session", _Recorder(report))
        assert R.maybe_refine_async(_dialog(6), "s1", settings) is True
        assert _wait_until(lambda: L.load_state("s1").refined_until == 6)
        st = L.load_state("s1")
        assert st.last_report == {
            "extracted": 6, "added": 2, "merged": 1, "noop": 3, "rejected": 1,
        }

    def test_error_does_not_write_report(self, settings, monkeypatch) -> None:
        """失败轮次只记 last_error，不污染上轮遥测。"""
        monkeypatch.setattr(R, "refine_session", _Recorder(R.RefineReport(error="boom")))
        assert R.maybe_refine_async(_dialog(6), "s1", settings) is True
        assert _wait_until(lambda: L.load_state("s1").last_error == "boom")
        assert L.load_state("s1").last_report == {}


class _FakeProvider:
    """真 refine_session 的假 provider：支持脚本化多次回复（抽取/裁决两段）。"""

    def __init__(self, reply: str | list[str]) -> None:
        self._replies = list(reply) if isinstance(reply, list) else [reply]
        self.prompts: list[str] = []
        self._thinking = True

    def is_thinking_enabled(self) -> bool:
        return self._thinking

    def set_thinking_enabled(self, enabled: bool) -> None:
        self._thinking = enabled

    async def stream(self, **kwargs):
        from agent.llm.base import TextDelta
        self.prompts.append(kwargs["messages"][-1].get_text())
        reply = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        yield TextDelta(text=reply)

    async def close(self) -> None:
        pass


class TestPromptBoundary:
    def test_refine_prompt_stays_within_budget(self, tmp_path: Path, monkeypatch) -> None:
        """提示词 = 窗口上限 + 旧画像上限 + 模板；钉住它不随库膨胀而失控。"""
        from agent.core.memory.profile_store import ProfileEntry, ProfileStore

        store = ProfileStore(tmp_path / "profile.json")
        for i in range(90):                   # 刚超过展示上限 80，验证截断生效
            store.upsert(ProfileEntry.new(f"事实{i}" * 8, "other", 0.8, "s0"))

        extract = json.dumps({
            "candidates": [{"content": "习惯深夜工作", "category": "schedule", "confidence": 0.9}]
        }, ensure_ascii=False)
        resolve = json.dumps({
            "facts": [{"op": "add", "content": "习惯深夜工作",
                       "category": "schedule", "confidence": 0.9}]
        }, ensure_ascii=False)
        fake = _FakeProvider([extract, resolve])
        monkeypatch.setattr(R, "_build_refine_provider", lambda s: fake)
        msgs = _dialog(60)                    # 超过窗口上限 40
        report = R.refine_session(msgs, "s1", Settings(profile_enabled=True), store=store)

        assert report.error == ""
        assert report.advanceable is True
        # 两段 prompt 各不超 20k 字符（40 条 × 300 字 + 模板 + top-K 召回）
        assert len(fake.prompts) == 2
        assert all(len(p) <= 20_000 for p in fake.prompts)

    def test_no_content_is_advanceable(self, tmp_path: Path) -> None:
        from agent.core.memory.profile_store import ProfileStore

        report = R.refine_session([_msg("user", "/memory")], "s1",
                                  Settings(profile_enabled=True),
                                  store=ProfileStore(tmp_path / "p.json"))
        assert report.skipped is True and report.advanceable is True

    def test_mock_without_provider_is_not_advanceable(self, tmp_path: Path) -> None:
        from agent.core.memory.profile_store import ProfileStore

        report = R.refine_session([_msg("user", "我习惯熬夜")], "s1",
                                  Settings(api_format="mock"),
                                  store=ProfileStore(tmp_path / "p.json"))
        assert report.skipped is True and report.advanceable is False
