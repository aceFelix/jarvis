"""事件轨（P2c）单元测试。

覆盖：追加/读取往返、坏行跳过、过期过滤、最近 N 条、保留期清理，
以及“失败静默”与空内容拒绝。

@author aceFelix
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.memory import profile_events as E


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    """每个用例独立事件文件，绝不碰真实 ~/.jarvis。"""
    E.configure_events_path(tmp_path / "memory" / "events.jsonl")
    yield
    E.reset_for_tests()


class TestAppendAndLoad:
    def test_roundtrip(self) -> None:
        assert E.append_event("schedule", "下周三面试", source_session="s1", ts=100.0, expire_at=200.0)
        events = E.load_events()
        assert len(events) == 1
        ev = events[0]
        assert (ev.category, ev.content, ev.source_session) == ("schedule", "下周三面试", "s1")
        assert ev.ts == pytest.approx(100.0)
        assert ev.expire_at == pytest.approx(200.0)

    def test_empty_content_rejected(self) -> None:
        assert E.append_event("other", "   ") is False
        assert E.load_events() == []

    def test_content_truncated(self) -> None:
        E.append_event("other", "长" * 500)
        assert len(E.load_events()[0].content) == E._MAX_CONTENT_CHARS

    def test_bad_lines_skipped(self) -> None:
        path = E.events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            'not json\n{"content": "有效事件", "ts": 1}\n{"ts": 2}\n',
            encoding="utf-8",
        )
        events = E.load_events()
        assert [e.content for e in events] == ["有效事件"]

    def test_missing_file_returns_empty(self) -> None:
        assert E.load_events() == []

    def test_load_sorted_by_ts(self) -> None:
        E.append_event("other", "后", ts=200.0)
        E.append_event("other", "前", ts=100.0)
        assert [e.content for e in E.load_events()] == ["前", "后"]


class TestRecentEvents:
    def test_skips_expired(self) -> None:
        E.append_event("other", "过期", ts=100.0, expire_at=150.0)
        E.append_event("other", "有效", ts=160.0, expire_at=0.0)
        events = E.recent_events(limit=5, now=200.0)
        assert [e.content for e in events] == ["有效"]

    def test_limit_returns_latest(self) -> None:
        for i in range(5):
            E.append_event("other", f"事件{i}", ts=float(i))
        events = E.recent_events(limit=2, now=10.0)
        assert [e.content for e in events] == ["事件3", "事件4"]

    def test_zero_limit(self) -> None:
        E.append_event("other", "事件", ts=1.0)
        assert E.recent_events(limit=0) == []


class TestPrune:
    def test_removes_expired_and_too_old(self) -> None:
        now = 1000.0 * 86400
        E.append_event("other", "过期", ts=now, expire_at=now - 1)
        E.append_event("other", "太老", ts=now - 200 * 86400)
        E.append_event("other", "保留", ts=now - 10 * 86400)

        assert E.prune_events(90, now=now) == 2
        assert [e.content for e in E.load_events()] == ["保留"]

    def test_retention_zero_only_removes_expired(self) -> None:
        now = 1000.0 * 86400
        E.append_event("other", "过期", ts=now, expire_at=now - 1)
        E.append_event("other", "很老但未到期", ts=now - 500 * 86400)
        assert E.prune_events(0, now=now) == 1
        assert [e.content for e in E.load_events()] == ["很老但未到期"]

    def test_prune_missing_file(self) -> None:
        assert E.prune_events(90) == 0

    def test_prune_no_tmp_left(self) -> None:
        now = 100.0
        E.append_event("other", "过期", ts=now, expire_at=now - 1)
        E.prune_events(90, now=now)
        assert not (E.events_path().parent / (E.events_path().name + ".tmp")).exists()


class TestFailureSilent:
    def test_append_failure_returns_false(self, monkeypatch) -> None:
        def _boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(E, "ensure_dir", _boom)
        assert E.append_event("other", "写不进去") is False

    def test_prune_failure_returns_zero(self, monkeypatch) -> None:
        E.append_event("other", "过期", ts=100.0, expire_at=50.0)

        def _boom(*a, **k):
            raise OSError("locked")

        monkeypatch.setattr(E, "ensure_dir", _boom)
        assert E.prune_events(90, now=200.0) == 0


class TestFileShape:
    def test_one_json_per_line(self) -> None:
        E.append_event("schedule", "A", ts=1.0)
        E.append_event("schedule", "B", ts=2.0)
        lines = E.events_path().read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["content"] == "A"


class TestClearEvents:
    """2026-11-19：`/memory clear` 必须把事件轨一起清掉。

    回归背景：clear 曾只清 profile.json，导致“画像已清空但 /memory events
    仍能看到旧行程”，且事件永远不会被清空。
    """

    def test_clear_removes_all_and_reports_count(self) -> None:
        E.append_event("schedule", "下周三面试", ts=100.0)
        E.append_event("schedule", "周五搬家", ts=200.0)
        assert E.clear_events() == 2
        assert E.load_events() == []
        assert not E.events_path().exists()

    def test_clear_missing_file_is_noop(self) -> None:
        assert E.clear_events() == 0

    def test_append_after_clear_works(self) -> None:
        E.append_event("schedule", "旧行程", ts=100.0)
        E.clear_events()
        assert E.append_event("schedule", "新行程", ts=200.0)
        assert [e.content for e in E.load_events()] == ["新行程"]
