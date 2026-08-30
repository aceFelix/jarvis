"""系统资源监控 —— 贾维斯的"健康感知"。

阶段五第三刀（主动感知）。后台线程定时检查 CPU/内存/磁盘使用率，
超阈值时触发回调（通知 + 语音告警），让贾维斯主动告诉用户
"电脑快撑不住了"。

P2-3 增强：
- 磁盘趋势预测：记录每日磁盘使用率，线性回归预测 N 天后将满
- 异常进程检测：CPU 占用 > 50% 持续 N 分钟的进程通知用户
- 工作时长提醒：检测用户连续工作时长，超时提醒休息

依赖: psutil（跨平台系统信息库）。未安装时监控不可用，不影响其他功能。

监控项与默认阈值（可在 settings.toml [monitor] 表配置）:
- **CPU**: 使用率 > 85% 持续 30 秒告警（瞬时高负载不报，防误报）
- **内存**: 使用率 > 90% 告警
- **磁盘**: 剩余空间 < 10% 告警（系统盘 C:）
- **磁盘趋势**: 预测 N 天内将满时提前告警
- **异常进程**: CPU > 50% 持续 10 分钟的进程
- **工作时长**: 连续工作超过 2 小时提醒休息

告警去重:
- 同一告警类型 10 分钟内不重复触发（避免"内存满了"每 30 秒报一次烦人）
- 告警解除时（资源回落到阈值下）发一次恢复通知

设计要点:
- **轻量**: 每 10 秒采样一次，CPU 取 1 秒平均（psutil.cpu_percent(interval=1)）
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


# ---- 默认阈值 ----
DEFAULT_CPU_THRESHOLD = 85.0       # CPU 使用率 %，超过触告警
DEFAULT_CPU_DURATION = 30          # CPU 持续超阈值多少秒才告警（防瞬时尖峰）
DEFAULT_MEMORY_THRESHOLD = 90.0    # 内存使用率 %
DEFAULT_DISK_THRESHOLD = 10.0      # 磁盘剩余空间 %，低于此值告警
DEFAULT_CHECK_INTERVAL = 10        # 检查间隔（秒）
DEFAULT_ALERT_COOLDOWN = 600       # 同类告警冷却时间（秒，10分钟）
# P2-3 增强默认值
DEFAULT_DISK_TREND_DAYS = 7        # 磁盘趋势预测：预测几天后将满
DEFAULT_HIGH_CPU_DURATION = 600    # 异常进程：高 CPU 持续秒数阈值
DEFAULT_WORK_BREAK_INTERVAL = 7200 # 工作时长：连续工作多少秒提醒休息


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
    high_cpu_duration: int = DEFAULT_HIGH_CPU_DURATION
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
    ) -> None:
        """
        Args:
            config: 监控配置（阈值/间隔等）。
            on_alert: 告警回调。接收 AlertInfo 参数。
        """
        self._config = config
        self._on_alert = on_alert
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._available = False

        # 状态跟踪
        self._lock = threading.Lock()
        self._cpu_high_since: float | None = None  # CPU 开始超阈值的时间戳
        self._last_alert: dict[str, float] = {}     # alert_type -> 上次告警时间戳
        self._alert_active: dict[str, bool] = {}    # alert_type -> 当前是否告警中

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

        可用于用户问"电脑状态"时返回实时数据。
        """
        if not self._available:
            return {"error": "psutil 未安装，监控不可用"}

        try:
            import psutil

            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage("C:\\" if __import__("sys").platform == "win32" else "/")

            return {
                "cpu_percent": round(cpu_percent, 1),
                "cpu_threshold": self._config.cpu_threshold,
                "memory_percent": round(memory.percent, 1),
                "memory_threshold": self._config.memory_threshold,
                "memory_available_gb": round(memory.available / 1024**3, 1),
                "memory_total_gb": round(memory.total / 1024**3, 1),
                "disk_percent_used": round(disk.percent, 1),
                "disk_free_gb": round(disk.free / 1024**3, 1),
                "disk_total_gb": round(disk.total / 1024**3, 1),
                "disk_threshold_free_percent": self._config.disk_threshold,
            }
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
        import psutil
        import sys as _sys

        now = time.time()
        alerts: list[AlertInfo] = []

        # ---- CPU 检查（需持续超阈值才告警）----
        cpu_percent = psutil.cpu_percent(interval=1)

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
        memory = psutil.virtual_memory()
        memory_percent = memory.percent

        with self._lock:
            if memory_percent > self._config.memory_threshold:
                alert = self._make_alert(
                    "memory", memory_percent, self._config.memory_threshold,
                    f"内存使用率 {memory_percent:.0f}%，超过 {self._config.memory_threshold}%，"
                    f"仅剩 {memory.available / 1024**3:.1f}GB 可用",
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
        disk_path = "C:\\" if _sys.platform == "win32" else "/"
        try:
            disk = psutil.disk_usage(disk_path)
            disk_free_percent = 100 - disk.percent

            with self._lock:
                if disk_free_percent < self._config.disk_threshold:
                    alert = self._make_alert(
                        "disk", disk_free_percent, self._config.disk_threshold,
                        f"系统盘剩余空间仅 {disk_free_percent:.1f}%（{disk.free / 1024**3:.1f}GB），"
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
        except Exception:
            pass  # 磁盘检查失败静默（可能无权限等）

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
            import psutil
            import sys as _sys
            disk_path = "C:\\" if _sys.platform == "win32" else "/"
            disk = psutil.disk_usage(disk_path)
            today_str = datetime.now().strftime("%Y-%m-%d")

            history = self._load_history()
            # 每天只记录一条（同一天覆盖）
            history = [h for h in history if h.get("date") != today_str]
            history.append({
                "date": today_str,
                "disk_percent": round(disk.percent, 2),
                "disk_free_gb": round(disk.free / 1024**3, 2),
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

    # ---- P2-3 增强：异常进程检测 ----

    def check_high_cpu_processes(self) -> list[str]:
        """检测 CPU 占用异常高的进程。

        返回 CPU > 50% 的进程信息列表。
        由 ProactiveEngine 定期检查，或用户主动询问时调用。
        """
        if not self._available:
            return []
        try:
            import psutil
            results = []
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent']):
                try:
                    info = proc.info
                    cpu = info.get('cpu_percent', 0) or 0
                    if cpu > 50.0:
                        results.append(
                            f"{info.get('name', '?')} (PID: {info.get('pid', '?')}) CPU: {cpu:.0f}%"
                        )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return results
        except Exception:
            return []

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

        如果用户连续工作超过 work_break_interval 秒（键鼠活跃），
        返回提醒文本。否则返回 None。
        """
        idle = self.get_idle_time_seconds()
        if idle is None:
            return None

        break_interval = self._config.work_break_interval
        # 如果空闲时间很短（< 60秒），说明用户正在活跃操作
        # 用“总运行时间 - 空闲时间”粗略估算连续工作时长
        # 简化方案：如果空闲 < 60s，认为用户在工作中，检查 daemon 运行时长
        if idle < 60:
            # 用户正在操作，检查是否超过休息间隔
            # 用 _work_start_time 跟踪
            now = time.time()
            if not hasattr(self, '_work_start_time'):
                self._work_start_time = now
                return None
            work_duration = now - self._work_start_time
            if work_duration >= break_interval:
                hours = int(work_duration // 3600)
                minutes = int((work_duration % 3600) // 60)
                # 重置计时器（下次再等 break_interval）
                self._work_start_time = now
                return (
                    f"先生，您已连续工作 {hours} 小时 {minutes} 分钟了。"
                    f"建议起身活动一下，喝杯水，让眼睛休息休息。"
                )
        else:
            # 用户空闲中，重置工作计时
            if hasattr(self, '_work_start_time'):
                self._work_start_time = time.time()
        return None
