"""系统资源感知源 —— 把休眠的 ``SystemMonitor`` 收编到统一总线（阶段 11）。

背景（aceFelix）：``core/daemon/monitor.py``（CPU/内存/磁盘阈值 + 磁盘趋势）一直是休眠件，
原设计由常驻 daemon 拉起，而现在宿主形态是 ``--serve`` / 桌面壳。W2 建总线时把 system
登记成"收编位"，刻意不给面板开关。本模块补上那条线。

三个设计取舍：

- **不 Start 监控线程，只索取快照**。``SystemMonitor.start()`` 会起一个每
  ``check_interval`` 秒自转的线程；本源若也起一个，就成了两条并行的判定线，
  阈值状态（``_last_alert`` / ``_alert_active``）还会各记各的。所以这里只用
  ``check_now()``——它不依赖线程状态，且内部冷却照常生效，天然去重。
- **阈值不另造一套**。直接吃 ``settings.monitor_*``（那些字段本来就在，只是没人读），
  避免出现"面板调阈值调不到真实生效值"的第二份真相。
- **采样不归本源管**。底层数值统一由 :mod:`agent.core.system_sampler` 的进程级
  共享采样器提供，右栏每 2 秒采的那份快照在这里直接复用；本源只负责把
  ``AlertInfo`` 翻译成总线事实。``state()`` 依然不做采样——面板快照可能每帧都取，
  不能把这里当成取实时值的入口。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.percept.event import SOURCE_SYSTEM, PerceptEvent

#: 总线排空本源的默认节拍（秒）。比 monitor 自己的 ``check_interval``（10s）宽松：
#: 资源阈值是分钟级事实，没必要每秒看一次。（采样已收口到 system_sampler，
#: 这里不再有 1 秒阻塞的顾虑，节拍宽松纯粹是因为阈值本身是慢变量）
DEFAULT_INTERVAL_S = 60

#: 告警等级 → 显著性先验。critical 值得立刻说，warning 只作为背景信息。
SALIENCE_BY_LEVEL = {"critical": 0.85, "warning": 0.55}


class SystemSource:
    """系统资源感知源（pull 型：总线按节拍调 :meth:`collect`）。

    ``name`` 与配置键 ``percept_system_enabled`` 对应。

    @author aceFelix
    """

    name = SOURCE_SYSTEM

    def __init__(
        self,
        *,
        monitor: Any = None,
        monitor_factory: Any = None,
        interval_s: float = DEFAULT_INTERVAL_S,
        cpu_threshold: float = 85.0,
        memory_threshold: float = 90.0,
        disk_threshold: float = 10.0,
        alert_cooldown: int = 600,
        disk_trend_days: int = 7,
    ) -> None:
        """
        Args:
            monitor / monitor_factory: 注入 ``daemon.monitor.SystemMonitor`` 实例或
                工厂（测试与定制用）。都不传时本源自己构造——延迟到第一次被采集。
            interval_s: 总线排空本源的节拍。
            cpu_threshold / memory_threshold: 使用率超过即告警（百分比）。
            disk_threshold: **磁盘剩余**低于即告警（百分比，方向与上面两个相反——
                这是 ``MonitorConfig`` 的既有口径，本源不改它）。
            alert_cooldown: 同类告警冷却秒数（由 ``SystemMonitor`` 内部记账）。
            disk_trend_days: 磁盘趋势预测天数。
        """
        self.interval_s = float(interval_s)
        self._monitor = monitor
        self._factory = monitor_factory
        self._thresholds = {
            "cpu_threshold": float(cpu_threshold),
            "memory_threshold": float(memory_threshold),
            "disk_threshold": float(disk_threshold),
            "alert_cooldown": int(alert_cooldown),
            "disk_trend_days": int(disk_trend_days),
        }
        self.unavailable = ""      # 依赖层面原因（psutil 缺失/安装失败）
        self.published = 0
        self.last_alert = ""       # 最近一条告警摘要（state() 给面板看，不重新采样）

    # ---- 总线契约 ----

    def collect(self) -> list[PerceptEvent]:
        """取一次资源快照，把触发的阈值告警转成总线事实。

        底层采样走 :mod:`agent.core.system_sampler` 的共享采样器——如果右栏的
        ``MetricsCollector``（2 秒节拍）刚采过，这里直接吃那份快照；纯终端无面板时
        由采样器自己现采（首次会预热 0.1 秒）。
        """
        monitor = self._ensure_monitor()
        if monitor is None:
            return []
        try:
            alerts = list(monitor.check_now() or [])
        except Exception as e:  # noqa: BLE001 - psutil 在某些平台上会抛
            self.unavailable = f"采样失败：{type(e).__name__}"
            return []
        out: list[PerceptEvent] = []
        for alert in alerts:
            ev = self._event(alert)
            if ev is not None:
                out.append(ev)
                self.published += 1
                self.last_alert = ev.summary()
        return out

    def close(self) -> None:
        """本源没有长驻线程可停；保留接口让总线统一调用不报错。"""
        self._monitor = None

    def state(self) -> dict[str, Any]:
        """运行状态（面板"在采什么"用）。

        刻意**不调** ``monitor.get_status()``：面板快照是每帧都可能取的，本源只回报
        自己的计数与最近一次告警摘要，不代它取实时值。
        """
        return {
            "unavailable": self.unavailable,
            "thresholds": dict(self._thresholds),
            "last_alert": self.last_alert,
            "published": self.published,
        }

    # ---- 内部 ----

    def _ensure_monitor(self) -> Any:
        """懒构造 + 懒判可用（幂等）。psutil 缺失时记原因，不抛。"""
        if self._monitor is None:
            if self._factory is not None:
                self._monitor = self._factory()
            else:
                try:
                    from agent.core.daemon.monitor import MonitorConfig, SystemMonitor
                except Exception as e:  # noqa: BLE001 - 模块级依赖问题不该冒到总线
                    self.unavailable = f"监控模块不可用：{type(e).__name__}"
                    return None
                self._monitor = SystemMonitor(
                    MonitorConfig(
                        enabled=True,
                        cpu_threshold=self._thresholds["cpu_threshold"],
                        memory_threshold=self._thresholds["memory_threshold"],
                        disk_threshold=self._thresholds["disk_threshold"],
                        alert_cooldown=self._thresholds["alert_cooldown"],
                        disk_trend_days=self._thresholds["disk_trend_days"],
                    ),
                    on_alert=lambda _a: None,   # 不用回调：本源自己拉快照
                )
        if not bool(getattr(self._monitor, "available", False)):
            if not self.unavailable:
                self.unavailable = "缺少 psutil（pip install psutil 后才能采）"
            return None
        self.unavailable = ""
        return self._monitor

    def _event(self, alert: Any) -> PerceptEvent | None:
        """把一条 ``AlertInfo`` 转成总线事实；不认识的等级按 warning 处理。"""
        alert_type = str(getattr(alert, "alert_type", "") or "unknown")
        level = str(getattr(alert, "level", "") or "warning").lower()
        message = str(getattr(alert, "message", "") or "").strip()
        if not message:
            return None
        # 恢复通知（level=recovery，如“CPU 已回落到 42%”）不是新事实，只是状态回正。
        # 放它进总线等于花一次打扰预算告诉用户“没事了”：按警告发会被播报，按低分发也
        # 只是噪声。需要查历史告警时看面板的观察范围（last_alert）即可，故在源头滤掉。
        if level == "recovery":
            return None
        value = float(getattr(alert, "value", 0) or 0)
        threshold = float(getattr(alert, "threshold", 0) or 0)
        return PerceptEvent(
            source=SOURCE_SYSTEM,
            kind=f"{alert_type}_alert",
            payload={
                "summary": message,
                "alert_type": alert_type,
                "level": level,
                "value": round(value, 1),
                "threshold": round(threshold, 1),
            },
            salience_hint=SALIENCE_BY_LEVEL.get(level, SALIENCE_BY_LEVEL["warning"]),
            # 同类同级的告警由总线在去重窗口内合并；monitor 侧还有 alert_cooldown
            # 这道更长的闸，两层各管一段时间尺度，不重复也不漏。
            dedup_key=f"{alert_type}:{level}",
        )
