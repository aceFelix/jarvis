"""定时任务调度器（agent.core.daemon.scheduler）单元测试。

覆盖:
- ScheduleTask 数据模型：序列化往返、trigger_time 解析
- Scheduler 任务管理：注册 / 取消 / 确认 / 查询 / 清理
- 到点触发：_check_and_fire / _fire_task / 重复任务下次时间计算
- 生命周期：start / stop / _run 主循环
- 持久化：_save / _load / 错过补偿逻辑

时间类逻辑通过 FakeDatetime 注入固定时钟，持久化文件通过
monkeypatch _schedule_file 重定向到 tmp_path，避免污染真实主目录。

@author aceFelix
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from agent.core.daemon.scheduler import ScheduleTask, Scheduler
from tests.daemon._fakes import FakeDatetime


class _FakeThread:
    """不真正启动线程的替身，避免测试留下后台线程。"""

    def __init__(self, target=None, daemon=None, name=None):
        self.target = target
        self.name = name
        self.started = False

    def start(self):
        self.started = True

    def join(self, timeout=None):
        pass


@pytest.fixture
def scheduler_mod(monkeypatch, tmp_path):
    """注入固定时钟 + 重定向持久化文件，返回 scheduler 模块。"""
    import agent.core.daemon.scheduler as mod

    FakeDatetime.set_now("2026-01-01 10:00:00")
    monkeypatch.setattr(mod, "datetime", FakeDatetime)
    monkeypatch.setattr(mod, "_schedule_file", lambda: tmp_path / "schedule.json")
    monkeypatch.setattr(mod.threading, "Thread", _FakeThread)
    return mod


@pytest.fixture
def make_scheduler(scheduler_mod):
    """构造一个 Scheduler 工厂（已注入假时钟与 tmp 持久化）。"""

    def _make(on_fire=None):
        fired = []
        if on_fire is None:
            on_fire = fired.append
        return Scheduler(on_fire=on_fire), fired

    return _make


# ---------------------------------------------------------------------------
# ScheduleTask 数据模型
# ---------------------------------------------------------------------------


class TestScheduleTask:
    """ScheduleTask 序列化与时间解析。"""

    def test_to_dict_from_dict_roundtrip(self):
        """to_dict → from_dict 应保持所有字段一致。"""
        task = ScheduleTask(
            id="abc123",
            trigger_at="2026-01-01T09:00:00",
            content="开会",
            repeat="daily",
            status="fired",
            created_at="2026-01-01T08:00:00",
            note="备注",
            acknowledged=True,
            escalate_count=2,
            max_escalate=5,
            last_fired_at="2026-01-01T09:00:00",
        )
        restored = ScheduleTask.from_dict(task.to_dict())
        assert restored == task

    def test_from_dict_missing_fields_use_defaults(self):
        """from_dict 缺字段时应回退默认值。"""
        task = ScheduleTask.from_dict({})
        assert task.id == ""
        assert task.repeat == "once"
        assert task.status == "pending"
        assert task.acknowledged is False
        assert task.escalate_count == 0
        assert task.max_escalate == 3

    def test_trigger_time_valid(self):
        """合法 ISO 字符串应解析为 datetime。"""
        task = ScheduleTask(trigger_at="2026-01-01T09:00:00")
        assert task.trigger_time == datetime(2026, 1, 1, 9, 0, 0)

    def test_trigger_time_invalid_returns_none(self):
        """非法字符串应返回 None 而非抛异常。"""
        assert ScheduleTask(trigger_at="not-a-date").trigger_time is None

    def test_trigger_time_empty_returns_none(self):
        """空字符串应返回 None。"""
        assert ScheduleTask(trigger_at="").trigger_time is None


# ---------------------------------------------------------------------------
# 任务管理
# ---------------------------------------------------------------------------


class TestTaskManagement:
    """Scheduler 任务增删改查。"""

    def test_add_task_creates_pending_task(self, make_scheduler):
        """add_task 应创建 pending 任务并持久化到文件。"""
        scheduler, _ = make_scheduler()
        task = scheduler.add_task(content="开会", trigger_at="2026-01-01T15:00:00", note="n1")

        assert task.status == "pending"
        assert task.repeat == "once"
        assert len(task.id) == 12
        assert task.note == "n1"
        assert task.trigger_at == "2026-01-01T15:00:00"

        # 持久化文件应包含该任务
        import agent.core.daemon.scheduler as mod

        data = json.loads(mod._schedule_file().read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["id"] == task.id

    def test_add_task_accepts_datetime_object(self, make_scheduler):
        """trigger_at 传 datetime 对象应转成 ISO 字符串。

        注：模块内 datetime 已被替换为 FakeDatetime（datetime 子类），
        用 FakeDatetime 构造入参以命中 isinstance 分支（行为与真实 datetime 一致）。
        """
        scheduler, _ = make_scheduler()
        task = scheduler.add_task(content="x", trigger_at=FakeDatetime(2026, 1, 2, 8, 30))
        assert task.trigger_at == "2026-01-02T08:30:00"

    def test_list_pending_sorted_by_trigger(self, make_scheduler):
        """list_pending 应按触发时间升序且只含 pending。"""
        scheduler, _ = make_scheduler()
        scheduler.add_task(content="晚", trigger_at="2026-01-01T20:00:00")
        early = scheduler.add_task(content="早", trigger_at="2026-01-01T08:00:00")
        scheduler.add_task(content="已取消", trigger_at="2026-01-01T09:00:00")
        scheduler.cancel_task(scheduler.list_all()[-1].id)

        pending = scheduler.list_pending()
        assert [t.content for t in pending] == ["早", "晚"]
        assert pending[0].id == early.id

    def test_list_all_contains_all_statuses(self, make_scheduler):
        """list_all 应包含已触发/已取消任务。"""
        scheduler, _ = make_scheduler()
        t1 = scheduler.add_task(content="a", trigger_at="2026-01-01T09:00:00")
        scheduler.cancel_task(t1.id)
        scheduler.add_task(content="b", trigger_at="2026-01-01T09:00:00")
        assert len(scheduler.list_all()) == 2

    def test_cancel_task_pending_only(self, make_scheduler):
        """cancel_task 只能取消 pending 任务。"""
        scheduler, _ = make_scheduler()
        task = scheduler.add_task(content="a", trigger_at="2026-01-01T09:00:00")
        assert scheduler.cancel_task(task.id) is True
        assert task.status == "cancelled"
        # 已取消的任务不能再取消
        assert scheduler.cancel_task(task.id) is False
        # 不存在的任务返回 False
        assert scheduler.cancel_task("no-such-id") is False

    def test_acknowledge_marks_task(self, make_scheduler):
        """acknowledge 应标记任务为已确认。"""
        scheduler, _ = make_scheduler()
        task = scheduler.add_task(content="a", trigger_at="2026-01-01T09:00:00")
        assert scheduler.acknowledge(task.id) is True
        assert task.acknowledged is True
        assert scheduler.acknowledge("no-such-id") is False

    def test_get_unacknowledged_fired_filters(self, make_scheduler):
        """get_unacknowledged_fired 只返回 fired + 未确认 + 未达升级上限的任务。"""
        scheduler, _ = make_scheduler()

        fireable = scheduler.add_task(content="可升级", trigger_at="2026-01-01T08:00:00")
        fireable.status = "fired"

        acked = scheduler.add_task(content="已确认", trigger_at="2026-01-01T08:00:00")
        acked.status = "fired"
        acked.acknowledged = True

        maxed = scheduler.add_task(content="已到上限", trigger_at="2026-01-01T08:00:00")
        maxed.status = "fired"
        maxed.escalate_count = maxed.max_escalate

        pending = scheduler.add_task(content="还没触发", trigger_at="2026-01-02T08:00:00")

        result = scheduler.get_unacknowledged_fired()
        assert [t.id for t in result] == [fireable.id]

    def test_clear_completed_keeps_recent(self, make_scheduler):
        """clear_completed 应保留 pending 与最近 keep_recent 条已结束任务。"""
        scheduler, _ = make_scheduler()
        pending = scheduler.add_task(content="待触发", trigger_at="2026-01-02T08:00:00")
        for i, hh in enumerate(("08", "09", "10")):
            t = scheduler.add_task(content=f"done-{hh}", trigger_at=f"2026-01-01T{hh}:00:00")
            t.status = "fired"

        cleaned = scheduler.clear_completed(keep_recent=2)
        assert cleaned == 1  # 最旧的一条 done-08 被清理
        all_tasks = scheduler.list_all()
        assert len(all_tasks) == 3
        assert all_tasks[0].id == pending.id
        remaining = {t.content for t in all_tasks}
        assert remaining == {"待触发", "done-09", "done-10"}

    def test_clear_completed_returns_zero_when_nothing_cleaned(self, make_scheduler):
        """没有可清理的任务时返回 0。"""
        scheduler, _ = make_scheduler()
        scheduler.add_task(content="a", trigger_at="2026-01-02T08:00:00")
        assert scheduler.clear_completed() == 0


# ---------------------------------------------------------------------------
# 到点触发
# ---------------------------------------------------------------------------


class TestFire:
    """任务到期触发逻辑。"""

    def test_check_and_fire_due_task(self, make_scheduler):
        """到期任务（trigger <= now）应被触发，一次性任务标记 fired。"""
        scheduler, fired = make_scheduler()
        task = scheduler.add_task(content="开会", trigger_at="2026-01-01T09:59:59")

        scheduler._check_and_fire()

        assert fired == [task]
        assert task.status == "fired"
        assert task.last_fired_at == "2026-01-01T10:00:00"

    def test_check_and_fire_future_task_not_fired(self, make_scheduler):
        """未到期任务不应被触发。"""
        scheduler, fired = make_scheduler()
        task = scheduler.add_task(content="开会", trigger_at="2026-01-01T10:00:01")

        scheduler._check_and_fire()

        assert fired == []
        assert task.status == "pending"

    def test_check_and_fire_invalid_trigger_skipped(self, make_scheduler):
        """trigger_at 解析失败的任务应被跳过。"""
        scheduler, fired = make_scheduler()
        scheduler.add_task(content="坏任务", trigger_at="bad-time")
        scheduler._check_and_fire()
        assert fired == []

    def test_fire_daily_task_updates_next_trigger(self, make_scheduler):
        """daily 重复任务触发后应顺延到明天且保持 pending。"""
        scheduler, fired = make_scheduler()
        task = scheduler.add_task(
            content="每日提醒", trigger_at="2026-01-01T09:00:00", repeat="daily"
        )
        task.acknowledged = True
        task.escalate_count = 2

        scheduler._fire_task(task)

        assert fired == [task]
        assert task.status == "pending"
        assert task.trigger_at == "2026-01-02T09:00:00"
        # 每次触发重置确认与升级状态
        assert task.acknowledged is False
        assert task.escalate_count == 0
        assert task.last_fired_at == "2026-01-01T10:00:00"

    def test_fire_weekly_task_updates_next_trigger(self, make_scheduler):
        """weekly 重复任务触发后应顺延 7 天。"""
        scheduler, _ = make_scheduler()
        task = scheduler.add_task(
            content="周会", trigger_at="2026-01-01T09:00:00", repeat="weekly"
        )
        scheduler._fire_task(task)
        assert task.trigger_at == "2026-01-08T09:00:00"
        assert task.status == "pending"

    def test_fire_once_task_marked_fired(self, make_scheduler):
        """一次性任务触发后应标记 fired。"""
        scheduler, _ = make_scheduler()
        task = scheduler.add_task(content="一次性", trigger_at="2026-01-01T09:00:00")
        scheduler._fire_task(task)
        assert task.status == "fired"

    def test_fire_callback_exception_is_swallowed(self, make_scheduler):
        """回调抛异常不应影响任务状态更新。"""
        def boom(task):
            raise RuntimeError("回调失败")

        scheduler, _ = make_scheduler(boom)
        task = scheduler.add_task(content="a", trigger_at="2026-01-01T09:00:00")
        scheduler._fire_task(task)
        assert task.status == "fired"

    def test_calc_next_trigger(self):
        """_calc_next_trigger 应按重复模式计算下次时间。"""
        once = ScheduleTask(trigger_at="2026-01-01T09:00:00", repeat="once")
        assert Scheduler._calc_next_trigger(once) is None

        daily = ScheduleTask(trigger_at="2026-01-01T09:00:00", repeat="daily")
        assert Scheduler._calc_next_trigger(daily) == datetime(2026, 1, 2, 9, 0, 0)

        weekly = ScheduleTask(trigger_at="2026-01-01T09:00:00", repeat="weekly")
        assert Scheduler._calc_next_trigger(weekly) == datetime(2026, 1, 8, 9, 0, 0)

        unknown = ScheduleTask(trigger_at="2026-01-01T09:00:00", repeat="hourly")
        assert Scheduler._calc_next_trigger(unknown) is None

        invalid = ScheduleTask(trigger_at="bad", repeat="daily")
        assert Scheduler._calc_next_trigger(invalid) is None


# ---------------------------------------------------------------------------
# 生命周期与主循环
# ---------------------------------------------------------------------------


class TestLifecycle:
    """start / stop / _run。"""

    def test_start_loads_persisted_tasks(self, scheduler_mod, tmp_path):
        """start 时应从文件加载任务，且不重复启动。"""
        path = tmp_path / "schedule.json"
        path.write_text(
            json.dumps([
                {"id": "saved1", "trigger_at": "2026-01-01T11:00:00", "content": "已存任务"}
            ]),
            encoding="utf-8",
        )
        scheduler = Scheduler(on_fire=lambda t: None)
        scheduler.start()

        assert scheduler._started is True
        assert scheduler._thread is not None
        assert [t.id for t in scheduler.list_all()] == ["saved1"]

        # 第二次 start 幂等，不重新加载
        scheduler._tasks.append(ScheduleTask(id="extra"))
        scheduler.start()
        assert len(scheduler.list_all()) == 2

        scheduler.stop()
        assert scheduler._started is False
        assert scheduler._thread is None

    def test_stop_without_start_is_safe(self, make_scheduler):
        """未启动直接 stop 不应抛异常。"""
        scheduler, _ = make_scheduler()
        scheduler.stop()

    def test_run_exits_when_stop_event_set(self, make_scheduler):
        """stop_event 已设置时 _run 应立即退出。"""
        scheduler, _ = make_scheduler()
        scheduler._stop_event.set()
        scheduler._run()  # 不应抛异常

    def test_run_swallows_check_exceptions(self, make_scheduler):
        """_check_and_fire 抛异常时主循环不应崩溃。"""
        scheduler, _ = make_scheduler()
        scheduler._stop_event.set()
        scheduler._check_and_fire = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        scheduler._run()  # 异常应被吞掉


# ---------------------------------------------------------------------------
# 持久化与错过补偿
# ---------------------------------------------------------------------------


class TestPersistence:
    """_save / _load / 错过补偿。"""

    def test_load_missed_compensation(self, scheduler_mod, tmp_path):
        """启动加载时应补偿错过的任务：超过 1 小时标记 fired，1 小时内保留。"""
        path = tmp_path / "schedule.json"
        # 假时间 2026-01-01 10:00:00
        path.write_text(
            json.dumps([
                # 一次性，错过 2 小时 → 标记 fired（防止开机轰炸）
                {"id": "missed-2h", "trigger_at": "2026-01-01T08:00:00", "repeat": "once"},
                # 一次性，错过 30 分钟 → 保留 pending，启动后立即触发
                {"id": "missed-30m", "trigger_at": "2026-01-01T09:30:00", "repeat": "once"},
                # 每日任务，错过 2 小时 → 保持 pending（重复任务不补偿标记）
                {"id": "daily-old", "trigger_at": "2026-01-01T08:00:00", "repeat": "daily"},
            ]),
            encoding="utf-8",
        )
        scheduler = Scheduler(on_fire=lambda t: None)
        scheduler._load()

        by_id = {t.id: t for t in scheduler.list_all()}
        assert by_id["missed-2h"].status == "fired"
        assert by_id["missed-30m"].status == "pending"
        assert by_id["daily-old"].status == "pending"

    def test_load_missing_file_is_noop(self, make_scheduler):
        """持久化文件不存在时加载为空列表。"""
        scheduler, _ = make_scheduler()
        scheduler._load()
        assert scheduler.list_all() == []

    def test_load_corrupt_json_resets_tasks(self, scheduler_mod, tmp_path):
        """JSON 损坏时 _tasks 应重置为空而非崩溃。"""
        path = tmp_path / "schedule.json"
        path.write_text("{ not valid json !!!", encoding="utf-8")
        scheduler = Scheduler(on_fire=lambda t: None)
        scheduler._load()
        assert scheduler.list_all() == []
