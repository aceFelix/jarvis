"""主动感知引擎（agent.core.daemon.proactive）单元测试。

覆盖:
- ProactiveConfig 配置字段
- ProactiveEngine 生命周期：start / stop / 幂等注册
- 任务分发：handle_task_fire / is_proactive_task
- 每日简报组装：问候语 / 节假日 / 今日提醒 / 截止日期 / 系统状态 / 日历
- 截止日期检查 / 日历检查的触发与容错
- _today_at 时间字符串解析

外部依赖（holidays 函数、deadline tracker、monitor、calendar source、
scheduler 持久化）全部通过 monkeypatch / 替身隔离，不依赖真实环境。

@author aceFelix
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.core.daemon.deadline import DeadlineTracker
from agent.core.daemon.proactive import (
    _BRIEFING_NOTE,
    _CALENDAR_NOTE,
    _DEADLINE_NOTE,
    _PROFILE_MAINT_NOTE,
    ProactiveConfig,
    ProactiveEngine,
)
from agent.core.daemon.scheduler import ScheduleTask, Scheduler
from tests.daemon._fakes import FakeDate, FakeDatetime


@pytest.fixture
def env(monkeypatch, tmp_path):
    """注入固定时钟 + 重定向 scheduler 持久化文件。"""
    import agent.core.daemon.proactive as mod
    import agent.core.daemon.scheduler as sch_mod

    FakeDatetime.set_now("2026-01-01 10:00:00")
    FakeDate.set_today("2026-01-01")
    monkeypatch.setattr(mod, "datetime", FakeDatetime)
    monkeypatch.setattr(mod, "date", FakeDate)
    monkeypatch.setattr(sch_mod, "_schedule_file", lambda: tmp_path / "schedule.json")
    return mod


@pytest.fixture
def make_engine(env):
    """构造一个使用真实 Scheduler（tmp 持久化）的引擎。"""

    def _make(
        config=None,
        tracker=None,
        calendar=None,
        monitor=None,
    ):
        scheduler = Scheduler(on_fire=lambda t: None)
        notifications = []
        engine = ProactiveEngine(
            scheduler=scheduler,
            config=config or ProactiveConfig(),
            deadline_tracker=tracker,
            calendar_source=calendar,
            monitor=monitor,
            on_notify=notifications.append,
        )
        return engine, scheduler, notifications

    return _make


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


class TestConfig:
    """ProactiveConfig 默认值与赋值。"""

    def test_defaults(self):
        cfg = ProactiveConfig()
        assert cfg.briefing_enabled is True
        assert cfg.briefing_time == "08:30"
        assert cfg.deadline_enabled is True
        assert cfg.deadline_check_time == "09:00"
        assert cfg.calendar_enabled is False
        assert cfg.calendar_check_interval_min == 30
        assert cfg.calendar_remind_minutes_before == 30

    def test_custom_values(self):
        cfg = ProactiveConfig(briefing_time="07:00", calendar_enabled=True)
        assert cfg.briefing_time == "07:00"
        assert cfg.calendar_enabled is True


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


class TestLifecycle:
    """start / stop / 任务注册。"""

    def test_start_registers_briefing_and_deadline_tasks(self, make_engine):
        """start 应注册每日简报 + 截止日期检查 + 画像维护三个 daily 任务。"""
        engine, scheduler, _ = make_engine(tracker=DeadlineTracker())
        engine.start()

        notes = {t.note for t in scheduler.list_all()}
        assert notes == {_BRIEFING_NOTE, _DEADLINE_NOTE, _PROFILE_MAINT_NOTE}
        assert all(t.repeat == "daily" for t in scheduler.list_all())
        # 任务 id 已登记，供 stop 取消
        assert len(engine._task_ids) == 3

    def test_start_idempotent(self, make_engine):
        """多次 start 不应重复注册任务。"""
        engine, scheduler, _ = make_engine(tracker=DeadlineTracker())
        engine.start()
        engine.start()
        assert len(scheduler.list_all()) == 3

    def test_start_skips_disabled_modules(self, make_engine):
        """禁用简报/截止日期/画像维护时不应注册对应任务。"""
        engine, scheduler, _ = make_engine(
            config=ProactiveConfig(
                briefing_enabled=False, deadline_enabled=False,
                profile_maintenance_enabled=False,
            ),
        )
        engine.start()
        assert scheduler.list_all() == []

    def test_start_deadline_requires_tracker(self, make_engine):
        """未提供 deadline_tracker 时不应注册截止日期任务。"""
        engine, scheduler, _ = make_engine(config=ProactiveConfig(deadline_enabled=True))
        engine.start()
        assert [t.note for t in scheduler.list_all()] == [_BRIEFING_NOTE, _PROFILE_MAINT_NOTE]

    def test_stop_cancels_registered_tasks(self, make_engine):
        """stop 应取消所有注册任务并清空登记。"""
        engine, scheduler, _ = make_engine(tracker=DeadlineTracker())
        engine.start()
        engine.stop()

        assert engine._started is False
        assert engine._task_ids == []
        assert all(t.status == "cancelled" for t in scheduler.list_all())

    def test_stop_without_start_is_safe(self, make_engine):
        """未 start 直接 stop 不应抛异常。"""
        engine, _, _ = make_engine()
        engine.stop()


# ---------------------------------------------------------------------------
# 任务分发
# ---------------------------------------------------------------------------


class TestDispatch:
    """handle_task_fire 按 note 分发。"""

    def test_dispatch_briefing(self, make_engine):
        engine, _, _ = make_engine()
        engine._fire_briefing = lambda: setattr(engine, "_hit", "briefing")
        engine.handle_task_fire(ScheduleTask(note=_BRIEFING_NOTE))
        assert engine._hit == "briefing"

    def test_dispatch_deadline(self, make_engine):
        engine, _, _ = make_engine()
        engine._fire_deadline_check = lambda: setattr(engine, "_hit", "deadline")
        engine.handle_task_fire(ScheduleTask(note=_DEADLINE_NOTE))
        assert engine._hit == "deadline"

    def test_dispatch_calendar(self, make_engine):
        engine, _, _ = make_engine()
        engine._fire_calendar_check = lambda: setattr(engine, "_hit", "calendar")
        engine.handle_task_fire(ScheduleTask(note=_CALENDAR_NOTE))
        assert engine._hit == "calendar"

    def test_dispatch_unknown_note_ignored(self, make_engine):
        engine, _, _ = make_engine()
        engine.handle_task_fire(ScheduleTask(note="some-other-note"))
        assert not hasattr(engine, "_hit")

    def test_is_proactive_task(self, make_engine):
        engine, _, _ = make_engine()
        assert engine.is_proactive_task(ScheduleTask(note=_BRIEFING_NOTE))
        assert engine.is_proactive_task(ScheduleTask(note=_DEADLINE_NOTE))
        assert engine.is_proactive_task(ScheduleTask(note=_CALENDAR_NOTE))
        assert not engine.is_proactive_task(ScheduleTask(note=""))
        assert not engine.is_proactive_task(ScheduleTask(note="user-task"))

    def test_find_task_by_note(self, make_engine):
        engine, scheduler, _ = make_engine()
        engine.start()
        assert engine._find_task_by_note(_BRIEFING_NOTE) is not None
        assert engine._find_task_by_note("nope") is None


# ---------------------------------------------------------------------------
# _today_at 时间解析
# ---------------------------------------------------------------------------


class TestTodayAt:
    """_today_at 把 HH:MM 转成今天的 ISO 时间（过了则用明天）。"""

    def test_future_time_uses_today(self, env):
        # 假时间 10:00，12:00 在未来 → 今天
        assert ProactiveEngine._today_at("12:00") == "2026-01-01T12:00:00"

    def test_past_time_uses_tomorrow(self, env):
        # 09:00 已过 → 明天
        assert ProactiveEngine._today_at("09:00") == "2026-01-02T09:00:00"

    def test_invalid_time_falls_back(self, env):
        # 非法格式回退 08:30，而 08:30 已过 10:00 → 明天
        assert ProactiveEngine._today_at("oops") == "2026-01-02T08:30:00"


# ---------------------------------------------------------------------------
# 每日简报
# ---------------------------------------------------------------------------


class TestBriefing:
    """_build_briefing 各区块与容错。"""

    def _patched_engine(self, make_engine, holidays=None, scheduler_tasks=None):
        """构造引擎 + 补丁：holidays 函数、scheduler pending 任务。"""
        holidays = holidays or {}
        import agent.core.daemon.holidays as hol_mod

        for name, value in holidays.items():
            getattr(pytest, "monkeypatch", None)  # 占位，实际用 monkeypatch 参数
        engine, scheduler, notifications = make_engine(tracker=DeadlineTracker())
        # 用 monkeypatch 直接设置 holidays 模块函数
        engine._holidays = holidays
        engine._scheduler_tasks = scheduler_tasks
        return engine, scheduler, notifications

    def _build(self, engine, monkeypatch, holidays=None, scheduler_tasks=None):
        """打补丁后组装简报。"""
        import agent.core.daemon.holidays as hol_mod

        for name, value in (holidays or {}).items():
            monkeypatch.setattr(hol_mod, name, value)
        if scheduler_tasks is not None:
            monkeypatch.setattr(engine._scheduler, "list_pending", lambda: scheduler_tasks)
        return engine._build_briefing()

    def test_greeting_morning(self, make_engine, monkeypatch):
        """早上时段问候语。"""
        FakeDatetime.set_now("2026-01-01 08:00:00")
        engine, _, _ = make_engine()
        text = self._build(engine, monkeypatch, holidays={
            "get_holiday_name": lambda: None,
            "is_workday": lambda: True,
        })
        assert text.startswith("早上好，先生。以下是今日简报：")

    def test_greeting_late_night(self, make_engine, monkeypatch):
        """深夜时段问候语。"""
        FakeDatetime.set_now("2026-01-01 03:00:00")
        engine, _, _ = make_engine()
        assert self._build(engine, monkeypatch).startswith("夜深了，先生。")

    def test_greeting_noon(self, make_engine, monkeypatch):
        FakeDatetime.set_now("2026-01-01 13:00:00")
        engine, _, _ = make_engine()
        assert self._build(engine, monkeypatch).startswith("中午好，先生。")

    def test_greeting_afternoon(self, make_engine, monkeypatch):
        FakeDatetime.set_now("2026-01-01 16:00:00")
        engine, _, _ = make_engine()
        assert self._build(engine, monkeypatch).startswith("下午好，先生。")

    def test_greeting_evening(self, make_engine, monkeypatch):
        FakeDatetime.set_now("2026-01-01 20:00:00")
        engine, _, _ = make_engine()
        assert self._build(engine, monkeypatch).startswith("晚上好，先生。")

    def test_holiday_branch(self, make_engine, monkeypatch):
        """节假日区块：今天有假日名称。"""
        engine, _, _ = make_engine()
        text = self._build(engine, monkeypatch, holidays={
            "get_holiday_name": lambda: "元旦",
        })
        assert "📅 今天是节假日：元旦" in text

    def test_weekend_branch(self, make_engine, monkeypatch):
        """非假日但非工作日 → 周末休息提示。"""
        engine, _, _ = make_engine()
        text = self._build(engine, monkeypatch, holidays={
            "get_holiday_name": lambda: None,
            "is_workday": lambda: False,
        })
        assert "📅 今天是周末，好好休息" in text

    def test_workday_branch(self, make_engine, monkeypatch):
        """非假日且是工作日。"""
        engine, _, _ = make_engine()
        text = self._build(engine, monkeypatch, holidays={
            "get_holiday_name": lambda: None,
            "is_workday": lambda: True,
        })
        assert "📅 今天是工作日" in text

    def test_holiday_section_exception_ignored(self, make_engine, monkeypatch):
        """节假日函数抛异常不应影响简报其余部分。"""
        def boom(*a, **k):
            raise RuntimeError("holidays 不可用")

        engine, _, _ = make_engine()
        text = self._build(engine, monkeypatch, holidays={
            "get_holiday_name": boom,
        })
        assert text.endswith("祝您今天顺利，先生。")

    def test_today_reminders_listed(self, make_engine, monkeypatch):
        """今日待触发提醒应列出，且 proactive 内部任务被排除。"""
        FakeDate.set_today("2026-01-01")
        tasks = [
            ScheduleTask(id="a", trigger_at="2026-01-01T08:30:00", content="晨会"),
            ScheduleTask(id="b", trigger_at="2026-01-01T14:00:00", content="项目评审"),
            ScheduleTask(id="c", trigger_at="2026-01-01T08:30:00", content="简报", note=_BRIEFING_NOTE),
        ]
        engine, _, _ = make_engine()
        text = self._build(engine, monkeypatch, scheduler_tasks=tasks)
        assert "⏰ 今日提醒（2 个）：" in text
        assert "08:30 晨会" in text
        assert "14:00 项目评审" in text

    def test_no_reminders_message(self, make_engine, monkeypatch):
        """没有今日提醒时显示空状态。"""
        engine, _, _ = make_engine()
        text = self._build(engine, monkeypatch, scheduler_tasks=[])
        assert "⏰ 今日无待触发提醒" in text

    def test_deadline_summary_section(self, make_engine, monkeypatch):
        """截止日期摘要区块。"""
        tracker = MagicMock()
        tracker.get_summary.return_value = "  📌 报告: 还剩 3 天"
        engine, _, _ = make_engine(tracker=tracker)
        text = self._build(engine, monkeypatch)
        assert "📋 活跃截止日期：" in text
        assert "  📌 报告: 还剩 3 天" in text

    def test_deadline_empty_summary_skipped(self, make_engine, monkeypatch):
        tracker = MagicMock()
        tracker.get_summary.return_value = ""
        engine, _, _ = make_engine(tracker=tracker)
        text = self._build(engine, monkeypatch)
        assert "活跃截止日期" not in text

    def test_monitor_status_section(self, make_engine, monkeypatch):
        """系统状态区块。"""
        monitor = MagicMock()
        monitor.get_status.return_value = {
            "cpu_percent": 42.0, "memory_percent": 61.0, "disk_free_gb": 88.0,
        }
        engine, _, _ = make_engine(monitor=monitor)
        text = self._build(engine, monkeypatch)
        assert "💻 系统状态：CPU 42.0% | 内存 61.0% | 磁盘剩余 88.0GB" in text

    def test_monitor_error_skipped(self, make_engine, monkeypatch):
        """monitor 返回 error 时跳过该区块。"""
        monitor = MagicMock()
        monitor.get_status.return_value = {"error": "psutil 未安装"}
        engine, _, _ = make_engine(monitor=monitor)
        text = self._build(engine, monkeypatch)
        assert "系统状态" not in text

    def test_calendar_section(self, make_engine, monkeypatch):
        """日历事件区块。"""
        calendar = MagicMock()
        calendar.get_today_events.return_value = ["09:00 评审", "14:00 面试"]
        engine, _, _ = make_engine(calendar=calendar)
        text = self._build(engine, monkeypatch)
        assert "📆 今日日程（2 个）：" in text
        assert "  • 09:00 评审" in text

    def test_calendar_more_than_ten_truncated(self, make_engine, monkeypatch):
        """超过 10 个事件时只显示前 10 个。"""
        calendar = MagicMock()
        calendar.get_today_events.return_value = [f"事件{i}" for i in range(15)]
        engine, _, _ = make_engine(calendar=calendar)
        text = self._build(engine, monkeypatch)
        assert text.count("  • 事件") == 10

    def test_fire_briefing_notifies(self, make_engine, monkeypatch):
        """_fire_briefing 应把组装好的简报发给 on_notify。"""
        engine, _, notifications = make_engine()
        text = self._build(engine, monkeypatch)
        monkeypatch.setattr(engine, "_build_briefing", lambda: text)
        engine._fire_briefing()
        assert notifications == [text]

    def test_fire_briefing_empty_text_no_notify(self, make_engine, monkeypatch):
        engine, _, notifications = make_engine()
        monkeypatch.setattr(engine, "_build_briefing", lambda: "")
        engine._fire_briefing()
        assert notifications == []

    def test_fire_briefing_exception_swallowed(self, make_engine, monkeypatch):
        def boom():
            raise RuntimeError("简报生成失败")

        engine, _, notifications = make_engine()
        monkeypatch.setattr(engine, "_build_briefing", boom)
        engine._fire_briefing()  # 不应抛异常
        assert notifications == []


# ---------------------------------------------------------------------------
# 截止日期 / 日历检查
# ---------------------------------------------------------------------------


class TestChecks:
    """_fire_deadline_check / _fire_calendar_check。"""

    def test_deadline_check_notifies(self, make_engine):
        tracker = MagicMock()
        tracker.check_today.return_value = ["msg1", "msg2"]
        engine, _, notifications = make_engine(tracker=tracker)
        engine._fire_deadline_check()
        assert notifications == ["📋 截止日期提醒：\nmsg1\nmsg2"]

    def test_deadline_check_no_messages(self, make_engine):
        tracker = MagicMock()
        tracker.check_today.return_value = []
        engine, _, notifications = make_engine(tracker=tracker)
        engine._fire_deadline_check()
        assert notifications == []

    def test_deadline_check_without_tracker(self, make_engine):
        engine, _, notifications = make_engine()
        engine._fire_deadline_check()  # 无 tracker 直接返回
        assert notifications == []

    def test_deadline_check_exception_swallowed(self, make_engine):
        tracker = MagicMock()
        tracker.check_today.side_effect = RuntimeError("boom")
        engine, _, notifications = make_engine(tracker=tracker)
        engine._fire_deadline_check()
        assert notifications == []

    def test_calendar_check_notifies(self, make_engine):
        calendar = MagicMock()
        calendar.get_upcoming_events.return_value = ["30分钟后: 09:30 会议 @ 3F"]
        engine, _, notifications = make_engine(calendar=calendar)
        engine._fire_calendar_check()
        assert notifications == ["📆 日程提醒：\n  • 30分钟后: 09:30 会议 @ 3F"]
        # 使用配置中的提前提醒分钟数
        calendar.get_upcoming_events.assert_called_once_with(minutes_before=30)

    def test_calendar_check_no_upcoming(self, make_engine):
        calendar = MagicMock()
        calendar.get_upcoming_events.return_value = []
        engine, _, notifications = make_engine(calendar=calendar)
        engine._fire_calendar_check()
        assert notifications == []

    def test_calendar_check_without_source(self, make_engine):
        engine, _, notifications = make_engine()
        engine._fire_calendar_check()
        assert notifications == []

    def test_calendar_check_exception_swallowed(self, make_engine):
        calendar = MagicMock()
        calendar.get_upcoming_events.side_effect = RuntimeError("boom")
        engine, _, notifications = make_engine(calendar=calendar)
        engine._fire_calendar_check()
        assert notifications == []
