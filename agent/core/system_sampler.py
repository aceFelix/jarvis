"""系统资源采样 —— CPU / 内存 / 磁盘的**唯一采样点**。

## 为什么要有这个文件

同一个物理事实（CPU/内存/磁盘）原先由两处代码各读一遍：

- ``agent/ui/workbench/metrics.py`` —— 右栏三条进度条，2 秒一次、非阻塞
- ``agent/core/daemon/monitor.py`` —— 阈值告警，10/60 秒一次、``cpu_percent(interval=1)`` 阻塞

两套实现不只是代码重复，还**互相污染**：``psutil.cpu_percent(interval=None)`` 返的是
「距上次调用」的平均值，谁先调谁就把对方的时间窗切碎——右栏 2 秒窗口尤其容易被
monitor 的 1 秒阻塞采样截断，数字跟着抖。``source_system.py`` 的模块说明里写过
「不要起第二条并行采样线」，指的正是这件事；当时只约束了 ``SystemMonitor.start()``
那条线程，右栏这条线是既存事实，等于已经存在两条。

现在收敛成：**本模块持有 ``psutil`` 的唯一 CPU/内存调用点**，``SystemSnapshot`` 是不可变
快照，右栏画图与阈值告警都读它。``SystemMonitor`` 仍独占告警状态机（持续时长/冷却/
恢复通知），采样与判定彻底分层。

## 三个取舍

- **节流而不是每次真采**：``min_interval_s``（默认 1 秒）内的重复请求直接吃缓存。
  右栏 2 秒节拍 → 每次都真采（2 > 1，新鲜度不变）；感知源 60 秒节拍若紧跟一次右栏
  采样 → 吃缓存，最多旧 2 秒。这正是「一份数据两个消费者」想要的，而不是把面板
  刷新率降到告警节拍。
- **CPU 首次短阻塞 0.1 秒预热**：``cpu_percent(interval=None)`` 首次调用无前值会返回 0.0，
  右栏首帧就会显示 0%。用 100ms 阻塞拿真实值——比 1 秒的旧实现小一个量级，且只在
  进程内第一次采样时发生。
- **磁盘 psutil 优先、``shutil`` 兜底**：``shutil.disk_usage`` 是标准库，psutil 缺失时
  右栏磁盘条仍然有数（这是 ``metrics.py`` 原本就做对的地方，保留）；但优先走 psutil，
  因为 ``monitor`` 的告警阈值一直是按 ``psutil`` 的 ``percent`` 口径定的。

@author aceFelix
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

#: 同一窗口内的重复请求吃缓存，避免两处消费者把 CPU 采样时间窗切碎。
DEFAULT_MIN_INTERVAL_S = 1.0

#: CPU 首次采样的短阻塞时长（秒）。见模块说明第 2 条取舍。
CPU_WARMUP_INTERVAL_S = 0.1

_GB = 1024 ** 3


def system_disk_root() -> str:
    """系统盘根：Windows 上是 ``C:\\``，其余平台是 ``/``。

    用 ``Path.home().anchor`` 而不是硬编码盘符，跨平台口径统一。

    @author aceFelix
    """
    return Path.home().anchor or "/"


def _as_float(value: Any, default: float = 0.0) -> float:
    """把 psutil 字段安全转成 float。

    只接受真正的数值类型：``psutil`` 的字段在某些替身/平台下可能是对象而非数字，
    静默当 0 处理比让整个快照塌掉好。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


@dataclass(frozen=True)
class SystemSnapshot:
    """一次系统资源采样的冻结结果。

    字段全部带默认值，采集失败时对应 ``*_ok`` 为 ``False`` 而不是抛异常——
    右栏宁可显示 0 也不该让面板整块消失。

    三个消费面各自取需要的字段，**阈值不在这里**（那是 ``MonitorConfig`` 的事）：

    - :meth:`to_metrics_payload` —— 右栏三条进度条的前端格式
    - :meth:`to_status_dict` —— ``SystemMonitor.get_status()`` 的兼容格式
    - 直接读 ``disk_free_percent`` —— 告警判定用（注意是**剩余**，方向与 CPU/内存相反）

    @author aceFelix
    """

    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_gb: float = 0.0
    memory_available_gb: float = 0.0
    memory_total_gb: float = 0.0
    disk_percent_used: float = 0.0
    disk_free_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_free_gb: float = 0.0
    disk_total_gb: float = 0.0
    disk_root: str = ""
    sampled_at: float = 0.0
    cpu_ok: bool = True
    memory_ok: bool = True
    disk_ok: bool = True
    #: 第一块采集失败时的原始异常文本（``类型: 描述``）。
    #: 给 ``SystemMonitor.get_status()`` 的 error 契约用——上层要能看出是
    #: 哪一类错误挂了，而不是只收到一句“采样异常”。
    error: str = ""

    def age_s(self, now: float | None = None) -> float:
        """距本次采样过了多少秒。"""
        return max(0.0, (time.time() if now is None else now) - self.sampled_at)

    def is_fresh(self, max_age_s: float, now: float | None = None) -> bool:
        """是否还在 ``max_age_s`` 秒内。"""
        return self.age_s(now) < float(max_age_s)

    def to_metrics_payload(self) -> dict[str, Any]:
        """右栏 metrics 事件的前端格式（键集合与历史版本完全一致）。

        消费方：``agent/ui/workbench/metrics.py`` → serve ``metrics`` 事件 / RPC。
        """
        return {
            "cpu": round(self.cpu_percent, 1),
            "memory": {
                "percent": round(self.memory_percent, 1),
                "used_gb": round(self.memory_used_gb, 1),
                "total_gb": round(self.memory_total_gb, 1),
            },
            "disk": {
                "percent": round(self.disk_percent_used, 1),
                "used_gb": round(self.disk_used_gb, 1),
                "total_gb": round(self.disk_total_gb, 1),
            },
        }

    def to_status_dict(self) -> dict[str, Any]:
        """``SystemMonitor.get_status()`` 的兼容格式（阈值由调用方补）。

        刻意**不含** ``memory_used_gb``：那一栏的 ``used`` 与 ``available`` 在 Windows 上
        不严格互补，``monitor`` 从没有这个字段，别在这里新增第二个口径。
        """
        return {
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_percent": round(self.memory_percent, 1),
            "memory_available_gb": round(self.memory_available_gb, 1),
            "memory_total_gb": round(self.memory_total_gb, 1),
            "disk_percent_used": round(self.disk_percent_used, 1),
            "disk_free_gb": round(self.disk_free_gb, 1),
            "disk_total_gb": round(self.disk_total_gb, 1),
        }


class SystemSampler:
    """带节流的系统资源采样器 —— ``psutil`` 的唯一 CPU/内存调用点。

    线程安全：内部一把锁串行化采样，多个消费者并发取快照不会重复采样。

    @author aceFelix
    """

    def __init__(
        self,
        *,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        disk_root: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """
        Args:
            min_interval_s: 缓存有效期（秒）；``<= 0`` 表示不节流、每次都真采。
            disk_root: 要统计的盘；``None`` 用 :func:`system_disk_root`。
            clock: 时基（测试可注入）。
        """
        self._min_interval_s = float(min_interval_s)
        self._disk_root = disk_root or system_disk_root()
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: SystemSnapshot | None = None
        self._cpu_warmed = False

    @property
    def disk_root(self) -> str:
        """本采样器统计的盘。"""
        return self._disk_root

    def snapshot(self, max_age_s: float | None = None) -> SystemSnapshot:
        """取一份快照；缓存还在有效期内就直接复用。

        Args:
            max_age_s: 本次要求的最大陈旧度（秒）。``None`` 用构造时的
                ``min_interval_s``；传 ``0`` 强制重新采样。
        """
        limit = self._min_interval_s if max_age_s is None else float(max_age_s)
        with self._lock:
            now = self._clock()
            cached = self._cached
            if cached is not None and limit > 0 and (now - cached.sampled_at) < limit:
                return cached
            self._cached = self._sample(now)
            return self._cached

    def invalidate(self) -> None:
        """丢弃缓存（测试或配置变更后强制下一次真采）。"""
        with self._lock:
            self._cached = None

    # ---- 内部 ----

    def _cpu_interval(self) -> float | None:
        """首次采样用短阻塞预热，之后非阻塞（依赖上次调用至今的时间窗）。"""
        if not self._cpu_warmed:
            self._cpu_warmed = True
            return CPU_WARMUP_INTERVAL_S
        return None

    def _sample(self, now: float) -> SystemSnapshot:
        """真正的采样。任何一块失败只把对应 ``*_ok`` 置 False，不抛。"""
        cpu_percent = 0.0
        cpu_ok = False
        memory_percent = 0.0
        memory_used_gb = 0.0
        memory_available_gb = 0.0
        memory_total_gb = 0.0
        memory_ok = False
        disk_percent_used = 0.0
        disk_free_gb = 0.0
        disk_used_gb = 0.0
        disk_total_gb = 0.0
        disk_ok = False
        first_error = ""

        try:
            import psutil
        except Exception as e:  # noqa: BLE001 - psutil 缺失只是降级，不该冒到面板
            psutil = None  # type: ignore[assignment]
            first_error = f"{type(e).__name__}: {e}"

        if psutil is not None:
            try:
                cpu_percent = _as_float(
                    psutil.cpu_percent(interval=self._cpu_interval())
                )
                cpu_ok = True
            except Exception as e:  # noqa: BLE001 - 平台差异/采样失败都降级
                cpu_percent = 0.0
                first_error = first_error or f"{type(e).__name__}: {e}"

            try:
                mem = psutil.virtual_memory()
                memory_percent = _as_float(getattr(mem, "percent", 0))
                memory_total_gb = _as_float(getattr(mem, "total", 0)) / _GB
                memory_available_gb = _as_float(getattr(mem, "available", 0)) / _GB
                # used 与 available 在 Windows 上不严格互补，右栏「已用」用 psutil 自己的
                # used 口径，避免面板数字与历史版本对不上。
                used = getattr(mem, "used", None)
                if used is None:
                    memory_used_gb = max(0.0, memory_total_gb - memory_available_gb)
                else:
                    memory_used_gb = _as_float(used) / _GB
                memory_ok = True
            except Exception as e:  # noqa: BLE001
                first_error = first_error or f"{type(e).__name__}: {e}"

            disk_ok, disk_percent_used, disk_free_gb, disk_used_gb, disk_total_gb = (
                self._disk_from_psutil(psutil)
            )

        if not disk_ok:
            disk_ok, disk_percent_used, disk_free_gb, disk_used_gb, disk_total_gb = (
                self._disk_from_shutil()
            )

        return SystemSnapshot(
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            memory_used_gb=memory_used_gb,
            memory_available_gb=memory_available_gb,
            memory_total_gb=memory_total_gb,
            disk_percent_used=disk_percent_used,
            disk_free_percent=max(0.0, 100.0 - disk_percent_used) if disk_ok else 0.0,
            disk_used_gb=disk_used_gb,
            disk_free_gb=disk_free_gb,
            disk_total_gb=disk_total_gb,
            disk_root=self._disk_root,
            sampled_at=now,
            cpu_ok=cpu_ok,
            memory_ok=memory_ok,
            disk_ok=disk_ok,
            error=first_error,
        )

    def _disk_from_psutil(self, psutil: Any) -> tuple[bool, float, float, float, float]:
        """磁盘：psutil 优先（告警阈值一直按它的 ``percent`` 口径定）。

        ``percent`` 可用就不需要 ``total``——有些平台的替身/简化对象只给
        ``percent`` / ``free``。只有从 ``used/total`` 反算百分比时才必须拿到 ``total``。
        """
        try:
            usage = psutil.disk_usage(self._disk_root)
            free = _as_float(getattr(usage, "free", 0))
            used = _as_float(getattr(usage, "used", 0))
            total = _as_float(getattr(usage, "total", 0))
            percent = getattr(usage, "percent", None)
            if isinstance(percent, (int, float)) and not isinstance(percent, bool):
                percent_used = float(percent)
            elif total > 0:
                percent_used = used / total * 100
            else:
                return (False, 0.0, 0.0, 0.0, 0.0)
            return (True, percent_used, free / _GB, used / _GB, total / _GB)
        except Exception:  # noqa: BLE001 - 无权限/盘不存在都退回标准库
            return (False, 0.0, 0.0, 0.0, 0.0)

    def _disk_from_shutil(self) -> tuple[bool, float, float, float, float]:
        """磁盘兜底：标准库，psutil 缺失时右栏磁盘条仍有数。"""
        try:
            usage = shutil.disk_usage(self._disk_root)
            if usage.total <= 0:
                return (False, 0.0, 0.0, 0.0, 0.0)
            return (
                True,
                usage.used / usage.total * 100,
                usage.free / _GB,
                usage.used / _GB,
                usage.total / _GB,
            )
        except Exception:  # noqa: BLE001
            return (False, 0.0, 0.0, 0.0, 0.0)


# ---- 进程级共享实例 ----

_default_sampler: SystemSampler | None = None
_default_lock = threading.Lock()


def default_sampler() -> SystemSampler:
    """取进程级共享采样器（懒构造、线程安全）。

    右栏 ``MetricsCollector`` 与 ``SystemMonitor`` 都从这里取，因此同一事实只采一次。

    @author aceFelix
    """
    global _default_sampler
    if _default_sampler is None:
        with _default_lock:
            if _default_sampler is None:
                _default_sampler = SystemSampler()
    return _default_sampler


def reset_default_sampler(sampler: SystemSampler | None = None) -> None:
    """重置共享采样器（**测试专用**：避免上一例的缓存/替身泄漏到下一例）。

    Args:
        sampler: 要装上的实例；``None`` 表示清空，下次 :func:`default_sampler`
            会重新构造。
    """
    global _default_sampler
    with _default_lock:
        _default_sampler = sampler
