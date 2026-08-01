"""实时聊天（/talk 双工语音）会话管理 —— RealtimeTalkMixin。

管理实时聊天窗口的单例生命周期：启动/唤起 pywebview 窗口、后台线程运行
RealtimeTalk 的 asyncio 事件循环、响应监听线程、停止/清理。

从原 daemon.py 拆分而来，由 JarvisDaemon 混入。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import datetime


class RealtimeTalkMixin:
    """实时聊天混入：窗口生命周期 + 会话状态。"""

    # ── 状态读取 / 开关 ──

    def _read_realtime_talk_enabled(self) -> bool:
        """读取实时聊天开关最新状态（内存缓存）。

        @author aceFelix
        """
        return self._realtime_talk_enabled

    def _toggle_realtime_talk(self) -> None:
        """切换托盘「实时聊天」开关状态。

        开启时持久化配置并启动实时语音对话子进程；
        关闭时终止子进程并更新配置到 settings.toml。

        @author aceFelix
        """
        self._realtime_talk_enabled = not self._realtime_talk_enabled
        try:
            from agent.config.settings import save_realtime_talk_auto_start
            save_realtime_talk_auto_start(self._realtime_talk_enabled)
        except Exception as e:
            self._daemon_log("保存实时聊天配置失败: %s", e)

        ui = self._ui
        if self._realtime_talk_enabled:
            # 互斥：开启实时聊天前停止语音会话
            if self._voice_session_active:
                self._stop_voice_session()
            if ui:
                ui.info("🎙️ 实时聊天已开启")
            self._start_realtime_talk()
        else:
            if ui:
                ui.info("🔇 实时聊天已关闭")
            self._stop_realtime_talk()
        # 刷新托盘菜单让状态立刻生效
        self._refresh_tray_menu()

    def _is_realtime_talk_running(self) -> bool:
        """检查实时聊天后台线程是否仍在运行。"""
        return self._realtime_talk_thread is not None and self._realtime_talk_thread.is_alive()

    def _refresh_tray_menu(self) -> None:
        """通知 pystray 刷新托盘菜单，让状态变化立刻可见。"""
        if self._tray is None or not self._tray.available:
            return
        try:
            icon = self._tray._icon
            if icon is not None and hasattr(icon, "update_menu"):
                icon.update_menu()
        except Exception:
            pass

    # ── 启动 ──

    def _start_realtime_talk(self) -> None:
        """启动实时双工语音对话窗口。

        在 daemon 进程内创建/唤起一个 pywebview 窗口，并在独立后台线程中
        运行 RealtimeTalk 的 asyncio 事件循环。daemon 生命周期内只维护
        一个窗口实例，反复开关复用该窗口。

        @author aceFelix
        """
        try:
            from agent.ui.realtime_window import RealtimeTalkWindow
        except ImportError as e:
            ui = self._ui
            if ui:
                ui.warn(f"实时聊天窗口不可用: {e}（请安装 pywebview）")
            if self._tray and self._tray.available:
                self._tray.notify("J.A.R.V.I.S", "实时聊天窗口不可用，请安装 pywebview")
            return

        if self._realtime_talk_window is None:
            # daemon 模式下 RealtimeTalk 在父进程后台线程运行，
            # 子进程窗口只负责渲染 UI，不单独启动 RealtimeTalk。
            self._realtime_talk_window = RealtimeTalkWindow(
                on_close=self._on_realtime_window_closed,
                standalone=False,
            )

        self._realtime_talk_window.show()

        # 确保事件监听线程在运行（检测“结束”按钮和窗口恢复）
        self._start_realtime_response_watcher()

        if self._is_realtime_talk_running():
            if self._tray and self._tray.available:
                self._tray.notify("J.A.R.V.I.S", "实时聊天窗口已唤起")
            return

        self._start_realtime_session()

        ui = self._ui
        if ui:
            ui.info("🎙️ 已启动实时聊天窗口")
        if self._tray and self._tray.available:
            self._tray.notify("J.A.R.V.I.S", "实时聊天已启动")

    def _start_realtime_session(self) -> None:
        """启动一个新的 RealtimeTalk 会话线程。

        @author aceFelix
        """
        if self._is_realtime_talk_running():
            return
        self._realtime_talk_thread = threading.Thread(
            target=self._run_realtime_talk_in_thread,
            daemon=True,
        )
        self._realtime_talk_thread.start()

    def _start_realtime_response_watcher(self) -> None:
        """启动子进程事件监听线程。

        定期轮询子进程发来的事件：
        - end_session：用户点击“结束”按钮，停止当前会话
        - window_restored：用户从任务栏恢复窗口，自动启动新会话

        @author aceFelix
        """
        if getattr(self, "_rt_watcher_alive", False):
            return
        self._rt_watcher_alive = True

        def _watcher():
            from agent.ui.realtime_window.process import (
                EVT_END_SESSION,
                EVT_WINDOW_CLOSED,
                EVT_WINDOW_RESTORED,
            )
            try:
                while self._rt_watcher_alive and self._realtime_talk_enabled:
                    window = self._realtime_talk_window
                    if window is None:
                        time.sleep(0.5)
                        continue
                    # 兜底：子进程已自行退出（用户点 X），但事件没送达
                    try:
                        if not window.is_open:
                            self._daemon_log("实时聊天: 检测到窗口子进程已退出")
                            try:
                                from agent.config.settings import save_realtime_talk_auto_start
                                save_realtime_talk_auto_start(False)
                            except Exception:
                                pass
                            # 立即取消当前会话任务
                            task = getattr(self, "_realtime_talk_task", None)
                            if task is not None and not task.done():
                                try:
                                    task.cancel()
                                except Exception:
                                    pass
                            rt = getattr(self, "_current_rt", None)
                            if rt is not None:
                                rt._running = False
                            self._realtime_talk_enabled = False
                            self._realtime_talk_window = None
                            self._refresh_tray_menu()
                            break
                    except Exception as e:
                        self._daemon_log("实时聊天: 检测窗口状态时出错: %s", e)
                    try:
                        events = window.poll_response()
                        for evt in events:
                            event_type = evt.get("event", "")
                            if event_type == EVT_END_SESSION:
                                # 用户点击“结束”：停止当前会话
                                self._daemon_log("实时聊天: 用户点击结束，停止会话")
                                task = getattr(self, "_realtime_talk_task", None)
                                if task is not None and not task.done():
                                    try:
                                        task.cancel()
                                    except Exception:
                                        pass
                                rt = getattr(self, "_current_rt", None)
                                if rt is not None:
                                    rt._running = False
                            elif event_type == EVT_WINDOW_RESTORED:
                                # 用户从任务栏恢复窗口：自动启动新会话
                                if not self._is_realtime_talk_running():
                                    self._daemon_log("实时聊天: 窗口恢复，启动新会话")
                                    self._start_realtime_session()
                            elif event_type == EVT_WINDOW_CLOSED:
                                # 用户点击 X 关闭窗口：彻底关闭实时聊天
                                self._daemon_log("实时聊天: 窗口被关闭，彻底停止")
                                task = getattr(self, "_realtime_talk_task", None)
                                if task is not None and not task.done():
                                    try:
                                        task.cancel()
                                    except Exception:
                                        pass
                                rt = getattr(self, "_current_rt", None)
                                if rt is not None:
                                    rt._running = False
                                # 切换托盘状态为关闭并持久化
                                self._realtime_talk_enabled = False
                                try:
                                    from agent.config.settings import save_realtime_talk_auto_start
                                    save_realtime_talk_auto_start(False)
                                except Exception:
                                    pass
                                # 清理窗口引用（子进程已自行退出）
                                self._realtime_talk_window = None
                                # 刷新托盘菜单，确保状态立刻同步
                                self._refresh_tray_menu()
                    except Exception as e:
                        self._daemon_log("实时聊天: watcher 处理事件异常: %s", e)
                    time.sleep(0.3)
            except Exception as e:
                self._daemon_log("实时聊天: watcher 线程异常退出: %s", e)
            finally:
                self._rt_watcher_alive = False

        t = threading.Thread(target=_watcher, daemon=True)
        t.start()

    def _run_realtime_talk_in_thread(self) -> None:
        """在独立线程中运行 RealtimeTalk 的 asyncio 事件循环。"""
        asyncio.run(self._realtime_talk_loop())

    async def _realtime_talk_loop(self) -> None:
        """实时语音对话协程主循环。

        连接 DashScope 实时语音服务，音频/WebSocket 逻辑与 UI 解耦。
        窗口关闭或发生致命错误时退出循环。

        @author aceFelix
        """
        from agent.voice.realtime_talk import RealtimeTalk, DEFAULT_WS_URL
        from agent.ui.realtime_window import WebviewRealtimeTalkUI

        window = self._realtime_talk_window
        if window is None:
            return

        api_key = (
            self._settings.dashscope_api_key
            or os.environ.get("DASHSCOPE_API_KEY", "")
            or self._settings.api_key
            or os.environ.get("OPENAI_API_KEY", "")
        )
        if not api_key:
            window.emit("error", "未配置 DashScope API Key")
            return

        ui = WebviewRealtimeTalkUI(window, loop=asyncio.get_running_loop())

        # 动态注入当前时间，让实时聊天能准确回答时间/日期问题
        _weekdays = "一二三四五六日"
        _now = datetime.now()
        _time_ctx = _now.strftime(f"%Y年%m月%d日 %H:%M 星期{_weekdays[_now.weekday()]}")
        _instructions = (
            "你是贾维斯，先生的全能管家。用简洁自然的口语回复，"
            "不要输出思考过程。保持对话流畅自然。"
            f"\n当前时间：{_time_ctx}。如被问到时间，以此为准。"
            "\n【地域性查询规则】涉及天气、新闻、本地服务、附近推荐等地域性查询时，"
            "先用 Location 工具自动定位获取所在城市；只有定位失败时才询问先生所在地，"
            "不要自行假设任何城市。"
            "\n【回复长度规则】每次回复控制在3-5句话以内，不要长篇大论。"
            "如果用户要求长内容（讲故事、讲笑话、详细解释、长篇分析等），"
            "先说第一段（不超过5句），说完后问'要我继续吗？'，"
            "等先生回应后再说下一段，以此类推。"
            "\n当先生说\"退下\"、\"贾维斯退下\"、\"结束对话\"、\"再见\"、\"拜拜\"、\"没事了\"等表示结束的话时，"
            "调用 end_conversation 工具结束对话。"
        )

        rt = RealtimeTalk(
            api_key=api_key,
            model=getattr(self._settings, "realtime_model", "qwen-audio-3.0-realtime-flash"),
            voice=getattr(self._settings, "realtime_voice", "longanqian"),
            instructions=_instructions,
            ws_url=getattr(self._settings, "realtime_ws_url", "") or DEFAULT_WS_URL,
            workdir=getattr(self._settings, "workdir", "") or os.getcwd(),
        )
        self._current_rt = rt  # 供 response_watcher 访问以停止会话

        # 新会话开始时清空前端聊天记录并通知 UI
        try:
            window.emit("clear_chat", None)
            window.emit("session_started", None)
        except Exception:
            pass

        # 窗口关闭时同步停止 RealtimeTalk
        def _on_close() -> None:
            rt._running = False

        # 更新窗口关闭回调，确保点击关闭按钮能停止本会话
        original_on_close = window._on_close
        window._on_close = lambda: (_on_close(), original_on_close() if original_on_close else None)

        try:
            self._realtime_talk_task = asyncio.create_task(rt.run(ui))
            try:
                await self._realtime_talk_task
            except asyncio.CancelledError:
                # 用户主动结束/关闭窗口，正常退出
                pass
        except Exception as e:
            window.emit("error", f"实时聊天异常: {e}")
        finally:
            self._realtime_talk_task = None
            window._on_close = original_on_close
            self._current_rt = None
            # 通知 UI 会话已结束，显示“恢复对话”按钮（窗口保持打开）
            try:
                window.emit("status", "standby")
            except Exception:
                pass
            try:
                window.emit("session_ended", None)
            except Exception:
                pass
            self._daemon_log("实时聊天会话已结束，等待恢复")

    # ── 关闭 ──

    def _on_realtime_window_closed(self) -> None:
        """实时聊天窗口被关闭时的回调。

        由 RealtimeTalkWindow 在用户点击关闭按钮时调用，
        通知 daemon 更新托盘菜单状态（不销毁窗口实例）。
        """
        self._daemon_log("实时聊天窗口已关闭")
        # 窗口已关闭，同步托盘状态为关闭
        if self._realtime_talk_enabled:
            self._realtime_talk_enabled = False
            try:
                from agent.config.settings import save_realtime_talk_auto_start
                save_realtime_talk_auto_start(False)
            except Exception:
                pass
            self._refresh_tray_menu()

    def _stop_realtime_talk(self) -> None:
        """停止实时聊天窗口。

        隐藏窗口并等待后台 RealtimeTalk 线程结束。
        不销毁窗口实例，便于再次唤起复用。

        @author aceFelix
        """
        # 停止事件监听线程
        self._rt_watcher_alive = False

        # 取消当前正在运行的会话任务（如果有）
        task = getattr(self, "_realtime_talk_task", None)
        if task is not None and not task.done():
            try:
                task.cancel()
            except Exception:
                pass

        if self._realtime_talk_window is not None:
            try:
                # 通知 RealtimeTalk 停止（如果还在运行）
                self._realtime_talk_window._notify_close()
                # 隐藏窗口
                self._realtime_talk_window.hide()
            except Exception as e:
                self._daemon_log("隐藏实时聊天窗口失败: %s", e)

        if self._realtime_talk_thread is not None and self._realtime_talk_thread.is_alive():
            try:
                self._realtime_talk_thread.join(timeout=3)
            except Exception as e:
                self._daemon_log("等待实时聊天线程结束失败: %s", e)
        self._realtime_talk_thread = None
        self._current_rt = None
