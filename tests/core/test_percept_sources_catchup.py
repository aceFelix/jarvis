"""收编型感知源（calendar / system）的测试（阶段 11）。

盯的是一条已经踩过的链路：这两个源的底层能力（``daemon/calendar_source.py``、
``daemon/monitor.py``）在阶段 11 之前一直是**休眠件**——代码写好但从没被宿主装配过。
所以「能构造」不算完，必须验四件事：

1. **依赖缺失不抛**：没有日历后端 / 没装 psutil 时返回空列表并记 ``unavailable``，
   抛出去会让总线的"连败自动停用"把源标成坏了（见 ``factory.py`` 顶部同类取舍）；
2. **只报该报的**：日程要落在提前窗口内、已开始和全天的不报；
3. **不重复播报**：同一条日程反复采集只出一次——日历源这里特别容易错，
   因为公开接口 ``get_upcoming_events()`` 把"17分钟后"写进了文本，
   拿它当去重依据等于永不命中；
4. **``state()`` 不做重活**：资源源的 ``get_status()`` 内含 ``cpu_percent(interval=1)``，
   会阻塞一秒，面板快照绝不能调它。

@author aceFelix
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from agent.core.daemon.calendar_source import CalendarEvent
from agent.core.daemon.monitor import AlertInfo
from agent.core.percept.event import SOURCE_CALENDAR, SOURCE_SYSTEM
from agent.core.percept.source_calendar import CalendarSource
from agent.core.percept.source_system import SystemSource


@pytest.fixture(autouse=True)
def _isolated_announced_ledger(tmp_path, monkeypatch):
    """隔离「今天已报过」账本：任何用例都不许读写真实的 ``~/.jarvis`` 目录。

    不隔离会有两个后果：一是测试结果依赖本机真实日程历史（昨天报过的键会让
    ``collect()`` 返回空），二是跨天重置会把脏键写进用户目录。
    """
    monkeypatch.setattr(
        "agent.core.percept.source_calendar._STATE_FILE",
        tmp_path / "calendar_announced.json",
    )


class _FakeCalendarBackend:
    """假日历后端：只实现本源要用的两个成员（``available`` 与按日取事件）。"""

    def __init__(self, events=(), *, available=True):
        self.available = available
        self._events = list(events)
        self._backend = "ics"

    def _get_events_for_date(self, _day):
        return list(self._events)


def _cal(events=(), *, available=True, lead=30) -> CalendarSource:
    backend = _FakeCalendarBackend(events, available=available)
    return CalendarSource(calendar=backend, lead_minutes=lead)


def _at(minutes: float, title: str = "项目周会", **kw) -> CalendarEvent:
    """造一条相对「现在」偏移的日程（测试不能依赖真实日历）。"""
    start = datetime.now() + timedelta(minutes=minutes)
    return CalendarEvent(title=title, start=start, end=start + timedelta(minutes=30), **kw)


# ---- 日历源 ----


def test_calendar_reports_event_inside_lead_window():
    src = _cal([_at(10)])
    events = src.collect()
    assert len(events) == 1
    ev = events[0]
    assert ev.source == SOURCE_CALENDAR and ev.kind == "event_upcoming"
    assert "项目周会" in ev.summary()
    # 构造到采集之间过了几毫秒，取整后可能是 9：用区间断言，不卡死边界
    assert 9 <= ev.payload["minutes_until"] <= 10
    assert ev.salience_hint > 0.5      # 越近越大


def test_calendar_skips_events_outside_window():
    """还没进提前窗口的不报（报了就成噪音），已经开始的也不报。"""
    assert _cal([_at(120)]).collect() == []
    assert _cal([_at(-5)]).collect() == []


def test_calendar_skips_all_day_events():
    """全天事件没有具体时刻，按"临近"播报没有意义。"""
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    ev = CalendarEvent(title="国庆", start=start, all_day=True)
    assert _cal([ev]).collect() == []


def test_calendar_reports_each_event_once():
    """同一条日程反复采集只出一次——这正是「17分钟后」文本去重会漏的那种。"""
    src = _cal([_at(20)])
    first = src.collect()
    second = src.collect()
    assert len(first) == 1
    assert second == []
    assert src.skipped_repeat == 1
    assert src.state()["announced_today"] == 1


def test_calendar_dedup_key_is_stable_across_minutes(tmp_path):
    """去重键不含"还有几分钟"：同一条日程在不同轮次算出同一个键。

    这里刻意给第二个实例一个**独立账本**：本用例验的是键本身，不能让它读到
    第一个实例刚落盘的"已报过"（那是另一个用例要验的）。
    """
    ev = _at(20)
    src = _cal([ev])
    first = src.collect()[0]
    again = CalendarSource(
        calendar=_FakeCalendarBackend([ev]),
        state_path=tmp_path / "separate_ledger.json",
    ).collect()[0]
    assert first.key() == again.key()


def test_calendar_unavailable_backend_is_reported_not_raised():
    """没配 Outlook / ICS 时是"开了但没配齐"，不是故障：返回空 + 记原因，不抛。"""
    src = _cal([], available=False)
    assert src.collect() == []
    assert "日历后端" in src.unavailable
    # 再采一次也不抛（避免每轮都炸一次日志）
    assert src.collect() == []


def test_calendar_resets_announced_set_on_new_day():
    """同一天内不清空（去重仍生效），跨天才清（否则内存随天数线性涨）。"""
    src = _cal([_at(5)])
    src.collect()
    assert src.state()["announced_today"] == 1
    src.collect()                      # 同一天再采：已报集合不清
    assert src.state()["announced_today"] == 1
    src._reset_day(date.today() + timedelta(days=1))   # 换天
    assert src.state()["announced_today"] == 0


def test_calendar_announced_ledger_survives_rebuild(tmp_path):
    """账本落盘：总线热重装配（新实例）不该把同一条日程再报一遍。

    2026-10-26 实测暴露的缺陷就在这里：面板连点几次「保存」→ 每次重建总线 → 同一条
    日程被重复入库。“一天内每条日程只报一次”必须跳实例存在，否则重启几遍就念几遍。
    """
    ledger = tmp_path / "cal.json"
    ev = _at(10)                        # 两个实例共用同一条事件，去重键必然一致
    first = CalendarSource(calendar=_FakeCalendarBackend([ev]), state_path=ledger)
    assert len(first.collect()) == 1
    assert ledger.exists()

    second = CalendarSource(calendar=_FakeCalendarBackend([ev]), state_path=ledger)
    assert second.collect() == []            # 新实例读回账本 → 不重报
    assert second.state()["announced_today"] == 1


def test_calendar_ledger_from_other_day_is_ignored(tmp_path):
    """隔天的账本当空账本：昨天的键不能压住今天该报的日程。"""
    ledger = tmp_path / "cal.json"
    ledger.write_text(
        json.dumps({"day": "2000-01-01", "keys": ["2000-01-01T09:00:早会"]}),
        encoding="utf-8",
    )
    src = CalendarSource(calendar=_FakeCalendarBackend([_at(10)]), state_path=ledger)
    assert len(src.collect()) == 1


def test_calendar_backend_failure_is_contained():
    """后端读日历抛异常时只能记原因，不能让异常冒到总线。"""

    class _Boom(_FakeCalendarBackend):
        def _get_events_for_date(self, _day):
            raise RuntimeError("com 崩了")

    src = CalendarSource(calendar=_Boom())
    assert src.collect() == []
    assert "读取日历失败" in src.unavailable


def test_calendar_state_reports_config_shape():
    """state() 要给面板看出"用的哪个后端、有没有配 ICS、提前量多少"。"""
    src = CalendarSource(
        calendar=_FakeCalendarBackend(), backend="ics",
        ics_path="C:/tmp/a.ics", lead_minutes=15,
    )
    st = src.state()
    assert st["lead_minutes"] == 15
    assert st["ics_configured"] is True
    assert st["backend"] == "ics"


# ---- 系统资源源 ----


class _FakeMonitor:
    """假监控器：``check_now`` 返固定告警，并记录 ``get_status`` 是否被调用。"""

    def __init__(self, alerts=(), *, available=True, boom=False):
        self.available = available
        self._alerts = list(alerts)
        self._boom = boom
        self.get_status_calls = 0

    def check_now(self):
        if self._boom:
            raise RuntimeError("psutil 炸了")
        return list(self._alerts)

    def get_status(self):
        self.get_status_calls += 1
        return {"cpu_percent": 1.0}


def _alert(kind="cpu", level="critical", message="CPU 使用率 95%", **kw) -> AlertInfo:
    return AlertInfo(
        alert_type=kind, level=level, message=message,
        value=kw.get("value", 95.0), threshold=kw.get("threshold", 85.0),
    )


def test_system_turns_alerts_into_events():
    src = SystemSource(monitor=_FakeMonitor([_alert()]))
    events = src.collect()
    assert len(events) == 1
    ev = events[0]
    assert ev.source == SOURCE_SYSTEM and ev.kind == "cpu_alert"
    assert ev.payload["summary"] == "CPU 使用率 95%"
    assert ev.payload["level"] == "critical"


def test_system_salience_follows_level():
    """critical 值得立刻说，warning 只算背景信息。"""
    crit = SystemSource(monitor=_FakeMonitor([_alert(level="critical")])).collect()[0]
    warn = SystemSource(monitor=_FakeMonitor([_alert(level="warning")])).collect()[0]
    assert crit.salience_hint > warn.salience_hint


def test_system_dedup_key_groups_same_type_and_level():
    """同类同级的告警交给总线在去重窗口里合并（monitor 侧还有更长的冷却）。"""
    a = SystemSource(monitor=_FakeMonitor([_alert(value=91.0)])).collect()[0]
    b = SystemSource(monitor=_FakeMonitor([_alert(value=99.0)])).collect()[0]
    assert a.key() == b.key()


def test_system_skips_alerts_without_message():
    src = SystemSource(monitor=_FakeMonitor([_alert(message="")]))
    assert src.collect() == []


def test_system_drops_recovery_notices():
    """恢复通知（level=recovery）不进总线：它不是新事实，花一次打扰预算说“没事了”不值。

    monitor 会在告警解除时发一条 recovery（如“CPU 已回落到 42%”）。若照 warning 发出去，
    无论权重多少都会占用一次播报额度，而用户并没有可行动的信息。
    """
    src = SystemSource(monitor=_FakeMonitor([
        _alert(kind="cpu", level="recovery", message="CPU 使用率已回落到 42%"),
    ]))
    assert src.collect() == []
    assert src.state()["published"] == 0


def test_system_unavailable_psutil_is_reported_not_raised():
    src = SystemSource(monitor=_FakeMonitor([_alert()], available=False))
    assert src.collect() == []
    assert "psutil" in src.unavailable


def test_system_check_failure_is_contained():
    src = SystemSource(monitor=_FakeMonitor(boom=True))
    assert src.collect() == []
    assert "采样失败" in src.unavailable


def test_system_state_never_samples():
    """state() 不能调 get_status()——那里面的 cpu_percent(interval=1) 会阻塞一秒。"""
    monitor = _FakeMonitor([_alert()])
    src = SystemSource(monitor=monitor)
    src.collect()
    st = src.state()
    assert monitor.get_status_calls == 0
    assert st["published"] == 1
    assert st["last_alert"] == "CPU 使用率 95%"
    assert st["thresholds"]["cpu_threshold"] == 85.0


def test_system_thresholds_come_from_settings():
    """阈值必须来自 settings.monitor_*，不另造一份（否则面板调的不是生效值）。"""
    src = SystemSource(cpu_threshold=70.0, memory_threshold=80.0, disk_threshold=5.0,
                       alert_cooldown=120, disk_trend_days=3, work_break_interval=3600)
    th = src.state()["thresholds"]
    # ``work_break_interval`` 也在这张表里：久坐提醒的判定在 ``SystemMonitor._do_check``，
    # 而本源是它唯一的驱动点，不传下来字段就改不动。
    assert th == {
        "cpu_threshold": 70.0, "memory_threshold": 80.0, "disk_threshold": 5.0,
        "alert_cooldown": 120, "disk_trend_days": 3, "work_break_interval": 3600,
    }


@pytest.mark.parametrize("source_name", [SOURCE_CALENDAR, SOURCE_SYSTEM])
def test_sources_expose_bus_contract(source_name):
    """两个源都要满足总线契约：有 name / interval_s / collect / close / state。"""
    src = _cal() if source_name == SOURCE_CALENDAR else SystemSource(monitor=_FakeMonitor())
    assert src.name == source_name
    assert src.interval_s > 0
    assert callable(src.collect) and callable(src.close) and callable(src.state)
    src.close()
