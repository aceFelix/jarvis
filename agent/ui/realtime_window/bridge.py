"""WebviewRealtimeTalkUI —— RealtimeTalkUI 的 pywebview 实现。

将 RealtimeTalk 产生的事件通过队列转发到独立窗口前端，
驱动方舟反应炉动画与聊天气泡更新。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent.core.context import RealtimeTalkUI
from agent.ui.realtime_window.window import RealtimeTalkWindow


class WebviewRealtimeTalkUI(RealtimeTalkUI):
    """RealtimeTalk 的窗口 UI 实现。

    所有 on_* 方法把事件推入 RealtimeTalkWindow 的事件队列，
    由前端 JS 轮询拉取并更新界面。

    @author aceFelix
    """

    def __init__(
        self,
        window: RealtimeTalkWindow,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._window = window
        self._loop = loop

    # ------------------------------------------------------------------
    # RealtimeTalkUI 扩展方法
    # ------------------------------------------------------------------

    def on_status(self, status: str) -> None:
        """转发状态变化事件。"""
        self._window.emit("status", status)

    def on_volume(self, level: float) -> None:
        """转发音量事件。"""
        self._window.emit("volume", level)

    def on_user_speaking(self, speaking: bool) -> None:
        """转发用户说话状态。"""
        self._window.emit("user_speaking", speaking)

    def on_ai_speaking(self, speaking: bool) -> None:
        """转发 AI 说话状态。"""
        self._window.emit("ai_speaking", speaking)

    def on_user_transcript(self, text: str) -> None:
        """转发用户转录文本。"""
        self._window.emit("user_transcript", text)

    def on_ai_transcript(self, text: str) -> None:
        """转发 AI 转录文本（完整）。"""
        self._window.emit("ai_transcript", text)

    def on_ai_transcript_delta(self, text: str) -> None:
        """转发 AI 转录文本增量（流式）。"""
        self._window.emit("ai_transcript_delta", text)

    def is_running(self) -> bool:
        """窗口未关闭即认为 UI 仍在运行。"""
        return self._window.is_open

    # ------------------------------------------------------------------
    # UIProtocol 基础方法（RealtimeTalk 中仍会调用 info/warn/error）
    # ------------------------------------------------------------------

    def assistant_text(self, text: str) -> None:
        """普通文本输出映射为 AI 气泡。"""
        self._window.emit("ai_transcript", text)

    def assistant_thinking(self, text: str) -> None:
        """思考链内容暂不显示在实时聊天窗口。"""
        pass

    def tool_use(self, tool_name: str, tool_input: dict[str, Any], tool_use_id: str) -> None:
        """实时语音对话中不展示工具调用细节。"""
        pass

    def tool_result(
        self, tool_name: str, tool_use_id: str, content: str, *, is_error: bool = False
    ) -> None:
        """实时语音对话中不展示工具结果。"""
        pass

    def info(self, text: str) -> None:
        """普通信息：仅显示非转录类提示。

        用户/AI 转录文本现在统一通过 ``on_user_transcript`` /
        ``on_ai_transcript`` 处理，避免 info() 中再次解析产生重复气泡。
        """
        # 启动/结束提示、空行等不显示为气泡
        if not text or text.startswith("🎙️") or text.startswith("已退出") or text.startswith("="):
            return
        # 其余信息作为 AI 系统提示显示
        self._window.emit("ai_transcript", text)

    def warn(self, text: str) -> None:
        """警告信息以错误样式显示。"""
        self._window.emit("error", text)

    def error(self, text: str) -> None:
        """错误信息显示为系统消息。"""
        self._window.emit("error", text)

    def ask_user(self, prompt: str) -> str:
        """实时语音对话不需要阻塞式询问，返回空字符串。"""
        return ""
