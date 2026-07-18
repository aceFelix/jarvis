"""工具运行时上下文。

ToolUseContext（该类型有 200+ 字段，是整个系统的神经中枢）。
v0.1先大幅精简，只保留 v0.1 真正需要的部分，按需扩展。

设计要点：
- ToolContext 是不可变快照的"近似"——里面持有的可变对象（messages 列表、
  AppState）由外部管理，工具不应直接修改它们，应通过返回 ToolResult.new_messages
  或 context 的显式方法。
- set_tool_ui 用于工具自定义渲染（v0.1 先不实现完整 UI，仅做钩子）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from agent.core.message import Message


class UIProtocol(Protocol):
    """UI 层的极简协议。工具和 query loop 只依赖这个抽象，不依赖具体 Rich 实现。"""

    def assistant_text(self, text: str) -> None: ...
    def assistant_thinking(self, text: str) -> None:
        """流式思考增量回调（深度思考/思维链）。UI 可选实现。"""
        ...
    def tool_use(self, tool_name: str, tool_input: dict[str, Any], tool_use_id: str) -> None: ...
    def tool_result(
        self, tool_name: str, tool_use_id: str, content: str, *, is_error: bool = False
    ) -> None: ...
    def info(self, text: str) -> None: ...
    def warn(self, text: str) -> None: ...
    def error(self, text: str) -> None: ...
    def ask_user(self, prompt: str) -> str:
        """阻塞式询问用户，返回用户输入。权限提示、AskUserTool 用。"""
        ...


class RealtimeTalkUI(UIProtocol, Protocol):
    """实时双工语音对话的 UI 协议。

    在 UIProtocol 基础上扩展实时对话专用的状态/音量/转录回调，
    使 RichCLI 与 Webview 窗口两种实现都能被 RealtimeTalk 使用。

    @author aceFelix
    """

    def on_status(self, status: str) -> None:
        """状态变化：connecting / standby / listening / speaking / error。"""
        ...

    def on_volume(self, level: float) -> None:
        """麦克风音量级别，0.0 ~ 1.0。"""
        ...

    def on_user_speaking(self, speaking: bool) -> None:
        """用户开始/停止说话。"""
        ...

    def on_ai_speaking(self, speaking: bool) -> None:
        """AI 开始/停止说话。"""
        ...

    def on_user_transcript(self, text: str) -> None:
        """用户语音转录完成。"""
        ...

    def on_ai_transcript(self, text: str) -> None:
        """AI 语音转录完成。"""
        ...

    def is_running(self) -> bool:
        """UI 是否仍在运行（窗口未关闭）。"""
        ...


@dataclass
class ToolContext:
    """工具执行上下文。

    生命周期：每次 query 循环开始时构造，传给所有工具调用。
    子代理场景下会克隆并改写 workdir/permission_mode 等字段。

    Attributes:
        workdir: 当前工作目录（工具的相对路径以此为根）。
        messages: 当前对话历史（只读引用，工具可读不可写）。
        abort_event: 取消信号。工具应在长操作中检查此事件。
        permission_mode: 当前权限模式（见 permissions/modes.py）。
        ui: UI 抽象，用于工具向用户展示进度/结果。
        extra: 工具自定义数据的自由存储区（类似原项目的 nested_memory_attachment_triggers
            等零散字段，这里统一收口）。
        on_assistant_text: 文本增量回调（阶段三语音模式用）。
            设置后，QueryLoop 每收到一个 TextDelta，除了给 ui.assistant_text，
            还会调用此回调（如 tts.feed）。普通文本模式不设此字段。
    """

    workdir: str
    messages: list[Message]
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    permission_mode: str = "default"
    ui: UIProtocol | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    on_assistant_text: Callable[[str], None] | None = None

    def clone_for_subagent(self, workdir: str | None = None) -> ToolContext:
        """子代理场景克隆上下文。messages 共享引用（子代理读父对话历史）。"""
        return ToolContext(
            workdir=workdir or self.workdir,
            messages=self.messages,
            abort_event=asyncio.Event(),
            permission_mode=self.permission_mode,
            ui=self.ui,
            extra=dict(self.extra),
            on_assistant_text=self.on_assistant_text,
        )
