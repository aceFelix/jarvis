"""工具执行结果与权限结果的数据模型。

对应原项目 Tool.ts 中的 ToolResult<T>、ValidationResult、PermissionResult。
刻意保持精简：只保留运行时真正需要的字段，避免过度抽象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.core.message import ImageContent


class PermissionBehavior(str, Enum):
    """权限判定结果的三态：允许 / 拒绝 / 询问。

    对应原项目 PermissionResult.behavior。fail-closed 原则：默认 ASK。
    """

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionResult:
    """工具权限判定结果。

    Attributes:
        behavior: 三态行为。
        updated_input: 权限校验过程中可能改写的入参（例如路径规范化后）。
        reason: 给用户/模型看的解释（对应 permissionExplainer.ts）。
    """

    behavior: PermissionBehavior
    updated_input: dict[str, Any] | None = None
    reason: str | None = None

    @classmethod
    def allow(cls, reason: str | None = None) -> PermissionResult:
        return cls(behavior=PermissionBehavior.ALLOW, reason=reason)

    @classmethod
    def deny(cls, reason: str) -> PermissionResult:
        return cls(behavior=PermissionBehavior.DENY, reason=reason)

    @classmethod
    def ask(cls, reason: str | None = None) -> PermissionResult:
        return cls(behavior=PermissionBehavior.ASK, reason=reason)


@dataclass
class ValidationResult:
    """输入合法性校验结果（对应原 Tool.ts validateInput）。

    通过返回 result=True；失败返回 False 并附带 message 给模型看，
    让模型能基于错误信息自我修正。
    """

    ok: bool
    message: str = ""

    @classmethod
    def pass_(cls) -> ValidationResult:
        return cls(ok=True)

    @classmethod
    def fail(cls, message: str) -> ValidationResult:
        return cls(ok=False, message=message)


@dataclass
class ToolResult:
    """工具执行结果。

    对应原项目 ToolResult<T>，data 字段是工具自定义的输出结构。
    new_messages 用于工具在执行过程中产生的副作用消息（例如 AskUserTool
    把用户的回答作为新的 user message 塞回对话）。

    Attributes:
        data: 工具输出（将被序列化为 tool_result content 回传给 LLM）。
        new_messages: 工具产生的额外消息（可选）。
        is_error: 是否为错误结果。True 时 LLM 看到的是 is_error=true 的 tool_result。
        images: 附带的图片内容块（可选，多模态）。非空时 orchestrator 会透传到
            ToolResultContent.images，让支持视觉的 LLM 直接看到图片。图片走独立
            通道，不受 data 文本的超长截断影响（例如 ScreenShot 截图回传）。
    """

    data: Any = None
    new_messages: list[Any] = field(default_factory=list)
    is_error: bool = False
    images: list["ImageContent"] = field(default_factory=list)

    @classmethod
    def ok(cls, data: Any, *, images: list["ImageContent"] | None = None) -> ToolResult:
        return cls(data=data, is_error=False, images=images or [])

    @classmethod
    def error(cls, message: str) -> ToolResult:
        return cls(data=message, is_error=True)
