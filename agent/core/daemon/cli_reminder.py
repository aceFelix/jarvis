"""CLI / 语音模式的定时提醒宿主。

桌面（serve）路径在 `agent/serve/app.py` 里注册 ScheduleReminder 一族工具，
而纯 CLI 与语音 REPL 此前**从未注册**——于是用户对终端贾维斯说「明天中午
提醒我面试」，它搜不到工具是事实而非幻觉，接着无视工具描述里「不要自行绕道
拼凑实现」的警告，转去手搓 `Register-ScheduledTask`，思考长度撞穿
`max_tokens` 后开始空转（见 docs/fixlogs/agent-loop-stall-fix.md 第六节）。
本模块把 Scheduler 生命周期搬进 REPL 进程，让三条装配路径能力对齐。

设计要点:
- **只接 schedule（提醒）一族**：deadline 工具的到期触发依赖 ProactiveEngine
  的每日检查，本宿主不起引擎，注册它只会制造「能添加、不会提醒」的假能力。
- **播报走注入的 notify 回调**：回调运行在 Scheduler 后台线程，实现方必须
  线程安全且不抛异常；宿主再兜一层 try/except，保证 UI 故障不会让调度线程
  停止响应后续任务。
- **不做 TTS**：语音模式下 PyAudio 并发有已知崩溃陷阱（`barge_in` 默认关闭
  就是为此），主动播报能力仍只属于桌面/常驻 daemon 路径。
- **仅进程存活期间触发**：CLI 关闭后不会提醒；跨进程常驻请用桌面版。
- **顺手带上自动清理的调度**：本模块是 REPL 里唯一拥有 Scheduler 的地方，
  自动清理只能挂在这里（详见 :mod:`agent.core.daemon.auto_clean_schedule`）。
  它与提醒互不干扰：清理任务另起线程执行，不占调度器线程。

@author aceFelix
"""

from __future__ import annotations

from typing import Callable

from agent.core.daemon.scheduler import ScheduleTask, Scheduler


class CliReminderHost:
    """把 Scheduler 挂进 REPL 进程，并向工具注册表挂载提醒工具。

    用法::

        host = CliReminderHost(notify=ui.notify_reminder)
        host.start()
        host.register_tools(registry)   # 返回注册的工具数
        ...
        host.stop()                      # 退出前回收后台线程

    @author aceFelix
    """

    def __init__(self, notify: Callable[[str], None], settings: object | None = None) -> None:
        """
        Args:
            notify: 到期播报回调，接收一条已格式化的提醒文本。必须线程安全。
            settings: 全局 Settings（用于读 ``maintenance_*`` / ``auto_clean_*``）。
                传 None 则自动清理一律不启用——不给隐式默认值，因为“删文件”
                这个动作不应当因为没传参数就自己跑起来。
        """
        self._notify = notify
        self._settings = settings
        self._scheduler: Scheduler | None = None
        self._auto_clean_on = False

    @property
    def scheduler(self) -> Scheduler | None:
        """内部 Scheduler（未 start 时为 None），供命令/测试按需访问。"""
        return self._scheduler

    def start(self) -> Scheduler:
        """创建并启动 Scheduler 后台线程（幂等）。

        Scheduler 会自行加载持久化的待触发任务，因此上次会话遗留、尚未到期
        的提醒在本次进程存活期间同样会按时触发。
        """
        if self._scheduler is None:
            self._scheduler = Scheduler(on_fire=self._on_fire)
            self._scheduler.start()
            self._setup_auto_clean()
        return self._scheduler

    def register_tools(self, registry) -> int:
        """把 ScheduleReminder / ListSchedule / CancelSchedule /
        AcknowledgeReminder 挂到注册表（延迟加载工具，经 ToolSearch 取用）。

        必须先 start()：工具构造函数需要可用的 Scheduler 实例。

        Returns: 实际注册的工具数（0 表示已存在或未启动）。
        """
        if self._scheduler is None:
            return 0
        from agent.tools.extensions.schedule_tool import register_schedule_tools

        return register_schedule_tools(registry, self._scheduler)

    def stop(self) -> None:
        """停止调度线程。REPL 退出前调用，避免后台线程拖住进程退出。"""
        if self._scheduler is not None:
            self._scheduler.stop()
            self._scheduler = None

    def _on_fire(self, task: ScheduleTask) -> None:
        """Scheduler 后台线程的到期回调：格式化文本后交 notify 播报。

        重复任务（daily/weekly）由 Scheduler 自行重排下一次，本回调只管播报。
        播报异常一律吞掉——不能让一次 UI 失败终止整个调度循环。

        自动清理任务走另一个分支：它不播报，且必须**立即返回**（真正的清理
        在别的线程里跑）。Scheduler 的文档明确要求回调快返回，这里若直接
        执行清理，同一秒到期的提醒会被往后压。
        """
        from agent.core.daemon import auto_clean_schedule as acs

        if acs.is_auto_clean_task(task):
            cfg = self._auto_clean_cfg()
            acs.fire(
                max_risk=cfg["max_risk"], keep_days=cfg["keep_days"],
                min_mb=cfg["min_mb"], on_done=self._on_auto_clean_done,
            )
            return

        text = f"提醒：{task.content}"
        try:
            self._notify(text)
        except Exception:
            pass

    # ---- 自动清理 ----

    def _auto_clean_cfg(self) -> dict:
        """从 Settings 读出自动清理的参数（缺字段一律走保守默认）。"""
        s = self._settings
        get = lambda name, default: getattr(s, name, default)  # noqa: E731
        return {
            "max_risk": "low",   # 自动清理固定只到 low：medium 及以上都该由人拍板
            "keep_days": int(get("maintenance_quarantine_days", 7) or 7),
            "min_mb": float(get("maintenance_auto_clean_min_mb", 20.0) or 0.0),
            "time": str(get("maintenance_auto_clean_time", "04:30") or "04:30"),
        }

    def _setup_auto_clean(self) -> None:
        """注册每日任务 + 启动时机会清理（只改文件的那几条，见 auto_clean 模块）。

        两个开关同时成立才启用：``maintenance_enabled``（维护层总闸）与
        ``auto_clean_enabled``（自动清理子闸）。任一为 false 都安静退出，
        不在终端里刷“已跳过”一类噪声。
        """
        s = self._settings
        if s is None or self._scheduler is None:
            return
        if not getattr(s, "maintenance_enabled", False):
            return
        if not getattr(s, "auto_clean_enabled", False):
            return

        from agent.core.daemon import auto_clean_schedule as acs

        cfg = self._auto_clean_cfg()
        try:
            acs.register(self._scheduler, time_str=cfg["time"])
        except Exception as e:  # noqa: BLE001 - 注册失败不影响提醒能力
            import logging

            logging.getLogger("jarvis.maintenance.auto_clean").warning(
                "自动清理任务注册失败: %s", e
            )
            return
        self._auto_clean_on = True

        # 启动时的机会清理：纯 CLI 是短会话，光靠 04:30 那一刻可能永远遇不上
        try:
            acs.run_on_startup_if_due(
                max_risk=cfg["max_risk"], keep_days=cfg["keep_days"],
                min_mb=cfg["min_mb"], on_done=self._on_auto_clean_done,
            )
        except Exception as e:  # noqa: BLE001
            import logging

            logging.getLogger("jarvis.maintenance.auto_clean").warning(
                "启动清理检查失败: %s", e
            )

    def _on_auto_clean_done(self, result: object) -> None:
        """清理完成回调（工作线程里执行）。

        刻意**不播报**：自动清理是零感知垃圾的例行公事，天天报一句“我清了
        3 MB”反而是在消耗用户的注意力。只有清出真东西（超过
        ``auto_clean_notify_mb``）才提示一句，且走普通 info 不占打扰预算。
        """
        s = self._settings
        threshold = float(getattr(s, "maintenance_auto_clean_notify_mb", 200.0) or 0.0)
        try:
            reclaimed = float(getattr(result, "reclaimed_mb", 0.0) or 0.0)
            ran = bool(getattr(result, "ran", False))
        except Exception:  # noqa: BLE001
            return
        if not ran or reclaimed < threshold:
            return
        try:
            summary = getattr(result, "summary", None)
            self._notify(f"自动清理：{summary() if callable(summary) else reclaimed}")
        except Exception:
            pass
