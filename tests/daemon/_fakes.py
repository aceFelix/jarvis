"""daemon 测试共用替身工具。

提供可注入的假时钟（FakeDatetime / FakeDate），让时间类逻辑测试
不依赖真实系统时间，保证测试可重复、可确定。

用法::

    monkeypatch.setattr(scheduler_module, "datetime", FakeDatetime)
    FakeDatetime.set_now("2026-01-01 10:00:00")

@author aceFelix
"""

from __future__ import annotations

from datetime import date, datetime


class FakeDatetime(datetime):
    """datetime 替身：now() 返回注入的固定时间（默认 2026-01-01 10:00:00）。

    继承真实 datetime，保证 fromisoformat / replace / strftime / 比较运算
    行为与真实 datetime 一致，仅覆盖 now() 返回固定值。
    注意：_now 必须存 FakeDatetime 实例，否则 isinstance(dt, FakeDatetime)
    判断（如 holidays.is_holiday）会失效。
    """

    _now: datetime = datetime(2026, 1, 1, 10, 0, 0)

    @classmethod
    def set_now(cls, dt: datetime | str) -> None:
        """设置当前假时间。支持 datetime 对象或 "YYYY-MM-DD HH:MM:SS" 字符串。"""
        if isinstance(dt, str):
            dt = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
        cls._now = cls(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond)

    @classmethod
    def now(cls, tz=None) -> datetime:  # noqa: D102
        return cls._now


FakeDatetime._now = FakeDatetime(2026, 1, 1, 10, 0, 0)


class FakeDate(date):
    """date 替身：today() 返回注入的固定日期（默认 2026-01-01）。"""

    _today: date = date(2026, 1, 1)

    @classmethod
    def set_today(cls, d: date | str) -> None:
        """设置当前假日期。支持 date 对象或 "YYYY-MM-DD" 字符串。"""
        if isinstance(d, str):
            d = date.fromisoformat(d)
        cls._today = cls(d.year, d.month, d.day)

    @classmethod
    def today(cls) -> date:  # noqa: D102
        return cls._today


FakeDate._today = FakeDate(2026, 1, 1)
