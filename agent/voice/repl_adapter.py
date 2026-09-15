"""REPL 语音适配器 —— 把解耦后的 voice_loop 事件映射回终端 RichCLI。

``/voice`` 命令的宿主适配器：实现 ``VoiceSessionEvents`` 协议，将状态 /
转录 / 提示事件还原为终端输出与 RichCLI 内部渲染状态（``_voice_mode`` /
``_thinking_*``），保持解耦前 ``/voice`` 的原有体验（测试保底）。

键盘 ESC 打断也由本适配器挂接：创建 ``_KeyBargeInWatcher``，回调中中断
阻塞的 STT 录音（``stt._request_stop``）并置位外部 ``interrupt_event``，
由 voice_loop 轮询该 event 触发即时打断（停 TTS / 中止 LLM）。

serve / 桌面壳路径不使用本模块（用 engine.ServeVoiceAdapter）。

@author aceFelix
"""

from __future__ import annotations

import sys
import threading
from typing import Any

from agent.ui.cli import RichCLI
from agent.voice import stt as stt_module
from agent.voice.barge_in import _KeyBargeInWatcher
from agent.voice.voice_events import (
    LEVEL_WARN,
    STATE_THINKING,
    VoiceEventsBase,
)


class RichCLIVoiceAdapter(VoiceEventsBase):
    """终端宿主适配器：events → RichCLI 输出 + 内部渲染状态。"""

    def __init__(self, ui: RichCLI) -> None:
        self._ui = ui
        self._key_watcher: _KeyBargeInWatcher | None = None

    # ---- VoiceSessionEvents 实现 ----

    def on_state(self, state: str) -> None:
        """状态迁移 → 同步 RichCLI 语音渲染开关。

        thinking 阶段开启 ``_voice_mode``（思考走 TTS 简短提示、终端精简），
        并重置思考 Live 渲染状态；其余状态关闭 ``_voice_mode`` 恢复常规渲染。
        """
        ui = self._ui
        if state == STATE_THINKING:
            ui._voice_mode = True
            ui._voice_tts_feed = None
            ui._thinking_started = False
            if getattr(ui, "_thinking_live", None) is not None:
                try:
                    ui._thinking_live.stop()
                except Exception:
                    pass
                ui._thinking_live = None
            ui._thinking_buf = ""
        else:
            ui._voice_mode = False
            ui._voice_tts_feed = None

    def on_user_partial(self, text: str) -> None:
        """识别增量 → 终端同行刷新进度；空文本表示清行（聆听结束/中断）。"""
        if not text:
            self.clear_partial_line()
            return
        sys.stdout.write(f"\r  识别中: {text}")
        sys.stdout.flush()

    def on_user_transcript(self, text: str) -> None:
        """识别完成 → 清进度行并打印用户文本。"""
        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()
        self._ui.info(f"🧑 你说: {text}")

    def on_info(self, msg: str, level: str = "info") -> None:
        """提示 / 告警 → RichCLI info / warn。"""
        if level == LEVEL_WARN:
            self._ui.warn(msg)
        else:
            self._ui.info(msg)

    def on_error(self, msg: str) -> None:
        """错误 → RichCLI error。"""
        self._ui.error(msg)

    # ---- 键盘 ESC 打断挂接 ----

    def start_key_watcher(self, interrupt_event: threading.Event) -> bool:
        """创建并启动键盘 ESC 监听，回调置位 interrupt_event。

        回调同时中断阻塞中的 ``stt.listen()``（pyaudio C 阻塞无法被
        asyncio 信号打断），使聆听阶段也能响应 ESC。

        Returns:
            keyboard 库可用且监听已启动返回 True，否则 False。
        """
        def _on_esc() -> None:
            stt_module._request_stop()  # 中断阻塞中的 stt.listen()
            interrupt_event.set()      # 通知 voice_loop 即时打断 TTS/LLM

        watcher = _KeyBargeInWatcher(_on_esc)
        if not watcher.available:
            return False
        watcher.start()
        self._key_watcher = watcher
        return True

    def stop_key_watcher(self) -> None:
        """停止键盘 ESC 监听（voice_loop 退出后调用）。"""
        if self._key_watcher is not None:
            try:
                self._key_watcher.stop()
            except Exception:
                pass
            self._key_watcher = None

    # ---- 兼容别名：供 voice_loop 清理 stdout 进度行 ----

    def clear_partial_line(self) -> None:
        """清除终端识别进度行（聆听中断 / 退出时调用）。"""
        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()


def make_repl_adapter(ui: Any) -> RichCLIVoiceAdapter:
    """构造 REPL 适配器（工厂，便于 voice_commands 注入与测试替换）。"""
    return RichCLIVoiceAdapter(ui)
