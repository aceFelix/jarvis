"""工具与 MCP / server 命令处理器。

包含 /tools, /mcp, /server 命令。

@author aceFelix
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


def _print_tools(ui: Any, registry: Any) -> None:
    """列出可用工具。"""
    lines = [f"  - {t.name}: {t.description.splitlines()[0]}" for t in registry.all()]
    text = "[bold]可用工具:[/bold]\n" + "\n".join(lines)
    if ui._console:
        ui._console.print(text)
    else:
        print(text)


async def handle_tools(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /tools。"""
    _print_tools(ctx.ui, ctx.registry)
    return True


def _show_mcp(ui: Any, mcp_client: Any) -> None:
    """/mcp — 查看 MCP server 连接状态。"""
    from agent.core.extensions.mcp_client import mcp_config_path, load_mcp_config

    config_path = mcp_config_path()
    ui.info(f"MCP 配置文件: {config_path}")
    exists = "✓" if config_path.exists() else "✗"
    ui.info(f"  配置文件存在: {exists}")

    if mcp_client is None:
        ui.info("MCP 未启用（settings.enable_mcp=false 或启动异常）")
        return

    if not mcp_client.available:
        ui.info("MCP SDK 未安装（pip install mcp 启用）")
        return

    config = load_mcp_config()
    if not config:
        ui.info("（无 MCP server 配置。编辑上述文件添加 mcpServers 配置。）")
        return

    ui.info(f"\n配置的 server（{len(config)} 个）:")
    connections = {c.name: c for c in mcp_client.list_connections()}
    for name in config:
        conn = connections.get(name)
        if conn and conn.connected:
            ui.info(f"  [{name}] ✓ 已连接（{len(conn.tools)} 个工具）")
            for t in conn.tools:
                ui.info(f"      - {t.name}: {t.description[:60]}")
        else:
            ui.info(f"  [{name}] ✗ 未连接")

    total_tools = sum(len(c.tools) for c in connections.values())
    ui.info(f"\n共 {len(connections)} 个已连接，{total_tools} 个工具")


async def handle_mcp(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /mcp。"""
    _show_mcp(ctx.ui, ctx.mcp_client)
    return True


async def _server_start(
    ui: Any,
    settings: Any,
    registry: Any,
    stripped: str,
) -> None:
    """/server [目录] [--port 端口] [--command "命令"] — 启动开发服务器。

    自动识别 Vite / Next.js / Vue CLI / Webpack / CRA / Nuxt / Gatsby 等项目。
    端口被占用时会自动递增，日志写入 ~/.jarvis/dev_server_logs/。

    @author aceFelix
    """
    parts = stripped.split(None, 1)
    rest = parts[1] if len(parts) > 1 else ""

    project_dir = ""
    port = None
    command = ""
    wait_seconds = 10

    tokens = shlex.split(rest) if rest else []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--port" and i + 1 < len(tokens):
            try:
                port = int(tokens[i + 1])
            except ValueError:
                ui.warn(f"端口号无效: {tokens[i + 1]}")
                return
            i += 2
        elif tok == "--command" and i + 1 < len(tokens):
            command = tokens[i + 1]
            i += 2
        elif tok == "--wait" and i + 1 < len(tokens):
            try:
                wait_seconds = int(tokens[i + 1])
            except ValueError:
                ui.warn(f"等待秒数无效: {tokens[i + 1]}")
                return
            i += 2
        elif not tok.startswith("-") and not project_dir:
            project_dir = tok
            i += 1
        else:
            ui.warn(f"未知参数: {tok}")
            return

    args: dict[str, Any] = {
        "project_dir": project_dir,
        "port": port,
        "command": command,
        "wait_seconds": wait_seconds,
    }

    from agent.tools.extensions.dev_server_tool import DevServerTool
    from agent.core.context import ToolContext

    tool = DevServerTool()
    tctx = ToolContext(
        ui=ui,
        settings=settings,
        workdir=settings.workdir,
        messages=[],
    )

    perm = tool.check_permissions(args, tctx)
    if perm.behavior.value == "ask":
        ui.info(perm.message or "请求启动开发服务器")
        try:
            ans = input("确认启动? (y/n): ").strip().lower()
        except EOFError:
            ans = "y"
        if ans not in ("y", "yes", "是"):
            ui.info("已取消")
            return

    ui.info(f"正在启动开发服务器: {project_dir or settings.workdir} ...")
    try:
        result = await tool.call(args, tctx)
    except Exception as e:
        ui.error(f"启动失败: {e}")
        return

    if result.is_error:
        ui.error(f"启动失败:\n{result.data}")
        return

    ui.info(result.data)


async def handle_server(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /server [args]。"""
    await _server_start(ctx.ui, ctx.settings, ctx.registry, stripped)
    return True
