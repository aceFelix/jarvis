"""右栏系统指标采集：CPU / 内存 / 磁盘三件套（一期口径）。

**采样已收口**：本模块不再自己调 ``psutil``，一律走
:mod:`agent.core.system_sampler` 的进程级共享采样器——右栏画图与
:class:`~agent.core.daemon.monitor.SystemMonitor` 的阈值告警读同一份快照。
历史上这里与 monitor 各采一次，``psutil.cpu_percent(interval=None)`` 的
「距上次调用」语义会让两边互相切碎时间窗，右栏数字跟着抖。

后台线程周期采集（默认 2 秒），把最新指标推入事件队列，前端轮询后渲染到右栏。
采集失败（psutil 缺失/平台限制）时降级为零值，不阻塞窗口。

@author aceFelix
"""

from __future__ import annotations

import queue
import threading
from typing import Any

from agent.core.system_sampler import default_sampler

# 采集间隔（秒）：2 秒足够趋势感知，又不至于频繁唤醒
COLLECT_INTERVAL = 2.0


def collect_metrics() -> dict[str, Any]:
    """采集一次系统指标快照。

    返回结构（前端直接消费）::

        {
            "cpu": 百分比(0-100),
            "memory": {"percent": 百分比, "used_gb": 已用, "total_gb": 总量},
            "disk": {"percent": 百分比, "used_gb": 已用, "total_gb": 总量},
        }

    采样细节（首次 0.1 秒预热、1 秒节流、磁盘 psutil 优先 shutil 兜底）全在
    :class:`~agent.core.system_sampler.SystemSampler` 里，本函数只做格式转换。

    @author aceFelix
    """
    return default_sampler().snapshot().to_metrics_payload()


class MetricsCollector:
    """系统指标采集线程：周期采集并推事件到前端事件队列。

    事件格式：``{"type": "metrics", "payload": <collect_metrics() 结果>}``

    @author aceFelix
    """

    def __init__(self, event_queue: queue.Queue[dict[str, Any]], interval: float = COLLECT_INTERVAL) -> None:
        self._event_queue = event_queue
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动采集线程（幂等）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        # 预热：共享采样器首次采样要 0.1 秒阻塞拿真实 CPU 值，提前在这里付掉，
        # 免得首帧事件是 0%。采样器自己也有已预热判断，重复调无副作用。
        try:
            default_sampler().snapshot()
        except Exception:
            pass
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="workbench-metrics", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止采集线程。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _loop(self) -> None:
        """采集主循环：每间隔推一次 metrics 事件。"""
        while not self._stop.wait(self._interval):
            try:
                self._event_queue.put_nowait({"type": "metrics", "payload": collect_metrics()})
            except Exception:
                pass
