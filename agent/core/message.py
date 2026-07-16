"""对话消息类型。

刻意用 dataclass 而非 pydantic —— 这些是内部数据结构，不需要序列化校验开销。
给 LLM 的格式转换在 llm/ 层完成。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Self


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class TextContent:
    """文本内容块。一条 message 的 content 可以是多个 block。"""

    type: Literal["text"] = "text"
    text: str = ""


@dataclass
class ImageContent:
    """图片内容块（base64 编码）。

    用于把截图等图片作为 image content block 回传给支持视觉的 LLM
    （如 Claude / GPT-4o），让模型真正"看"到屏幕，而不只是拿到文件路径。

    Attributes:
        data: base64 编码的图片数据（不含 `data:` 前缀，纯 base64 字符串）。
        media_type: MIME 类型，如 "image/png" / "image/jpeg"。
    """

    data: str
    media_type: str = "image/png"
    type: Literal["image"] = "image"


@dataclass
class ThinkingContent:
    """思维链思考内容块。

    用于存储模型的深度思考过程（qwen3.7-plus enable_thinking 等）。
    在 assistant message 的 content 列表中，ThinkingContent 在 ToolUseContent
    之前出现，对应 ReAct 循环中的 Think 阶段。

    与 TextContent 分离的目的：
    - UI 可将其渲染为可折叠的暗色面板（不干扰正式回复）
    - 下一轮对话时可选择性清除（节省 token）
    - 工具结果回灌时不携带思考内容
    """

    text: str
    type: Literal["thinking"] = "thinking"


@dataclass
class ToolUseContent:
    """模型发起的工具调用（对应 Anthropic API 的 tool_use block）。"""

    id: str
    name: str
    input: dict[str, Any]
    type: Literal["tool_use"] = "tool_use"


@dataclass
class ToolResultContent:
    """工具执行结果（回传给模型，作为 user message 的 content block）。

    Attributes:
        tool_use_id: 对应的 ToolUseContent.id。
        content: 结果文本。复杂结构可在调用方序列化为字符串。
        is_error: 是否为错误结果。
        images: 附带的图片内容块（可选）。非空时 provider 会把 tool_result
            的 content 序列化成 [text, image...] 列表，让支持视觉的 LLM 直接
            看到图片。图片走独立通道，不受 content 的超长截断影响。
    """

    tool_use_id: str
    content: str
    is_error: bool = False
    images: list[ImageContent] = field(default_factory=list)
    type: Literal["tool_result"] = "tool_result"


ContentBlock = ThinkingContent | TextContent | ToolUseContent | ToolResultContent | ImageContent


@dataclass
class Message:
    """统一消息结构。

    对应原项目 Message 联合类型。这里用一个带 role 的 dataclass 简化处理，
    content 是 block 列表，能覆盖 assistant(text+tool_use)/user(text+tool_result)/
    system 的所有场景。

    Attributes:
        role: user / assistant / system。
        content: 内容块列表。
        id: 消息唯一 ID（用于日志/持久化）。
        timestamp: 创建时间戳。
    """

    role: Literal["user", "assistant", "system"]
    content: list[ContentBlock] = field(default_factory=list)
    id: str = field(default_factory=_new_id)
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def user_text(cls, text: str) -> Self:
        return cls(role="user", content=[TextContent(text=text)])

    @classmethod
    def assistant_text(cls, text: str) -> Self:
        return cls(role="assistant", content=[TextContent(text=text)])

    @classmethod
    def system_text(cls, text: str) -> Self:
        return cls(role="system", content=[TextContent(text=text)])

    def get_text(self) -> str:
        """拼接所有 text block 的纯文本（不含思考内容）。"""
        return "".join(b.text for b in self.content if isinstance(b, TextContent))

    def get_thinking(self) -> str:
        """获取思考内容的纯文本。"""
        return "".join(b.text for b in self.content if isinstance(b, ThinkingContent))

    def get_tool_uses(self) -> list[ToolUseContent]:
        return [b for b in self.content if isinstance(b, ToolUseContent)]
