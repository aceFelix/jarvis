"""日历事件感知源 —— 把休眠的 ``CalendarSource`` 收编到统一总线（阶段 11）。

背景（aceFelix）：``core/daemon/calendar_source.py`` 早就写好了（Outlook COM / ICS 双后端），
但一直是休眠件——没有任何宿主装配它。W2 建总线时把 calendar 登记成"收编位"
（``PENDING_SOURCES``），刻意不给面板开关，避免"有开关、背后没人采"的假可用。
本模块就是补上那条线：**底层能力不动，只加一层总线适配**。

三个设计取舍：

- **不自己起线程**。日历是拉取型事实（"有没有日程临近"），不是推送型信号；
  由总线按 ``interval_s`` 调 :meth:`collect` 即可，多一个线程只会多一处泄漏点。
- **去重键必须稳定**。``CalendarSource.get_upcoming_events()`` 返回的是
  ``"17分钟后: 10:30-11:00 项目周会"``——分钟数每轮都在变，用它算指纹等于永不命中，
  同一条日程会被播到开始为止。所以本源读结构化事件、自己拼稳定 key
  （``开始时刻:标题``），并在进程内记账"这条已经报过了"。
- **依赖缺失不算故障**。没配 Outlook 也没配 ICS 时 ``available`` 为 False，
  本源只记 ``unavailable`` 并返回空列表，**绝不抛**——总线有"连败自动停用"，
  抛出去会让面板显示一个误导人的错误（见 ``factory.py`` 顶部同类取舍）。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

from agent.core.percept.event import SOURCE_CALENDAR, PerceptEvent

#: 默认提前量（分钟）：与 ``CalendarConfig.remind_minutes_before`` 的出厂值一致。
DEFAULT_LEAD_MINUTES = 30

#: 总线排空本源的默认节拍（秒）。日历不是秒级事实，5 分钟一次足够。
DEFAULT_INTERVAL_S = 300

#: “今天已经报过的日程”账本路径。与 ``source_mail`` 的 ``mail_seen.json`` 同一模式：
#: 进程重启、或面板改配置触发总线热重装配后，“这条日程已经说过了”不能跟着内存
#: 一起归零——否则同一天每改一次配置就把临近日程重播一遍（2026-10-26 实测发现）。
_STATE_FILE = Path.home() / ".jarvis" / "percept" / "calendar_announced.json"

#: 账本环形上限（条）。一天几十条足够，超出按字典序丢最前面的。
_STATE_CAP = 200


class CalendarSource:
    """日历感知源（pull 型：总线按节拍调 :meth:`collect`）。

    ``name`` 与配置键 ``percept_calendar_enabled`` 对应，面板按它渲染开关。

    @author aceFelix
    """

    name = SOURCE_CALENDAR

    def __init__(
        self,
        *,
        calendar: Any = None,
        calendar_factory: Any = None,
        lead_minutes: int = DEFAULT_LEAD_MINUTES,
        interval_s: float = DEFAULT_INTERVAL_S,
        backend: str = "auto",
        ics_path: str = "",
        ics_url: str = "",
        state_path: Path | str | None = None,
    ) -> None:
        """
        Args:
            calendar / calendar_factory: 注入 ``daemon.calendar_source.CalendarSource``
                实例或工厂（测试与定制用）。都不传时本源自己构造——**延迟到第一次
                被采集**才 import，源关着时连依赖都不碰。
            lead_minutes: 事件开始前多久开始报（0 = 不提前，只报已经开始的不合理，
                故最小按 1 处理）。
            interval_s: 总线排空本源的节拍。
            backend / ics_path / ics_url: 日历后端配置，**直接吃 ``[calendar]`` 段**
                （那些字段本来就有），不在感知层另造一份。
            state_path: “今天已报过”账本的落盘路径（测试传 tmp 目录）。
        """
        self.interval_s = float(interval_s)
        self._calendar = calendar
        self._factory = calendar_factory
        self._lead = max(1, int(lead_minutes or DEFAULT_LEAD_MINUTES))
        self._backend_pref = str(backend or "auto")
        self._ics_path = str(ics_path or "")
        self._ics_url = str(ics_url or "")

        self._lock = threading.Lock()
        self._announced: set[str] = set()   # 已播过的稳定键
        self._announced_day: date | None = None
        self._state_path = Path(state_path) if state_path else _STATE_FILE
        self.unavailable = ""               # 依赖/配置层面的原因（面板可见，非运行时故障）
        self.published = 0
        self.skipped_repeat = 0             # 被"已报过"挡掉的条数（证明去重生效）
        self._load_state()

    # ---- 总线契约 ----

    def collect(self) -> list[PerceptEvent]:
        """取出"即将开始且尚未报过"的日程，每条只报一次。"""
        backend = self._ensure_backend()
        if backend is None:
            return []

        try:
            events = self._events_of_day(backend, date.today())
        except Exception as e:  # noqa: BLE001 - 第三方日历后端什么都可能抛
            # 后端偶发失败不升级成"源坏了"：记下原因，下个节拍再试
            self.unavailable = f"读取日历失败：{type(e).__name__}"
            return []

        now = datetime.now()
        out: list[PerceptEvent] = []
        with self._lock:
            self._reset_day(date.today())
            for ev in events:
                key = self._stable_key(ev)
                if key in self._announced:
                    self.skipped_repeat += 1
                    continue
                minutes = self._minutes_until(ev, now)
                if minutes is None or minutes < 0 or minutes > self._lead:
                    continue
                self._announced.add(key)
                out.append(self._event(ev, minutes))
                self.published += 1
            if out:
                self._save_state()      # 一次采集后统一落盘：一条日程一天就报一回
        return out

    def close(self) -> None:
        """日历源无长驻资源；留接口是为了与其他源同形。"""
        self._calendar = None

    def state(self) -> dict[str, Any]:
        """运行状态（面板"在采什么"与导出共用）。"""
        backend_name = ""
        backend = self._calendar
        if backend is not None:
            # daemon 侧把实际后端记在私有字段上；只读不改，取不到就留空
            backend_name = str(getattr(backend, "_backend", "") or "")
        return {
            "unavailable": self.unavailable,
            "backend": backend_name or self._backend_pref,
            "ics_configured": bool(self._ics_path or self._ics_url),
            "lead_minutes": self._lead,
            "announced_today": len(self._announced),
            "skipped_repeat": self.skipped_repeat,
            "published": self.published,
        }

    # ---- 内部 ----

    def _ensure_backend(self) -> Any:
        """懒构造 + 懒判可用（幂等）。不可用时记原因并不再重试。"""
        if self._calendar is None:
            if self._factory is not None:
                self._calendar = self._factory()
            else:
                try:
                    from agent.core.daemon.calendar_source import (
                        CalendarConfig,
                        CalendarSource as CalendarBackend,
                    )
                except Exception as e:  # noqa: BLE001 - 模块级依赖问题不该冒到总线
                    self.unavailable = f"日历模块不可用：{type(e).__name__}"
                    return None
                self._calendar = CalendarBackend(
                    CalendarConfig(
                        enabled=True,
                        backend=self._backend_pref,
                        ics_path=self._ics_path,
                        ics_url=self._ics_url,
                        remind_minutes_before=self._lead,
                    )
                )
        if not bool(getattr(self._calendar, "available", False)):
            # 既没 Outlook 也没配 ICS：这是"开了但没配齐"，不是运行时故障
            if not self.unavailable:
                self.unavailable = "没有可用的日历后端（未配 Outlook 或 ICS）"
            return None
        self.unavailable = ""
        return self._calendar

    def _events_of_day(self, backend: Any, day: date) -> list[Any]:
        """取某个日期的结构化事件。

        这里用的是 ``_get_events_for_date``（私有）而不是公开的
        ``get_upcoming_events``：后者把"17分钟后"写进了返回的字符串里，
        拿来当去重依据等于每条日程都会重复播报。私有方法在本包内使用是有意为之，
        与 ``calendar_source.py`` 同仓同包，接口变动一起改。
        """
        getter = getattr(backend, "_get_events_for_date", None)
        if not callable(getter):
            return []
        return list(getter(day) or [])

    def _reset_day(self, day: date) -> None:
        """跨天后清空"已报过"的集合（内存与账本都不能随天数线性涨）。"""
        if self._announced_day != day:
            self._announced_day = day
            self._announced.clear()
            self._save_state()      # 隔天的旧键没有任何价值，连同磁盘一起置空

    def _load_state(self) -> None:
        """读回"今天已报过"的日程键（文件缺失/损坏/隔天都当空账本）。

        关键细节：命中时要把 ``_announced_day`` 一并对齐到今天。不这么做的话，
        ``collect`` 里第一次 ``_reset_day`` 会因为 ``_announced_day is None`` 而把
        刚读回的账本清空，落盘就白写了。
        """
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        if str(raw.get("day") or "") != date.today().isoformat():
            return
        keys = raw.get("keys")
        if not isinstance(keys, list):
            return
        self._announced = {str(k) for k in keys if isinstance(k, str)}
        self._announced_day = date.today()

    def _save_state(self) -> None:
        """把"今天已报过"的日程键落盘（失败不外溢：存储问题不该让采集失败）。"""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "day": date.today().isoformat(),
                "keys": sorted(self._announced)[-_STATE_CAP:],
            }
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError:
            pass

    @staticmethod
    def _stable_key(ev: Any) -> str:
        """跨轮稳定的去重键：开始时刻 + 标题。"""
        start = getattr(ev, "start", None)
        title = str(getattr(ev, "title", "") or "")
        return f"{start.isoformat(timespec='minutes') if start else ''}:{title}"

    @staticmethod
    def _minutes_until(ev: Any, now: datetime) -> float | None:
        """距开始还有多少分钟；全天事件与无开始时刻的返回 None（不参与本轮）。"""
        if bool(getattr(ev, "all_day", False)):
            return None
        start = getattr(ev, "start", None)
        if not isinstance(start, datetime):
            return None
        return (start - now).total_seconds() / 60.0

    def _event(self, ev: Any, minutes: float) -> PerceptEvent:
        """把一条日历事件转成总线事实。

        显著性随"越近越重要"线性上升：踩点时 0.9，刚进提前窗口时 0.5。
        最终能不能打扰用户仍由 W1 打分决定，这里只是先验。
        """
        start = getattr(ev, "start", None)
        time_str = start.strftime("%H:%M") if isinstance(start, datetime) else ""
        end = getattr(ev, "end", None)
        span = f"{time_str}-{end.strftime('%H:%M')}" if time_str and end else time_str
        title = str(getattr(ev, "title", "") or "")
        location = str(getattr(ev, "location", "") or "")
        loc = f" @ {location}" if location else ""
        mins = int(max(0, minutes))
        near = 1.0 - (mins / float(self._lead)) if self._lead else 1.0
        return PerceptEvent(
            source=SOURCE_CALENDAR,
            kind="event_upcoming",
            payload={
                "summary": f"{mins} 分钟后有日程：{span} {title}{loc}".strip(),
                "title": title,
                "start": start.isoformat(timespec="minutes") if isinstance(start, datetime) else "",
                "location": location,
                "minutes_until": mins,
            },
            salience_hint=round(min(0.9, 0.5 + 0.4 * max(0.0, near)), 3),
            dedup_key=self._stable_key(ev),   # 显式稳定键：不靠 payload 指纹
        )
