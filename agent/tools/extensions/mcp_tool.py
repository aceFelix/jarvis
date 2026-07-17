"""MCP 工具包装器 —— 把 MCP server 暴露的工具适配成 jarvis 的 Tool 协议。

每个 MCP 工具包装成一个 MCPToolWrapper 实例，注册进 ToolRegistry。
模型调用时，call() 转发到 MCPClient.call_tool()。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.mcp_client import MCPClient, McpToolDef
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import Tool


class MCPToolWrapper(Tool):
    """把一个 MCP 工具适配成 jarvis Tool。

    工具名格式: mcp__<server>__<tool>（避免与内置工具冲突，且可溯源）。
    权限: 默认 ASK（MCP server 是外部进程，调用应让用户知晓）。
    """

    def __init__(self, client: MCPClient, tool_def: McpToolDef) -> None:
        self._client = client
        self._tool_def = tool_def
        self.name = f"mcp__{tool_def.server_name}__{tool_def.name}"
        self.description = (
            f"[MCP/{tool_def.server_name}] {tool_def.description}".strip()
        )
        self.input_schema = tool_def.input_schema
        # MCP 工具结果通常不大，但保险起见给个上限
        self.max_result_chars = 20_000

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """转发调用到 MCP server。"""
        try:
            result_text = await self._client.call_tool(
                self._tool_def.server_name,
                self._tool_def.name,
                args,
            )
            return ToolResult.ok(result_text)
        except KeyError as e:
            return ToolResult.error(f"MCP server 未连接: {e}")
        except RuntimeError as e:
            return ToolResult.error(str(e))
        except Exception as e:
            return ToolResult.error(f"MCP 工具调用异常: {type(e).__name__}: {e}")

    def is_read_only(self, args: dict[str, Any]) -> bool:
        """MCP 工具的只读性未知，保守返回 False（假设会写）。"""
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """MCP 工具默认 ASK（外部进程，用户应知晓）。

        yolo 模式下由 _relax_by_mode 放宽为 ALLOW。
        """
        return PermissionResult.ask("MCP 外部工具调用需确认")


def register_mcp_tools(registry, client: MCPClient) -> int:
    """把 MCPClient 已连接的所有工具注册进 ToolRegistry。

    返回注册的工具数。mcp SDK 不可用或无连接时返回 0。
    """
    if not client.available:
        return 0

    tools = client.list_tools()
    count = 0
    for tool_def in tools:
        wrapper = MCPToolWrapper(client, tool_def)
        try:
            registry.register(wrapper)
            count += 1
        except ValueError:
            # 同名工具已注册（重复连接），跳过
            continue
    return count
