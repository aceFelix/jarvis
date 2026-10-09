"""工作台 UI 桥接：UIProtocol / RealtimeTalkUI 的事件队列实现。

QueryLoop 与 RealtimeTalk 只依赖 UI 协议，不感知 GUI 细节：
两个适配器把所有 UI 调用转成事件推入队列，前端 JS 轮询渲染。

事件类型约定（与前端 app.js 一一对应）：

文本对话（QueryLoop）：
- assistant_text / assistant_thinking / tool_use / tool_result
- info / warn / error / ask_user（询问转前端弹窗）
- assistant_done（一轮结束，前端收尾气泡）

实时语音（RealtimeTalk，沿用原 realtime_window 事件协议——该窗口包已于
2026-09 删除，协议由工作台与 serve/桌面壳继续沿用）：
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

    def remote_user_message(self, channel: str, text: str) -> None:
        """远端通道（手机 / 微信）用户消息：前端按普通用户气泡渲染并标注来源。

        与本地 ``user_message``（前端发送时已自行上屏、事件被跳过防双气泡）不同，
        远端消息桌面端没有本地回显，需专门事件驱动一条带来源标记（微信 / 手机）的
        用户气泡，而非居中系统提示。@author aceFelix
        """
        self._emit("remote_user_message", {"channel": channel, "text": text})

    def ask_user(self, prompt: str) -> str:
        """阻塞式询问：推事件给前端弹窗，等待 answer_user 回填。

        超时 10 分钟兜底返回空串，避免引擎线程永久挂起。
        仅供同步宿主（REPL 等）使用；异步宿主（serve 引擎循环）必须走
        ask_user_async，否则阻塞等待会饿死宿主事件循环。
        """
        self._answer_event.clear()
        self._answer_text = ""
        self._emit("ask_user", prompt)
        self._answer_event.wait(timeout=600)
        return self._answer_text

    async def ask_user_async(self, prompt: str) -> str:
        """ask_user 的异步版：serve 引擎循环等异步宿主专用。

        同步版 ask_user 用 threading.Event.wait 阻塞调用线程；serve 宿主下
        调用方就是引擎事件循环线程，阻塞期间 _command_loop 无法消费
        answer_user 指令（同环串行）→ 自死锁直到 600s 超时。异步版把等待
        放 asyncio.to_thread 工作线程，引擎循环保持可调度，answer_user
        能被即时消费。事件外抛 / 回填语义与同步版完全一致。

        @author aceFelix
        """
        import asyncio

        self._answer_event.clear()
        self._answer_text = ""
        self._emit("ask_user", prompt)
        await asyncio.to_thread(self._answer_event.wait, 600)
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
    事件类型与原 realtime_window 协议保持一致，前端可复用同一套渲染分支。

    转写落库（2026-10）：实时语音对话发生在 DashScope 实时通道内，不经
    QueryLoop，此前退出后语音内容不进会话历史（会话只剩文本轮）。本适配器
    把 user/ai 转写按轮配对后交给 owner（ChatEngine.commit_voice_turn）
    落库，全双工与 PyAudio 两条桌面语音路径共用此逻辑。配对规则：

    - 正常顺序：用户转写先到 → ai_transcript.done 到达即配对 [问, 答]；
    - 转写滞后（输入转写常晚于回复转写）：回答先到且用户开过口
      （on_user_speaking(True)，服务端 speech_started 的权威信号）→ 暂存，
      等用户转写到了再按 [问, 答] 顺序落库；
    - 开场白/主动播报（用户从未开口）：AI 转写单独落库；
    - 回声轮（转写与 AI 刚播内容重合）：丢弃不落库（与引擎同口径判定）；
    - 打断轮（用户转写后未获回复又开口）：未配对的旧问句先单独落库。

    @author aceFelix
    """

    def __init__(self, emitter: _EventEmitter, owner: Any = None) -> None:
        super().__init__(emitter)
        # owner：ChatEngine，提供 commit_voice_turn(user, ai) 落库；测试传 None
        self._owner = owner
        # 待配对的用户转写（None=无在途问句）
        self._staged_user: str | None = None
        # 待配对的 AI 转写（滞后到达的回答，等用户转写到了配对）
        self._staged_ai: str | None = None
        # 上次落库后用户是否开过口：区分"滞后回答"（暂存等配对）与
        # "开场白"（立即单独落库）的唯一依据
        self._speech_since_flush = False
        # AI 最近一次转写全文：回声轮判定基准（与 RealtimeEngine 同口径）
        self._last_ai_text = ""

    def on_status(self, status: str) -> None:
        """状态变化：connecting / standby / listening / speaking / error。"""
        self._emit("status", status)

    def on_volume(self, level: float) -> None:
        """麦克风音量（0-1）：驱动反应炉波纹抖动。"""
        self._emit("volume", level)

    def on_user_speaking(self, speaking: bool) -> None:
        self._emit("user_speaking", speaking)
        # 落库配对信号：服务端 speech_started（回声保护后）才置位，
        # 代表"有真实用户语音"——区别于纯环境音/回声轮
        if speaking:
            self._speech_since_flush = True

    def on_ai_speaking(self, speaking: bool) -> None:
        """AI 说话状态：驱动波纹律动（说话时波纹加速）。"""
        self._emit("ai_speaking", speaking)

    def on_user_transcript(self, text: str) -> None:
        self._emit("user_transcript", text)
        if not self._owner:
            return  # 无 owner（测试/独立窗口）：只做事件透传，不落库
        stripped = (text or "").strip()
        if not stripped:
            return
        # 回声轮过滤：转写与 AI 刚播内容重合 → 外放被麦克风重新拾取的
        # 伪用户轮，不落库（与 RealtimeEngine 的 is_echo 判定同口径）
        if self._last_ai_text and (
            stripped in self._last_ai_text or self._last_ai_text in stripped
        ):
            return
        # 上一条用户转写还没配上回答又来新问句：旧轮是被打断/未获回复的轮，
        # 先单独落库（保留"说过这话"的事实），再开新一轮
        if self._staged_user is not None:
            self._persist(self._staged_user, self._staged_ai)
            self._staged_user = None
            self._staged_ai = None
        self._staged_user = stripped
        # 转写滞后场景：AI 回答已先到 → 用户转写一到立即配对落库
        if self._staged_ai is not None:
            self._persist(self._staged_user, self._staged_ai)
            self._staged_user = None
            self._staged_ai = None

    def on_ai_transcript(self, text: str) -> None:
        self._emit("ai_transcript", text)
        stripped = (text or "").strip()
        if not stripped:
            return
        # 记录最近播读内容（回声判定基准），与引擎 _last_ai_transcript 同步
        self._last_ai_text = stripped
        if not self._owner:
            return
        if self._staged_user is not None:
            # 正常顺序：用户转写先到 → 回答一到即配对 [问, 答]
            self._persist(self._staged_user, stripped)
            self._staged_user = None
            self._staged_ai = None
            self._speech_since_flush = False
        elif self._speech_since_flush:
            # 转写滞后：用户开过口但转写未到 → 暂存回答，等配对
            self._staged_ai = stripped
        else:
            # 用户从未开口：开场白/主动播报，单独落库
            self._persist(None, stripped)
            self._speech_since_flush = False

    def on_ai_transcript_delta(self, text: str) -> None:
        self._emit("ai_transcript_delta", text)

    def flush_pending_transcripts(self) -> None:
        """语音会话结束时的收尾落库：把仍在配对缓冲里的残轮写入历史。

        由引擎在 talk 任务 finally 中调用——停止瞬间可能还有一问未答
        （或回答转写未到），不 flush 这些内容就永久丢失。@author aceFelix
        """
        if self._staged_user is None and self._staged_ai is None:
            return
        self._persist(self._staged_user, self._staged_ai)
        self._staged_user = None
        self._staged_ai = None

    def _persist(self, user_text: str | None, ai_text: str | None) -> None:
        """把一对（或单项）转写交给 owner 落库；owner 异常静默（不影响语音）。"""
        if self._owner is None or (not user_text and not ai_text):
            return
        try:
            self._owner.commit_voice_turn(user_text, ai_text)
        except Exception:
            pass

    def is_running(self) -> bool:
        """窗口存活判断由引擎外部控制，这里恒真（窗口关闭时引擎自行停止）。"""
        return True

    # 实时语音模式下 info() 只保留有效提示，避免转录文本重复成气泡
    def info(self, text: str) -> None:
        if not text or text.startswith("🎙️") or text.startswith("已退出") or text.startswith("="):
            return
        self._emit("info", text)
