"""工作台 UI 桥接：UIProtocol / RealtimeTalkUI 的事件队列实现。

QueryLoop 与 RealtimeTalk 只依赖 UI 协议，不感知 GUI 细节：
两个适配器把所有 UI 调用转成事件推入队列，前端 JS 轮询渲染。

事件类型约定（与前端 app.js 一一对应）：

文本对话（QueryLoop）：
- assistant_text / assistant_thinking / tool_use / tool_result
- info / warn / error / ask_user（询问转前端弹窗）
- assistant_done（一轮结束，前端收尾气泡）

实时语音（RealtimeTalk，沿用 realtime_window 协议）：
- status / volume / user_speaking / ai_speaking
- user_transcript / ai_transcript / ai_transcript_delta

@author aceFelix
"""

from __future__ import annotations

import queue
import threading
from typing import Any


class _EventEmitter:
    """事件队列的最小封装：统一 {"type","payload"} 结构入队。"""

    def __init__(self, event_queue: queue.Queue[dict[str, Any]]) -> None:
        self._event_queue = event_queue

    def emit(self, event_type: str, payload: Any) -> None:
        try:
            self._event_queue.put_nowait({"type": event_type, "payload": payload})
        except Exception:
            pass


class WorkbenchUI:
    """QueryLoop 的 GUI 适配器（实现 UIProtocol）。

    assistant_text 的流式增量与工具事件逐条推给前端；
    ``ask_user`` 阻塞等待前端回答（通过 ``answer_user`` 回填）。

    @author aceFelix
    """

    def __init__(self, emitter: _EventEmitter) -> None:
        self._emit = emitter.emit
        # ask_user 的跨线程握手：前端回答后 set
        self._answer_event = threading.Event()
        self._answer_text = ""

    # ---- UIProtocol 基础方法 ----

    def assistant_text(self, text: str) -> None:
        """流式文本增量：前端追加到当前 AI 气泡。"""
        self._emit("assistant_text", text)

    def assistant_thinking(self, text: str) -> None:
        """思考链增量：前端渲染为浅色思考块。"""
        self._emit("assistant_thinking", text)

    def tool_use(self, tool_name: str, tool_input: dict[str, Any], tool_use_id: str) -> None:
        """工具调用开始：前端渲染可折叠的工具卡片。"""
        # tool_input 可能含不可 JSON 序列化对象，降级为 str
        try:
            import json

            json.dumps(tool_input)
            payload_input = tool_input
        except Exception:
            payload_input = {k: str(v) for k, v in tool_input.items()}
        self._emit("tool_use", {"name": tool_name, "input": payload_input, "id": tool_use_id})

    def tool_result(
        self, tool_name: str, tool_use_id: str, content: str, *, is_error: bool = False
    ) -> None:
        """工具结果：前端填充对应工具卡片内容。"""
        self._emit(
            "tool_result",
            {"name": tool_name, "id": tool_use_id, "content": content, "is_error": is_error},
        )

    def info(self, text: str) -> None:
        self._emit("info", text)

    def warn(self, text: str) -> None:
        self._emit("warn", text)

    def error(self, text: str) -> None:
        self._emit("error", text)

    def ask_user(self, prompt: str) -> str:
        """阻塞式询问：推事件给前端弹窗，等待 answer_user 回填。

        超时 10 分钟兜底返回空串，避免引擎线程永久挂起。
        """
        self._answer_event.clear()
        self._answer_text = ""
        self._emit("ask_user", prompt)
        self._answer_event.wait(timeout=600)
        return self._answer_text

    def answer_user(self, text: str) -> None:
        """前端回答 ask_user 弹窗后由 JSBridge 调用。"""
        self._answer_text = text
        self._answer_event.set()

    # ---- 对话轮次收尾 ----

    def assistant_done(self) -> None:
        """一轮对话结束：前端把累积的增量气泡定稿。

        由引擎在 loop.run() 返回后显式调用（非 UIProtocol 方法）。
        """
        self._emit("assistant_done", "")


class WorkbenchRealtimeUI(WorkbenchUI):
    """RealtimeTalk 的 GUI 适配器（实现 RealtimeTalkUI 扩展）。

    继承 WorkbenchUI 复用基础方法，追加实时对话的状态/音量/转录回调。
    事件类型与 realtime_window 保持一致，前端可复用同一套渲染分支。

    @author aceFelix
    """

    def on_status(self, status: str) -> None:
        """状态变化：connecting / standby / listening / speaking / error。"""
        self._emit("status", status)

    def on_volume(self, level: float) -> None:
        """麦克风音量（0-1）：驱动反应炉波纹抖动。"""
        self._emit("volume", level)

    def on_user_speaking(self, speaking: bool) -> None:
        self._emit("user_speaking", speaking)

    def on_ai_speaking(self, speaking: bool) -> None:
        """AI 说话状态：驱动波纹律动（说话时波纹加速）。"""
        self._emit("ai_speaking", speaking)

    def on_user_transcript(self, text: str) -> None:
        self._emit("user_transcript", text)

    def on_ai_transcript(self, text: str) -> None:
        self._emit("ai_transcript", text)

    def on_ai_transcript_delta(self, text: str) -> None:
        self._emit("ai_transcript_delta", text)

    def is_running(self) -> bool:
        """窗口存活判断由引擎外部控制，这里恒真（窗口关闭时引擎自行停止）。"""
        return True

    # 实时语音模式下 info() 只保留有效提示，避免转录文本重复成气泡
    def info(self, text: str) -> None:
        if not text or text.startswith("🎙️") or text.startswith("已退出") or text.startswith("="):
            return
        self._emit("info", text)
