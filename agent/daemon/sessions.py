"""daemon 交互会话管理 —— SessionMixin。

管理语音对话 / 文本对话两种唤起会话：
- 语音会话：线程运行 voice_loop，托盘/热键可开关，退出后回待命
- 文本会话：detached 模式弹终端窗口（FastTerminalSpawner），前台模式走进程内 REPL
- 触发入口：托盘回调 / 热键回调（互斥：语音 ⇄ 实时 ⇄ 文本）

从原 daemon.py 拆分而来，终端弹出逻辑已合并到 terminal_spawner.FastTerminalSpawner。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import os
import threading

from agent.core.context import ToolContext
from agent.daemon.platform_utils import _is_detached


class SessionMixin:
    """交互会话混入：语音 / 文本会话 + 触发分发。"""

    # ── 语音会话状态切换 ──

    def _toggle_voice_session(self) -> None:
        """切换托盘「语音对话」会话状态。

        如果当前没有语音会话在运行，则关闭实时聊天（互斥）并启动新会话；
        如果当前已有语音会话在运行，则干净地停止它。

        @author aceFelix
        """
        self._daemon_log("[toggle_voice_session] active=%s", self._voice_session_active)
        if self._voice_session_active:
            self._stop_voice_session()
            return

        # 启动语音会话前，关闭实时聊天（互斥）
        if self._realtime_talk_enabled:
            self._realtime_talk_enabled = False
            try:
                from agent.config.settings import save_realtime_talk_auto_start
                save_realtime_talk_auto_start(False)
            except Exception as e:
                self._daemon_log("保存实时聊天配置失败: %s", e)
            self._stop_realtime_talk()

        # 启动新的语音会话线程
        self._voice_session_active = True
        from agent.daemon.voice_state import set_voice_enabled
        set_voice_enabled(True)
        ui = self._ui
        if ui:
            ui.info("🎙️ 语音对话已启动")
        self._voice_session_stop_event.clear()
        self._voice_session_thread = threading.Thread(
            target=self._run_voice_session, daemon=True
        )
        self._voice_session_thread.start()
        self._daemon_log("[toggle_voice_session] 语音会话线程已启动")

    def _stop_voice_session(self) -> None:
        """干净地停止当前语音对话会话。

        通过 stop_event 通知 voice_loop 退出，并中断当前录音/推理。

        @author aceFelix
        """
        if not self._voice_session_active:
            return
        ui = self._ui
        if ui:
            ui.info("🔇 正在停止语音对话...")
        self._voice_session_stop_event.set()
        # 中断当前录音/推理，让 voice_loop 尽快退出
        try:
            from agent.voice import stt as stt_module
            stt_module._request_stop()
        except Exception:
            pass
        try:
            if self._ctx is not None:
                self._ctx.abort_event.set()
        except Exception:
            pass
        thread = self._voice_session_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._voice_session_active = False
        from agent.daemon.voice_state import set_voice_enabled
        set_voice_enabled(False)
        if ui:
            ui.info("🔇 语音对话已停止")
        try:
            self._tray.update_menu()
        except Exception:
            pass

    def _read_voice_active(self) -> bool:
        """读取语音对话会话是否活跃。

        @author aceFelix
        """
        return self._voice_session_active

    # ── 触发分发（托盘 / 热键）──

    def _trigger_voice(self) -> None:
        """热键/托盘触发语音对话。

        新逻辑：语音对话默认不启动，热键/托盘点击时启动一次语音会话。
        如果当前已有语音会话在运行，仅托盘通知"已在语音模式"。
        如果实时聊天正在运行，先停止它（互斥）。
        """
        if self._voice_session_active:
            if self._tray and self._tray.available:
                self._tray.notify("J.A.R.V.I.S", "已在语音对话模式中")
            return

        # 互斥：停止实时聊天
        if self._realtime_talk_enabled:
            self._realtime_talk_enabled = False
            try:
                from agent.config.settings import save_realtime_talk_auto_start
                save_realtime_talk_auto_start(False)
            except Exception as e:
                self._daemon_log("保存实时聊天配置失败: %s", e)
            self._stop_realtime_talk()

        self._toggle_voice_session()

    def _trigger_text(self) -> None:
        """托盘触发文本对话。

        文本对话与语音/实时互斥：触发前先停止语音会话和实时聊天，
        再弹出文本终端。
        """
        self._daemon_log("[trigger_text] 触发, is_detached=%s", _is_detached())
        # 互斥：停止语音会话
        if self._voice_session_active:
            self._stop_voice_session()
        # 互斥：停止实时聊天
        if self._realtime_talk_enabled:
            self._realtime_talk_enabled = False
            try:
                from agent.config.settings import save_realtime_talk_auto_start
                save_realtime_talk_auto_start(False)
            except Exception as e:
                self._daemon_log("保存实时聊天配置失败: %s", e)
            self._stop_realtime_talk()

        if _is_detached():
            # 弹出终端窗口（FastTerminalSpawner 复用/置顶/快速启动）
            self._daemon_log("[trigger_text] 弹出文本终端")
            self._spawner.bring_up()
        else:
            # 前台模式（--with-tray）：走事件循环
            self._wake_mode = "text"
            self._wake_event.set()

    def _trigger_quit(self) -> None:
        """托盘退出：强制终止整个进程。

        daemon 启动后默认进入 voice_loop，其内部 stt.listen() 会阻塞主线程
        （最长 stt_max_seconds 或 standby 6s）。设置 _quit_event 无法及时
        中断阻塞中的 voice_loop。直接 os._exit(0) 保证托盘「退出贾维斯」
        立即生效。资源清理由 OS 回收（与前台 --with-tray 模式行为一致）。
        """
        os._exit(0)

    # ── 会话运行 ──

    def _run_voice_session(self) -> None:
        """唤起一次语音对话会话。

        voice_loop 内部通过 voice_state 文件检测开关状态:
        - 文件为 true → 正常对话
        - 文件为 false → 进入待机（只听唤醒词"贾维斯"）

        通过 ``self._voice_session_stop_event`` 可以从托盘/热键干净地停止会话。

        @author aceFelix
        """
        ui = self._ui
        assert ui is not None and self._loop is not None and self._ctx is not None

        self._voice_session_active = True
        self._voice_session_stop_event.clear()
        self._daemon_log("[_run_voice_session] 语音会话开始")
        try:
            from agent.voice.voice_loop import voice_loop
            asyncio.run(voice_loop(
                ui, self._settings, self._loop, self._ctx,
                daemon_mode=True,
                stop_event=self._voice_session_stop_event,
            ))
        except ImportError as e:
            ui.error(f"语音模块不可用: {e}")
            self._daemon_log("[_run_voice_session] 语音模块导入失败: %s", e)
        except Exception as e:
            ui.error(f"语音会话异常: {type(e).__name__}: {e}")
            self._daemon_log("[_run_voice_session] 语音会话异常: %s: %s", type(e).__name__, e)
        finally:
            self._daemon_log("[_run_voice_session] 语音会话结束")
            self._voice_session_active = False
            try:
                self._tray.update_menu()
            except Exception:
                pass
            # 语音会话结束后增量保存
            try:
                from agent.session_manager import _auto_save
                _auto_save(ui, self._messages,
                           workdir=self._settings.workdir,
                           model=self._settings.model,
                           provider=self._settings.provider,
                           verbose=False,
                           settings=self._settings)
            except Exception:
                pass
            ui.info("💤 回到待命状态")

    def _run_text_session(self) -> None:
        """唤起一次文本对话（单轮，exit 回后台）。"""
        ui = self._ui
        assert ui is not None

        # detached（无窗口）模式下 stdin 不可用，弹出一个新的终端窗口
        # 运行 jarvis REPL（自动恢复上次会话，独立进程，关闭不影响 daemon）
        if _is_detached():
            self._spawner.bring_up()
            return

        assert self._loop is not None and self._ctx is not None
        ui.info("📝 文本对话模式（输入 /back 回后台，/exit 退出贾维斯）")
        while not self._quit_event.is_set():
            try:
                user_input = ui.read_user_input("> ")
            except (EOFError, KeyboardInterrupt):
                break
            if not user_input:
                continue
            stripped = user_input.strip()
            if stripped.lower() in ("/back", "/sleep", "/standby"):
                break
            if stripped.lower() in ("/exit", "/quit"):
                self._quit_event.set()
                break
            # 普通对话
            try:
                asyncio.run(self._loop.run(stripped, self._ctx))
            except KeyboardInterrupt:
                self._ctx.abort_event.set()
                self._ctx = ToolContext(
                    workdir=self._settings.workdir,
                    messages=self._messages,
                    permission_mode=self._settings.permission_mode.value,
                    ui=ui,
                )
            except Exception as e:
                ui.error(f"运行出错: {type(e).__name__}: {e}")
            # 每轮对话后增量保存（防窗口被强杀丢失记忆）
            try:
                from agent.session_manager import _auto_save
                _auto_save(ui, self._messages,
                           workdir=self._settings.workdir,
                           model=self._loop._model or self._settings.model,
                           provider=self._settings.provider,
                           verbose=False,
                           settings=self._settings)
            except Exception:
                pass
        ui.info("💤 回到待命状态")
