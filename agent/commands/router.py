"""命令路由模块。

定义 REPL 运行时的共享状态 CommandContext，维护命令分发路由表，
把 repl() 中巨大的斜杠命令 if-else 链条拆分到各领域处理器。

@author aceFelix
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from agent.commands.handlers.cli_anything_commands import (
    handle_cli_anything,
)
from agent.commands.handlers.collab_commands import (
    handle_agents,
    handle_plan,
    handle_tasks,
)
from agent.commands.handlers.core_commands import (
    handle_clear,
    handle_compact,
    handle_context,
    handle_cost,
    handle_diff,
    handle_doctor,
    handle_exit,
    handle_help,
    handle_mode,
    handle_reset,
    handle_rewind,
    handle_think,
)
from agent.commands.handlers.media_commands import (
    handle_image,
    handle_paste,
    handle_say,
)
from agent.commands.handlers.model_commands import (
    handle_model,
    handle_models,
)
from agent.commands.handlers.phone_commands import (
    handle_connect_phone,
)
from agent.commands.handlers.plugin_commands import (
    handle_plugin,
    handle_plugins,
)
from agent.commands.handlers.session_commands import (
    handle_load,
    handle_loads,
    handle_memory,
    handle_save,
    handle_sessions,
)
from agent.commands.handlers.skill_commands import (
    handle_dispatch_skill,
    handle_skills,
)
from agent.commands.handlers.tool_commands import (
    handle_mcp,
    handle_server,
    handle_tools,
)
from agent.commands.handlers.voice_commands import (
    handle_listen,
    handle_voice,
    handle_talk,
)
from agent.commands.handlers.init_command import handle_init
from agent.commands.handlers.wechat_commands import (
    handle_connect_wechat,
    handle_disconnect_wechat,
)
from agent.commands.handlers.config_commands import handle_config_show
from agent.core.message import Message
from agent.core.orchestrator import ToolOrchestrator
from agent.core.query_loop import QueryLoop
from agent.core.tool import ToolRegistry
from agent.permissions import PermissionChecker
from agent.ui.cli import RichCLI


CommandHandler = Callable[["CommandContext", str], Awaitable[bool] | bool]


@dataclass
class CommandContext:
    """REPL 运行时共享状态上下文。

    聚合命令处理过程中需要读写的全部状态，避免处理器依赖 main.py 中的局部变量。

    @author aceFelix
    """

    ui: RichCLI
    settings: Any
    provider: Any
    registry: ToolRegistry
    checker: PermissionChecker
    recovery: Any
    orchestrator: ToolOrchestrator
    loop: QueryLoop
    ctx: Any  # ToolContext
    model: str
    messages: list[Message]
    dialog_count: int
    team_mgr: Any
    task_list: Any
    mcp_client: Any
    session_name: str
    title_generated: bool
    system_prompt: str
    tray: Any = None
    should_exit: bool = False


# 精确匹配命令表
COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "/exit": handle_exit,
    "/quit": handle_exit,
    "/q": handle_exit,
    "/help": handle_help,
    "/h": handle_help,
    "?": handle_help,
    "/mode": handle_mode,
    "/reset": handle_reset,
    "/clear": handle_clear,
    "/compact": handle_compact,
    "/cost": handle_cost,
    "/context": handle_context,
    "/rewind": handle_rewind,
    "/diff": handle_diff,
    "/doctor": handle_doctor,
    "/save": handle_save,
    "/loads": handle_loads,
    "/sessions": handle_sessions,
    "/ls-sessions": handle_sessions,
    "/memory": handle_memory,
    "/skills": handle_skills,
    "/plugin": handle_plugins,
    "/plugins": handle_plugins,
    "/agents": handle_agents,
    "/tasks": handle_tasks,
    "/mcp": handle_mcp,
    "/tools": handle_tools,
    "/plan": handle_plan,
    "/think": handle_think,
    "/listen": handle_listen,
    "/mic": handle_listen,
    "/voice": handle_voice,
    "/talk": handle_talk,
    "/connect-phone": handle_connect_phone,
    "/phone": handle_connect_phone,
    "/connect-wechat": handle_connect_wechat,
    "/wechat": handle_connect_wechat,
    "/disconnect-wechat": handle_disconnect_wechat,
    "/paste": handle_paste,
    "/p": handle_paste,
    "/clipboard": handle_paste,
    "/image": handle_image,
    "/img": handle_image,
    "/say": handle_say,
    "/models": handle_models,
    "/harnesses": handle_cli_anything,
    "/cli_anything": handle_cli_anything,
    "/init": handle_init,
    "/config": handle_config_show,
}

# 前缀匹配命令表（按注册顺序优先匹配）
PREFIX_HANDLERS: list[tuple[str, CommandHandler]] = [
    ("/mode ", handle_mode),
    ("/save ", handle_save),
    ("/load ", handle_load),
    ("/load", handle_load),  # /load 无参数时仍走 handle_load
    ("/plugin install ", handle_plugin),
    ("/plugin uninstall ", handle_plugin),
    ("/plugin search", handle_plugin),
    ("/plugin info ", handle_plugin),
    ("/plugin update", handle_plugin),
    ("/plugin enable ", handle_plugin),
    ("/plugin disable ", handle_plugin),
    ("/plugin create ", handle_plugin),
    ("/plugin validate ", handle_plugin),
    ("/cli_anything ", handle_cli_anything),
    ("/model ", handle_model),
    ("/rewind ", handle_rewind),
    ("/diff ", handle_diff),
    ("/server ", handle_server),
    ("/server", handle_server),
    ("/image ", handle_image),
    ("/img ", handle_image),
    ("/say ", handle_say),
    ("/think ", handle_think),
    ("/config ", handle_config_show),
    ("/config", handle_config_show),
]


async def dispatch_command(ctx: CommandContext, stripped: str) -> bool:
    """分发斜杠命令到对应处理器。

    Args:
        ctx: REPL 共享状态。
        stripped: 用户输入去除首尾空格的字符串。

    Returns:
        True 表示命令已被处理（repl 应 continue 或根据 ctx.should_exit 退出）；
        False 表示不是已知命令，应走普通对话流程。
    """
    cmd = stripped.lower()

    # 精确匹配
    handler = COMMAND_HANDLERS.get(cmd)
    if handler is not None:
        result = handler(ctx, stripped)
        if inspect.isawaitable(result):
            return await result
        return result

    # 前缀匹配
    for prefix, handler in PREFIX_HANDLERS:
        if cmd.startswith(prefix):
            result = handler(ctx, stripped)
            if inspect.isawaitable(result):
                return await result
            return result

    # 动态 skill 匹配：/<skill-name>
    result = handle_dispatch_skill(ctx, stripped)
    if inspect.isawaitable(result):
        result = await result
    if result:
        return True

    return False
