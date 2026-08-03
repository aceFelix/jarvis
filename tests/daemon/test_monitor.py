"""系统资源监控（agent.core.daemon.monitor）单元测试。

覆盖:
- MonitorConfig / AlertInfo 数据模型
- SystemMonitor 生命周期：start / stop / available
- _do_check 告警逻辑：CPU 持续阈值 / 内存 / 磁盘 / 冷却 / 恢复通知
- get_status 状态查询与容错
- 磁盘趋势预测 predict_disk_full（线性回归）
- 异常进程检测 / 空闲时间 / 工作时长提醒

所有系统调用（psutil、ctypes、time、sys.platform、线程）均通过
monkeypatch / 替身隔离，不依赖真实硬件环境。

@author aceFelix
"""

from __future__ import annotations

import json
import sys

import psutil
import pytest
from unittest.mock import MagicMock

from agent.core.daemon.monitor import (
    AlertInfo,
    DEFAULT_CPU_THRESHOLD,
    MonitorConfig,
    SystemMonitor,
)


class _FakeThread:
    """不真正启动线程的替身。"""

    def __init__(self, target=None, daemon=None, name=None):
        self.target = target
        self.name = name
        self.started = False

    def start(self):
        self.started = True

    def join(self, timeout=None):
        pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    """重定向监控历史文件到 tmp + 替身线程。"""
    import agent.core.daemon.monitor as mod

    # _history_file 是实例方法：打补丁到类上，返回 tmp 路径
    monkeypatch.setattr(
        mod.SystemMonitor, "_history_file",
        lambda self: tmp_path / "monitor_history.json",
    )
    monkeypatch.setattr(mod.threading, "Thread", _FakeThread)
    return mod


@pytest.fixture
def make_monitor(env):
    """构造 SystemMonitor 工厂。"""

    def _make(config=None, on_alert=None):
        alerts = []
        if on_alert is None:
            on_alert = alerts.append
        return SystemMonitor(config=config or MonitorConfig(), on_alert=on_alert), alerts

    return _make


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


class TestModels:
    """MonitorConfig / AlertInfo。"""

    def test_config_defaults(self):
        cfg = MonitorConfig()
        assert cfg.cpu_threshold == DEFAULT_CPU_THRESHOLD
        assert cfg.cpu_duration == 30
        assert cfg.memory_threshold == 90.0
        assert cfg.disk_threshold == 10.0
        assert cfg.check_interval == 10
        assert cfg.alert_cooldown == 600
        assert cfg.enabled is True
        assert cfg.disk_trend_days == 7
        assert cfg.high_cpu_duration == 600
        assert cfg.work_break_interval == 7200

    def test_alert_info_default_timestamp(self):
        alert = AlertInfo(alert_type="cpu", level="warning", message="x", value=1.0, threshold=2.0)
        assert alert.timestamp is not None

    def test_alert_info_keeps_timestamp(self):
        from datetime import datetime
        ts = datetime(2026, 1, 1, 10, 0, 0)
        alert = AlertInfo(
            alert_type="cpu", level="warning", message="x",
            value=1.0, threshold=2.0, timestamp=ts,
        )
        assert alert.timestamp is ts


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


class TestLifecycle:
    """available / start / stop / check_now。"""

    def test_available_true_when_psutil_installed(self, make_monitor):
        monitor, _ = make_monitor()
        assert monitor.available is True

    def test_available_false_without_psutil(self, env, monkeypatch):
        monkeypatch.setitem(sys.modules, "psutil", None)
        monitor = SystemMonitor(config=MonitorConfig(), on_alert=lambda a: None)
        assert monitor.available is False

    def test_start_returns_false_when_unavailable(self, env, monkeypatch):
        monkeypatch.setitem(sys.modules, "psutil", None)
        monitor = SystemMonitor(config=MonitorConfig(), on_alert=lambda a: None)
        assert monitor.start() is False

    def test_start_success_and_stop(self, make_monitor):
        monitor, _ = make_monitor()
        assert monitor.start() is True
        assert monitor._started is True
        assert monitor._thread is not None
        monitor.stop()
        assert monitor._started is False
        assert monitor._thread is None

    def test_start_idempotent(self, make_monitor):
        monitor, _ = make_monitor()
        assert monitor.start() is True
        assert monitor.start() is True  # 已启动返回 True，不重启线程

    def test_start_disabled_config(self, make_monitor):
        monitor, _ = make_monitor(config=MonitorConfig(enabled=False))
        assert monitor.start() is False

    def test_stop_without_start(self, make_monitor):
        monitor, _ = make_monitor()
        monitor.stop()  # 不应抛异常

    def test_check_now_not_available(self, env, monkeypatch):
        monkeypatch.setitem(sys.modules, "psutil", None)
        monitor = SystemMonitor(config=MonitorConfig(), on_alert=lambda a: None)
        assert monitor.check_now() == []

    def test_run_swallows_check_exceptions(self, env, make_monitor):
        monitor, _ = make_monitor()
        monitor._stop_event.set()
        monitor._do_check = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        monitor._run()  # 异常被吞


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    """get_status 状态查询。"""

    def test_status_success(self, env, make_monitor, monkeypatch):
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=0: 42.34)
        monkeypatch.setattr(
            psutil, "virtual_memory",
            lambda: MagicMock(percent=61.5, available=8 * 1024**3, total=16 * 1024**3),
        )
        monkeypatch.setattr(
            psutil, "disk_usage",
            lambda p: MagicMock(percent=55.0, free=100 * 1024**3, total=500 * 1024**3),
        )
        monitor, _ = make_monitor()
        status = monitor.get_status()
        assert status["cpu_percent"] == 42.3
        assert status["memory_percent"] == 61.5
        assert status["memory_available_gb"] == 8.0
        assert status["disk_free_gb"] == 100.0
        assert status["disk_threshold_free_percent"] == 10.0

    def test_status_error_when_unavailable(self, env, monkeypatch):
        monkeypatch.setitem(sys.modules, "psutil", None)
        monitor = SystemMonitor(config=MonitorConfig(), on_alert=lambda a: None)
        assert "error" in monitor.get_status()

    def test_status_error_on_exception(self, env, make_monitor, monkeypatch):
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=0: (_ for _ in ()).throw(RuntimeError("oops")))
        monitor, _ = make_monitor()
        status = monitor.get_status()
        assert "error" in status
        assert "RuntimeError" in status["error"]


# ---------------------------------------------------------------------------
# _do_check 告警逻辑
# ---------------------------------------------------------------------------


class TestDoCheck:
    """_do_check 的 CPU/内存/磁盘告警与恢复。"""

    @pytest.fixture
    def quiet(self, monkeypatch):
        """默认把 CPU/内存/磁盘设为安全值。"""
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=0: 10.0)
        monkeypatch.setattr(psutil, "virtual_memory", lambda: MagicMock(percent=40.0, available=1))
        monkeypatch.setattr(psutil, "disk_usage", lambda p: MagicMock(percent=50.0, free=1))

    def test_cpu_alert_after_duration(self, env, make_monitor, monkeypatch):
        """CPU 持续超阈值达到持续时间才告警。"""
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=0: 95.0)
        monkeypatch.setattr(psutil, "virtual_memory", lambda: MagicMock(percent=40.0, available=1))
        monkeypatch.setattr(psutil, "disk_usage", lambda p: MagicMock(percent=50.0, free=1))
        now = {"t": 1000.0}
        monkeypatch.setattr(env.time, "time", lambda: now["t"])

        monitor, alerts = make_monitor()
        # 第一次：超阈值但持续时间不足 30s → 不告警
        assert monitor._do_check() == []
        # 35 秒后仍超阈值 → 告警
        now["t"] = 1035.0
        result = monitor._do_check()
        assert len(result) == 1
        assert result[0].alert_type == "cpu"
        assert result[0].level == "warning"  # 95 < 85*1.2=102
        assert "持续 35 秒" in result[0].message

    def test_cpu_critical_level(self, env, make_monitor, monkeypatch):
        """值超过阈值 1.2 倍 → critical。"""
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=0: 110.0)
        monkeypatch.setattr(psutil, "virtual_memory", lambda: MagicMock(percent=40.0, available=1))
        monkeypatch.setattr(psutil, "disk_usage", lambda p: MagicMock(percent=50.0, free=1))
        now = {"t": 1000.0}
        monkeypatch.setattr(env.time, "time", lambda: now["t"])
        monitor, _ = make_monitor()
        monitor._cpu_high_since = 950.0  # 已持续 50 秒
        result = monitor._do_check()
        assert result[0].alert_type == "cpu"
        assert result[0].level == "critical"

    def test_cpu_cooldown(self, env, make_monitor, monkeypatch):
        """冷却期内同类告警不重复触发。"""
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=0: 110.0)
        monkeypatch.setattr(psutil, "virtual_memory", lambda: MagicMock(percent=40.0, available=1))
        monkeypatch.setattr(psutil, "disk_usage", lambda p: MagicMock(percent=50.0, free=1))
        now = {"t": 1000.0}
        monkeypatch.setattr(env.time, "time", lambda: now["t"])
        monitor, _ = make_monitor()
        monitor._cpu_high_since = 900.0
        assert len(monitor._do_check()) == 1
        # 10 秒后再查（仍在 600s 冷却内）→ 无新告警
        now["t"] = 1010.0
        assert monitor._do_check() == []

    def test_cpu_recovery_notification(self, env, make_monitor, monkeypatch):
        """CPU 回落时发恢复通知。"""
        monkeypatch.setattr(psutil, "virtual_memory", lambda: MagicMock(percent=40.0, available=1))
        monkeypatch.setattr(psutil, "disk_usage", lambda p: MagicMock(percent=50.0, free=1))
        now = {"t": 1000.0}
        monkeypatch.setattr(env.time, "time", lambda: now["t"])
        monitor, _ = make_monitor()
        # 先制造一个 CPU 告警中的状态
        monitor._alert_active["cpu"] = True
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=0: 10.0)
        result = monitor._do_check()
        assert len(result) == 1
        assert result[0].level == "recovery"
        assert monitor._alert_active["cpu"] is False

    def test_memory_alert_and_recovery(self, env, make_monitor, monkeypatch):
        """内存超阈值告警，回落后发恢复。"""
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=0: 10.0)
        monkeypatch.setattr(psutil, "disk_usage", lambda p: MagicMock(percent=50.0, free=1))
        now = {"t": 1000.0}
        monkeypatch.setattr(env.time, "time", lambda: now["t"])
        mem = {"percent": 95.0, "available": 1 * 1024**3}
        monkeypatch.setattr(psutil, "virtual_memory", lambda: MagicMock(**mem))
        monitor, alerts = make_monitor()

        result = monitor._do_check()
        assert [a.alert_type for a in result] == ["memory"]
        assert "内存使用率 95%" in result[0].message

        # 回落 → 恢复通知
        mem["percent"] = 50.0
        result = monitor._do_check()
        assert result[0].alert_type == "memory"
        assert result[0].level == "recovery"

    def test_disk_alert_and_recovery(self, env, make_monitor, monkeypatch):
        """磁盘剩余不足告警，恢复后通知。"""
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=0: 10.0)
        monkeypatch.setattr(psutil, "virtual_memory", lambda: MagicMock(percent=40.0, available=1))
        now = {"t": 1000.0}
        monkeypatch.setattr(env.time, "time", lambda: now["t"])
        disk = {"percent": 96.0, "free": 5 * 1024**3}  # 剩余 4% < 10%
        monkeypatch.setattr(psutil, "disk_usage", lambda p: MagicMock(**disk))
        monitor, _ = make_monitor()

        result = monitor._do_check()
        assert [a.alert_type for a in result] == ["disk"]
        assert "剩余空间仅 4.0%" in result[0].message

        disk["percent"] = 50.0  # 剩余 50%
        result = monitor._do_check()
        assert result[0].alert_type == "disk"
        assert result[0].level == "recovery"

    def test_disk_check_exception_ignored(self, env, make_monitor, monkeypatch):
        """disk_usage 抛异常时静默跳过，不影响其他告警。"""
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=0: 10.0)
        monkeypatch.setattr(psutil, "virtual_memory", lambda: MagicMock(percent=40.0, available=1))
        monkeypatch.setattr(psutil, "disk_usage", lambda p: (_ for _ in ()).throw(PermissionError("no")))
        now = {"t": 1000.0}
        monkeypatch.setattr(env.time, "time", lambda: now["t"])
        monitor, _ = make_monitor()
        assert monitor._do_check() == []

    def test_on_alert_exception_swallowed(self, env, monkeypatch):
        """告警回调抛异常不应影响检查。"""
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=0: 10.0)
        monkeypatch.setattr(psutil, "virtual_memory", lambda: MagicMock(percent=95.0, available=1))
        monkeypatch.setattr(psutil, "disk_usage", lambda p: MagicMock(percent=50.0, free=1))
        now = {"t": 1000.0}
        monkeypatch.setattr(env.time, "time", lambda: now["t"])

        def boom(alert):
            raise RuntimeError("通知失败")

        monitor = SystemMonitor(config=MonitorConfig(), on_alert=boom)
        monitor._do_check()  # 不应抛异常


# ---------------------------------------------------------------------------
# 磁盘历史与趋势预测
# ---------------------------------------------------------------------------


class TestDiskHistory:
    """record_disk_usage / predict_disk_full / _load_history。"""

    def test_record_disk_usage_writes_history(self, env, make_monitor, monkeypatch, tmp_path):
        monkeypatch.setattr(psutil, "disk_usage", lambda p: MagicMock(percent=62.5, free=100 * 1024**3))
        monitor, _ = make_monitor()
        monitor.record_disk_usage()
        history = json.loads((tmp_path / "monitor_history.json").read_text(encoding="utf-8"))
        assert len(history) == 1
        assert history[0]["disk_percent"] == 62.5

    def test_record_same_day_overwrites(self, env, make_monitor, monkeypatch, tmp_path):
        monkeypatch.setattr(psutil, "disk_usage", lambda p: MagicMock(percent=50.0, free=1))
        monitor, _ = make_monitor()
        monitor.record_disk_usage()
        monkeypatch.setattr(psutil, "disk_usage", lambda p: MagicMock(percent=70.0, free=1))
        monitor.record_disk_usage()
        history = json.loads((tmp_path / "monitor_history.json").read_text(encoding="utf-8"))
        assert len(history) == 1  # 同一天只保留一条
        assert history[0]["disk_percent"] == 70.0

    def test_record_not_available(self, env, monkeypatch):
        monkeypatch.setitem(sys.modules, "psutil", None)
        monitor = SystemMonitor(config=MonitorConfig(), on_alert=lambda a: None)
        monitor.record_disk_usage()  # 不应抛异常

    def test_load_history_missing_file(self, env, make_monitor, tmp_path):
        monitor, _ = make_monitor()
        assert monitor._load_history() == []

    def test_load_history_corrupt(self, env, make_monitor, tmp_path):
        (tmp_path / "monitor_history.json").write_text("{bad", encoding="utf-8")
        monitor, _ = make_monitor()
        assert monitor._load_history() == []

    def test_predict_insufficient_data(self, env, make_monitor, tmp_path):
        monitor, _ = make_monitor()
        (tmp_path / "monitor_history.json").write_text(json.dumps([
            {"disk_percent": 60.0}, {"disk_percent": 61.0},
        ]), encoding="utf-8")
        assert monitor.predict_disk_full() is None

    def test_predict_too_few_points(self, env, make_monitor, tmp_path):
        # 3 条记录但只有 2 条含 disk_percent
        monitor, _ = make_monitor()
        (tmp_path / "monitor_history.json").write_text(json.dumps([
            {"disk_percent": 60.0}, {"date": "x"}, {"disk_percent": 62.0},
        ]), encoding="utf-8")
        assert monitor.predict_disk_full() is None

    def test_predict_decreasing_trend(self, env, make_monitor, tmp_path):
        monitor, _ = make_monitor()
        (tmp_path / "monitor_history.json").write_text(json.dumps([
            {"disk_percent": 80.0}, {"disk_percent": 70.0}, {"disk_percent": 60.0},
        ]), encoding="utf-8")
        assert monitor.predict_disk_full() is None

    def test_predict_current_already_high(self, env, make_monitor, tmp_path):
        monitor, _ = make_monitor()
        (tmp_path / "monitor_history.json").write_text(json.dumps([
            {"disk_percent": 90.0}, {"disk_percent": 95.0}, {"disk_percent": 98.0},
        ]), encoding="utf-8")
        assert monitor.predict_disk_full() is None

    def test_predict_far_future_no_alert(self, env, make_monitor, tmp_path):
        # 每天增长 2%，从 64% 到 95% 需要 15.5 天 > 7 天 → 不告警
        monitor, _ = make_monitor()
        (tmp_path / "monitor_history.json").write_text(json.dumps([
            {"disk_percent": 60.0}, {"disk_percent": 62.0}, {"disk_percent": 64.0},
        ]), encoding="utf-8")
        assert monitor.predict_disk_full() is None

    def test_predict_alert_text(self, env, make_monitor, tmp_path):
        # 每天增长 10%，从 80% 到 95% 需要 1.5 天 ≤ 7 天 → 预警
        monitor, _ = make_monitor()
        (tmp_path / "monitor_history.json").write_text(json.dumps([
            {"disk_percent": 60.0}, {"disk_percent": 70.0}, {"disk_percent": 80.0},
        ]), encoding="utf-8")
        text = monitor.predict_disk_full()
        assert text is not None
        assert "磁盘空间趋势预警" in text
        assert "约 2 天后磁盘将满" in text


# ---------------------------------------------------------------------------
# 进程 / 空闲 / 工作时长
# ---------------------------------------------------------------------------


class TestProcessAndWork:
    """check_high_cpu_processes / get_idle_time_seconds / check_work_break。"""

    def test_high_cpu_processes(self, env, make_monitor, monkeypatch):
        class FakeProc:
            def __init__(self, info):
                self._info = info

            @property
            def info(self):
                if isinstance(self._info, Exception):
                    raise self._info
                return self._info

        procs = [
            FakeProc({"pid": 1, "name": "chrome", "cpu_percent": 80.0}),
            FakeProc({"pid": 2, "name": "notepad", "cpu_percent": 5.0}),
            FakeProc(psutil.NoSuchProcess(pid=3)),
            FakeProc(psutil.AccessDenied(pid=4)),
        ]
        monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: procs)
        monitor, _ = make_monitor()
        result = monitor.check_high_cpu_processes()
        assert len(result) == 1
        assert "chrome (PID: 1) CPU: 80%" in result[0]

    def test_high_cpu_processes_not_available(self, env, monkeypatch):
        monkeypatch.setitem(sys.modules, "psutil", None)
        monitor = SystemMonitor(config=MonitorConfig(), on_alert=lambda a: None)
        assert monitor.check_high_cpu_processes() == []

    def test_high_cpu_processes_iter_exception(self, env, make_monitor, monkeypatch):
        monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: (_ for _ in ()).throw(RuntimeError("x")))
        monitor, _ = make_monitor()
        assert monitor.check_high_cpu_processes() == []

    def test_idle_time_non_windows(self, env, make_monitor, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monitor, _ = make_monitor()
        assert monitor.get_idle_time_seconds() is None

    def test_idle_time_windows(self, env, make_monitor, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")

        class FakeStructure:
            """模拟 ctypes.Structure 的最小替身（dwTime 默认 0，源码只设置 cbSize）。"""

            dwTime = 0

            def __init__(self, *a, **k):
                pass

        fake_ctypes = MagicMock()
        fake_ctypes.Structure = FakeStructure
        fake_ctypes.c_uint = int
        fake_ctypes.sizeof.return_value = 8
        fake_ctypes.byref.side_effect = lambda x: x
        fake_ctypes.windll.user32.GetLastInputInfo.return_value = 0
        fake_ctypes.windll.kernel32.GetTickCount.return_value = 2000
        monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

        monitor, _ = make_monitor()
        # dwTime 默认 0，GetTickCount=2000 → 2 秒
        assert monitor.get_idle_time_seconds() == 2.0

    def test_idle_time_windows_exception(self, env, make_monitor, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        fake_ctypes = MagicMock()
        fake_ctypes.Structure = type("S", (), {})
        fake_ctypes.windll.user32.GetLastInputInfo.side_effect = OSError("ctypes 失败")
        monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
        monitor, _ = make_monitor()
        assert monitor.get_idle_time_seconds() is None

    def test_work_break_idle_unknown(self, env, make_monitor, monkeypatch):
        monitor, _ = make_monitor()
        monkeypatch.setattr(monitor, "get_idle_time_seconds", lambda: None)
        assert monitor.check_work_break() is None

    def test_work_break_active_user_flow(self, env, make_monitor, monkeypatch):
        """用户活跃（idle<60s）：首次记录开始时间，超时后提醒并重置。"""
        now = {"t": 1000.0}
        monkeypatch.setattr(env.time, "time", lambda: now["t"])
        monitor, _ = make_monitor()
        monkeypatch.setattr(monitor, "get_idle_time_seconds", lambda: 5.0)
        assert monitor.check_work_break() is None  # 首次：记录开始时间
        now["t"] = 1000.0 + 7200  # 2 小时后
        text = monitor.check_work_break()
        assert text is not None
        assert "已连续工作 2 小时 0 分钟" in text
        # 提醒后计时器重置，再次检查不提醒
        assert monitor.check_work_break() is None

    def test_work_break_within_interval(self, env, make_monitor, monkeypatch):
        now = {"t": 1000.0}
        monkeypatch.setattr(env.time, "time", lambda: now["t"])
        monitor, _ = make_monitor()
        monkeypatch.setattr(monitor, "get_idle_time_seconds", lambda: 5.0)
        monitor.check_work_break()  # 开始计时
        now["t"] = 1500.0  # 仅 500 秒
        assert monitor.check_work_break() is None

    def test_work_break_idle_resets_timer(self, env, make_monitor, monkeypatch):
        now = {"t": 1000.0}
        monkeypatch.setattr(env.time, "time", lambda: now["t"])
        monitor, _ = make_monitor()
        monkeypatch.setattr(monitor, "get_idle_time_seconds", lambda: 5.0)
        monitor.check_work_break()  # 设置开始时间
        monkeypatch.setattr(monitor, "get_idle_time_seconds", lambda: 300.0)  # 空闲 5 分钟
        now["t"] = 5000.0
        assert monitor.check_work_break() is None  # 空闲重置计时，不提醒
