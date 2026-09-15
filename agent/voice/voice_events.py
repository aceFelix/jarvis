"""语音会话事件协议 —— /voice 半双工循环与宿主 UI 的解耦边界。

voice_loop（STT→LLM→TTS）不再直接依赖终端 RichCLI，而是把一切对外输出
收敛到本模块定义的 ``VoiceSessionEvents`` 回调协议（sink）。不同宿主各自
提供适配器实现该协议：

- REPL 适配器（``repl_adapter.RichCLIVoiceAdapter``）：把事件映射回终端
  RichCLI 输出与内部渲染状态，保持 ``/voice`` 命令原有体验；
- serve 适配器（``agent.ui.workbench.engine.ServeVoiceAdapter``）：把事件
  转成 WS 事件外抛给桌面壳（jarvis-desktop），实现图形化半双工语音。

状态机（``on_state`` 的取值）::

    dialog     进入对话阶段（连续 听→想→说 循环）
    listening  正在录音识别
    thinking   LLM 流式推理中
    speaking   TTS 播报中
    standby    退下待机（等唤醒词）
    exited     语音会话彻底结束

@author aceFelix
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# ---- 状态常量（on_state 取值） ----
STATE_DIALOG = "dialog"
STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"
STATE_STANDBY = "standby"
STATE_EXITED = "exited"

# ---- 信息级别（on_info 的 level 取值） ----
LEVEL_INFO = "info"
LEVEL_WARN = "warn"


@runtime_checkable
class VoiceSessionEvents(Protocol):
    """语音会话对外输出协议（宿主适配器实现）。

    所有方法均应为非阻塞轻量操作（实现方自行决定投递到终端 / 事件队列）。
    """

    def on_state(self, state: str) -> None:
        """状态迁移通知（取值见 STATE_* 常量）。"""
        ...

    def on_user_partial(self, text: str) -> None:
        """STT 识别中的增量文本（终端用于刷新进度行；GUI 可忽略）。"""
        ...

    def on_user_transcript(self, text: str) -> None:
        """一轮识别完成的最终用户文本。"""
        ...

    def on_ai_text_delta(self, text: str) -> None:
        """LLM 回复的流式文本增量（与喂 TTS 的同源）。"""
        ...

    def on_ai_text(self, text: str) -> None:
        """一轮回复的完整文本（流式结束后的全量校验）。"""
        ...

    def on_info(self, msg: str, level: str = LEVEL_INFO) -> None:
        """提示 / 告警信息（level: info / warn）。"""
        ...

    def on_error(self, msg: str) -> None:
        """错误信息（识别失败 / 模块不可用等）。"""
        ...


class VoiceEventsBase:
    """``VoiceSessionEvents`` 的空实现基类。

    适配器继承后只覆盖关心的回调，其余静默忽略，避免实现全部方法。
    """

    def on_state(self, state: str) -> None:  # noqa: D102
        pass

    def on_user_partial(self, text: str) -> None:  # noqa: D102
        pass

    def on_user_transcript(self, text: str) -> None:  # noqa: D102
        pass

    def on_ai_text_delta(self, text: str) -> None:  # noqa: D102
        pass

    def on_ai_text(self, text: str) -> None:  # noqa: D102
        pass

    def on_info(self, msg: str, level: str = LEVEL_INFO) -> None:  # noqa: D102
        pass

    def on_error(self, msg: str) -> None:  # noqa: D102
        pass


class CollectingVoiceEvents(VoiceEventsBase):
    """把事件收集到内存列表的实现（单测 / 调试用）。

    ``records`` 为 ``(kind, payload)`` 序列，便于断言事件顺序。
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, object]] = []

    def _rec(self, kind: str, payload: object) -> None:
        self.records.append((kind, payload))

    def on_state(self, state: str) -> None:  # noqa: D102
        self._rec("state", state)

    def on_user_partial(self, text: str) -> None:  # noqa: D102
        self._rec("user_partial", text)

    def on_user_transcript(self, text: str) -> None:  # noqa: D102
        self._rec("user_transcript", text)

    def on_ai_text_delta(self, text: str) -> None:  # noqa: D102
        self._rec("ai_text_delta", text)

    def on_ai_text(self, text: str) -> None:  # noqa: D102
        self._rec("ai_text", text)

    def on_info(self, msg: str, level: str = LEVEL_INFO) -> None:  # noqa: D102
        self._rec("info", (msg, level))

    def on_error(self, msg: str) -> None:  # noqa: D102
        self._rec("error", msg)

    def states(self) -> list[str]:
        """返回按序的 state 事件值列表（断言状态迁移用）。"""
        return [p for k, p in self.records if k == "state"]
