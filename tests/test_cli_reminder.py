"""CLI 定时提醒宿主测试（把桌面的 ScheduleReminder 能力对齐到 CLI/语音端）。

背景（fixlog: agent-loop-stall-fix 第六节）：提醒工具族此前只在 serve 路径
注册，纯 CLI/语音 REPL 里 ToolSearch 搜不到「提醒」是事实而非幻觉；模型因此
绕道手搓 Register-ScheduledTask，思考撞穿 max_tokens 后空转，且语音模式的
SIGINT 只置标志不抛异常，长思考流里无人检查 abort → Ctrl+C 停不掉。

覆盖：注册门控（未 start 不注册）、四个工具挂载且为延迟加载、到期回调文本
与 UI 异常吞掉、真 Scheduler 端到端触发、RichCLI 后台播报的打印与提示符重绘。

@author aceFelix
"""

from __future__ import annotations

import io
import time
from datetime import datetime, timedelta

import pytest

from agent.core.daemon.cli_reminder import CliReminderHost
from agent.core.tool import ToolRegistry


class _StubScheduler:
    """只满足 register_schedule_tools 构造需要的调度器替身。"""


# ---------- 注册门控与挂载 ----------

def test_register_tools_before_start_returns_zero():
    """未 start() 时不得注册工具：工具构造需要可用的 Scheduler 实例。"""
    host = CliReminderHost(notify=lambda t: None)
    reg = ToolRegistry()
    assert host.register_tools(reg) == 0
    assert "ScheduleReminder" not in reg


def test_register_tools_mounts_four_deferred_tools():
    """挂载 ScheduleReminder / ListSchedule / CancelSchedule /
    AcknowledgeReminder 四个工具，且都标为延迟加载（经 ToolSearch 取用）。"""
    host = CliReminderHost(notify=lambda t: None)
    host._scheduler = _StubScheduler()  # 注入替身，绕开真实持久化
    reg = ToolRegistry()

    count = host.register_tools(reg)

    assert count == 4
    for name in ("ScheduleReminder", "ListSchedule", "CancelSchedule",
                 "AcknowledgeReminder"):
        assert name in reg
        assert reg.get(name).deferred is True


def test_register_tools_is_idempotent():
    """重复注册不应报错或翻倍（注册函数内部按名字跳过已存在项）。"""
    host = CliReminderHost(notify=lambda t: None)
    host._scheduler = _StubScheduler()
    reg = ToolRegistry()

    assert host.register_tools(reg) == 4
    assert host.register_tools(reg) == 0


# ---------- 到期回调 ----------

def test_on_fire_formats_text_and_swallows_ui_error():
    """回调文本带「提醒：」前缀；notify 抛异常必须被吞掉。

    notify 跑在 Scheduler 后台线程，一次 UI 故障若外抛会终止整个调度循环，
    导致后续所有提醒静默失效。
    """
    got: list[str] = []
    host = CliReminderHost(notify=got.append)

    class _T:
        content = "面试"

    host._on_fire(_T())
    assert got == ["提醒：面试"]

    def _boom(_text):
        raise RuntimeError("终端炸了")

    host_broken = CliReminderHost(notify=_boom)
    host_broken._on_fire(_T())  # 不抛即通过


def test_stop_is_safe_without_start():
    """未启动就 stop 不得抛异常（REPL 初始化失败路径也会走清理）。"""
    host = CliReminderHost(notify=lambda t: None)
    host.stop()
    assert host.scheduler is None


# ---------- 端到端：真 Scheduler 触发 ----------

def test_reminder_fires_end_to_end(monkeypatch, tmp_path):
    """到点提醒经宿主回调落到 notify：写任务 → 调度线程触发 → notify 收到。

    Scheduler 每秒轮询一次，本用例等待上限 5 秒。持久化文件重定向到 tmp_path，
    不碰用户真实的 schedule.json。
    """
    from agent.core.daemon import scheduler as sched_mod

    store = tmp_path / "schedule.json"
    monkeypatch.setattr(sched_mod, "_schedule_file", lambda: store)

    got: list[str] = []
    host = CliReminderHost(notify=got.append)
    real = host.start()
    try:
        real.add_task(
            content="面试",
            trigger_at=(datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds"),
        )
        deadline = time.time() + 5.0
        while not got and time.time() < deadline:
            time.sleep(0.1)
    finally:
        host.stop()

    assert got == ["提醒：面试"]
    assert host.scheduler is None  # stop 已释放


# ---------- RichCLI 后台播报 ----------

def _make_cli():
    """构造 console 指向 StringIO 的 RichCLI（不跑启动动画）。"""
    pytest.importorskip("rich")
    from rich.console import Console

    from agent.ui.cli import RichCLI

    buf = io.StringIO()
    ui = RichCLI(boot_animation=False)
    ui._console = Console(file=buf, force_terminal=False, width=100,
                          color_system=None)
    return ui, buf


def test_notify_reminder_prints_without_pt_session():
    """无 prompt_toolkit 会话（重定向/非交互）时退化为普通打印，不抛异常。"""
    ui, buf = _make_cli()
    ui._pt_session = None

    ui.notify_reminder("提醒：面试")

    out = buf.getvalue()
    assert "面试" in out and "⏰" in out


def test_notify_reminder_invalidates_prompt_app():
    """有 PT 会话时打印后必须 invalidate，让提示符重绘到提醒下方。

    缺这一步时提醒会叠在正在输入的行上（屏幕被顶乱），用户看不到提醒内容。
    """
    ui, _buf = _make_cli()
    calls: list[str] = []

    class _App:
        def invalidate(self):
            calls.append("invalidate")

    class _Session:
        app = _App()

    ui._pt_session = _Session()
    ui.notify_reminder("提醒：喝水")

    assert calls == ["invalidate"]


def test_notify_reminder_survives_broken_app():
    """app 属性缺失或 invalidate 抛异常都不外抛（后台线程保护）。"""
    ui, _buf = _make_cli()

    class _BadApp:
        def invalidate(self):
            raise RuntimeError("重绘失败")

    class _Session:
        app = _BadApp()

    ui._pt_session = _Session()
    ui.notify_reminder("提醒：开会")  # 不抛即通过
