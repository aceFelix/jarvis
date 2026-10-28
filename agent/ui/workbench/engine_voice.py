"""ChatEngine 的语音播报（voice）编排（从 engine.py 拆出）。

与 engine_talk 的全双工对话不同，这一组管的是「把回答念出来」这条单向通路：
``_handle_start_voice`` 起播报、``_handle_stop_voice`` 停播报、
``_handle_interrupt_voice`` 打断、``_stop_voice_watchers`` 收掉随播报起的看守协程。

拆分动机：engine.py 原 1252 行、单类 46 个方法。方法体逐行原样搬迁，
行为与拆分前一致；以 mixin 形式挂回 ChatEngine，调用方无需改动。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import threading
from agent.ui.workbench.voice_adapter import (  # noqa: F401
    _EVT_VOICE_STARTED,
    _EVT_VOICE_STOPPED,
    ServeVoiceAdapter,
)


class EngineVoiceMixin:
    """语音播报启停/打断与看守协程回收的方法组（原样搬自 ChatEngine）。"""

    # ---- /voice 半双工语音 ----

    async def _handle_start_voice(self) -> None:
        """启动 /voice 半双工：独立 asyncio task 跑解耦 voice_loop。

        与 /talk 互斥（先停实时语音）；麦克风 barge-in watcher 挂接
        interrupt_event（播报中开口自动打断），桌面壳按钮另走
        interrupt_voice 指令置位同一 event（双通道）。

        @author aceFelix
        """
        if getattr(self, "_voice_task", None) is not None:
            self._emitter.emit("info", "半双工语音已在运行")
            return
        # 与 /talk 互斥：进入半双工前先停实时语音，避免麦克风/扬声器冲突
        if getattr(self, "_talk_task", None) is not None:
            await self._handle_stop_talk()
        await self._ensure_session()
        s = self._settings
        adapter = ServeVoiceAdapter(self._emitter)
        self._voice_interrupt = threading.Event()
        self._voice_stop = threading.Event()
        # 麦克风 barge-in（双通道之一）：TTS 播报中检测用户开口自动打断
        self._voice_mic_watcher = None
        if getattr(s, "voice_barge_in", False):
            try:
                from agent.voice.barge_in import _BargeInWatcher
                w = _BargeInWatcher(lambda: self._voice_interrupt.set())
                if getattr(w, "available", True):
                    w.start()
                    self._voice_mic_watcher = w
            except Exception:
                self._voice_mic_watcher = None

        async def _voice_main() -> None:
            try:
                from agent.voice.voice_loop import voice_loop
                await voice_loop(
                    adapter, s, self._query_loop, self._ctx,
                    stop_event=self._voice_stop,
                    interrupt_event=self._voice_interrupt,
                    # 每轮语音对话落库：ctx.messages 即 self._messages（同一
                    # 列表），轮次结束走与文本对话同一套自动保存/标题生成——
                    # 此前半双工语音只写内存不存盘，重启即丢（2026-10 修复）。
                    on_turn_end=self._after_turn,
                )
            except Exception as e:
                self._emitter.emit("error", f"半双工语音异常: {type(e).__name__}: {e}")
            finally:
                self._voice_task = None
                self._stop_voice_watchers()
                self._emitter.emit(_EVT_VOICE_STOPPED, "")

        self._voice_task = asyncio.get_event_loop().create_task(_voice_main())
        self._emitter.emit(_EVT_VOICE_STARTED, "")

    async def _handle_stop_voice(self) -> None:
        """停止半双工语音：置 stop_event + 中断阻塞录音，让 voice_loop 干净退出。"""
        stop_ev = getattr(self, "_voice_stop", None)
        if stop_ev is not None:
            stop_ev.set()
        # 中断可能阻塞在 pyaudio 的 stt.listen()（C 扩展不吃 asyncio 取消）
        try:
            from agent.voice import stt as stt_module
            stt_module._request_stop()
        except Exception:
            pass
        task = getattr(self, "_voice_task", None)
        if task is not None:
            # 给 voice_loop 一点时间走干净退出（释放语音锁 / 音频）
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
            except Exception:
                task.cancel()
            self._voice_task = None
        self._stop_voice_watchers()
        self._emitter.emit(_EVT_VOICE_STOPPED, "")

    async def _handle_interrupt_voice(self) -> bool:
        """打断当前播报 / 推理（桌面壳按钮通道）：置位 interrupt_event。"""
        intr = getattr(self, "_voice_interrupt", None)
        if intr is None:
            return False
        intr.set()
        # 聆听阶段打断：同时中断阻塞的 stt.listen()
        try:
            from agent.voice import stt as stt_module
            stt_module._request_stop()
        except Exception:
            pass
        return True

    def _stop_voice_watchers(self) -> None:
        """停止麦克风 barge-in watcher（voice 退出时回收）。"""
        w = getattr(self, "_voice_mic_watcher", None)
        if w is not None:
            try:
                w.stop()
            except Exception:
                pass
            self._voice_mic_watcher = None
