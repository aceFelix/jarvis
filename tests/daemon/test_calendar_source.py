"""日历数据源（agent.core.daemon.calendar_source）单元测试。

覆盖:
- CalendarConfig / CalendarEvent 数据模型（字符串渲染、距开始分钟数）
- CalendarSource 后端选择：auto / outlook / ics / 不可用
- Outlook COM 后端：模拟 win32com 链式调用 + 单条失败跳过
- ICS 后端：本地文件 / URL 抓取、缓存命中与过期、事件过滤
- ICS 解析：VEVENT 解析、行折叠、TZID / 全天事件 / 时间格式
- 外部调用（win32com、winreg、urllib、系统时间）全部 mock / 注入

@author aceFelix
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.core.daemon.calendar_source import CalendarConfig, CalendarEvent, CalendarSource
from tests.daemon._fakes import FakeDate, FakeDatetime


def _fake_module(name, **attrs):
    """构造一个可被 import 的假模块（ModuleType 支持 __spec__，避免 import 报错）。"""
    m = ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m

# 一段标准 ICS 内容（两个事件：一个普通会议、一个全天事件）
ICS_SAMPLE = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:项目评审
DTSTART:20260101T093000
DTEND:20260101T103000
LOCATION:3楼会议室
END:VEVENT
BEGIN:VEVENT
SUMMARY:团建
DTSTART;VALUE=DATE:20260102
DTEND;VALUE=DATE:20260103
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture
def env(monkeypatch):
    """注入固定时钟。"""
    import agent.core.daemon.calendar_source as mod

    FakeDatetime.set_now("2026-01-01 10:00:00")
    FakeDate.set_today("2026-01-01")
    monkeypatch.setattr(mod, "datetime", FakeDatetime)
    monkeypatch.setattr(mod, "date", FakeDate)
    return mod


@pytest.fixture
def ics_file(tmp_path):
    """写入一个临时 ICS 文件。"""

    def _write(content=ICS_SAMPLE, name="cal.ics"):
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return str(path)

    return _write


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


class TestModels:
    """CalendarConfig / CalendarEvent。"""

    def test_config_defaults(self):
        cfg = CalendarConfig()
        assert cfg.enabled is False
        assert cfg.backend == "auto"
        assert cfg.ics_path == ""
        assert cfg.ics_url == ""
        assert cfg.remind_minutes_before == 30
        assert cfg.cache_ttl_seconds == 1800

    def test_event_str_with_end_and_location(self):
        ev = CalendarEvent(
            title="会议", start=FakeDatetime(2026, 1, 1, 9, 30),
            end=FakeDatetime(2026, 1, 1, 10, 30), location="3F",
        )
        assert str(ev) == "09:30-10:30 会议 @ 3F"

    def test_event_str_without_end(self):
        ev = CalendarEvent(title="晨会", start=FakeDatetime(2026, 1, 1, 9, 0))
        assert str(ev) == "09:00 晨会"

    def test_event_str_all_day(self):
        ev = CalendarEvent(title="团建", start=FakeDatetime(2026, 1, 2), all_day=True)
        assert str(ev) == "[全天] 团建"

    def test_minutes_until_start(self, env):
        # 假时间 10:00，事件 10:30 开始 → 30 分钟后
        ev = CalendarEvent(title="x", start=FakeDatetime(2026, 1, 1, 10, 30))
        assert ev.minutes_until_start == 30.0

    def test_minutes_until_start_negative_when_past(self, env):
        ev = CalendarEvent(title="x", start=FakeDatetime(2026, 1, 1, 9, 30))
        assert ev.minutes_until_start == -30.0


# ---------------------------------------------------------------------------
# 后端选择
# ---------------------------------------------------------------------------


class TestBackendResolution:
    """_resolve_backend / available。"""

    def test_available_false_when_disabled(self, env):
        source = CalendarSource(CalendarConfig(enabled=False))
        assert source.available is False

    def test_resolve_outlook(self, env, monkeypatch):
        source = CalendarSource(CalendarConfig(enabled=True, backend="outlook"))
        monkeypatch.setattr(source, "_outlook_available", lambda: True)
        assert source._resolve_backend() == "outlook"

    def test_resolve_outlook_unavailable(self, env, monkeypatch):
        source = CalendarSource(CalendarConfig(enabled=True, backend="outlook"))
        monkeypatch.setattr(source, "_outlook_available", lambda: False)
        assert source._resolve_backend() is None

    def test_resolve_ics_with_path(self, env):
        source = CalendarSource(CalendarConfig(enabled=True, backend="ics", ics_path="x.ics"))
        assert source._resolve_backend() == "ics"

    def test_resolve_ics_with_url(self, env):
        source = CalendarSource(CalendarConfig(enabled=True, backend="ics", ics_url="http://x"))
        assert source._resolve_backend() == "ics"

    def test_resolve_ics_without_config(self, env):
        source = CalendarSource(CalendarConfig(enabled=True, backend="ics"))
        assert source._resolve_backend() is None

    def test_resolve_auto_prefers_outlook(self, env, monkeypatch):
        source = CalendarSource(CalendarConfig(enabled=True, backend="auto"))
        monkeypatch.setattr(source, "_outlook_available", lambda: True)
        assert source._resolve_backend() == "outlook"

    def test_resolve_auto_falls_back_to_ics(self, env, monkeypatch):
        source = CalendarSource(CalendarConfig(enabled=True, backend="auto", ics_url="http://x"))
        monkeypatch.setattr(source, "_outlook_available", lambda: False)
        assert source._resolve_backend() == "ics"

    def test_resolve_auto_nothing(self, env, monkeypatch):
        source = CalendarSource(CalendarConfig(enabled=True, backend="auto"))
        monkeypatch.setattr(source, "_outlook_available", lambda: False)
        assert source._resolve_backend() is None

    def test_outlook_available_false_on_linux(self, env, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        source = CalendarSource(CalendarConfig())
        assert source._outlook_available() is False

    def test_outlook_available_true_on_windows(self, env, monkeypatch):
        """win32 + COM 注册存在 → True。"""
        monkeypatch.setattr(sys, "platform", "win32")
        key = MagicMock()
        fake_winreg = _fake_module(
            "winreg", HKEY_CLASSES_ROOT="hkcr", KEY_READ=0,
            OpenKey=MagicMock(return_value=key), CloseKey=MagicMock(),
        )
        monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
        # win32com 伪装成包，保证 import win32com.client 成功
        fake_client = _fake_module("win32com.client")
        fake_win32com = _fake_module("win32com", __path__=[], client=fake_client)
        monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
        monkeypatch.setitem(sys.modules, "win32com.client", fake_client)

        source = CalendarSource(CalendarConfig())
        assert source._outlook_available() is True

    def test_outlook_available_registry_error(self, env, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        fake_winreg = _fake_module(
            "winreg", HKEY_CLASSES_ROOT="hkcr",
            OpenKey=MagicMock(side_effect=OSError("no reg")), CloseKey=MagicMock(),
        )
        monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
        source = CalendarSource(CalendarConfig())
        assert source._outlook_available() is False


# ---------------------------------------------------------------------------
# Outlook COM 后端
# ---------------------------------------------------------------------------


class TestOutlookBackend:
    """_get_outlook_events 的 COM 调用链。"""

    @pytest.fixture
    def outlook_env(self, env, monkeypatch):
        """注入假 win32com / pythoncom / winreg 模块（ModuleType，可正常 import）。"""
        fake_client = _fake_module("win32com.client", Dispatch=MagicMock())
        # win32com 需伪装成包（__path__ + client 子模块属性），import win32com.client 才能成功
        fake_win32com = _fake_module("win32com", __path__=[], client=fake_client)
        monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
        monkeypatch.setitem(sys.modules, "win32com.client", fake_client)
        monkeypatch.setitem(
            sys.modules, "pythoncom",
            _fake_module("pythoncom", CoInitialize=MagicMock(), CoUninitialize=MagicMock()),
        )
        monkeypatch.setitem(
            sys.modules, "winreg",
            _fake_module("winreg", HKEY_CLASSES_ROOT="hkcr", OpenKey=MagicMock(), CloseKey=MagicMock()),
        )
        return fake_client

    def _com_item(self, *, subject="会议", start, end=None, location="", all_day=False):
        """构造一个 Outlook item 替身（含 .Start 的时间字段）。"""
        item = MagicMock()
        item.Subject = subject
        item.Start = start
        item.End = end
        item.Location = location
        item.AllDayEvent = all_day
        return item

    def test_com_flow_parses_items(self, env, outlook_env):
        fake_client = outlook_env
        outlook = MagicMock()
        fake_client.Dispatch.return_value = outlook
        items = MagicMock()
        outlook.GetNamespace.return_value.GetDefaultFolder.return_value.Items = items
        item = self._com_item(
            subject="项目评审",
            start=SimpleNamespace(year=2026, month=1, day=1, hour=9, minute=30),
            end=SimpleNamespace(year=2026, month=1, day=1, hour=10, minute=30),
            location="3F",
        )
        items.Restrict.return_value = [item]

        source = CalendarSource(CalendarConfig())
        source._backend = "outlook"
        events = source._get_outlook_events(FakeDate(2026, 1, 1))

        assert len(events) == 1
        assert events[0].title == "项目评审"
        assert events[0].start == FakeDatetime(2026, 1, 1, 9, 30)
        assert events[0].end == FakeDatetime(2026, 1, 1, 10, 30)
        assert events[0].location == "3F"

    def test_com_bad_item_skipped(self, env, outlook_env):
        fake_client = outlook_env
        outlook = MagicMock()
        fake_client.Dispatch.return_value = outlook
        items = MagicMock()
        outlook.GetNamespace.return_value.GetDefaultFolder.return_value.Items = items
        # 该 item 的 Start 只有 year，缺 month → 构造 datetime 抛异常被跳过
        bad = MagicMock()
        bad.Start = SimpleNamespace(year=2026)
        items.Restrict.return_value = [bad]

        source = CalendarSource(CalendarConfig())
        source._backend = "outlook"
        assert source._get_outlook_events(FakeDate(2026, 1, 1)) == []

    def test_com_dispatch_failure_returns_empty(self, env, outlook_env):
        fake_client = outlook_env
        fake_client.Dispatch.side_effect = RuntimeError("COM 失败")
        source = CalendarSource(CalendarConfig())
        source._backend = "outlook"
        assert source._get_outlook_events(FakeDate(2026, 1, 1)) == []


# ---------------------------------------------------------------------------
# ICS 后端：抓取与缓存
# ---------------------------------------------------------------------------


class TestIcsFetch:
    """_fetch_ics_content / _load_ics_cached。"""

    def test_fetch_from_local_file(self, env, ics_file):
        source = CalendarSource(CalendarConfig(ics_path=ics_file()))
        content = source._fetch_ics_content()
        assert "BEGIN:VEVENT" in content

    def test_fetch_missing_file_then_url(self, env, monkeypatch):
        class FakeResponse:
            """模拟 urlopen 返回的上下文管理器响应。"""

            def __init__(self, data):
                self._data = data

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return self._data

        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=10: FakeResponse(b"BEGIN:VCALENDAR remote"),
        )
        source = CalendarSource(CalendarConfig(ics_path="nope.ics", ics_url="http://remote/cal.ics"))
        assert source._fetch_ics_content() == "BEGIN:VCALENDAR remote"

    def test_fetch_url_failure_returns_none(self, env, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", MagicMock(side_effect=OSError("网络错误")))
        source = CalendarSource(CalendarConfig(ics_url="http://remote/cal.ics"))
        assert source._fetch_ics_content() is None

    def test_fetch_nothing_configured(self, env):
        source = CalendarSource(CalendarConfig())
        assert source._fetch_ics_content() is None

    def test_cached_within_ttl(self, env, monkeypatch):
        source = CalendarSource(CalendarConfig(cache_ttl_seconds=1800))
        source._ics_cache = [CalendarEvent(title="缓存的", start=FakeDatetime(2026, 1, 1, 9, 0))]
        source._ics_cache_time = 100.0
        monkeypatch.setattr("time.time", lambda: 500.0)  # 距缓存 400s < 1800s

        fetch = MagicMock(return_value="x")
        monkeypatch.setattr(source, "_fetch_ics_content", fetch)
        events = source._load_ics_cached()

        assert events == source._ics_cache
        fetch.assert_not_called()

    def test_cache_expired_reloads(self, env, monkeypatch, ics_file):
        source = CalendarSource(
            CalendarConfig(ics_path=ics_file(), cache_ttl_seconds=1800)
        )
        source._ics_cache_time = 100.0
        monkeypatch.setattr("time.time", lambda: 5000.0)  # 距缓存 4900s > 1800s

        events = source._load_ics_cached()
        assert len(events) == 2
        assert source._ics_cache_time == 5000.0

    def test_fetch_failure_clears_cache(self, env, monkeypatch):
        source = CalendarSource(CalendarConfig(cache_ttl_seconds=1800))
        source._ics_cache_time = 100.0
        monkeypatch.setattr("time.time", lambda: 5000.0)
        monkeypatch.setattr(source, "_fetch_ics_content", lambda: None)
        assert source._load_ics_cached() == []


# ---------------------------------------------------------------------------
# ICS 后端：事件过滤与解析
# ---------------------------------------------------------------------------


class TestIcsEvents:
    """_get_ics_events / _parse_ics / _parse_vevent / 行折叠 / 时间解析。"""

    def test_ics_events_filtered_by_date(self, env, ics_file):
        source = CalendarSource(CalendarConfig(ics_path=ics_file()))
        events = source._get_ics_events(FakeDate(2026, 1, 1))
        assert [e.title for e in events] == ["项目评审"]
        assert events[0].all_day is False

    def test_ics_all_day_cross_day_included(self, env, ics_file):
        """全天事件跨天时，落在 [start, end) 内的日期都应命中。"""
        source = CalendarSource(CalendarConfig(ics_path=ics_file()))
        events = source._get_ics_events(FakeDate(2026, 1, 2))
        assert [e.title for e in events] == ["团建"]
        assert events[0].all_day is True

    def test_parse_ics_skips_broken_vevent(self, env):
        source = CalendarSource(CalendarConfig())
        content = (
            "BEGIN:VEVENT\nSUMMARY:好的\nDTSTART:20260101T090000\nEND:VEVENT\n"
            "BEGIN:VEVENT\nSUMMARY:缺DTSTART\nEND:VEVENT\n"
        )
        events = source._parse_ics(content)
        assert [e.title for e in events] == ["好的"]

    def test_parse_vevent_full(self, env):
        source = CalendarSource(CalendarConfig())
        block = (
            "SUMMARY:周会\n"
            "DTSTART;TZID=Asia/Shanghai:20260721T090000\n"
            "DTEND;TZID=Asia/Shanghai:20260721T100000\n"
            "LOCATION:线上\n"
        )
        ev = source._parse_vevent(block)
        assert ev is not None
        assert ev.title == "周会"
        assert ev.location == "线上"
        assert ev.start == FakeDatetime(2026, 7, 21, 9, 0)
        assert ev.all_day is False

    def test_parse_vevent_all_day(self, env):
        source = CalendarSource(CalendarConfig())
        block = "SUMMARY:全天安排\nDTSTART;VALUE=DATE:20260721\n"
        ev = source._parse_vevent(block)
        assert ev is not None
        assert ev.all_day is True
        assert ev.start == FakeDatetime(2026, 7, 21)

    def test_parse_vevent_missing_dtstart(self, env):
        source = CalendarSource(CalendarConfig())
        assert source._parse_vevent("SUMMARY:没有时间") is None

    def test_parse_vevent_invalid_datetime(self, env):
        source = CalendarSource(CalendarConfig())
        assert source._parse_vevent("SUMMARY:x\nDTSTART:20260799T090000") is None

    def test_parse_vevent_bad_line_ignored(self, env):
        """无冒号的行应被忽略，不影响解析。"""
        source = CalendarSource(CalendarConfig())
        block = "NOT-A-KEY-LINE\nSUMMARY:x\nDTSTART:20260721T090000\n"
        ev = source._parse_vevent(block)
        assert ev is not None
        assert ev.title == "x"

    def test_unfold_ics_lines(self, env):
        source = CalendarSource(CalendarConfig())
        raw = "SUMMARY:第一行\r\n 续行一\n\t续行二\nNEXT:值"
        lines = source._unfold_ics_lines(raw)
        assert lines == ["SUMMARY:第一行续行一续行二", "NEXT:值"]

    def test_parse_ics_datetime_formats(self, env):
        source = CalendarSource(CalendarConfig())
        # UTC 格式（Z 结尾）
        assert source._parse_ics_datetime("20260721T090000Z", {}) == FakeDatetime(2026, 7, 21, 9, 0)
        # 本地格式
        assert source._parse_ics_datetime("20260721T090000", {}) == FakeDatetime(2026, 7, 21, 9, 0)
        # 纯日期（全天）
        assert source._parse_ics_datetime("20260721", {}) == FakeDatetime(2026, 7, 21)
        # 非法格式
        assert source._parse_ics_datetime("bad-date", {}) is None


# ---------------------------------------------------------------------------
# 对外查询接口
# ---------------------------------------------------------------------------


class TestQueries:
    """get_today_events / get_upcoming_events。"""

    def test_get_today_events_disabled(self, env):
        source = CalendarSource(CalendarConfig(enabled=False))
        assert source.get_today_events() == []

    def test_get_today_events_returns_texts(self, env, ics_file, monkeypatch):
        source = CalendarSource(CalendarConfig(enabled=True, backend="ics", ics_path=ics_file()))
        texts = source.get_today_events()
        assert texts == ["09:30-10:30 项目评审 @ 3楼会议室"]

    def test_get_upcoming_events_disabled(self, env):
        source = CalendarSource(CalendarConfig(enabled=False))
        assert source.get_upcoming_events() == []

    def test_get_upcoming_events_window(self, env, ics_file):
        """10:00 时查询未来 30 分钟：只包含 10:30 前开始的事件。"""
        content = (
            "BEGIN:VEVENT\nSUMMARY:马上开始\nDTSTART:20260101T101500\nEND:VEVENT\n"
            "BEGIN:VEVENT\nSUMMARY:稍晚\nDTSTART:20260101T110000\nEND:VEVENT\n"
            "BEGIN:VEVENT\nSUMMARY:已开始\nDTSTART:20260101T093000\nEND:VEVENT\n"
            "BEGIN:VEVENT\nSUMMARY:全天\nDTSTART;VALUE=DATE:20260101\nEND:VEVENT\n"
        )
        source = CalendarSource(
            CalendarConfig(enabled=True, backend="ics", ics_path=ics_file(content, "win.ics"))
        )
        upcoming = source.get_upcoming_events(minutes_before=30)
        # 只有"马上开始"落在 (now, now+30min] 窗口内；全天事件被排除
        assert len(upcoming) == 1
        assert "15分钟后: 10:15 马上开始" in upcoming[0]

    def test_get_upcoming_uses_config_default(self, env, ics_file):
        content = (
            "BEGIN:VEVENT\nSUMMARY:稍晚\nDTSTART:20260101T110000\nEND:VEVENT\n"
        )
        source = CalendarSource(
            CalendarConfig(enabled=True, backend="ics", ics_path=ics_file(content, "def.ics"), remind_minutes_before=90)
        )
        upcoming = source.get_upcoming_events()
        # 10:00 + 90min → 11:30；"稍晚"（11:00）应命中
        assert any("11:00 稍晚" in u for u in upcoming)
