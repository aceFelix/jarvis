"""Bridge UI —— 跨设备协同的 UIProtocol 实现。

将 QueryLoop 产生的 UI 事件（assistant_text / tool_use / tool_result / info / warn / error 等）
通过线程安全队列缓冲，再由独立的 stream() 任务异步推送到 WebSocket 客户端（手机 PWA）。

设计要点：
- UIProtocol 回调是同步调用，query 可能在主线程或子线程 loop 中执行，
  所以用线程安全的 queue.Queue 代替 asyncio.Queue，避免跨 loop 问题。
- stream() 通过 run_in_executor 异步读取队列，不阻塞 asyncio loop。
- 工具结果可能很长，截断到 2000 字符避免撑爆手机端。
- ask_user 返回空串：远程手机端不支持阻塞式询问（plan 模式下也不会触发）。

事件格式（推送到 WS 的 JSON 字符串）::

    {"event": "assistant_text",  "data": "你好"}
    {"event": "assistant_thinking", "data": "..."}
    {"event": "tool_use",        "data": {"name": "Bash", "input": {...}, "id": "xxx"}}
    {"event": "tool_result",     "data": {"name": "Bash", "id": "xxx", "content": "...", "is_error": false}}
    {"event": "info" / "warn" / "error", "data": "..."}

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
import queue as _queue
from typing import Any

from agent.core.context import UIProtocol


class BridgeUI:
    """跨设备协同 UI，实现 agent.core.context.UIProtocol。

    所有 UI 事件被序列化为 JSON 字符串放入线程安全队列，由 stream() 任务推送到 WebSocket。
    同时把关键事件（用户消息、助手回复、状态信息）转发给电脑终端 UI，
    让手机和电脑终端都能看到同一段对话。

    生命周期：BridgeServer 每轮 query 创建一个 BridgeUI，query 结束后调用 finish() 收尾。

    @author aceFelix
    """

    def __init__(self, desktop_ui: UIProtocol | None = None) -> None:
        # 线程安全队列：跨线程安全，query 可能在主线程执行而 stream 在子线程读取
        self._queue: "_queue.Queue[str | None]" = _queue.Queue()
        # 电脑终端 UI（RichCLI），用于在电脑端同步显示手机对话
        self._desktop_ui = desktop_ui

    def _emit(self, event: str, data: Any) -> None:
        """同步放入一条 UI 事件到队列（线程安全，任何线程都能调用）。

        同时把关键事件转发给电脑终端 UI，让电脑端同步显示手机对话。

        Args:
            event: 事件名，如 "assistant_text" / "tool_use" / "info" 等。
            data: 事件数据，任意可 JSON 序列化的对象。
        """
        msg = json.dumps({"event": event, "data": data}, ensure_ascii=False)
        self._queue.put(msg)  # 线程安全

        # 转发关键事件到电脑终端 UI（RichCLI），实现手机和电脑同屏
        if self._desktop_ui is not None:
            try:
                if event == "assistant_text":
                    self._desktop_ui.assistant_text(data)
                elif event == "info":
                    self._desktop_ui.info(data)
                elif event == "warn":
                    self._desktop_ui.warn(data)
                elif event == "error":
                    self._desktop_ui.error(data)
                elif event == "ask_user":
                    self._desktop_ui.info(f"[手机端询问] {data}")
            except Exception:
                # 电脑终端 UI 可能处于输入状态，转发失败不阻塞手机端
                pass

    async def stream(self, ws) -> None:
        """异步从队列取事件推送到 WebSocket，直到收到 None 结束信号。

        通过 run_in_executor 读取线程安全队列，不阻塞 asyncio loop。

        Args:
            ws: websockets 连接对象。
        """
        loop = asyncio.get_running_loop()
        try:
            while True:
                # 在线程池中阻塞读取队列，不阻塞 loop
                msg = await loop.run_in_executor(None, self._queue.get)
                if msg is None:  # 结束哨兵：本轮 query 的所有事件已投递完毕
                    break
                await ws.send(msg)
        except Exception:
            # ws 已关闭 / 取消等情况，静默退出
            return

    # ---- UIProtocol 实现 ----

    def user_message(self, text: str) -> None:
        """手机端用户发送的消息：推送到手机 UI 并同步显示在电脑终端。"""
        # 电脑终端显示用户消息（带前缀区分来源）
        if self._desktop_ui is not None:
            try:
                self._desktop_ui.info(f"[手机] {text}")
            except Exception:
                pass
        # 手机端也显示自己发的消息（from=phone 用于前端去重）
        self._emit("user_message", {"text": text, "from": "phone"})

    def assistant_text(self, text: str) -> None:
        """助手正式回复的文本增量（流式）。"""
        self._emit("assistant_text", text)

    def assistant_thinking(self, text: str) -> None:
        """思维链增量（深度思考 / reasoning）。"""
        self._emit("assistant_thinking", text)

    def tool_use(self, tool_name: str, tool_input: dict, tool_use_id: str) -> None:
        """工具调用开始：工具名 + 输入参数 + 调用 ID。"""
        self._emit(
            "tool_use",
            {"name": tool_name, "input": tool_input, "id": tool_use_id},
        )

    def tool_result(
        self,
        tool_name: str,
        tool_use_id: str,
        content: str,
        *,
        is_error: bool = False,
    ) -> None:
        """工具调用结果。超长内容截断到 2000 字符，避免撑爆手机端消息流。"""
        self._emit(
            "tool_result",
            {
                "name": tool_name,
                "id": tool_use_id,
                "content": (content or "")[:2000],
                "is_error": is_error,
            },
        )

    def info(self, text: str) -> None:
        """普通信息提示（如"正在思考..."）。"""
        self._emit("info", text)

    def warn(self, text: str) -> None:
        """警告信息。"""
        self._emit("warn", text)

    def error(self, text: str) -> None:
        """错误信息。"""
        self._emit("error", text)

    def ask_user(self, prompt: str) -> str:
        """阻塞式询问 —— 远程手机端不支持阻塞交互。

        plan 权限模式下不会触发权限询问；此处仅投递事件供前端展示提示，
        并立即返回空串，避免 query_loop 卡死。

        Args:
            prompt: 询问提示文本。

        Returns:
            固定返回空串。
        """
        self._emit("ask_user", prompt)
        return ""

    def finish(self) -> None:
        """查询完成后投递结束哨兵，通知 stream() 任务退出。

        在 BridgeServer._run_query 的 finally 中调用，确保所有 UI 事件已被消费。
        """
        self._queue.put(None)  # 线程安全


class BroadcastUI:
    """广播 UI：同时更新电脑终端 UI 和手机端 WebSocket。

    用于电脑端在终端输入消息时，把对话过程同步推送给已连接的手机端。
    包装一个主 UI（RichCLI）和一个 BridgeServer，实现 UIProtocol。

    @author aceFelix
    """

    def __init__(self, desktop_ui: UIProtocol, bridge: Any) -> None:
        self._desktop_ui = desktop_ui
        self._bridge = bridge

    def _broadcast(self, event: str, data: Any) -> None:
        """把事件广播给手机端（线程安全）。"""
        try:
            self._bridge.broadcast(event, data)
        except Exception:
            pass

    def assistant_text(self, text: str) -> None:
        """助手回复文本：电脑终端流式显示 + 手机端同步。"""
        self._desktop_ui.assistant_text(text)
        self._broadcast("assistant_text", text)

    def assistant_thinking(self, text: str) -> None:
        """思维链：只显示在电脑终端，不同步到手机（避免手机端信息过载）。"""
        self._desktop_ui.assistant_thinking(text)

    def tool_use(self, tool_name: str, tool_input: dict, tool_use_id: str) -> None:
        """工具调用：电脑终端显示 + 手机端同步（手机端默认折叠）。"""
        self._desktop_ui.tool_use(tool_name, tool_input, tool_use_id)
        self._broadcast("tool_use", {"name": tool_name, "input": tool_input, "id": tool_use_id})

    def tool_result(
        self,
        tool_name: str,
        tool_use_id: str,
        content: str,
        *,
        is_error: bool = False,
    ) -> None:
        """工具结果：电脑终端显示 + 手机端同步（截断）。"""
        self._desktop_ui.tool_result(tool_name, tool_use_id, content, is_error=is_error)
        self._broadcast(
            "tool_result",
            {"name": tool_name, "id": tool_use_id, "content": (content or "")[:2000], "is_error": is_error},
        )

    def info(self, text: str) -> None:
        """普通信息：两端同步。"""
        self._desktop_ui.info(text)
        self._broadcast("info", text)

    def warn(self, text: str) -> None:
        """警告：两端同步。"""
        self._desktop_ui.warn(text)
        self._broadcast("warn", text)

    def error(self, text: str) -> None:
        """错误：两端同步。"""
        self._desktop_ui.error(text)
        self._broadcast("error", text)

    def ask_user(self, prompt: str) -> str:
        """阻塞询问：只走电脑终端，手机端显示提示但不阻塞。"""
        self._broadcast("ask_user", prompt)
        return self._desktop_ui.ask_user(prompt)

    def user_message(self, text: str) -> None:
        """用户消息：同步到手机端（电脑终端由调用方提前显示）。"""
        self._broadcast("user_message", {"text": text, "from": "desktop"})

    def finish(self) -> None:
        """电脑端查询结束：同步 done 事件到手机端。"""
        self._broadcast("done", None)
