"""共享系统采样器测试（agent/core/system_sampler.py）。

覆盖的重心是**去重本身**：右栏画图与阈值告警必须读同一份快照，而不是各采一遍。
其余用例覆盖快照形状、节流/强制刷新、CPU 预热、psutil 缺失与采样异常的降级路径。

替身用 ``monkeypatch.setitem(sys.modules, "psutil", fake)`` 安装——随用例自动还原，
不会像模块级直接赋值那样把假 psutil 泄漏给同进程的其他测试文件。

@author aceFelix
"""

from __future__ import annotations

import sys
import types

import pytest

from agent.core.system_sampler import (
    CPU_WARMUP_INTERVAL_S,
    DEFAULT_MIN_INTERVAL_S,
    SystemSampler,
    SystemSnapshot,
    default_sampler,
    reset_default_sampler,
    system_disk_root,
)


@pytest.fixture(autouse=True)
def _clean_default():
    """每例前后重置进程级共享采样器。"""
    reset_default_sampler()
    yield
    reset_default_sampler()


def _install_psutil(
    monkeypatch,
    *,
    cpu=42.34,
    mem=None,
    disk=None,
    cpu_exc=None,
    mem_exc=None,
    disk_exc=None,
):
    """装一个假 psutil，返回 (module, calls)。

    ``calls["cpu"]`` 记录每次 ``cpu_percent`` 收到的 ``interval``——
    预热与节流都靠它断言。
    """
    mod = types.ModuleType("psutil")
    calls = {"cpu": []}

    def _cpu_percent(interval=None):
        calls["cpu"].append(interval)
        if cpu_exc is not None:
            raise cpu_exc
        return cpu

    def _virtual_memory():
        if mem_exc is not None:
            raise mem_exc
        return mem if mem is not None else types.SimpleNamespace(
            percent=61.5, used=5 * 1024 ** 3, total=16 * 1024 ** 3, available=8 * 1024 ** 3
        )

    def _disk_usage(path):
        if disk_exc is not None:
            raise disk_exc
        return disk if disk is not None else types.SimpleNamespace(
            percent=55.0, used=275 * 1024 ** 3, free=225 * 1024 ** 3, total=500 * 1024 ** 3
        )

    mod.cpu_percent = _cpu_percent
    mod.virtual_memory = _virtual_memory
    mod.disk_usage = _disk_usage
    monkeypatch.setitem(sys.modules, "psutil", mod)
    return mod, calls


# ---------------------------------------------------------------------------
# 快照数据类
# ---------------------------------------------------------------------------


def test_system_disk_root_is_absolute():
    """系统盘根是绝对路径锚点（Windows 上即 C:\\）。"""
    root = system_disk_root()
    assert root
    assert root == system_disk_root()  # 稳定


def test_metrics_payload_shape_matches_frontend_contract():
    """右栏格式：cpu + memory/disk 各三键，与历史版本一致。"""
    snap = SystemSnapshot(
        cpu_percent=37.44,
        memory_percent=68.06,
        memory_used_gb=5.312,
        memory_total_gb=7.81,
        disk_percent_used=85.44,
        disk_used_gb=85.4,
        disk_total_gb=100.0,
    )
    payload = snap.to_metrics_payload()
    assert set(payload) == {"cpu", "memory", "disk"}
    assert payload["cpu"] == 37.4
    assert set(payload["memory"]) == {"percent", "used_gb", "total_gb"}
    assert payload["memory"]["percent"] == 68.1
    assert set(payload["disk"]) == {"percent", "used_gb", "total_gb"}
    assert payload["disk"]["percent"] == 85.4


def test_status_dict_keeps_monitor_fields_and_omits_used():
    """monitor 兼容格式：不带 memory_used_gb（那一栏没有 used 口径）。"""
    snap = SystemSnapshot(
        cpu_percent=12.0,
        memory_percent=30.0,
        memory_used_gb=5.0,
        memory_available_gb=8.0,
        memory_total_gb=16.0,
        disk_percent_used=55.0,
        disk_free_gb=225.0,
        disk_total_gb=500.0,
    )
    status = snap.to_status_dict()
    assert "memory_used_gb" not in status
    assert status["cpu_percent"] == 12.0
    assert status["memory_available_gb"] == 8.0
    assert status["disk_free_gb"] == 225.0
    # 阈值是 MonitorConfig 的事，快照不掺和
    assert "cpu_threshold" not in status


def test_snapshot_age_and_freshness():
    """age_s / is_fresh 按显式时基判断，不依赖真实时钟。"""
    snap = SystemSnapshot(sampled_at=1000.0)
    assert snap.age_s(now=1000.5) == 0.5
    assert snap.age_s(now=900.0) == 0.0       # 时钟回拨也不给负值
    assert snap.is_fresh(1.0, now=1000.5) is True
    assert snap.is_fresh(1.0, now=1002.0) is False


# ---------------------------------------------------------------------------
# 节流与共享（去重的核心）
# ---------------------------------------------------------------------------


def test_sampler_reuses_cache_within_interval(monkeypatch):
    """节流窗口内第二次请求直接吃缓存，不重复调 psutil。"""
    _mod, calls = _install_psutil(monkeypatch)
    now = {"t": 1000.0}
    sampler = SystemSampler(min_interval_s=1.0, clock=lambda: now["t"])

    first = sampler.snapshot()
    now["t"] += 0.2
    second = sampler.snapshot()

    assert first is second                      # 同一个对象，不是重新构造
    assert len(calls["cpu"]) == 1               # 只真采了一次


def test_sampler_resamples_after_interval(monkeypatch):
    """过了节流窗口就真采一次。"""
    _mod, calls = _install_psutil(monkeypatch)
    now = {"t": 1000.0}
    sampler = SystemSampler(min_interval_s=1.0, clock=lambda: now["t"])

    sampler.snapshot()
    now["t"] += 2.0
    second = sampler.snapshot()

    assert len(calls["cpu"]) == 2
    assert second.sampled_at == 1002.0


def test_sampler_max_age_zero_forces_resample(monkeypatch):
    """``max_age_s=0`` 强制现采（日记录这类一天只写一次的场景要用）。"""
    _mod, calls = _install_psutil(monkeypatch)
    now = {"t": 1000.0}
    sampler = SystemSampler(min_interval_s=60.0, clock=lambda: now["t"])

    sampler.snapshot()
    sampler.snapshot(max_age_s=0)

    assert len(calls["cpu"]) == 2


def test_sampler_invalidate_drops_cache(monkeypatch):
    """invalidate 之后下一次一定真采。"""
    _mod, calls = _install_psutil(monkeypatch)
    now = {"t": 1000.0}
    sampler = SystemSampler(min_interval_s=60.0, clock=lambda: now["t"])

    sampler.snapshot()
    sampler.invalidate()
    sampler.snapshot()

    assert len(calls["cpu"]) == 2


def test_metrics_and_monitor_share_one_snapshot(monkeypatch):
    """**本次重构的目的**：右栏采集与阈值告警读同一份快照，只真采一次。"""
    _mod, calls = _install_psutil(monkeypatch, cpu=42.34)

    from agent.core.daemon.monitor import MonitorConfig, SystemMonitor
    from agent.ui.workbench.metrics import collect_metrics

    payload = collect_metrics()
    monitor = SystemMonitor(MonitorConfig(), on_alert=lambda _a: None)
    status = monitor.get_status()

    assert len(calls["cpu"]) == 1               # 两处消费者，一次真采
    assert payload["cpu"] == status["cpu_percent"] == 42.3


def test_default_sampler_is_process_wide_singleton():
    """默认采样器是进程级共享实例，reset 后换成新的。"""
    a = default_sampler()
    assert a is default_sampler()

    replacement = SystemSampler(min_interval_s=0)
    reset_default_sampler(replacement)
    assert default_sampler() is replacement


def test_default_sampler_uses_one_second_throttle():
    """生产默认节流 1 秒：右栏 2 秒节拍每次都真采，告警侧紧跟时吃缓存。"""
    assert DEFAULT_MIN_INTERVAL_S == 1.0
    assert default_sampler().disk_root == system_disk_root()


# ---------------------------------------------------------------------------
# CPU 预热
# ---------------------------------------------------------------------------


def test_cpu_warmup_then_non_blocking(monkeypatch):
    """首次采样用 0.1 秒短阻塞拿真实值，之后一律非阻塞。"""
    _mod, calls = _install_psutil(monkeypatch)
    sampler = SystemSampler(min_interval_s=0)

    sampler.snapshot()
    sampler.snapshot()

    assert calls["cpu"][0] == CPU_WARMUP_INTERVAL_S
    assert calls["cpu"][1] is None


def test_warmup_only_once_per_sampler(monkeypatch):
    """预热只付一次，invalidate 不会让它重新阻塞。"""
    _mod, calls = _install_psutil(monkeypatch)
    sampler = SystemSampler(min_interval_s=0)

    sampler.snapshot()
    sampler.invalidate()
    sampler.snapshot()

    assert calls["cpu"] == [CPU_WARMUP_INTERVAL_S, None]


# ---------------------------------------------------------------------------
# 降级路径
# ---------------------------------------------------------------------------


def test_missing_psutil_still_reports_disk(monkeypatch):
    """psutil 缺失：CPU/内存标不可用，磁盘仍走 shutil 出数（右栏磁盘条不空）。"""
    monkeypatch.setitem(sys.modules, "psutil", None)  # 让 import psutil 直接失败

    snap = SystemSampler(min_interval_s=0).snapshot()

    assert snap.cpu_ok is False
    assert snap.memory_ok is False
    assert snap.disk_ok is True
    assert snap.disk_total_gb > 0
    assert "Error" in snap.error                 # ImportError 之类
    # 右栏格式仍然完整（不会整块消失）
    assert set(snap.to_metrics_payload()) == {"cpu", "memory", "disk"}


def test_cpu_failure_records_error_and_keeps_disk(monkeypatch):
    """CPU 采样抛错：标不可用 + 留原始异常类型；磁盘不受牵连。"""
    _install_psutil(monkeypatch, cpu_exc=RuntimeError("boom"))

    snap = SystemSampler(min_interval_s=0).snapshot()

    assert snap.cpu_ok is False
    assert snap.cpu_percent == 0.0
    assert "RuntimeError" in snap.error
    assert snap.memory_ok is True
    assert snap.disk_ok is True


def test_memory_failure_records_error(monkeypatch):
    """内存采样抛错：标不可用，CPU 照常。"""
    _install_psutil(monkeypatch, mem_exc=ValueError("nope"))

    snap = SystemSampler(min_interval_s=0).snapshot()

    assert snap.memory_ok is False
    assert "ValueError" in snap.error
    assert snap.cpu_ok is True


def test_disk_percent_only_without_total(monkeypatch):
    """只有 percent/free、没有 total 的盘对象也要能算（不强行要求 total）。"""
    _install_psutil(
        monkeypatch,
        disk=types.SimpleNamespace(percent=96.0, free=5 * 1024 ** 3),
    )

    snap = SystemSampler(min_interval_s=0).snapshot()

    assert snap.disk_ok is True
    assert snap.disk_percent_used == 96.0
    assert snap.disk_free_percent == pytest.approx(4.0)
    assert snap.disk_free_gb == pytest.approx(5.0)


def test_disk_falls_back_to_shutil_on_psutil_error(monkeypatch):
    """psutil 读盘失败时回落到标准库，仍然 disk_ok。"""
    _install_psutil(monkeypatch, disk_exc=PermissionError("denied"))

    snap = SystemSampler(min_interval_s=0).snapshot()

    assert snap.disk_ok is True
    assert snap.disk_total_gb > 0


def test_disk_free_percent_is_complement_of_used(monkeypatch):
    """剩余与已用互补——告警判的是剩余（方向与 CPU/内存相反）。"""
    _install_psutil(
        monkeypatch,
        disk=types.SimpleNamespace(percent=85.4, used=85 * 1024 ** 3,
                                   free=15 * 1024 ** 3, total=100 * 1024 ** 3),
    )

    snap = SystemSampler(min_interval_s=0).snapshot()

    assert snap.disk_percent_used == pytest.approx(85.4)
    assert snap.disk_free_percent == pytest.approx(14.6)
