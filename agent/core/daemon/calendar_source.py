"""日历数据源 —— 贾维斯的"日程感知"。

P2-3 主动提醒系统模块之一。让贾维斯能读取用户的日历事件，
在每日简报中展示今日日程，并在事件临近时提前提醒。

双后端策略:
1. **Outlook COM**（优先，Windows + 已安装 Outlook）:
   - win32com.client.Dispatch("Outlook.Application")
   - 实时读取日历事件，支持重复事件
2. **ICS 文件/URL**（回退，跨平台）:
   - 解析标准 .ics 日历文件（VCALENDAR/VEVENT）
   - 支持本地文件或远程 URL 订阅
   - 轻量手写解析，不依赖 icalendar 库

配置（settings.toml [calendar]）:
    enabled = true
    backend = "auto"        # auto / outlook / ics
    ics_path = ""           # 本地 .ics 文件路径
    ics_url = ""            # 远程 .ics 订阅 URL
    remind_minutes_before = 30

设计要点:
- **优雅降级**: Outlook 不可用时自动回退 ICS；ICS 也没配置则返回空
- **缓存**: ICS 文件/URL 每 30 分钟重新读取，避免频繁 IO
- **线程安全**: 读取操作加锁（daemon 多线程环境）
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


class CalendarConfig:
    """日历数据源配置。"""

    def __init__(
        self,
        *,
        enabled: bool = False,
        backend: str = "auto",  # auto / outlook / ics
        ics_path: str = "",
        ics_url: str = "",
        remind_minutes_before: int = 30,
        cache_ttl_seconds: int = 1800,  # ICS 缓存 30 分钟
    ) -> None:
        self.enabled = enabled
        self.backend = backend
        self.ics_path = ics_path
        self.ics_url = ics_url
        self.remind_minutes_before = remind_minutes_before
        self.cache_ttl_seconds = cache_ttl_seconds


# ---------------------------------------------------------------------------
# 事件数据
# ---------------------------------------------------------------------------


class CalendarEvent:
    """一个日历事件。"""

    def __init__(
        self,
        *,
        title: str,
        start: datetime,
        end: datetime | None = None,
        location: str = "",
        all_day: bool = False,
    ) -> None:
        self.title = title
        self.start = start
        self.end = end
        self.location = location
        self.all_day = all_day

    def __str__(self) -> str:
        if self.all_day:
            return f"[全天] {self.title}"
        time_str = self.start.strftime("%H:%M")
        end_str = f"-{self.end.strftime('%H:%M')}" if self.end else ""
        loc = f" @ {self.location}" if self.location else ""
        return f"{time_str}{end_str} {self.title}{loc}"

    @property
    def minutes_until_start(self) -> float:
        """距事件开始还有多少分钟。负数=已开始。"""
        return (self.start - datetime.now()).total_seconds() / 60


# ---------------------------------------------------------------------------
# CalendarSource 主类
# ---------------------------------------------------------------------------


class CalendarSource:
    """日历数据源。

    自动选择后端（Outlook COM 或 ICS），提供统一的事件查询接口。

    用法::

        source = CalendarSource(config=CalendarConfig(enabled=True))
        events = source.get_today_events()
        upcoming = source.get_upcoming_events(minutes_before=30)
    """

    def __init__(self, config: CalendarConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._backend: str | None = None  # 实际使用的后端
        # ICS 缓存
        self._ics_cache: list[CalendarEvent] = []
        self._ics_cache_time: float = 0

    @property
    def available(self) -> bool:
        """日历数据源是否可用。"""
        if not self._config.enabled:
            return False
        return self._resolve_backend() is not None

    def get_today_events(self) -> list[str]:
        """获取今日所有事件的文本描述列表。"""
        if not self._config.enabled:
            return []
        events = self._get_events_for_date(date.today())
        return [str(ev) for ev in events]

    def get_upcoming_events(self, minutes_before: int | None = None) -> list[str]:
        """获取即将开始的事件（在 minutes_before 分钟内）。"""
        if not self._config.enabled:
            return []
        if minutes_before is None:
            minutes_before = self._config.remind_minutes_before

        now = datetime.now()
        window_end = now + timedelta(minutes=minutes_before)

        events = self._get_events_for_date(now.date())
        upcoming = []
        for ev in events:
            if ev.all_day:
                continue
            if now <= ev.start <= window_end:
                mins = int(ev.minutes_until_start)
                upcoming.append(f"{mins}分钟后: {ev}")
        return upcoming

    # ---- 后端选择 ----

    def _resolve_backend(self) -> str | None:
        """解析实际使用的后端。"""
        if self._backend is not None:
            return self._backend

        backend = self._config.backend

        if backend == "outlook":
            if self._outlook_available():
                self._backend = "outlook"
            else:
                self._backend = None
        elif backend == "ics":
            if self._config.ics_path or self._config.ics_url:
                self._backend = "ics"
            else:
                self._backend = None
        else:  # auto
            # 优先 Outlook，回退 ICS
            if self._outlook_available():
                self._backend = "outlook"
            elif self._config.ics_path or self._config.ics_url:
                self._backend = "ics"
            else:
                self._backend = None

        return self._backend

    def _outlook_available(self) -> bool:
        """检测 Outlook COM 是否可用。"""
        try:
            import sys
            if sys.platform != "win32":
                return False
            import win32com.client  # noqa: F401
            # 尝试创建 Outlook 应用对象（不实际启动 Outlook）
            # 注意：这会启动 Outlook 进程（如果未运行）
            # 为避免不必要的启动，只检查 COM 注册
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CLASSES_ROOT,
                r"Outlook.Application\CLSID",
                0, winreg.KEY_READ
            )
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    # ---- 事件获取 ----

    def _get_events_for_date(self, target_date: date) -> list[CalendarEvent]:
        """获取指定日期的事件列表。"""
        backend = self._resolve_backend()
        if backend == "outlook":
            return self._get_outlook_events(target_date)
        elif backend == "ics":
            return self._get_ics_events(target_date)
        return []

    # ---- Outlook COM 后端 ----

    def _get_outlook_events(self, target_date: date) -> list[CalendarEvent]:
        """通过 Outlook COM 获取指定日期的事件。"""
        try:
            import win32com.client
            import pythoncom

            # COM 线程初始化（daemon 后台线程需要）
            pythoncom.CoInitialize()
            try:
                outlook = win32com.client.Dispatch("Outlook.Application")
                namespace = outlook.GetNamespace("MAPI")
                calendar = namespace.GetDefaultFolder(9)  # 9 = olFolderCalendar

                # 查询范围：目标日期 00:00 ~ 次日 00:00
                start_str = target_date.strftime("%Y-%m-%d") + " 00:00"
                end_date = target_date + timedelta(days=1)
                end_str = end_date.strftime("%Y-%m-%d") + " 00:00"

                items = calendar.Items
                items.IncludeRecurrences = True
                items.Sort("[Start]")

                # Restrict 过滤
                restriction = f"[Start] >= '{start_str}' AND [Start] < '{end_str}'"
                filtered = items.Restrict(restriction)

                events = []
                for item in filtered:
                    try:
                        title = getattr(item, "Subject", "(无标题)")
                        start = item.Start
                        end = getattr(item, "End", None)
                        location = getattr(item, "Location", "")
                        all_day = getattr(item, "AllDayEvent", False)

                        # COM datetime → Python datetime
                        start_dt = datetime(
                            start.year, start.month, start.day,
                            start.hour, start.minute
                        )
                        end_dt = None
                        if end:
                            end_dt = datetime(
                                end.year, end.month, end.day,
                                end.hour, end.minute
                            )

                        events.append(CalendarEvent(
                            title=title,
                            start=start_dt,
                            end=end_dt,
                            location=location,
                            all_day=all_day,
                        ))
                    except Exception:
                        continue

                return events
            finally:
                pythoncom.CoUninitialize()
        except Exception:
            return []

    # ---- ICS 文件/URL 后端 ----

    def _get_ics_events(self, target_date: date) -> list[CalendarEvent]:
        """从 ICS 文件/URL 获取指定日期的事件。"""
        events = self._load_ics_cached()
        # 过滤目标日期的事件
        result = []
        for ev in events:
            if ev.start.date() == target_date:
                result.append(ev)
            elif ev.all_day and ev.start.date() <= target_date:
                # 全天事件可能跨天
                if ev.end and ev.end.date() >= target_date:
                    result.append(ev)
        return result

    def _load_ics_cached(self) -> list[CalendarEvent]:
        """加载 ICS 事件（带缓存）。"""
        now = time.time()
        with self._lock:
            if (now - self._ics_cache_time) < self._config.cache_ttl_seconds and self._ics_cache:
                return self._ics_cache

        # 缓存过期，重新加载
        raw_ics = self._fetch_ics_content()
        if not raw_ics:
            return []

        events = self._parse_ics(raw_ics)
        with self._lock:
            self._ics_cache = events
            self._ics_cache_time = now
        return events

    def _fetch_ics_content(self) -> str | None:
        """获取 ICS 文件内容（本地文件或远程 URL）。"""
        # 优先本地文件
        if self._config.ics_path:
            try:
                path = Path(self._config.ics_path)
                if path.exists():
                    return path.read_text(encoding="utf-8")
            except Exception:
                pass

        # 远程 URL
        if self._config.ics_url:
            try:
                import urllib.request
                req = urllib.request.Request(
                    self._config.ics_url,
                    headers={"User-Agent": "Jarvis/1.0"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.read().decode("utf-8")
            except Exception:
                pass

        return None

    def _parse_ics(self, content: str) -> list[CalendarEvent]:
        """轻量解析 ICS 内容（手写 VEVENT 解析，不依赖 icalendar 库）。

        支持:
        - DTSTART / DTEND（含 TZID 和 VALUE=DATE）
        - SUMMARY
        - LOCATION
        - 基本 RRULE 不展开（仅返回单次事件）
        """
        events = []
        # 按 VEVENT 分块
        vevent_pattern = re.compile(
            r"BEGIN:VEVENT(.*?)END:VEVENT",
            re.DOTALL
        )

        for match in vevent_pattern.finditer(content):
            block = match.group(1)
            try:
                ev = self._parse_vevent(block)
                if ev:
                    events.append(ev)
            except Exception:
                continue

        return events

    def _parse_vevent(self, block: str) -> CalendarEvent | None:
        """解析单个 VEVENT 块。"""
        # 提取字段（处理行折叠：以空格/tab 开头的行是上一行的续行）
        lines = self._unfold_ics_lines(block)
        fields: dict[str, str] = {}
        field_params: dict[str, dict[str, str]] = {}

        for line in lines:
            if ":" not in line:
                continue
            key_part, value = line.split(":", 1)
            # 解析参数（如 DTSTART;TZID=Asia/Shanghai:20260721T090000）
            parts = key_part.split(";")
            key = parts[0].upper()
            params = {}
            for p in parts[1:]:
                if "=" in p:
                    pk, pv = p.split("=", 1)
                    params[pk.upper()] = pv
            fields[key] = value.strip()
            field_params[key] = params

        # 必须有 DTSTART
        if "DTSTART" not in fields:
            return None

        title = fields.get("SUMMARY", "(无标题)")
        location = fields.get("LOCATION", "")
        start = self._parse_ics_datetime(fields["DTSTART"], field_params.get("DTSTART", {}))
        if start is None:
            return None

        end = None
        if "DTEND" in fields:
            end = self._parse_ics_datetime(fields["DTEND"], field_params.get("DTEND", {}))

        # 判断全天事件
        all_day = "VALUE" in field_params.get("DTSTART", {}) and \
                  field_params["DTSTART"]["VALUE"].upper() == "DATE"

        return CalendarEvent(
            title=title,
            start=start,
            end=end,
            location=location,
            all_day=all_day,
        )

    @staticmethod
    def _unfold_ics_lines(text: str) -> list[str]:
        """展开 ICS 行折叠（RFC 5545: 以空格/tab 开头的行是续行）。"""
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        result = []
        for line in lines:
            if line.startswith((" ", "\t")) and result:
                result[-1] += line[1:]
            else:
                result.append(line)
        return result

    @staticmethod
    def _parse_ics_datetime(value: str, params: dict[str, str]) -> datetime | None:
        """解析 ICS 日期时间值。

        支持格式:
        - 20260721T090000Z (UTC)
        - 20260721T090000 (本地)
        - 20260721 (全天事件)
        """
        value = value.strip()
        try:
            if "T" in value:
                # 日期时间
                clean = value.rstrip("Z")
                dt = datetime.strptime(clean, "%Y%m%dT%H%M%S")
                return dt
            else:
                # 纯日期（全天事件）
                dt = datetime.strptime(value, "%Y%m%d")
                return dt
        except (ValueError, TypeError):
            return None
