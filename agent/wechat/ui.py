"""WeChat UI —— 微信渠道的 UIProtocol 实现。

收集 QueryLoop 产出的 UI 事件，拼接 assistant_text 为完整回复文本。
工具调用等过程信息可选地生成简短状态文本，但不发送到微信（避免刷屏）。

设计要点：
- QueryLoop 的 UI 回调是同步的，WeChatUI 只做简单的字符串拼接。
- 最终通过 get_reply() 获取完整回复，由 WeChatBridge 发送到微信。
- 同时把关键事件转发给电脑终端 UI，让终端能看到微信端的对话。

@author aceFelix
"""

from __future__ import annotations

from typing import Any


class WeChatUI:
    """微信渠道 UI，实现 agent.core.context.UIProtocol。

    收集 assistant_text 拼接为完整回复。工具调用过程记录但不发到微信。
    同时转发关键事件到电脑终端 UI（RichCLI），实现终端同步显示微信对话。

    生命周期：WeChatBridge 每轮 query 创建一个 WeChatUI，query 结束后调 get_reply()。

    @author aceFelix
    """

    def __init__(self, desktop_ui: Any = None) -> None:
        """
        Args:
            desktop_ui: 电脑终端 UI（RichCLI），用于同步显示微信对话。可选。
        """
        self._reply_parts: list[str] = []
        self._desktop_ui = desktop_ui

    def get_reply(self) -> str:
        """获取拼接好的完整回复文本。"""
        return "".join(self._reply_parts).strip()

    # ---- UIProtocol 实现 ----

    def user_message(self, text: str) -> None:
        """微信用户发送的消息：在电脑终端显示。"""
        if self._desktop_ui is not None:
            try:
                self._desktop_ui.info(f"[微信] {text}")
            except Exception:
                pass

    def assistant_text(self, text: str) -> None:
        """助手回复文本增量（流式）：拼接收集 + 终端同步。"""
        self._reply_parts.append(text)
        if self._desktop_ui is not None:
            try:
                self._desktop_ui.assistant_text(text)
            except Exception:
                pass

    def assistant_thinking(self, text: str) -> None:
        """思维链增量：只显示在终端，不发微信。"""
        if self._desktop_ui is not None:
            try:
                self._desktop_ui.assistant_thinking(text)
            except Exception:
                pass

    def tool_use(self, tool_name: str, tool_input: dict, tool_use_id: str) -> None:
        """工具调用开始：终端显示，微信不发。"""
        if self._desktop_ui is not None:
            try:
                self._desktop_ui.tool_use(tool_name, tool_input, tool_use_id)
            except Exception:
                pass

    def tool_result(
        self,
        tool_name: str,
        tool_use_id: str,
        content: str,
        *,
        is_error: bool = False,
    ) -> None:
        """工具调用结果：终端显示，微信不发。"""
        if self._desktop_ui is not None:
            try:
                self._desktop_ui.tool_result(
                    tool_name, tool_use_id, content, is_error=is_error
                )
            except Exception:
                pass

    def info(self, text: str) -> None:
        """普通信息：终端显示。"""
        if self._desktop_ui is not None:
            try:
                self._desktop_ui.info(text)
            except Exception:
                pass

    def warn(self, text: str) -> None:
        """警告：终端显示。"""
        if self._desktop_ui is not None:
            try:
                self._desktop_ui.warn(text)
            except Exception:
                pass

    def error(self, text: str) -> None:
        """错误：终端显示 + 记入回复（让用户知道出错了）。"""
        if self._desktop_ui is not None:
            try:
                self._desktop_ui.error(text)
            except Exception:
                pass
        # 错误信息也发到微信，让用户知道
        if not self._reply_parts:
            self._reply_parts.append(f"[处理出错] {text}")

    def ask_user(self, prompt: str) -> str:
        """阻塞式询问：微信渠道不支持交互式询问，返回空串。"""
        if self._desktop_ui is not None:
            try:
                self._desktop_ui.info(f"[微信端询问] {prompt}")
            except Exception:
                pass
        return ""
