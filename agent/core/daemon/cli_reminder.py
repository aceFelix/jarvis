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

    def __init__(self, notify: Callable[[str], None]) -> None:
        """
        Args:
            notify: 到期播报回调，接收一条已格式化的提醒文本。必须线程安全。
        """
        self._notify = notify
        self._scheduler: Scheduler | None = None

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
        """
        text = f"提醒：{task.content}"
        try:
            self._notify(text)
        except Exception:
            pass
