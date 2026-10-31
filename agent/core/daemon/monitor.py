"""系统资源监控 —— 贾维斯的"健康感知"。

阶段五第三刀（主动感知）。后台线程定时检查 CPU/内存/磁盘使用率，
超阈值时触发回调（通知 + 语音告警），让贾维斯主动告诉用户
"电脑快撑不住了"。

P2-3 增强：
- 磁盘趋势预测：记录每日磁盘使用率，线性回归预测 N 天后将满
- 工作时长提醒：检测用户连续工作时长，超时提醒休息

**异常进程检测已下线**（2026-10-31）：原来的 ``check_high_cpu_processes()`` 只返回
``"chrome.exe (PID: 1) CPU: 67%"`` 这种字符串，而 ``core/maintenance/health.py`` 的
进程区块给的是 CPU + 内存 + 启动时间的结构化 Top N，且遵循「只发现、只解释、只建议」
的处置原则。两套并存只会让人搞不清该信哪个，故删除本模块那份，需要看进程请用
``/health``。

依赖: psutil（跨平台系统信息库）。未安装时监控不可用，不影响其他功能。

监控项与默认阈值（可在 settings.toml [monitor] 表配置）:
- **CPU**: 使用率 > 85% 持续 30 秒告警（瞬时高负载不报，防误报）
- **内存**: 使用率 > 90% 告警
- **磁盘**: 剩余空间 < 10% 告警（系统盘 C:）
- **磁盘趋势**: 预测 N 天内将满时提前告警
- **工作时长**: 连续工作超过 2 小时提醒休息（详见 ``check_work_break``）

告警去重:
- 同一告警类型 10 分钟内不重复触发（避免"内存满了"每 30 秒报一次烦人）
- 告警解除时（资源回落到阈值下）发一次恢复通知

设计要点:
- **轻量**: 每 10 秒检查一次；采样本身走 ``agent/core/system_sampler.py`` 的进程级
  共享采样器（右栏每 2 秒也在采，这里直接读那份快照，不再阻塞 1 秒重采）
- **线程安全**: _lock 保护 _last_alert 等状态
- **优雅降级**: psutil 未装 → start() 返回 False，daemon 照常运行
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from agent.core.system_sampler import default_sampler


# ---- 默认阈值 ----
DEFAULT_CPU_THRESHOLD = 85.0       # CPU 使用率 %，超过触告警
DEFAULT_CPU_DURATION = 30          # CPU 持续超阈值多少秒才告警（防瞬时尖峰）
DEFAULT_MEMORY_THRESHOLD = 90.0    # 内存使用率 %
DEFAULT_DISK_THRESHOLD = 10.0      # 磁盘剩余空间 %，低于此值告警
DEFAULT_CHECK_INTERVAL = 10        # 检查间隔（秒）
DEFAULT_ALERT_COOLDOWN = 600       # 同类告警冷却时间（秒，10分钟）
# P2-3 增强默认值
DEFAULT_DISK_TREND_DAYS = 7        # 磁盘趋势预测：预测几天后将满
DEFAULT_WORK_BREAK_INTERVAL = 7200 # 工作时长：连续工作多少秒提醒休息
#: 判“真的离位了”的空闲阈值（秒）。低于它算“还在工位上”继续累计，超过它
#: 才把久坐计时归零。定成 5 分钟而非几十秒：集中写代码时几十秒不碰键鼠太常见，
#: 阈值太小会让用户每停一下就把 2 小时的账清零，久坐提醒永远攒不满。
WORK_BREAK_IDLE_RESET = 300


@dataclass
class MonitorConfig:
    """监控配置。从 settings.toml [monitor] 表加载。"""
    cpu_threshold: float = DEFAULT_CPU_THRESHOLD
    cpu_duration: int = DEFAULT_CPU_DURATION
    memory_threshold: float = DEFAULT_MEMORY_THRESHOLD
    disk_threshold: float = DEFAULT_DISK_THRESHOLD
    check_interval: int = DEFAULT_CHECK_INTERVAL
    alert_cooldown: int = DEFAULT_ALERT_COOLDOWN
    enabled: bool = True
    # P2-3 增强
    disk_trend_days: int = DEFAULT_DISK_TREND_DAYS
    work_break_interval: int = DEFAULT_WORK_BREAK_INTERVAL


@dataclass
class AlertInfo:
    """一条告警信息。"""
    alert_type: str       # cpu / memory / disk
    level: str            # warning / critical
    message: str          # 告警文本
    value: float          # 当前值
    threshold: float      # 阈值
    timestamp: datetime = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class SystemMonitor:
    """系统资源监控器。

    后台 daemon 线程定时采样系统资源，超阈值触发回调。

    用法::

        monitor = SystemMonitor(
            config=MonitorConfig(),
            on_alert=lambda alert: print(f"告警: {alert.message}"),
        )
        if monitor.start():
            # 监控运行中
            monitor.stop()
    """

    def __init__(
        self,
        config: MonitorConfig,
        on_alert: Callable[[AlertInfo], None],
        sampler: object = None,
    ) -> None:
        """
        Args:
            config: 监控配置（阈值/间隔等）。
            on_alert: 告警回调。接收 AlertInfo 参数。
            sampler: 采样器（``SystemSampler``）。``None`` 用进程级共享实例——
                右栏每 2 秒采的那份快照可以直接复用，不必重采。
        """
        self._config = config
        self._on_alert = on_alert
        self._sampler_override = sampler
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._available = False

        # 状态跟踪
        self._lock = threading.Lock()
        self._cpu_high_since: float | None = None  # CPU 开始超阈值的时间戳
        self._last_alert: dict[str, float] = {}     # alert_type -> 上次告警时间戳
        self._alert_active: dict[str, bool] = {}    # alert_type -> 当前是否告警中
        # 久坐计时起点（None=还没开始计）。以前用 ``hasattr(self, '_work_start_time')``
        # 懒初始化——那种写法在属性名走样时会静默失效，这里改成显式字段。
        self._work_start_time: float | None = None

        # 检查 psutil 是否可用
        try:
            import psutil  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False

    @property
    def available(self) -> bool:
        """psutil 是否可用。"""
        return self._available

    def _sampler(self) -> object:
        """取采样器：注入的优先，否则用进程级共享实例（见 core/system_sampler.py）。"""
        if self._sampler_override is not None:
            return self._sampler_override
        return default_sampler()

    def start(self) -> bool:
        """启动监控线程。返回是否成功启动（psutil 不可用返回 False）。"""
        if not self._available:
            return False
        if self._started or not self._config.enabled:
            return self._started
        self._started = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="jarvis-monitor")
        self._thread.start()
        return True

    def stop(self) -> None:
        """停止监控。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._started = False

    def check_now(self) -> list[AlertInfo]:
        """立即执行一次检查，返回当前触发的告警列表。

        可用于手动触发检查（如用户问"电脑状态怎么样"）。
        """
        if not self._available:
            return []
        return self._do_check()

    def get_status(self) -> dict:
        """获取当前系统资源状态（不触发告警）。

        可用于用户问"电脑状态"时返回实时数据。采样走共享采样器，
        返回字段与历史版本完全一致（阈值也一并带上）。
        """
        if not self._available:
            return {"error": "psutil 未安装，监控不可用"}

        try:
            snap = self._sampler().snapshot()
            # 契约不变：CPU / 内存采不到时仍返回 error dict（带原始异常类型），
            # 简报那侧靠 `"error" not in status` 判断这一段能不能播。
            if not snap.cpu_ok or not snap.memory_ok:
                return {"error": f"获取系统状态失败: {snap.error or '采样异常'}"}
            status = snap.to_status_dict()
            status.update({
                "cpu_threshold": self._config.cpu_threshold,
                "memory_threshold": self._config.memory_threshold,
                "disk_threshold_free_percent": self._config.disk_threshold,
            })
            return status
        except Exception as e:
            return {"error": f"获取系统状态失败: {type(e).__name__}: {e}"}

    # ---- 内部 ----

    def _run(self) -> None:
        """监控主循环。"""
        while not self._stop_event.is_set():
            try:
                self._do_check()
            except Exception:
                pass
            self._stop_event.wait(timeout=self._config.check_interval)

    def _do_check(self) -> list[AlertInfo]:
        """执行一次检查，触发告警回调。返回本次触发的告警列表。"""
        now = time.time()
        alerts: list[AlertInfo] = []

        # 采样统一走共享采样器（agent/core/system_sampler.py）：右栏每 2 秒也在采，
        # 这里直接读它刚采的快照，不必再阻塞 1 秒重采一遍。
        snap = self._sampler().snapshot()

        # ---- CPU 检查（需持续超阈值才告警）----
        cpu_percent = snap.cpu_percent

        with self._lock:
            if cpu_percent > self._config.cpu_threshold:
                if self._cpu_high_since is None:
                    self._cpu_high_since = now
                duration = now - self._cpu_high_since
                if duration >= self._config.cpu_duration:
                    alert = self._make_alert(
                        "cpu", cpu_percent, self._config.cpu_threshold,
                        f"CPU 使用率持续 {duration:.0f} 秒超过 {self._config.cpu_threshold}%，"
                        f"当前 {cpu_percent:.0f}%",
                        now,
                    )
                    if alert:
                        alerts.append(alert)
            else:
                # CPU 回落
                if self._alert_active.get("cpu"):
                    self._alert_active["cpu"] = False
                    alerts.append(AlertInfo(
                        alert_type="cpu",
                        level="recovery",
                        message=f"CPU 使用率已回落到 {cpu_percent:.0f}%，恢复正常",
                        value=cpu_percent,
                        threshold=self._config.cpu_threshold,
                    ))
                self._cpu_high_since = None

        # ---- 内存检查 ----
        memory_percent = snap.memory_percent

        with self._lock:
            if memory_percent > self._config.memory_threshold:
                alert = self._make_alert(
                    "memory", memory_percent, self._config.memory_threshold,
                    f"内存使用率 {memory_percent:.0f}%，超过 {self._config.memory_threshold}%，"
                    f"仅剩 {snap.memory_available_gb:.1f}GB 可用",
                    now,
                )
                if alert:
                    alerts.append(alert)
            elif self._alert_active.get("memory"):
                self._alert_active["memory"] = False
                alerts.append(AlertInfo(
                    alert_type="memory",
                    level="recovery",
                    message=f"内存使用率已回落到 {memory_percent:.0f}%，恢复正常",
                    value=memory_percent,
                    threshold=self._config.memory_threshold,
                ))

        # ---- 磁盘检查 ----
        # 采样失败（无权限/盘不存在）时静默跳过，与原实现一致。
        if snap.disk_ok:
            disk_free_percent = snap.disk_free_percent
            with self._lock:
                if disk_free_percent < self._config.disk_threshold:
                    alert = self._make_alert(
                        "disk", disk_free_percent, self._config.disk_threshold,
                        f"系统盘剩余空间仅 {disk_free_percent:.1f}%（{snap.disk_free_gb:.1f}GB），"
                        f"低于 {self._config.disk_threshold}% 阈值，建议清理",
                        now,
                    )
                    if alert:
                        alerts.append(alert)
                elif self._alert_active.get("disk"):
                    self._alert_active["disk"] = False
                    alerts.append(AlertInfo(
                        alert_type="disk",
                        level="recovery",
                        message=f"磁盘剩余空间恢复到 {disk_free_percent:.1f}%",
                        value=disk_free_percent,
                        threshold=self._config.disk_threshold,
                    ))

        # ---- 久坐提醒（P2-3）----
        # 前三项是“机器到危险线了”，这一项是“人该歇了”——两种性质不同，所以不走
        # ``_make_alert``：它的 ``alert_cooldown`` 管的是“同一件事多久不再说”，
        # 而久坐提醒的节流靠 ``check_work_break`` 自己“说过就重置计时”，天然就是
        # 一个 ``work_break_interval`` 长的周期，再套冷却只会把间隔拉得更长。
        #
        # 以前 ``check_work_break`` 写完整了却**没挂在这里**，所以配置项改了也白改：
        # 这条链是 ``SystemSource.collect()``（每 60s）→ ``check_now()`` → 本方法，
        # 是全工程唯一在跑的监控驱动点（``start()`` 从来没被调用过）。
        try:
            rest_text = self.check_work_break()
        except Exception:
            rest_text = None
        if rest_text:
            alerts.append(AlertInfo(
                alert_type="work_break",
                level="warning",
                message=rest_text,
                value=float(self._config.work_break_interval),
                threshold=float(self._config.work_break_interval),
            ))

        # 触发回调
        for alert in alerts:
            try:
                self._on_alert(alert)
            except Exception:
                pass

        return alerts

    def _make_alert(
        self,
        alert_type: str,
        value: float,
        threshold: float,
        message: str,
        now: float,
    ) -> AlertInfo | None:
        """构造告警（带冷却检查）。调用方需持锁。返回 None=冷却中不告警。"""
        last = self._last_alert.get(alert_type, 0)
        if now - last < self._config.alert_cooldown:
            return None  # 冷却中

        self._last_alert[alert_type] = now
        self._alert_active[alert_type] = True

        level = "critical" if value > threshold * 1.2 else "warning"
        return AlertInfo(
            alert_type=alert_type,
            level=level,
            message=message,
            value=value,
            threshold=threshold,
        )

    # ---- P2-3 增强：磁盘趋势预测 ----

    def _history_file(self) -> Path:
        """监控历史数据文件: ~/.jarvis/monitor_history.json"""
        return Path.home() / ".jarvis" / "monitor_history.json"

    def record_disk_usage(self) -> None:
        """记录当前磁盘使用率到历史文件（每天一条）。

        由 ProactiveEngine 每日简报时调用，或 daemon 启动时调用。
        """
        if not self._available:
            return
        try:
            # max_age_s=0 强制现采：日记录一天只写一条，宁可多采一次，
            # 不能把 2 秒前的缓存当成"今天"的值写进趋势库。
            snap = self._sampler().snapshot(max_age_s=0)
            if not snap.disk_ok:
                return
            today_str = datetime.now().strftime("%Y-%m-%d")

            history = self._load_history()
            # 每天只记录一条（同一天覆盖）
            history = [h for h in history if h.get("date") != today_str]
            history.append({
                "date": today_str,
                "disk_percent": round(snap.disk_percent_used, 2),
                "disk_free_gb": round(snap.disk_free_gb, 2),
            })
            # 保留最近 90 天
            history = history[-90:]
            self._save_history(history)
        except Exception:
            pass

    def predict_disk_full(self) -> str | None:
        """基于历史数据预测磁盘何时将满。

        用简单线性回归拟合磁盘使用率趋势。
        返回告警文本，或 None（无趋势/数据不足）。
        """
        history = self._load_history()
        if len(history) < 3:  # 至少 3 天数据才能做趋势
            return None

        # 提取数据点
        points = [(i, h["disk_percent"]) for i, h in enumerate(history) if "disk_percent" in h]
        if len(points) < 3:
            return None

        # 简单线性回归: y = a + b*x
        n = len(points)
        sum_x = sum(p[0] for p in points)
        sum_y = sum(p[1] for p in points)
        sum_xy = sum(p[0] * p[1] for p in points)
        sum_x2 = sum(p[0] ** 2 for p in points)

        denom = n * sum_x2 - sum_x ** 2
        if denom == 0:
            return None

        b = (n * sum_xy - sum_x * sum_y) / denom  # 斜率（每天增长 %）
        a = (sum_y - b * sum_x) / n  # 截距

        if b <= 0:
            return None  # 磁盘使用率在下降，无需告警

        # 预测达到 95% 的天数
        current_percent = points[-1][1]
        target_percent = 95.0
        if current_percent >= target_percent:
            return None  # 已经很高了，由磁盘阈值告警处理

        days_to_full = (target_percent - current_percent) / b
        trend_days = self._config.disk_trend_days

        if days_to_full <= trend_days:
            return (
                f"磁盘空间趋势预警：按当前增长速度，"
                f"约 {days_to_full:.0f} 天后磁盘将满（当前 {current_percent:.1f}%，"
                f"每天增长 {b:.2f}%）。建议提前清理。"
            )
        return None

    def _load_history(self) -> list[dict]:
        """加载监控历史数据。"""
        path = self._history_file()
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_history(self, history: list[dict]) -> None:
        """保存监控历史数据。"""
        try:
            path = self._history_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ---- P2-3 增强：工作时长提醒 ----

    def get_idle_time_seconds(self) -> float | None:
        """获取用户空闲时间（秒）。

        Windows: 通过 GetLastInputInfo 获取最后一次键鼠输入时间。
        其他平台: 返回 None（不支持）。
        """
        try:
            import sys as _sys
            if _sys.platform == "win32":
                import ctypes
                class LASTINPUTINFO(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
                lii = LASTINPUTINFO()
                lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
                ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
                millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                return millis / 1000.0
            return None
        except Exception:
            return None

    def check_work_break(self) -> str | None:
        """检查是否需要提醒用户休息。

        连续工作时长靠“键鼠有多久没动”反推：空闲短于
        :data:`WORK_BREAK_IDLE_RESET` 视为还在工位上，继续累计；一旦超过它，
        判定为真中断（开会、午休、走开），计时从真正回来的那一刻重新起算。

        以前这里用的是“空闲 < 60 秒就算在工作”，与``work_break_interval``
        （默认 2 小时）根本对不上：用户只要发一会儿呆、读段文档，计时就归零，
        2 小时永远攒不满。这也是该功能尽管测试全绿却从未真正触发过的原因之一。

        Returns:
            该休息时的提醒文本；不需要提醒时返回 ``None``。
        """
        idle = self.get_idle_time_seconds()
        if idle is None:
            return None

        now = time.time()
        # 离位够久：不是“没在干活”，而是“已经歇过了”，计时从头再来。
        # 起点用 ``now - idle`` 而不是 ``now``——这样即使隔一分钟才轮询一次，
        # 也是从用户真正落座那一刻开始算。
        if idle >= WORK_BREAK_IDLE_RESET:
            self._work_start_time = now - idle
            return None
        if self._work_start_time is None:
            self._work_start_time = now - idle
            return None

        work_duration = now - self._work_start_time
        if work_duration < self._config.work_break_interval:
            return None

        hours = int(work_duration // 3600)
        minutes = int((work_duration % 3600) // 60)
        # 提醒过就重置计时（下一轮再从零攒 ``work_break_interval``），
        # 否则每 60 秒都会重算一次并重复告警。
        self._work_start_time = now
        return (
            f"先生，您已连续工作 {hours} 小时 {minutes} 分钟了。"
            f"建议起身活动一下，喝杯水，让眼睛休息休息。"
        )
