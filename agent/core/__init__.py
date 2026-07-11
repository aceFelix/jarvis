"""核心层。

聚合 Agent 运行时最核心的抽象: 工具协议、消息、上下文、结果、编排、主循环。
"""

from agent.core.context import ToolContext
from agent.core.message import Message, TextContent, ToolResultContent, ToolUseContent
from agent.core.result import PermissionBehavior, PermissionResult, ToolResult, ValidationResult
from agent.core.tool import Tool, ToolRegistry, build_default_registry

__all__ = [
    "Tool",
    "ToolRegistry",
    "build_default_registry",
    "ToolContext",
    "Message",
    "TextContent",
    "ToolUseContent",
    "ToolResultContent",
    "PermissionBehavior",
    "PermissionResult",
    "ToolResult",
    "ValidationResult",
]
