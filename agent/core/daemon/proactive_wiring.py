"""ProactiveEngine 装配：把 settings 翻成一个装好日历源与系统监控的引擎。

**为什么单独成文件**：``hub.py`` 早就贴着 800 行上限，而这一整段是「配置 → 实例」
的纯装配，既不属于感知总线也不属于通知投递。搬出来之后 hub 里只剩一次调用。

**为什么以前简报里没有「今日日程」「系统状态」两段**：``ProactiveEngine`` 的
``calendar_source`` / ``monitor`` 两个参数一直没人传（hub 那句注释写「本期不接线
（二期）」，把两件事混在一起说了），于是 ``_build_briefing`` 里两个 ``if`` 永远进
不去。现在按 ``[calendar] enabled`` 与 ``[monitor]`` 阈值真装配。

``CalendarSource`` 自己在 ``get_today_events`` / ``get_upcoming_events`` 里检查
``config.enabled``，关掉时只返回空列表——所以外层不需要再判一次，传实例即可。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.daemon.calendar_source import CalendarConfig, CalendarSource
from agent.core.daemon.deadline import DeadlineTracker
from agent.core.daemon.monitor import MonitorConfig, SystemMonitor
from agent.core.daemon.proactive import ProactiveConfig, ProactiveEngine


def build_calendar_source(settings: Any) -> CalendarSource:
    """按 ``[calendar]`` 段构造日历源。

    Args:
        settings: 全局配置（取 ``calendar_*`` 字段）。

    Returns:
        日历源实例；``enabled=False`` 时它自己返回空列表，不会抛错也不会读文件。
    """
    return CalendarSource(CalendarConfig(
        enabled=bool(getattr(settings, "calendar_enabled", False)),
        backend=str(getattr(settings, "calendar_backend", "auto") or "auto"),
        ics_path=str(getattr(settings, "calendar_ics_path", "") or ""),
        ics_url=str(getattr(settings, "calendar_ics_url", "") or ""),
        remind_minutes_before=int(getattr(settings, "calendar_remind_minutes_before", 30) or 30),
    ))


def build_system_monitor(settings: Any) -> SystemMonitor:
    """按 ``[monitor]`` 阈值构造监控实例（**不 start**）。

    简报只用 ``get_status()``，它走 ``system_sampler`` 的共享快照，不需要起线程
    ——「不要起第二条并行采样线」是那个模块的硬约束，这里必须遵守。

    Args:
        settings: 全局配置（取 ``monitor_*`` 阈值）。

    Returns:
        监控实例；psutil 缺失时内部 ``_available`` 为假，``get_status()`` 回错误
        dict，简报侧靠 ``"error" not in status`` 跳过这一段。
    """
    return SystemMonitor(
        MonitorConfig(
            enabled=True,
            cpu_threshold=float(getattr(settings, "monitor_cpu_threshold", 85.0) or 85.0),
            memory_threshold=float(getattr(settings, "monitor_memory_threshold", 90.0) or 90.0),
            disk_threshold=float(getattr(settings, "monitor_disk_threshold", 10.0) or 10.0),
            alert_cooldown=int(getattr(settings, "monitor_alert_cooldown", 600) or 600),
            disk_trend_days=int(getattr(settings, "monitor_disk_trend_days", 7) or 7),
            # 简报这个实例不跑 ``check_now()``，久坐提醒实际由感知源驱动；
            # 这里仍把值传齐，是为了两边口径一致——将来若有人把它切到采集模式，
            # 不会默默用回默认的 2 小时。
            work_break_interval=int(
                getattr(settings, "monitor_work_break_interval", 7200) or 7200
            ),
        ),
        # 告警回调是构造必填项，但这里的实例只用来 ``get_status()`` 读快照，
        # 既不 start 线程也不会触发告警——阈值告警那条路在感知源（SystemSource）
        # 里跑，别在这里再开一份。
        on_alert=lambda _a: None,
    )


def build_proactive_engine(
    settings: Any,
    *,
    scheduler: Any,
    deadline_tracker: DeadlineTracker,
    on_notify: Any,
    budget: Any = None,
) -> ProactiveEngine:
    """把 settings 装配成 ProactiveEngine。

    Args:
        settings: 全局配置（简报/截止日期/日历/监控/自动清理各段）。
        scheduler: 宿主调度器（引擎把每日任务注册进去）。
        deadline_tracker: 截止日期追踪器。
        on_notify: 通知出口回调。
        budget: 打扰预算单例；``None`` 表示未启用预算，直接播。

    Returns:
        装好日历源与系统监控的引擎实例。
    """
    return ProactiveEngine(
        scheduler=scheduler,
        config=ProactiveConfig(
            briefing_enabled=settings.briefing_enabled,
            briefing_time=settings.briefing_time,
            deadline_enabled=settings.deadline_enabled,
            deadline_check_time=settings.deadline_check_time,
            calendar_enabled=bool(getattr(settings, "calendar_enabled", False)),
            calendar_remind_minutes_before=int(
                getattr(settings, "calendar_remind_minutes_before", 30) or 30
            ),
            # 画像维护纯本地计算（无 LLM、无打扰），恒开
            profile_maintenance_enabled=True,
            # 自动清理：总闸与子闸同时成立才启用，与 REPL 路径保持同一口径
            auto_clean_enabled=(
                bool(getattr(settings, "maintenance_enabled", False))
                and bool(getattr(settings, "maintenance_auto_clean_enabled", False))
            ),
            auto_clean_time=str(
                getattr(settings, "maintenance_auto_clean_time", "04:30") or "04:30"
            ),
            auto_clean_min_mb=float(
                getattr(settings, "maintenance_auto_clean_min_mb", 20.0) or 0.0
            ),
        ),
        deadline_tracker=deadline_tracker,
        calendar_source=build_calendar_source(settings),
        monitor=build_system_monitor(settings),
        on_notify=on_notify,
        budget=budget,
    )
