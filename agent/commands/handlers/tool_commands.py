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


def _split_server_args(stripped: str) -> tuple[str, list[str]]:
    """从 /server 命令行中拆分出子命令与参数 tokens。

    支持两种写法：
    - ``/server [start|run|serve] [目录] [选项]`` —— 启动（默认动作）
    - ``/server [stop|close|kill] [目录/--pid/--port]`` —— 关闭

    返回 ``(action, tokens)``，其中 action 为 ``"start"`` 或 ``"stop"``。

    @author aceFelix
    """
    parts = stripped.split(None, 1)
    rest = parts[1] if len(parts) > 1 else ""
    tokens = shlex.split(rest) if rest else []

    if tokens:
        head = tokens[0].lower()
        if head in ("stop", "close", "kill", "shutdown"):
            return "stop", tokens[1:]
        if head in ("start", "run", "serve", "up"):
            return "start", tokens[1:]
    return "start", tokens


def _parse_server_args(ui: Any, tokens: list[str], action: str) -> dict[str, Any] | None:
    """把 /server 的参数解析为 DevServer 工具的入参字典。

    支持的参数：位置参数（项目目录）、``--port``、``--pid``（仅 stop）、
    ``--command``（仅 start）、``--wait``（仅 start）。
    参数非法时打印提示并返回 None，由调用方直接结束。

    @author aceFelix
    """
    project_dir = ""
    port = None
    pid = None
    command = ""
    wait_seconds = 10

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--port" and i + 1 < len(tokens):
            try:
                port = int(tokens[i + 1])
            except ValueError:
                ui.warn(f"端口号无效: {tokens[i + 1]}")
                return None
            i += 2
        elif tok == "--pid" and i + 1 < len(tokens):
            try:
                pid = int(tokens[i + 1])
            except ValueError:
                ui.warn(f"PID 无效: {tokens[i + 1]}")
                return None
            i += 2
        elif tok == "--command" and i + 1 < len(tokens):
            command = tokens[i + 1]
            i += 2
        elif tok == "--wait" and i + 1 < len(tokens):
            try:
                wait_seconds = int(tokens[i + 1])
            except ValueError:
                ui.warn(f"等待秒数无效: {tokens[i + 1]}")
                return None
            i += 2
        elif not tok.startswith("-") and not project_dir:
            project_dir = tok
            i += 1
        else:
            ui.warn(f"未知参数: {tok}")
            return None

    args: dict[str, Any] = {
        "action": action,
        "project_dir": project_dir,
        "port": port,
    }
    if action == "stop":
        args["pid"] = pid
    else:
        args["command"] = command
        args["wait_seconds"] = wait_seconds
    return args


async def _run_dev_server(
    ui: Any,
    settings: Any,
    args: dict[str, Any],
    *,
    action_label: str,
) -> None:
    """统一执行 DevServer 工具：权限确认 → 调用 → 打印结果。

    启动与关闭共用同一套流程，避免两处复制粘贴；仅通过 ``action_label``
    在文案上区分（"启动开发服务器" / "关闭开发服务器"）。

    @author aceFelix
    """
    from agent.core.context import ToolContext
    from agent.tools.extensions.dev_server_tool import DevServerTool

    tool = DevServerTool()
    tctx = ToolContext(
        ui=ui,
        settings=settings,
        workdir=settings.workdir,
        messages=[],
    )

    perm = tool.check_permissions(args, tctx)
    if perm.behavior.value == "ask":
        ui.info(perm.message or f"请求{action_label}")
        try:
            ans = input(f"确认{action_label}? (y/n): ").strip().lower()
        except EOFError:
            ans = "y"
        if ans not in ("y", "yes", "是"):
            ui.info("已取消")
            return

    try:
        result = await tool.call(args, tctx)
    except Exception as e:
        ui.error(f"{action_label}失败: {e}")
        return

    if result.is_error:
        ui.error(f"{action_label}失败:\n{result.data}")
        return

    ui.info(result.data)


async def handle_server(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /server [start|stop] [args]。

    - ``/server [目录] [--port N] [--command "…"] [--wait N]`` —— 启动开发服务器，
      自动识别 Vite / Next.js / Vue CLI / Webpack / CRA / Nuxt / Gatsby 等项目。
    - ``/server stop [目录] [--pid N] [--port N]`` —— 关闭由 Jarvis 启动的开发服务器。

    @author aceFelix
    """
    action, tokens = _split_server_args(stripped)
    args = _parse_server_args(ctx.ui, tokens, action)
    if args is None:
        return True
    action_label = "关闭开发服务器" if action == "stop" else "启动开发服务器"
    await _run_dev_server(ctx.ui, ctx.settings, args, action_label=action_label)
    return True
