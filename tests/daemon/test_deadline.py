"""截止日期追踪器（agent.core.daemon.deadline）单元测试。

覆盖:
- Deadline 数据模型：序列化往返、due 解析、days_left 计算
- DeadlineTracker 增删改查：add / complete / remove / 列表查询
- 每日检查 check_today：分级提醒（7/3/1/0 天）、逾期、去重、备注
- get_summary 摘要文本
- 持久化 _save / _load（重定向到 tmp_path）

日期逻辑通过 FakeDate 注入固定日期（默认 2026-01-01），
持久化文件通过 monkeypatch _deadlines_file 重定向，不污染真实主目录。

@author aceFelix
"""

from __future__ import annotations

import json

import pytest

from agent.core.daemon.deadline import Deadline, DeadlineTracker
from tests.daemon._fakes import FakeDate, FakeDatetime


@pytest.fixture
def env(monkeypatch, tmp_path):
    """注入固定日期 + 重定向持久化文件。"""
    import agent.core.daemon.deadline as mod

    FakeDate.set_today("2026-01-01")
    FakeDatetime.set_now("2026-01-01 10:00:00")
    monkeypatch.setattr(mod, "date", FakeDate)
    monkeypatch.setattr(mod, "datetime", FakeDatetime)
    monkeypatch.setattr(mod, "_deadlines_file", lambda: tmp_path / "deadlines.json")
    return mod


@pytest.fixture
def tracker(env):
    """构造一个使用 tmp 持久化的 tracker。"""
    return DeadlineTracker()


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


class TestDeadlineModel:
    """Deadline 字段与属性。"""

    def test_to_dict_from_dict_roundtrip(self):
        d = Deadline(
            id="d1", title="项目交付", due_date="2026-02-01",
            remind_days=[7, 3, 0], status="active", note="给客户",
            reminded_dates=["2026-01-01"],
        )
        restored = Deadline.from_dict(d.to_dict())
        assert restored == d

    def test_from_dict_defaults(self):
        d = Deadline.from_dict({})
        assert d.remind_days == [7, 3, 1, 0]
        assert d.status == "active"
        assert d.reminded_dates == []

    def test_due_property(self):
        assert Deadline(due_date="2026-02-01").due == FakeDate(2026, 2, 1)
        assert Deadline(due_date="bad-date").due is None
        assert Deadline(due_date="").due is None

    def test_days_left(self, env):
        # 假日期 2026-01-01：02-01 还有 31 天
        assert Deadline(due_date="2026-02-01").days_left == 31
        # 当天 0 天
        assert Deadline(due_date="2026-01-01").days_left == 0
        # 逾期负数
        assert Deadline(due_date="2025-12-30").days_left == -2
        # 解析失败 None
        assert Deadline(due_date="oops").days_left is None


# ---------------------------------------------------------------------------
# 增删改查
# ---------------------------------------------------------------------------


class TestTrackerCRUD:
    """add / complete / remove / 查询。"""

    def test_add_creates_deadline(self, env, tracker):
        d = tracker.add(title="交付", due_date="2026-02-01", note="备注")
        assert d.id and len(d.id) == 12
        assert d.status == "active"
        assert d.remind_days == [7, 3, 1, 0]
        assert d.note == "备注"
        assert d.created_at == "2026-01-01T10:00:00"
        # 持久化
        data = json.loads(env._deadlines_file().read_text(encoding="utf-8"))
        assert len(data) == 1

    def test_add_custom_remind_days(self, env, tracker):
        d = tracker.add(title="x", due_date="2026-02-01", remind_days=[1])
        assert d.remind_days == [1]

    def test_add_invalid_date_raises(self, env, tracker):
        with pytest.raises(ValueError):
            tracker.add(title="x", due_date="not-a-date")

    def test_complete(self, env, tracker):
        d = tracker.add(title="x", due_date="2026-02-01")
        assert tracker.complete(d.id) is True
        assert d.status == "done"
        # 已完成不能再 complete
        assert tracker.complete(d.id) is False
        # 不存在返回 False
        assert tracker.complete("nope") is False

    def test_remove(self, env, tracker):
        d = tracker.add(title="x", due_date="2026-02-01")
        assert tracker.remove(d.id) is True
        assert tracker.list_all() == []
        assert tracker.remove("nope") is False

    def test_list_active_excludes_done_sorted(self, env, tracker):
        late = tracker.add(title="晚", due_date="2026-03-01")
        early = tracker.add(title="早", due_date="2026-01-15")
        done = tracker.add(title="完成", due_date="2026-01-10")
        tracker.complete(done.id)

        active = tracker.list_active()
        assert [d.id for d in active] == [early.id, late.id]
        assert all(d.status in ("active", "overdue") for d in active)

    def test_list_all(self, env, tracker):
        tracker.add(title="a", due_date="2026-02-01")
        tracker.add(title="b", due_date="2026-03-01")
        assert len(tracker.list_all()) == 2


# ---------------------------------------------------------------------------
# 每日检查
# ---------------------------------------------------------------------------


class TestCheckToday:
    """check_today 分级提醒逻辑。"""

    def test_remind_at_days_left(self, env, tracker):
        # 今天 2026-01-01，deadline 01-08 → 距 7 天，命中 remind_days 默认 [7,3,1,0]
        d = tracker.add(title="周报", due_date="2026-01-08")
        msgs = tracker.check_today()
        assert msgs == ["📌 距离「周报」还有 7 天（截止日: 2026-01-08）"]

    def test_remind_at_due_day(self, env, tracker):
        d = tracker.add(title="交稿", due_date="2026-01-01")
        msgs = tracker.check_today()
        assert msgs == ["🔴 今天是「交稿」的截止日！"]

    def test_overdue_marks_status_and_reminds(self, env, tracker):
        d = tracker.add(title="过期任务", due_date="2025-12-30")
        msgs = tracker.check_today()
        assert d.status == "overdue"
        assert msgs == ["⚠️ 「过期任务」已逾期 2 天（截止日: 2025-12-30）"]

    def test_no_reminder_outside_remind_days(self, env, tracker):
        # 距 5 天不在默认 [7,3,1,0] 中 → 不提醒
        tracker.add(title="无提醒", due_date="2026-01-06")
        assert tracker.check_today() == []

    def test_done_skipped(self, env, tracker):
        d = tracker.add(title="已完成", due_date="2026-01-01")
        tracker.complete(d.id)
        assert tracker.check_today() == []

    def test_invalid_due_skipped(self, env, tracker):
        tracker._deadlines.append(Deadline(title="坏数据", due_date="oops"))
        assert tracker.check_today() == []

    def test_dedup_same_day(self, env, tracker):
        d = tracker.add(title="去重", due_date="2026-01-08")
        first = tracker.check_today()
        second = tracker.check_today()
        assert len(first) == 1
        assert second == []  # 同一天不重复提醒
        assert d.reminded_dates == ["2026-01-01"]

    def test_overdue_reminds_every_day(self, env, tracker):
        d = tracker.add(title="逾期", due_date="2025-12-30")
        tracker.check_today()
        # 第二天（手动清除去重记录）应继续提醒
        d.reminded_dates.clear()
        msgs = tracker.check_today()
        assert len(msgs) == 1

    def test_note_appended(self, env, tracker):
        tracker.add(title="带备注", due_date="2026-01-08", note="需要客户确认")
        msgs = tracker.check_today()
        assert "备注: 需要客户确认" in msgs[0]

    def test_multiple_reminders_same_day(self, env, tracker):
        tracker.add(title="A", due_date="2026-01-08")
        tracker.add(title="B", due_date="2026-01-01")
        msgs = tracker.check_today()
        assert len(msgs) == 2
        assert all("「" in m for m in msgs)


# ---------------------------------------------------------------------------
# 摘要
# ---------------------------------------------------------------------------


class TestSummary:
    """get_summary 摘要文本。"""

    def test_empty_when_no_active(self, env, tracker):
        assert tracker.get_summary() == ""

    def test_summary_branches(self, env, tracker):
        tracker.add(title="今天", due_date="2026-01-01")
        tracker.add(title="还剩", due_date="2026-01-08")
        tracker.add(title="逾期", due_date="2025-12-20")
        summary = tracker.get_summary()
        assert "🔴 今天: 今天截止！" in summary
        assert "📌 还剩: 还剩 7 天" in summary
        assert "⚠️ 逾期: 已逾期 12 天" in summary

    def test_summary_skips_invalid_due(self, env, tracker):
        tracker._deadlines.append(Deadline(title="坏数据", due_date="oops"))
        # 不会崩溃且返回空（没有有效 deadline）
        assert tracker.get_summary() == ""


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------


class TestPersistence:
    """_save / _load。"""

    def test_load_restores_deadlines(self, env, tmp_path):
        path = tmp_path / "deadlines.json"
        path.write_text(json.dumps([
            {"id": "saved1", "title": "已存", "due_date": "2026-02-01"}
        ]), encoding="utf-8")
        t = DeadlineTracker()
        assert [d.id for d in t.list_all()] == ["saved1"]

    def test_load_missing_file(self, env, tracker):
        assert tracker.list_all() == []

    def test_load_corrupt_json(self, env, tmp_path):
        path = tmp_path / "deadlines.json"
        path.write_text("{broken", encoding="utf-8")
        t = DeadlineTracker()
        assert t.list_all() == []
