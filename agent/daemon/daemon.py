"""贾维斯常驻守护进程 —— JarvisDaemon 核心。

本文件只保留 JarvisDaemon 的核心编排（状态初始化、装配、主循环、清理），
具体能力按职责拆分到独立模块，通过 Mixin 组合：

- ``platform_utils``: 平台 / 进程纯函数（detached 启动、解释器定位、依赖检测）
- ``hotkey`` / ``tray``: 全局热键监听、系统托盘图标
- ``sessions``: 语音 / 文本交互会话（SessionMixin）
- ``realtime``: 实时聊天窗口生命周期（RealtimeTalkMixin）
- ``notifications``: 调度 / 主动提醒 / 监控 / 视觉事件回调（NotificationMixin）
- ``terminal_spawner``: 文本终端弹出（FastTerminalSpawner，含 warm 预启动）

为保持对外兼容，本模块仍导出 ``JarvisDaemon``、``TrayIcon``、
``launch_detached_daemon``、``_is_detached``（main.py 依赖）。

@author aceFelix
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from agent.config.settings import Settings
from agent.core.context import ToolContext
from agent.core.message import Message
from agent.core.query_loop import QueryLoop
from agent.core.tool import ToolRegistry, build_default_registry, register_subagent_tool
from agent.core.orchestrator import ToolOrchestrator
from agent.daemon.hotkey import HotkeyListener
from agent.daemon.notifications import NotificationMixin
from agent.daemon.platform_utils import (
    _daemon_log_file,
    _is_detached,
    _is_macos,
    _is_windows,
    launch_detached_daemon,
)
from agent.daemon.realtime import RealtimeTalkMixin
from agent.daemon.sessions import SessionMixin
from agent.daemon.tray import TrayIcon
from agent.prompts.system import build_system_prompt
from agent.ui.cli import RichCLI


class JarvisDaemon(RealtimeTalkMixin, SessionMixin, NotificationMixin):
    """贾维斯常驻守护进程。

    管理:
    - 后台待命状态
    - 热键监听 + 托盘图标
    - 唤起交互（语音/文本）→ 退下回后台

    用法::

        daemon = JarvisDaemon(settings)
        daemon.run()  # 阻塞，直到用户退出
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ui: RichCLI | None = None
        self._loop: QueryLoop | None = None
        self._registry: ToolRegistry | None = None
        self._orchestrator: ToolOrchestrator | None = None
        self._provider = None
        self._mcp_client = None
        self._messages: list[Message] = []
        self._ctx: ToolContext | None = None

        # 唤起信号：热键/托盘触发后置位，daemon 循环检测
        self._wake_event = threading.Event()
        self._wake_mode: str = "voice"  # "voice" 或 "text"
        self._quit_event = threading.Event()
        # 实时聊天窗口：daemon 生命周期内单例，同一进程内通过 pywebview 承载
        self._realtime_talk_window: Any = None
        self._realtime_talk_thread: threading.Thread | None = None
        self._realtime_talk_task: Any = None  # 当前 asyncio Task，用于取消会话
        self._current_rt: Any = None          # 当前 RealtimeTalk 实例（供 watcher 停止）
        self._rt_watcher_alive: bool = False  # 事件监听线程运行标志
        # 语音对话会话状态：daemon 启动后默认不进入语音对话，仅托盘/热键触发
        # 旧逻辑：默认进入 voice_loop（语音随时待命）
        # 新逻辑：默认关闭，用户点击托盘「语音对话」才启动
        self._voice_session_active = False
        self._voice_session_thread: threading.Thread | None = None
        self._voice_session_stop_event = threading.Event()
        # 实时聊天开关状态：从配置读取，托盘切换后持久化到 settings.toml
        self._realtime_talk_enabled = bool(getattr(settings, "realtime_talk_auto_start", False))

        self._hotkey = HotkeyListener(
            settings.daemon_hotkey, self._trigger_voice
        )
        self._tray = TrayIcon(
            on_voice=self._trigger_voice,
            on_text=self._trigger_text,
            on_quit=self._trigger_quit,
            voice_active_getter=lambda: self._voice_session_active,
            voice_toggle=self._toggle_voice_session,
            realtime_enabled_getter=lambda: self._read_realtime_talk_enabled(),
            realtime_toggle=self._toggle_realtime_talk,
        )

        # 文本终端弹出器（窗口复用 / warm 预启动 / 快速冷启动）
        from agent.daemon.terminal_spawner import FastTerminalSpawner
        self._spawner = FastTerminalSpawner(
            settings,
            notify=self._tray_notify,
            log=self._daemon_log,
        )

    # ------------------------------------------------------------------
    # 运行
    # ------------------------------------------------------------------

    def run(self) -> int:
        """启动 daemon 主循环。阻塞直到用户退出。"""
        # 初始化组件（复用 main.py 的装配逻辑）
        self._setup()

        ui = self._ui
        assert ui is not None

        ui.info("=" * 56)
        ui.info("🏠 贾维斯常驻模式已启动")
        hotkey_status = f"热键 {self._settings.daemon_hotkey}" if self._hotkey.available else "热键不可用（需 pip install keyboard）"
        tray_status = "托盘图标已就绪" if self._tray.available else "托盘不可用（需 pip install pystray）"
        ui.info(f"   {hotkey_status}")
        ui.info(f"   {tray_status}")
        ui.info("   语音对话已关闭 · 点击托盘「语音对话」或按热键唤起")

        if self._realtime_talk_enabled:
            ui.info("   实时聊天默认开启 · 启动后会自动打开实时语音对话窗口")
            ui.info("   托盘右键「实时聊天」可关闭自动启动")
        else:
            ui.info("   实时聊天默认关闭 · 可在托盘菜单手动开启")

        ui.info("   Ctrl+C 退出")
        ui.info("=" * 56)

        # 启动热键和托盘
        if self._hotkey.available:
            if self._hotkey.start():
                ui.info(f"✓ 全局热键已注册: {self._settings.daemon_hotkey}")
            else:
                ui.warn("热键注册失败（可能需要管理员权限）")

        if self._tray.available:
            if self._tray.start(log_func=self._daemon_log):
                ui.info("✓ 系统托盘图标已启动")
            else:
                ui.warn("托盘图标启动失败")
                if _is_windows():
                    ui.warn("  请安装 Windows 托盘依赖: pip install pywin32")
                elif _is_macos():
                    ui.warn("  macOS 托盘需要 pystray + Pillow: pip install pystray Pillow")
                self._daemon_log("托盘启动失败，请检查依赖")

        if not self._hotkey.available and not self._tray.available:
            ui.warn("⚠ 热键和托盘均不可用，仅能通过语音唤醒词唤起")

        # 主动感知：启动定时任务调度器 + 系统监控器 + 节假日检查（阶段五第三刀）
        self._scheduler.start()
        if self._scheduler.list_pending():
            ui.info(f"⏰ 已加载 {len(self._scheduler.list_pending())} 个待触发提醒")

        # P2-3 主动提醒系统：启动主动感知引擎
        self._proactive.start()
        if self._settings.briefing_enabled:
            ui.info(f"📋 主动提醒已启动（每日简报 {self._settings.briefing_time}）")
        if self._settings.deadline_enabled:
            active_deadlines = self._deadline_tracker.list_active()
            if active_deadlines:
                ui.info(f"📌 已加载 {len(active_deadlines)} 个活跃截止日期")
        if self._settings.calendar_enabled and self._calendar_source.available:
            ui.info("📆 日历集成已启用")

        # 记录磁盘使用率（用于趋势预测）
        try:
            self._monitor.record_disk_usage()
        except Exception:
            pass

        if self._monitor.start():
            ui.info("📡 系统资源监控已启动（CPU/内存/磁盘）")
        elif self._settings.monitor_enabled:
            ui.info("📡 系统监控不可用（pip install psutil 启用）")

        # 节假日提醒：检查明天是否节假日
        try:
            from agent.core.daemon.holidays import check_tomorrow_holiday
            holiday_msg = check_tomorrow_holiday()
            if holiday_msg:
                ui.info(f"📅 {holiday_msg}")
                # 托盘通知
                if self._tray and self._tray.available:
                    try:
                        self._tray.notify("节假日提醒", holiday_msg)
                    except Exception:
                        pass
        except Exception:
            pass

        # 启动后默认不进入语音对话模式（用户需求：只有托盘开启时才启动）。
        # 语音对话会话通过托盘「语音对话」或热键手动触发。
        ui.info("🔇 语音对话已关闭，点击托盘「语音对话」或按热键唤起")

        # 启动后根据实时聊天开关决定是否默认启动实时语音对话子进程。
        # 实时对话在独立进程中运行，不阻塞 daemon 主循环，用户按 ESC 退出。
        if self._realtime_talk_enabled:
            ui.info("🎙️ 实时聊天默认开启，正在启动实时语音对话窗口")
            # 延迟一点启动，让托盘图标和日志先就位
            threading.Timer(1.0, self._start_realtime_talk).start()
        else:
            ui.info("🔇 实时聊天默认关闭，保持后台待命")

        # daemon 主循环
        try:
            while not self._quit_event.is_set():
                # 待命状态：等待唤醒信号（每 0.5s 检查一次，也响应 Ctrl+C）
                if self._wake_event.wait(timeout=0.5):
                    self._wake_event.clear()
                    mode = self._wake_mode
                    if mode == "voice":
                        self._run_voice_session()
                    elif mode == "text":
                        self._run_text_session()
        except KeyboardInterrupt:
            pass
        finally:
            self._cleanup()
            ui.info("贾维斯已退出常驻模式。再见，先生。")

        return 0

    # ------------------------------------------------------------------
    # 装配
    # ------------------------------------------------------------------

    def _setup(self) -> None:
        """装配所有组件（与 main.py repl() 类似，但适配 daemon）。"""
        from agent.bootstrap import _build_provider, _build_checker

        settings = self._settings
        self._ui = RichCLI(verbose=settings.verbose, boot_animation=False)
        ui = self._ui

        self._provider = _build_provider(settings)
        self._registry = build_default_registry()
        # 子代理协作工具注入（阶段五第二刀）
        register_subagent_tool(self._registry, provider=self._provider, permission_mode=settings.permission_mode)

        # 视觉监控（阶段五扩展）：mediapipe 实时手势/人脸检测
        from agent.core.daemon.vision_watcher import VisionWatcher
        self._vision_watcher = VisionWatcher(on_event=self._on_vision_event)
        from agent.tools.vision.vision_tools import register_vision_tools
        register_vision_tools(self._registry, watcher_factory=lambda: self._vision_watcher)

        # 主动感知（阶段五第三刀）：定时任务调度器 + 系统监控器
        from agent.core.daemon.scheduler import Scheduler
        from agent.tools.extensions.schedule_tool import register_schedule_tools
        self._scheduler = Scheduler(on_fire=self._on_schedule_fire)
        register_schedule_tools(self._registry, self._scheduler)

        from agent.core.daemon.monitor import SystemMonitor, MonitorConfig
        self._monitor = SystemMonitor(
            config=MonitorConfig(
                enabled=settings.monitor_enabled,
                cpu_threshold=settings.monitor_cpu_threshold,
                memory_threshold=settings.monitor_memory_threshold,
                disk_threshold=settings.monitor_disk_threshold,
                check_interval=settings.monitor_check_interval,
                alert_cooldown=settings.monitor_alert_cooldown,
                # P2-3 增强
                disk_trend_days=settings.monitor_disk_trend_days,
                high_cpu_duration=settings.monitor_high_cpu_duration,
                work_break_interval=settings.monitor_work_break_interval,
            ),
            on_alert=self._on_monitor_alert,
        )

        # P2-3 主动提醒系统：截止日期追踪 + 日历数据源 + 主动感知引擎
        from agent.core.daemon.deadline import DeadlineTracker
        from agent.tools.extensions.deadline_tool import register_deadline_tools
        self._deadline_tracker = DeadlineTracker()
        register_deadline_tools(self._registry, self._deadline_tracker)

        from agent.core.daemon.calendar_source import CalendarSource, CalendarConfig
        self._calendar_source = CalendarSource(
            config=CalendarConfig(
                enabled=settings.calendar_enabled,
                backend=settings.calendar_backend,
                ics_path=settings.calendar_ics_path,
                ics_url=settings.calendar_ics_url,
                remind_minutes_before=settings.calendar_remind_minutes_before,
            )
        )

        from agent.core.daemon.proactive import ProactiveEngine, ProactiveConfig
        self._proactive = ProactiveEngine(
            scheduler=self._scheduler,
            config=ProactiveConfig(
                briefing_enabled=settings.briefing_enabled,
                briefing_time=settings.briefing_time,
                deadline_enabled=settings.deadline_enabled,
                deadline_check_time=settings.deadline_check_time,
                calendar_enabled=settings.calendar_enabled,
                calendar_remind_minutes_before=settings.calendar_remind_minutes_before,
            ),
            deadline_tracker=self._deadline_tracker,
            calendar_source=self._calendar_source if settings.calendar_enabled else None,
            monitor=self._monitor,
            on_notify=self._on_proactive_notify,
        )

        checker = _build_checker(settings)
        self._orchestrator = ToolOrchestrator(
            registry=self._registry, permission_checker=checker
        )

        system_prompt = build_system_prompt(settings.workdir, self._registry, enable_thinking=getattr(settings, 'enable_thinking', True))
        if settings.system_prompt_append:
            system_prompt += "\n\n" + settings.system_prompt_append

        model = settings.model or self._provider.default_model
        self._loop = QueryLoop(
            provider=self._provider,
            registry=self._registry,
            orchestrator=self._orchestrator,
            system=system_prompt,
            model=model,
            max_iterations=settings.max_iterations,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            enable_compaction=settings.context_compaction,
            compaction_threshold=settings.compaction_threshold,
            keep_recent_messages=settings.keep_recent_messages,
            vendor_fallback=settings.vendor_fallback,
            custom_models=settings.custom_models,
        )

        self._ctx = ToolContext(
            workdir=settings.workdir,
            messages=self._messages,
            permission_mode=settings.permission_mode.value,
            ui=ui,
        )

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _daemon_log(self, fmt: str, *args: object) -> None:
        """写调试日志到 daemon.log（仅关键事件，不刷屏）。"""
        try:
            log_path = _daemon_log_file()
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                ts = time.strftime("%H:%M:%S")
                msg = fmt % args if args else fmt
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    def _tray_notify(self, title: str, message: str) -> None:
        """托盘通知（安全版本：托盘不可用时静默跳过）。"""
        tray = self._tray
        if tray is not None and tray.available:
            try:
                tray.notify(title, message)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def _cleanup(self) -> None:
        """清理资源。"""
        # 退出前最终保存会话
        if self._messages:
            try:
                from agent.session_manager import _auto_save
                _auto_save(self._ui, self._messages,
                           workdir=self._settings.workdir,
                           model=self._settings.model,
                           provider=self._settings.provider,
                           verbose=False)
            except Exception:
                pass
        # 停止语音会话
        self._stop_voice_session()
        # 停止实时聊天并销毁窗口，避免 daemon 退出后残留 GUI
        self._stop_realtime_talk()
        if self._realtime_talk_window is not None:
            try:
                self._realtime_talk_window.destroy()
            except Exception:
                pass
            self._realtime_talk_window = None
        # 清理文本终端子进程（FastTerminalSpawner 复用/预启动的进程）
        try:
            self._spawner.stop()
        except Exception:
            pass
        # 先停热键/托盘，避免清理期间重复触发
        self._hotkey.stop()
        self._tray.stop()
        # 停止视觉监控（释放摄像头）
        if hasattr(self, '_vision_watcher') and self._vision_watcher is not None:
            try:
                self._vision_watcher.stop()
            except Exception:
                pass
        if self._mcp_client is not None:
            try:
                import asyncio
                asyncio.run(self._mcp_client.disconnect_all())
            except Exception:
                pass
        if self._provider is not None:
            try:
                import asyncio
                asyncio.run(self._provider.close())
            except Exception:
                pass


# 为了保持与 main.py 等外部调用方的兼容，重导出常用符号。
# main.py 依赖: TrayIcon / launch_detached_daemon / _is_detached
__all__ = [
    "JarvisDaemon",
    "TrayIcon",
    "HotkeyListener",
    "launch_detached_daemon",
    "_is_detached",
    "_daemon_log_file",
]
