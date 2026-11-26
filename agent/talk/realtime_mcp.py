"""实时语音 MCP 工具装配 —— 从 realtime_talk.py 按职责拆出（800 行上限）。

连接 MCP server、把外部工具注册进本地工具表。**本模块只加载、不发任何
服务端指令** —— 工具集随 run() 中唯一一次初始 session.update 一次性注册。

【为什么不允许会话中途 session.update（2026-09-28 实机定性，aceFelix）】
官方约束：会话配置须在首次发送音频之前（IDLE 状态）完成，非 IDLE 时
session.update "部分受限"。实机事件链（~/.jarvis/logs/diag.log）证明：
MCP 就绪后中途补发一次 session.update(tools)，从其后的第一条响应开始，
服务端创建的每条响应都在出生 ~100ms 内返回 response.done[cancelled] ——
链上没有任何 speech_started（非用户打断）、没有客户端 response.cancel
（非自发取消）、没有 error 事件，且取消快到客户端半双工静音都来不及生效。
典型症状即"第一轮对话正常、之后 AI 永远不开口"。两次不同会话
（update 落在响应生成中 / 落在用户说话中）均复现同一签名。

因此 MCP 工具必须在首个 session.update 之前同步加载完毕（run() 现于
WebSocket 建连前完成加载，随初始 session.update 一次性注册），本模块
不再持有 ws、不再发送 session.update。

@author aceFelix
"""

from __future__ import annotations

from typing import Any


async def init_mcp_tools(talk: Any, ui: Any) -> list[dict]:
    """连接 MCP server，注册可执行工具并返回其 schema 列表。

    写入 talk 的两个状态：

    - ``_mcp_client``：MCP 客户端引用（同时用于防 GC，None 表示未接入）
    - ``_registry_tool_map``：追加可执行工具对象（按名查找后真正执行）

    schema 列表由调用方（适配器）合入工具集后经 engine.set_tools 注册给
    服务端——本模块不持有服务端配置，也不发任何服务端指令。

    任何一步失败都只是"没有外部工具"，不影响语音对话本身。

    Returns:
        MCP 工具 schema 列表（realtime API 格式），失败时为空列表。

    @author aceFelix
    """
    talk._mcp_client = None
    schemas: list[dict] = []
    try:
        from agent.core.extensions.mcp_client import MCPClient, load_mcp_config
        from agent.tools.extensions.mcp_tool import register_mcp_tools
        from agent.core.tool import ToolRegistry

        mcp_client = MCPClient()
        if not mcp_client.available:
            return schemas

        config = load_mcp_config()
        if not config:
            return schemas

        results = await mcp_client.connect_all(config)
        connected = sum(1 for v in results.values() if v)
        if connected == 0:
            return schemas

        mcp_registry = ToolRegistry()
        count = register_mcp_tools(mcp_registry, mcp_client)

        for tool in mcp_registry.all():
            talk._registry_tool_map[tool.name] = tool
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                },
            })

        talk._mcp_client = mcp_client  # 保持引用防止 GC
        ui.info(f"MCP: {connected}/{len(config)} server 已连接，注册 {count} 个工具")
    except ImportError:
        pass  # MCP SDK 未安装
    except Exception as e:
        ui.warn(f"MCP 接入异常: {e}")
    return schemas


async def load_mcp_tools_async(talk: Any, ui: Any) -> list[dict]:
    """加载 MCP 工具并返回 schema 列表（不发任何服务端指令）。

    由适配器在会话启动前调用（务必早于首个 session.update），返回的
    schema 由适配器合入工具集、随初始 session.update 一次性注册给服务端。
    加载失败或超时只是"没有外部工具"，不影响语音对话本身（适配器侧有
    wait_for 超时兜底）。

    Returns:
        MCP 工具 schema 列表；失败/无配置时为空列表。

    @author aceFelix
    """
    try:
        schemas = await init_mcp_tools(talk, ui)
        if schemas:
            ui.info(f"🛠️ MCP 外部工具已就绪（+{len(schemas)} 个），将随会话初始化注册")
        return schemas
    except Exception as e:
        ui.warn(f"MCP 外部工具加载异常: {e}")
        return []
