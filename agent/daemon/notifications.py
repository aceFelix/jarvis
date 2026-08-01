"""daemon 事件通知处理 —— NotificationMixin。

集中 JarvisDaemon 的外部事件回调：定时任务、主动提醒、系统监控告警、视觉事件。
统一走「终端日志 + 托盘通知 + 待机语音播报」通道。

从原 daemon.py 拆分而来，由 JarvisDaemon 混入。

@author aceFelix
"""

from __future__ import annotations


class NotificationMixin:
    """事件回调混入：调度 / 主动提醒 / 监控 / 视觉。"""

    def _on_schedule_fire(self, task) -> None:
        """定时任务到期回调：托盘通知 + 语音播报。

        在 Scheduler 后台线程触发。语音播报通过 TTS 异步播放，
        不阻塞调度器。如果贾维斯正在对话中，仅托盘通知不打断。

        P2-3: ProactiveEngine 注册的任务由引擎自己处理（生成简报/检查截止日期）。
        """
        # P2-3: ProactiveEngine 任务分发
        if self._proactive.is_proactive_task(task):
            self._proactive.handle_task_fire(task)
            return

        ui = self._ui
        if ui:
            ui.info(f"⏰ 定时提醒: {task.content}")

        # 托盘通知
        try:
            if self._tray and self._tray.available:
                self._tray.notify("贾维斯提醒", task.content)
        except Exception:
            pass

        # 语音播报（仅当不在对话中时，避免打断正在进行的语音对话）
        try:
            if not self._wake_event.is_set():
                # 待机中，用 TTS 播报提醒
                import threading as _t
                def _speak():
                    try:
                        from agent.voice.tts import CosyVoiceTTS
                        tts = CosyVoiceTTS(
                            api_key=self._settings.api_key,
                            model=self._settings.tts_model,
                            voice=self._settings.tts_voice,
                        )
                        tts.speak(f"先生，提醒您：{task.content}")
                    except Exception:
                        pass
                _t.Thread(target=_speak, daemon=True).start()
        except Exception:
            pass

    def _on_proactive_notify(self, message: str) -> None:
        """主动感知引擎通知回调：托盘通知 + 语音播报。

        用于每日简报、截止日期提醒、日历事件提醒等。
        与普通提醒相同的播报通道，但内容更丰富。
        """
        ui = self._ui
        if ui:
            # 取第一行作为日志摘要
            first_line = message.split("\n")[0][:60]
            ui.info(f"📋 {first_line}")

        # 托盘通知（用第一行作为摘要）
        try:
            if self._tray and self._tray.available:
                summary = message.split("\n")[0][:100]
                self._tray.notify("贾维斯主动提醒", summary)
        except Exception:
            pass

        # 语音播报（仅待机时）
        try:
            if not self._wake_event.is_set():
                import threading as _t
                def _speak():
                    try:
                        from agent.voice.tts import CosyVoiceTTS
                        tts = CosyVoiceTTS(
                            api_key=self._settings.api_key,
                            model=self._settings.tts_model,
                            voice=self._settings.tts_voice,
                        )
                        # 简报较长，只播报前 200 字
                        speak_text = message[:200]
                        if len(message) > 200:
                            speak_text += "……详细内容请查看托盘通知。"
                        tts.speak(speak_text)
                    except Exception:
                        pass
                _t.Thread(target=_speak, daemon=True).start()
        except Exception:
            pass

    def _on_monitor_alert(self, alert) -> None:
        """系统监控告警回调：托盘通知 + 语音告警。"""
        ui = self._ui
        if ui:
            level_icon = "⚠️" if alert.level == "warning" else "🚨"
            ui.info(f"{level_icon} 系统告警 [{alert.alert_type}]: {alert.message}")

        # 托盘通知
        try:
            if self._tray and self._tray.available:
                title = "系统告警" if alert.level != "recovery" else "状态恢复"
                self._tray.notify(title, alert.message)
        except Exception:
            pass

        # 语音告警（仅严重告警才语音，recovery 不打扰）
        if alert.level == "critical":
            try:
                if not self._wake_event.is_set():
                    import threading as _t
                    def _speak():
                        try:
                            from agent.voice.tts import CosyVoiceTTS
                            tts = CosyVoiceTTS(
                                api_key=self._settings.api_key,
                                model=self._settings.tts_model,
                                voice=self._settings.tts_voice,
                            )
                            tts.speak(f"先生，{alert.message}")
                        except Exception:
                            pass
                    _t.Thread(target=_speak, daemon=True).start()
            except Exception:
                pass

    def _on_vision_event(self, event) -> None:
        """视觉监控事件回调：托盘通知 + 语音播报。

        在 VisionWatcher 后台线程触发。手势/人脸事件 → 托盘通知 + TTS 播报。
        AUTO_STOPPED 事件 → 通知用户监控已自动关闭。
        仅待机中才语音播报，不打断正在进行的对话。
        """
        ui = self._ui
        if ui:
            icon = {
                "gesture": "👆",
                "face_appear": "👤",
                "face_disappear": "👋",
                "auto_stopped": "⏹️",
            }.get(event.event_type.value, "👁️")
            ui.info(f"{icon} 视觉事件: {event.description}")

        # 托盘通知
        try:
            if self._tray and self._tray.available:
                self._tray.notify("贾维斯视觉", event.description)
        except Exception:
            pass

        # 语音播报（仅待机中，不打断对话）
        try:
            if not self._wake_event.is_set():
                import threading as _t
                def _speak():
                    try:
                        from agent.voice.tts import CosyVoiceTTS
                        tts = CosyVoiceTTS(
                            api_key=self._settings.api_key,
                            model=self._settings.tts_model,
                            voice=self._settings.tts_voice,
                        )
                        tts.speak(f"先生，{event.description}")
                    except Exception:
                        pass
                _t.Thread(target=_speak, daemon=True).start()
        except Exception:
            pass
