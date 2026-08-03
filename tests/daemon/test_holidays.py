"""中国法定节假日模块（agent.core.daemon.holidays）单元测试。

覆盖:
- 2026 年节假日 / 调休数据查询接口
- is_holiday / is_workday / get_holiday_name 的字符串、datetime、None 三种入参
- check_tomorrow_holiday 提醒触发逻辑（首日 / 假期中 / 非节假日）
- get_upcoming_holidays 未来节假日枚举

时间通过 FakeDatetime 注入固定时间，保证断言可确定。

@author aceFelix
"""

from __future__ import annotations

from datetime import datetime

import pytest

import agent.core.daemon.holidays as hol
from tests.daemon._fakes import FakeDatetime


@pytest.fixture
def env(monkeypatch):
    """注入固定时钟（默认 2026-01-01 10:00）。"""
    FakeDatetime.set_now("2026-01-01 10:00:00")
    monkeypatch.setattr(hol, "datetime", FakeDatetime)
    return hol


# ---------------------------------------------------------------------------
# 数据查询
# ---------------------------------------------------------------------------


class TestData:
    """年度节假日数据。"""

    def test_holidays_for_2026(self):
        assert hol._get_holidays_for_year(2026) is hol.HOLIDAYS_2026
        assert "2026-01-01" in hol._get_holidays_for_year(2026)

    def test_holidays_other_year_empty(self):
        assert hol._get_holidays_for_year(2027) == {}

    def test_workdays_for_2026(self):
        assert hol._get_workdays_for_year(2026) is hol.WORKDAYS_2026
        assert "2026-02-14" in hol._get_workdays_for_year(2026)

    def test_workdays_other_year_empty(self):
        assert hol._get_workdays_for_year(2027) == {}


# ---------------------------------------------------------------------------
# is_holiday
# ---------------------------------------------------------------------------


class TestIsHoliday:
    """节假日判断。"""

    def test_holiday_str(self):
        assert hol.is_holiday("2026-01-01") is True
        assert hol.is_holiday("2026-01-04") is False
        assert hol.is_holiday("2026-10-07") is True

    def test_holiday_other_year_no_data(self):
        assert hol.is_holiday("2027-01-01") is False

    def test_holiday_datetime(self):
        assert hol.is_holiday(datetime(2026, 1, 1, 12, 0)) is True
        assert hol.is_holiday(datetime(2026, 1, 5, 12, 0)) is False

    def test_holiday_none_uses_today(self, env):
        # 假今天 2026-01-01（元旦）→ True
        assert hol.is_holiday() is True
        FakeDatetime.set_now("2026-01-04 10:00:00")
        assert hol.is_holiday() is False


# ---------------------------------------------------------------------------
# is_workday
# ---------------------------------------------------------------------------


class TestIsWorkday:
    """工作日判断（含调休）。"""

    def test_workday_weekday(self):
        # 2026-01-05 是周一
        assert hol.is_workday("2026-01-05") is True

    def test_weekend_not_workday(self):
        # 2026-01-04 是周日
        assert hol.is_workday("2026-01-04") is False

    def test_holiday_not_workday(self):
        assert hol.is_workday("2026-01-01") is False

    def test_adjust_workday_saturday(self):
        # 2026-02-14 调休上班（周六）
        assert hol.is_workday("2026-02-14") is True

    def test_adjust_workday_sunday(self):
        # 2026-02-15 调休上班（周日）
        assert hol.is_workday("2026-02-15") is True

    def test_workday_datetime(self):
        assert hol.is_workday(datetime(2026, 1, 5, 8, 0)) is True

    def test_workday_none_uses_today(self, env):
        # 假今天 2026-01-01（节假日）→ False
        assert hol.is_workday() is False
        FakeDatetime.set_now("2026-01-05 10:00:00")
        assert hol.is_workday() is True


# ---------------------------------------------------------------------------
# get_holiday_name
# ---------------------------------------------------------------------------


class TestGetHolidayName:
    """节假日名称。"""

    def test_name_found(self):
        assert hol.get_holiday_name("2026-01-01") == "元旦"
        assert hol.get_holiday_name("2026-02-17") == "春节（除夕）"
        assert hol.get_holiday_name("2026-10-01") == "国庆节"

    def test_name_none_for_workday(self):
        assert hol.get_holiday_name("2026-01-05") is None

    def test_name_datetime(self):
        assert hol.get_holiday_name(datetime(2026, 5, 1, 9, 0)) == "劳动节"

    def test_name_none_uses_today(self, env):
        assert hol.get_holiday_name() == "元旦"
        FakeDatetime.set_now("2026-01-05 10:00:00")
        assert hol.get_holiday_name() is None


# ---------------------------------------------------------------------------
# check_tomorrow_holiday
# ---------------------------------------------------------------------------


class TestCheckTomorrowHoliday:
    """明天放假的提醒逻辑。"""

    def test_tomorrow_not_holiday_returns_none(self, env):
        # 2026-01-05（周一）明天 01-06 不是节假日
        FakeDatetime.set_now("2026-01-05 20:00:00")
        assert hol.check_tomorrow_holiday() is None

    def test_holiday_first_day_workday_today(self, env):
        # 春节前最后一个工作日 2026-02-16（周一），明天 02-17 除夕
        FakeDatetime.set_now("2026-02-16 20:00:00")
        text = hol.check_tomorrow_holiday()
        assert text == "先生，明天开始放春节（除夕）假，记得安排好手头的事。"

    def test_already_in_holiday_returns_none(self, env):
        # 2026-02-20 正在春节假期中，明天也是假期 → 不重复提醒
        FakeDatetime.set_now("2026-02-20 09:00:00")
        assert hol.check_tomorrow_holiday() is None

    def test_first_day_weekend_today(self, env, monkeypatch):
        # 明天是节假日首日且今天非工作日（周末）→ 假期愉快文案
        FakeDatetime.set_now("2026-04-03 20:00:00")  # 明天 04-04 清明（周六）
        monkeypatch.setattr(hol, "is_workday", lambda *a: False)
        text = hol.check_tomorrow_holiday()
        assert text == "先生，明天是清明节，祝您假期愉快。"


# ---------------------------------------------------------------------------
# get_upcoming_holidays
# ---------------------------------------------------------------------------


class TestUpcomingHolidays:
    """未来节假日枚举。"""

    def test_upcoming_within_days(self, env):
        # 假今天 2026-01-01，未来 5 天包含元旦三天
        FakeDatetime.set_now("2026-01-01 10:00:00")
        result = hol.get_upcoming_holidays(days=5)
        assert ("2026-01-01", "元旦") in result
        assert ("2026-01-03", "元旦") in result
        assert len(result) == 3
        # 按日期升序
        assert [d for d, _ in result] == sorted(d for d, _ in result)

    def test_upcoming_empty_when_no_holiday(self, env):
        # 2026-01-05 后 5 天内无节假日（01-06 ~ 01-10）
        FakeDatetime.set_now("2026-01-05 10:00:00")
        assert hol.get_upcoming_holidays(days=5) == []
