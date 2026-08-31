"""右栏系统指标采集：CPU / 内存 / 磁盘三件套（一期口径）。

后台线程周期采集（默认 2 秒），把最新指标推入事件队列，
前端轮询后渲染到右栏。采集失败（psutil 缺失/平台限制）时
降级为全零，不阻塞窗口。

@author aceFelix
"""

from __future__ import annotations

import queue
import shutil
import threading
from pathlib import Path
from typing import Any

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

    @author aceFelix
    """
    result: dict[str, Any] = {"cpu": 0.0, "memory": {}, "disk": {}}
    # CPU：psutil 非阻塞百分比（依赖上次调用间隔，首帧返回 0）
    try:
        import psutil

        result["cpu"] = round(psutil.cpu_percent(interval=None), 1)
        mem = psutil.virtual_memory()
        result["memory"] = {
            "percent": round(mem.percent, 1),
            "used_gb": round(mem.used / 1024**3, 1),
            "total_gb": round(mem.total / 1024**3, 1),
        }
    except Exception:
        pass
    # 磁盘：用 shutil 标准库读系统盘（Windows 上即 C:\），psutil 不可用时也能出数
    try:
        root = Path.home().anchor or "/"
        usage = shutil.disk_usage(root)
        result["disk"] = {
            "percent": round(usage.used / usage.total * 100, 1) if usage.total else 0.0,
            "used_gb": round(usage.used / 1024**3, 1),
            "total_gb": round(usage.total / 1024**3, 1),
        }
    except Exception:
        pass
    return result


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
        # 预热 cpu_percent：首次调用返回 0.0，提前调一次让首帧就有真实值
        try:
            import psutil

            psutil.cpu_percent(interval=None)
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
