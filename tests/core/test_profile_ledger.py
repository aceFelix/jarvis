"""提炼水位台账（profile_ledger）单元测试。

覆盖：默认值 / touch 与 advance / rewind 下调 / 损坏降级 / 原子写 /
50 会话上限 / 锁超时降级 / 并发不丢更新 / 空闲判定 / 只增不减 / 失败不推进。

@author aceFelix
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent.core.memory import profile_ledger as L


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    """每个用例独立台账文件，避免污染真实 ~/.jarvis。"""
    L.configure_ledger_path(tmp_path / "memory" / "refine_ledger.json")
    yield
    L.reset_for_tests()


def _path() -> Path:
    return L.ledger_path()


class TestBasics:
    def test_empty_state_defaults(self) -> None:
        st = L.load_state("s1")
        assert (st.refined_until, st.last_msg_ts, st.last_refine_ts, st.last_error) == (0, 0.0, 0.0, "")

    def test_touch_persists_and_updates_timestamp(self) -> None:
        L.touch("s1", 5, now=100.0)
        st = L.load_state("s1")
        assert st.last_msg_ts == pytest.approx(100.0)
        assert st.refined_until == 0
        data = json.loads(_path().read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert data["sessions"]["s1"]["last_msg_ts"] == pytest.approx(100.0)

    def test_advance_clears_delta(self) -> None:
        L.touch("s1", 10, now=100.0)
        L.advance("s1", 10, now=101.0)
        assert L.pending_delta("s1", 10) == 0
        st = L.load_state("s1")
        assert st.last_refine_ts == pytest.approx(101.0)

    def test_default_session_key_for_empty_name(self) -> None:
        L.touch("", 3, now=100.0)
        assert "default" in L.all_states()


class TestWatermarkGuards:
    def test_touch_lowers_watermark_when_messages_shrink(self) -> None:
        """`/rewind`、`/compact` 让消息变少：水位必须跟着降，否则永不提炼。"""
        L.touch("s1", 10, now=100.0)
        L.advance("s1", 10, now=101.0)
        L.touch("s1", 4, now=102.0)
        assert L.load_state("s1").refined_until == 4
        assert L.pending_delta("s1", 4) == 0

    def test_advance_never_moves_backwards(self) -> None:
        L.advance("s1", 10, now=100.0)
        L.advance("s1", 3, now=101.0)
        assert L.load_state("s1").refined_until == 10

    def test_mark_attempt_is_independent_from_success(self) -> None:
        """节流看"尝试"：即使失败也要记，否则每轮都会重试。"""
        L.mark_attempt("s1", now=100.0)
        L.mark_error("s1", "boom")
        st = L.load_state("s1")
        assert st.last_attempt_ts == pytest.approx(100.0)
        assert st.last_refine_ts == 0.0          # 失败不更新成功时间

        L.advance("s1", 3, now=101.0)
        st = L.load_state("s1")
        assert st.last_refine_ts == pytest.approx(101.0)
        assert st.last_attempt_ts == pytest.approx(100.0)

    def test_mark_error_does_not_advance(self) -> None:
        L.advance("s1", 5, now=100.0)
        L.mark_error("s1", "ProviderError: boom")
        st = L.load_state("s1")
        assert st.refined_until == 5
        assert "boom" in st.last_error
        # 成功推进后错误被清空
        L.advance("s1", 6, now=102.0)
        assert L.load_state("s1").last_error == ""


class TestRobustness:
    def test_corrupt_file_degrades_to_empty(self) -> None:
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text("not json {", encoding="utf-8")
        st = L.load_state("s1")
        assert st.refined_until == 0
        # 仍能正常写入并自愈
        L.advance("s1", 2, now=100.0)
        assert L.load_state("s1").refined_until == 2

    def test_atomic_write_leaves_no_tmp(self) -> None:
        L.touch("s1", 1, now=100.0)
        assert not (_path().parent / (_path().name + ".tmp")).exists()
        assert not (_path().parent / (_path().name + ".lock")).exists()

    def test_prune_keeps_most_recent_50(self) -> None:
        # 用 8 个会话 + 显式上限 5 验证淘汰序，避免 50 条真实的锁/写盘开销
        for i in range(8):
            L.touch(f"s{i:02d}", 1, now=float(i))
        dropped = L.prune_sessions(max_sessions=5)
        assert dropped == 3
        states = L.all_states()
        assert len(states) == 5
        for i in range(3):
            assert f"s{i:02d}" not in states
        assert "s07" in states

    def test_lock_timeout_degrades_to_memory_only(self, monkeypatch) -> None:
        """拿不到跨进程锁：不抛、只更新内存，且不产生半写文件。"""

        @contextmanager
        def _busy(lock_path, max_retries=30):  # noqa: ARG001
            raise TimeoutError("busy")
            yield  # pragma: no cover

        monkeypatch.setattr(L, "file_lock", _busy)
        L.touch("s1", 4, now=100.0)
        L.advance("s1", 4, now=101.0)
        st = L.load_state("s1")
        assert st.refined_until == 4
        assert not _path().exists()

    def test_concurrent_updates_do_not_lose_sessions(self) -> None:
        """读-改-写全程持锁：并发写不同会话不能互相覆盖。"""
        errors: list[BaseException] = []

        def _worker(sid: str) -> None:
            try:
                for i in range(1, 6):
                    L.touch(sid, i, now=float(i))
                    L.advance(sid, i, now=float(i) + 0.5)
            except BaseException as e:  # pragma: no cover - 失败时便于定位
                errors.append(e)

        threads = [threading.Thread(target=_worker, args=(f"s{n}",)) for n in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        states = L.all_states()
        assert set(states) == {"s0", "s1"}
        for sid in states:
            assert states[sid].refined_until == 5


class TestIdleDecision:
    def test_idle_sessions_requires_idle_and_delta(self) -> None:
        L.touch("old", 10, now=1000.0)
        L.advance("old", 4, now=1001.0)          # 还有 6 条增量
        L.touch("fresh", 10, now=3500.0)         # 刚动过
        L.advance("fresh", 4, now=3501.0)
        L.touch("tiny", 5, now=1000.0)
        L.advance("tiny", 4, now=1001.0)         # 只有 1 条增量

        due = L.idle_sessions(
            now=3700.0, idle_minutes=30, min_delta=3,
            counts={"old": 10, "fresh": 10, "tiny": 5},
        )
        assert due == ["old"]

    def test_idle_disabled_by_zero_threshold(self) -> None:
        L.touch("s1", 10, now=1000.0)
        assert L.idle_sessions(now=99999.0, idle_minutes=0, min_delta=1, counts={"s1": 10}) == []
        assert L.idle_sessions(now=99999.0, idle_minutes=30, min_delta=0, counts={"s1": 10}) == []


class TestReportTelemetry:
    """P1d 遥测：上轮提炼计数随 advance 同一次写入落盘（仅展示用）。"""

    def test_advance_with_report_persists(self) -> None:
        L.advance("s1", 8, now=100.0, report={"added": 2, "noop": 3, "rejected": 1})
        st = L.load_state("s1")
        assert st.last_report == {"added": 2, "noop": 3, "rejected": 1}
        data = json.loads(_path().read_text(encoding="utf-8"))
        assert data["sessions"]["s1"]["last_report"]["noop"] == 3

    def test_advance_without_report_keeps_old(self) -> None:
        L.advance("s1", 8, now=100.0, report={"added": 1})
        L.advance("s1", 12, now=200.0)
        st = L.load_state("s1")
        assert st.refined_until == 12
        assert st.last_report == {"added": 1}

    def test_report_whitelist_and_coerce(self) -> None:
        """白名单外字段、非整数、零值一律不进台账。"""
        L.advance(
            "s1", 1, now=100.0,
            report={"added": "3", "bogus": 9, "noop": 0, "rejected": None},
        )
        assert L.load_state("s1").last_report == {"added": 3}

    def test_corrupt_report_on_disk_does_not_break_load(self) -> None:
        _path().parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "sessions": {"s1": {"refined_until": 5, "last_report": "坏值"}}}
        _path().write_text(json.dumps(payload), encoding="utf-8")
        st = L.load_state("s1")
        assert st.refined_until == 5
        assert st.last_report == {}
