"""主动播报中枢 —— 复活 59f746a 休眠的主动感知套件，宿主从托盘换成 WS 事件流。

老 daemon 时代（``agent/daemon/notifications.py`` NotificationMixin）的三通道
播报（终端日志 + 托盘通知 + 待机 TTS）随常驻托盘架构下线而休眠，提交信息
明确「主动感知全套保留，等待 GUI 接线」。本模块即桌面壳（jarvis-desktop）
的接线体：

- 装配 ``Scheduler``（定时轮询）+ ``ProactiveEngine``（每日简报/截止日期）
  + ``DeadlineTracker``（截止日期追踪），配置字段沿用 settings 既有口径；
- 到期/通知不再走托盘与 TTS，而是投递 ``proactive_notify`` 事件进 serve
  事件队列，由事件泵 broadcast 给桌面壳（聊天气泡 + 系统通知）；
- 提醒工具（ScheduleReminder 等）经 ChatEngine registry_hook 挂载同一
  Scheduler 实例，对话里说「提醒我」即可创建任务。

与老 daemon 常驻语义的差异：serve 进程随桌面壳启停，播报仅在运行期间
生效。补救链：Scheduler 自带错过补偿（一次性任务 ≤1 小时）+ 本模块的
简报补播窗口（错过 ≤ settings.briefing_catchup_window_min 分钟、默认 120
即 2 小时，启动时补播一次）。

@author aceFelix
"""

from __future__ import annotations

import queue
import threading
from datetime import datetime
from typing import Any

from agent.config.settings import Settings
from agent.core.daemon.deadline import DeadlineTracker
from agent.core.daemon.proactive import ProactiveConfig, ProactiveEngine
from agent.core.daemon.scheduler import Scheduler, ScheduleTask
from agent.serve import protocol

# 补播窗口（分钟）已改由 settings.briefing_catchup_window_min 配置（默认 120），
# 便于随时调整“错过多久以内才补播”，不再作为模块常量硬编码。

# 补播延迟（秒）：等桌面壳 WS 连上再投事件，否则广播时无客户端接收。
# 属启动时序技术参数（非用户日常调整项），故保持常量、不进配置文件。
CATCHUP_DELAY_SEC = 5.0

# 截止日期提醒文本前缀（ProactiveEngine._fire_deadline_check 的固定格式），
# 用于在 on_notify 单一回调里区分简报与截止日期两类内容
_DEADLINE_PREFIX = "📋 截止日期提醒"


class ProactiveHub:
    """主动播报中枢：Scheduler + ProactiveEngine 的 serve 宿主装配体。

    用法::

        hub = ProactiveHub(settings, event_queue)
        hub.start()
        # ... serve 运行中，到期任务投 proactive_notify 事件 ...
        hub.stop()

    @author aceFelix
    """

    def __init__(
        self,
        settings: Settings,
        event_queue: queue.Queue[dict[str, Any]],
    ) -> None:
        """
        Args:
            settings: 全局配置（取 briefing_*/deadline_* 字段）。
            event_queue: serve 事件队列（与 ChatEngine 共用，事件泵消费）。
        """
        self._settings = settings
        self._event_queue = event_queue
        self._scheduler = Scheduler(on_fire=self._on_fire)
        self._deadline_tracker = DeadlineTracker()
        self._engine = ProactiveEngine(
            scheduler=self._scheduler,
            config=ProactiveConfig(
                briefing_enabled=settings.briefing_enabled,
                briefing_time=settings.briefing_time,
                deadline_enabled=settings.deadline_enabled,
                deadline_check_time=settings.deadline_check_time,
                # 日历源与系统监控本期不接线（二期），传 None 即优雅降级
                calendar_enabled=False,
                profile_maintenance_enabled=True,
            ),
            deadline_tracker=self._deadline_tracker,
            on_notify=self._on_notify,
        )
        self._started = False
        self._catchup_timer: threading.Timer | None = None

    # ---- 对外只读访问（registry_hook 注册提醒/截止日期工具用） ----

    @property
    def scheduler(self) -> Scheduler:
        """内部 Scheduler 单例（register_schedule_tools 挂载点）。"""
        return self._scheduler

    @property
    def deadline_tracker(self) -> DeadlineTracker:
        """内部 DeadlineTracker 单例（register_deadline_tools 挂载点）。"""
        return self._deadline_tracker

    # ---- 生命周期 ----

    def start(self) -> None:
        """启动调度器与主动感知引擎，并做简报补播检查（幂等）。"""
        if self._started:
            return
        self._started = True
        self._scheduler.start()
        self._engine.start()
        # 补播判定仅在进程启动时做一次，不持久化（重启不重复补）
        if self.missed_briefing_minutes() is not None:
            self._catchup_timer = threading.Timer(
                CATCHUP_DELAY_SEC, self._fire_catchup_briefing
            )
            self._catchup_timer.daemon = True
            self._catchup_timer.start()

    def stop(self) -> None:
        """停止引擎与调度器（幂等）。先撤补播定时器再停引擎。"""
        if not self._started:
            return
        self._started = False
        if self._catchup_timer is not None:
            self._catchup_timer.cancel()
            self._catchup_timer = None
        self._engine.stop()
        self._scheduler.stop()

    def acknowledge(self, task_id: str) -> bool:
        """确认提醒任务（桌面壳 proactive.ack 指令入口，停止升级重发）。"""
        return self._scheduler.acknowledge(task_id)

    # ---- 到期/通知回调（复刻老 NotificationMixin 语义，通道换成事件流） ----

    def _on_fire(self, task: ScheduleTask) -> None:
        """定时任务到期回调（Scheduler 后台线程触发）。

        ProactiveEngine 注册的任务转引擎自处理（生成简报/检查截止日期）；
        用户提醒任务投 proactive_notify 事件（老语义：托盘通知 + TTS 播报，
        桌面壳语义：聊天气泡 + 系统通知）。
        """
        if self._engine.is_proactive_task(task):
            self._engine.handle_task_fire(task)
            return
        self._emit(kind="reminder", title="贾维斯提醒", text=task.content, task_id=task.id)

    def _on_notify(self, message: str) -> None:
        """主动感知引擎通知回调（每日简报 / 截止日期提醒共用出口）。

        老语义按内容无差别走托盘 + TTS；这里按固定前缀区分 kind，
        让桌面壳能对简报与截止日期提醒做差异化渲染。
        """
        kind = "deadline" if message.startswith(_DEADLINE_PREFIX) else "briefing"
        self._emit(kind=kind, title="贾维斯主动提醒", text=message)

    def _emit(self, *, kind: str, title: str, text: str, task_id: str = "") -> None:
        """投递 proactive_notify 事件进 serve 事件队列（线程安全）。"""
        self._event_queue.put_nowait(
            {
                "type": protocol.EVT_PROACTIVE_NOTIFY,
                "payload": {
                    "kind": kind,
                    "title": title,
                    "text": text,
                    "task_id": task_id,
                },
            }
        )

    # ---- 简报补播 ----

    def missed_briefing_minutes(self) -> float | None:
        """计算今天简报错过多久（分钟），判定是否补播。

        补播窗口取自 ``settings.briefing_catchup_window_min``（默认 120 分钟）。

        Returns:
            需要补播时返回错过分钟数（0 < x ≤ 补播窗口）；否则 None
            （简报关闭 / 时间或窗口非法 / 时间未到 / 错过超窗口）。纯判定
            无副作用，便于单测。
        """
        if not self._settings.briefing_enabled:
            return None
        try:
            hour_s, minute_s = str(self._settings.briefing_time).split(":")
            target = datetime.now().replace(
                hour=int(hour_s), minute=int(minute_s), second=0, microsecond=0
            )
            # 窗口一并入 try：配置写了非法值时安全降级为“不补播”，不拖垮 serve 启动
            window_min = float(self._settings.briefing_catchup_window_min)
        except (ValueError, IndexError, TypeError):
            return None
        missed_min = (datetime.now() - target).total_seconds() / 60.0
        if 0 < missed_min <= window_min:
            return missed_min
        return None

    def _fire_catchup_briefing(self) -> None:
        """补播每日简报（Timer 线程触发）。异常静默，不影响 serve 主流程。"""
        try:
            self._engine._fire_briefing()
        except Exception:
            pass
