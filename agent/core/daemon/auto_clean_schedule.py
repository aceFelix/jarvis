"""自动清理的调度宿主 —— 把每日清理挂到 ``Scheduler`` 上。

## 为什么不塞进 ProactiveEngine

``ProactiveEngine`` 只在桌面（``serve/hub``）路径装配，纯 CLI 与语音 REPL
没有它（见 :mod:`agent.core.daemon.cli_reminder` 模块头注释的「只接 schedule
一族」）。而自动清理恰恰是「不开桌面壳也该发生」的能力，所以挂在两条路都有的
``Scheduler`` 上。

## 为什么触发后要另起线程

``Scheduler.on_fire`` 跑在调度器线程里，文档明确要求「快速返回，重活另起线程」。
自动清理要扫盘 + 移动文件，快的几十毫秒、慢的几秒；直接在里面跑会把同一时刻
到期的提醒往后压——用户只会看到「提醒迟到了」，看不出原因。

## 三层触发，任何一层生效就够

1. **每日定时**：Scheduler 的 daily 任务（04:30，错开画像维护的 03:30）
2. **启动时机会清理**：REPL/daemon 起来时若距上次超过 ``interval_h`` 就跑一次。
   纯 CLI 是短会话，只靠 daily 任务可能永远等不到那一刻
3. **手动**：``/clean --auto`` 直接调 :func:`agent.core.maintenance.auto_clean.run_auto_clean`

三条都走同一个函数，不存在「定时清的和手动清的不是一回事」这种分叉。

@author aceFelix
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable

from agent.core.daemon.scheduler import ScheduleTask, Scheduler

logger = logging.getLogger("jarvis.maintenance.auto_clean")

#: 任务标记：``Scheduler`` 靠它识别「这是我注册的内部任务」，不要当提醒播报
AUTO_CLEAN_NOTE = "__auto_clean__"

#: 默认每天几点跑。刻意排在画像维护（03:30）之后：深夜两件事挤在一起，
#: 出问题时不好判断是谁把机器拖起来的。
DEFAULT_TIME = "04:30"


def _today_at(time_str: str) -> str:
    """把 ``"HH:MM"`` 转成「未来最近一次」的 ISO 时间串。

    今天这个点已过就用明天的——daily 任务的首次触发必须落在未来，
    否则会在注册的瞬间被立刻触发一次（用户刚开机就被清一遍，很突兀）。
    """
    try:
        parts = time_str.split(":")
        hour, minute = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        hour, minute = 4, 30
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        hour, minute = 4, 30
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.isoformat()


def is_auto_clean_task(task: ScheduleTask) -> bool:
    """是不是本模块注册的自动清理任务。"""
    return getattr(task, "note", "") == AUTO_CLEAN_NOTE


def register(scheduler: Scheduler, *, time_str: str = DEFAULT_TIME) -> ScheduleTask | None:
    """注册每日自动清理任务（幂等）。返回任务对象，已存在则返回 None。"""
    for t in scheduler.list_pending():
        if t.note == AUTO_CLEAN_NOTE:
            return None
    task = scheduler.add_task(
        content="自动清理",
        trigger_at=_today_at(time_str),
        repeat="daily",
        note=AUTO_CLEAN_NOTE,
    )
    logger.info("自动清理任务已注册：每日 %s", time_str)
    return task


def fire(
    *,
    max_risk: str = "low",
    keep_days: int = 7,
    min_mb: float = 20.0,
    book: Any | None = None,
    quarantine_root: Any | None = None,
    on_done: Callable[[Any], None] | None = None,
    background: bool = True,
) -> Any:
    """执行一次自动清理。

    Args:
        max_risk: 风险档上限（与 ``auto_ok`` 是两道独立的闸）。
        keep_days: 隔离区保留天数。
        min_mb: 低于这个可回收量就不建批次。
        book: 规则库；None 用内置库。留给测试与自定义部署——一个会移用户文件的
            入口如果没法限定靶子，测试就只能拿真实磁盘当靶场。
        quarantine_root: 隔离区根目录；None 用 ``~/.jarvis/quarantine``。
        on_done: 结果回调（记日志 / 播报用），在**工作线程**里调，实现方须线程安全。
        background: True 另起线程（Scheduler 回调必须用这个）；False 同步返回结果。

    Returns:
        ``background=True`` 时返回 ``Thread``；False 时返回
        :class:`agent.core.maintenance.auto_clean.AutoCleanResult`（异常时为 None）。
    """
    from agent.core.maintenance.auto_clean import run_auto_clean

    # 同步模式要把结果带出去，但又不能用 return——_work 同时被线程路径复用
    holder: list[Any] = []

    def _work() -> None:
        try:
            result = run_auto_clean(
                book, max_risk=max_risk, keep_days=keep_days,
                min_mb=min_mb, quarantine_root=quarantine_root,
            )
        except Exception as e:  # noqa: BLE001 - 后台线程不能让异常逃逸
            logger.warning("自动清理异常: %s", e)
            return
        holder.append(result)
        if result.ran:
            logger.info("自动清理：%s", result.summary())
        else:
            logger.debug("自动清理跳过：%s", result.reason)
        if on_done is not None:
            try:
                on_done(result)
            except Exception as e:  # noqa: BLE001 - 回调故障不影响清理结论
                logger.warning("自动清理回调异常: %s", e)

    if not background:
        _work()
        return holder[0] if holder else None
    thread = threading.Thread(target=_work, daemon=True, name="jarvis-auto-clean")
    thread.start()
    return thread


def run_on_startup_if_due(
    *,
    interval_h: float = 20.0,
    max_risk: str = "low",
    keep_days: int = 7,
    min_mb: float = 20.0,
    on_done: Callable[[Any], None] | None = None,
) -> threading.Thread | None:
    """启动时的机会清理：距上次够久才跑。

    为什么需要它：纯 CLI 是短会话，用户可能从没撞上过 04:30 那一刻。
    没有这条，「随时清」就变成了「只有常驻时清」。

    Returns: 启动了线程则返回它，不需要跑则返回 None。
    """
    from agent.core.maintenance.auto_clean import is_due

    try:
        if not is_due(interval_h=interval_h):
            return None
    except Exception as e:  # noqa: BLE001 - 状态文件读不出来就当作该跑
        logger.debug("自动清理到期判断失败，按需执行: %s", e)
    return fire(
        max_risk=max_risk, keep_days=keep_days, min_mb=min_mb,
        on_done=on_done, background=True,
    )