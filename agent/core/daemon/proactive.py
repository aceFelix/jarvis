"""主动感知引擎 —— 贾维斯的"主动意识"中枢。

P2-3 主动提醒系统的核心调度器。统一管理所有主动提醒源：
- DailyBriefing: 每日简报（定时播报今日概览）
- DeadlineTracker: 截止日期分级提醒
- ReminderEscalation: 提醒升级/确认机制
- CalendarSource: 日历事件提前提醒

ProactiveEngine 不自己起线程，而是利用现有 Scheduler 注册定时任务，
到期时通过 on_notify 回调播报（复用 daemon 的托盘+TTS 通道）。

工作流:
    daemon 启动 → 创建 ProactiveEngine → engine.start()
      → 在 Scheduler 中注册每日简报任务（daily, briefing_time）
      → 在 Scheduler 中注册截止日期检查任务（daily, check_time）
      → 在 Scheduler 中注册日历检查任务（每 30 分钟）
      → 到期触发 → 收集数据 → 组装文本 → on_notify 播报

设计要点:
- **不自起线程**: 复用 Scheduler 的轮询机制，轻量无额外开销
- **幂等注册**: start() 多次调用不会重复注册任务（通过 note 标记识别）
- **优雅降级**: 任何子模块失败不影响其他模块
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Any, Callable

from agent.core.daemon.scheduler import Scheduler, ScheduleTask
from agent.core.daemon.deadline import DeadlineTracker


# ---------------------------------------------------------------------------
# 配置数据类
# ---------------------------------------------------------------------------


class ProactiveConfig:
    """主动感知引擎配置。从 Settings 字段构建。"""

    def __init__(
        self,
        *,
        briefing_enabled: bool = True,
        briefing_time: str = "08:30",
        deadline_enabled: bool = True,
        deadline_check_time: str = "09:00",
        calendar_enabled: bool = False,
        calendar_check_interval_min: int = 30,
        calendar_remind_minutes_before: int = 30,
        profile_maintenance_enabled: bool = True,
    ) -> None:
        self.briefing_enabled = briefing_enabled
        self.briefing_time = briefing_time
        self.deadline_enabled = deadline_enabled
        self.deadline_check_time = deadline_check_time
        self.calendar_enabled = calendar_enabled
        self.calendar_check_interval_min = calendar_check_interval_min
        self.profile_maintenance_enabled = profile_maintenance_enabled
        self.calendar_remind_minutes_before = calendar_remind_minutes_before


# ---------------------------------------------------------------------------
# ProactiveEngine
# ---------------------------------------------------------------------------

# 任务 note 标记（用于识别 ProactiveEngine 注册的任务，避免重复）
_BRIEFING_NOTE = "__proactive_briefing__"
_DEADLINE_NOTE = "__proactive_deadline__"
_CALENDAR_NOTE = "__proactive_calendar__"
_PROFILE_MAINT_NOTE = "__proactive_profile_maintenance__"


class ProactiveEngine:
    """主动感知引擎。

    统一管理每日简报、截止日期检查、日历事件提醒。
    通过 Scheduler 注册定时任务，到期触发 on_notify 回调。

    用法::

        engine = ProactiveEngine(
            scheduler=scheduler,
            config=ProactiveConfig(),
            deadline_tracker=tracker,
            on_notify=lambda msg: print(msg),
        )
        engine.start()
        # ... daemon 运行中 ...
        engine.stop()
    """

    def __init__(
        self,
        *,
        scheduler: Scheduler,
        config: ProactiveConfig,
        deadline_tracker: DeadlineTracker | None = None,
        calendar_source: Any | None = None,
        monitor: Any | None = None,
        on_notify: Callable[[str], None],
    ) -> None:
        """
        Args:
            scheduler: Scheduler 实例（复用其定时触发机制）。
            config: 主动感知配置。
            deadline_tracker: DeadlineTracker 实例（可选）。
            calendar_source: CalendarSource 实例（可选）。
            monitor: SystemMonitor 实例（可选，用于简报中的系统状态）。
            on_notify: 通知回调。接收组装好的文本，由 daemon 负责播报。
        """
        self._scheduler = scheduler
        self._config = config
        self._deadline_tracker = deadline_tracker
        self._calendar_source = calendar_source
        self._monitor = monitor
        self._on_notify = on_notify
        self._started = False
        # 注册的任务 ID（stop 时取消）
        self._task_ids: list[str] = []

    def start(self) -> None:
        """启动主动感知引擎。在 Scheduler 中注册定时任务。"""
        if self._started:
            return
        self._started = True

        # 注册每日简报任务
        if self._config.briefing_enabled:
            self._register_briefing()

        # 注册截止日期检查任务
        if self._config.deadline_enabled and self._deadline_tracker:
            self._register_deadline_check()

        # 注册画像记忆每日维护任务（Phase 1a M3：衰减 + 上限淘汰，凌晨静默执行）
        if self._config.profile_maintenance_enabled:
            self._register_profile_maintenance()

        # 注册日历检查任务（暂用 daily 任务模拟，后续可改为更频繁）
        # 注意：Scheduler 目前只支持 once/daily/weekly，
        # 日历检查暂用 daily 在 briefing_time 时一并检查
        # TODO: 后续扩展 Scheduler 支持 hourly/minutely 重复模式

    def stop(self) -> None:
        """停止主动感知引擎。取消注册的定时任务。"""
        if not self._started:
            return
        self._started = False
        for task_id in self._task_ids:
            self._scheduler.cancel_task(task_id)
        self._task_ids.clear()

    # ---- 注册定时任务 ----

    def _register_briefing(self) -> None:
        """注册每日简报任务。"""
        # 检查是否已存在（幂等）
        if self._find_task_by_note(_BRIEFING_NOTE):
            return

        trigger_at = self._today_at(self._config.briefing_time)
        task = self._scheduler.add_task(
            content="每日简报",
            trigger_at=trigger_at,
            repeat="daily",
            note=_BRIEFING_NOTE,
        )
        self._task_ids.append(task.id)

    def _register_deadline_check(self) -> None:
        """注册截止日期每日检查任务。"""
        if self._find_task_by_note(_DEADLINE_NOTE):
            return

        trigger_at = self._today_at(self._config.deadline_check_time)
        task = self._scheduler.add_task(
            content="截止日期检查",
            trigger_at=trigger_at,
            repeat="daily",
            note=_DEADLINE_NOTE,
        )
        self._task_ids.append(task.id)

    def _register_profile_maintenance(self) -> None:
        """注册画像记忆每日维护任务（凌晨 3:30，管家"睡眠整理记忆"）。

        纯本地计算（无 LLM、无打扰）：置信度衰减 + 超限淘汰。
        """
        if self._find_task_by_note(_PROFILE_MAINT_NOTE):
            return

        trigger_at = self._today_at("03:30")
        task = self._scheduler.add_task(
            content="画像记忆维护",
            trigger_at=trigger_at,
            repeat="daily",
            note=_PROFILE_MAINT_NOTE,
        )
        self._task_ids.append(task.id)

    def _fire_profile_maintenance(self) -> None:
        """执行画像维护：decay + prune。静默（写日志，不打扰用户）。"""
        import logging

        logger = logging.getLogger("jarvis.memory.profile")
        try:
            from agent.core.memory.profile_store import ProfileStore

            store = ProfileStore()
            removed = store.decay()
            pruned = store.prune_over_limit(200)
            if removed or pruned:
                logger.info(
                    "画像维护完成: 衰减清除 %d 条, 超限淘汰 %d 条, 剩余 %d 条",
                    removed, pruned, len(store),
                )
        except Exception as e:
            logger.warning("画像维护失败: %s", e)

    # ---- 任务触发处理 ----

    def handle_task_fire(self, task: ScheduleTask) -> None:
        """处理 ProactiveEngine 注册的任务触发。

        由 daemon 的 _on_schedule_fire 调用，根据 note 标记分发。

        Args:
            task: 触发的 ScheduleTask。
        """
        if task.note == _BRIEFING_NOTE:
            self._fire_briefing()
        elif task.note == _DEADLINE_NOTE:
            self._fire_deadline_check()
        elif task.note == _CALENDAR_NOTE:
            self._fire_calendar_check()
        elif task.note == _PROFILE_MAINT_NOTE:
            self._fire_profile_maintenance()

    def is_proactive_task(self, task: ScheduleTask) -> bool:
        """判断任务是否是 ProactiveEngine 注册的（用于 daemon 分发）。"""
        return task.note in (_BRIEFING_NOTE, _DEADLINE_NOTE, _CALENDAR_NOTE, _PROFILE_MAINT_NOTE)

    # ---- 每日简报 ----

    def _fire_briefing(self) -> None:
        """生成并播报每日简报。"""
        try:
            briefing = self._build_briefing()
            if briefing:
                self._on_notify(briefing)
        except Exception:
            pass  # 简报生成失败不影响 daemon

    def _build_briefing(self) -> str:
        """组装每日简报文本。"""
        today = date.today()
        lines: list[str] = []

        # 问候
        hour = datetime.now().hour
        if hour < 6:
            greeting = "夜深了，先生"
        elif hour < 12:
            greeting = "早上好，先生"
        elif hour < 14:
            greeting = "中午好，先生"
        elif hour < 18:
            greeting = "下午好，先生"
        else:
            greeting = "晚上好，先生"

        lines.append(f"{greeting}。以下是今日简报：")
        lines.append("")

        # 1. 节假日/工作日状态
        try:
            from agent.core.daemon.holidays import is_holiday, is_workday, get_holiday_name
            holiday_name = get_holiday_name()
            if holiday_name:
                lines.append(f"📅 今天是节假日：{holiday_name}")
            elif not is_workday():
                lines.append("📅 今天是周末，好好休息")
            else:
                lines.append("📅 今天是工作日")
        except Exception:
            pass

        # 2. 今日待触发提醒
        try:
            today_str = today.isoformat()
            pending = self._scheduler.list_pending()
            today_reminders = [
                t for t in pending
                if t.trigger_at.startswith(today_str) and t.note not in (_BRIEFING_NOTE, _DEADLINE_NOTE, _CALENDAR_NOTE)
            ]
            if today_reminders:
                lines.append(f"\n⏰ 今日提醒（{len(today_reminders)} 个）：")
                for t in today_reminders:
                    time_part = t.trigger_at[11:16] if len(t.trigger_at) > 16 else t.trigger_at
                    lines.append(f"  • {time_part} {t.content}")
            else:
                lines.append("\n⏰ 今日无待触发提醒")
        except Exception:
            pass

        # 3. 活跃截止日期
        if self._deadline_tracker:
            try:
                summary = self._deadline_tracker.get_summary()
                if summary:
                    lines.append(f"\n📋 活跃截止日期：")
                    lines.append(summary)
            except Exception:
                pass

        # 4. 系统健康摘要
        if self._monitor:
            try:
                status = self._monitor.get_status()
                if "error" not in status:
                    lines.append(
                        f"\n💻 系统状态：CPU {status['cpu_percent']}% | "
                        f"内存 {status['memory_percent']}% | "
                        f"磁盘剩余 {status['disk_free_gb']}GB"
                    )
            except Exception:
                pass

        # 5. 日历事件
        if self._calendar_source:
            try:
                events = self._calendar_source.get_today_events()
                if events:
                    lines.append(f"\n📆 今日日程（{len(events)} 个）：")
                    for ev in events[:10]:  # 最多显示 10 个
                        lines.append(f"  • {ev}")
            except Exception:
                pass

        lines.append("\n祝您今天顺利，先生。")
        return "\n".join(lines)

    # ---- 截止日期检查 ----

    def _fire_deadline_check(self) -> None:
        """检查截止日期并播报提醒。"""
        if not self._deadline_tracker:
            return
        try:
            messages = self._deadline_tracker.check_today()
            if messages:
                text = "📋 截止日期提醒：\n" + "\n".join(messages)
                self._on_notify(text)
        except Exception:
            pass

    # ---- 日历检查 ----

    def _fire_calendar_check(self) -> None:
        """检查即将到来的日历事件并提前提醒。"""
        if not self._calendar_source:
            return
        try:
            upcoming = self._calendar_source.get_upcoming_events(
                minutes_before=self._config.calendar_remind_minutes_before
            )
            if upcoming:
                text = "📆 日程提醒：\n" + "\n".join(f"  • {ev}" for ev in upcoming)
                self._on_notify(text)
        except Exception:
            pass

    # ---- 工具方法 ----

    def _find_task_by_note(self, note: str) -> ScheduleTask | None:
        """在 Scheduler 中查找指定 note 的 pending 任务。"""
        for t in self._scheduler.list_pending():
            if t.note == note:
                return t
        return None

    @staticmethod
    def _today_at(time_str: str) -> str:
        """把 "HH:MM" 转成今天的 ISO datetime 字符串。

        如果时间已过，则用明天的（确保 daily 任务首次触发在未来）。
        """
        try:
            parts = time_str.split(":")
            hour, minute = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            hour, minute = 8, 30

        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # 如果今天的时间已过，用明天
        if target <= now:
            from datetime import timedelta
            target += timedelta(days=1)

        return target.strftime("%Y-%m-%dT%H:%M:%S")
