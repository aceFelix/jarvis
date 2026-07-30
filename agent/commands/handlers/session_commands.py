"""会话相关命令处理器。

包含 /save, /load, /loads, /sessions, /memory 等命令。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.session_manager import (
    _load_by_name,
    _load_by_picker,
    _list_sessions,
    _save_session,
)
from agent.ui.terminal_picker import pick_from_list

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


async def handle_save(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /save [name]。"""
    _save_session(ctx.ui, ctx.settings, stripped, ctx.messages)
    return True


async def handle_load(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /load <prefix>：前缀匹配加载会话。"""
    parts = stripped.split(None, 1)
    if len(parts) > 1 and parts[1].strip():
        want = parts[1].strip().lower()
        from agent.core.memory.store import list_sessions

        sessions = list_sessions()
        exact = next((s for s in sessions if s.name.lower() == want), None)
        if exact:
            _load_by_name(ctx.ui, ctx.settings, exact.name, ctx.messages)
        else:
            matches = [s for s in sessions if s.name.lower().startswith(want)]
            if not matches:
                ctx.ui.warn(f"无匹配会话: {want}（用 /sessions 查看保存列表）")
            elif len(matches) == 1:
                _load_by_name(ctx.ui, ctx.settings, matches[0].name, ctx.messages)
            else:
                match_items = [
                    (s.name, s.name, f"{s.message_count} 条消息 | {s.workdir or '(无)'}")
                    for s in matches
                ]
                picked = pick_from_list(match_items, title=f"「{want}」匹配 {len(matches)} 个会话")
                if picked:
                    _load_by_name(ctx.ui, ctx.settings, picked, ctx.messages)
    else:
        ctx.ui.warn("用法: /load <会话名前缀>（用 /sessions 查看并选择会话）")
    return True


async def handle_loads(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /loads /sessions /ls-sessions：列出并选择已保存会话。"""
    _load_by_picker(ctx.ui, ctx.messages)
    return True


async def handle_sessions(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /sessions：列出所有已保存会话。"""
    _list_sessions(ctx.ui)
    return True


def _show_memory(ui, settings) -> None:
    """/memory — 查看长期记忆文件。"""
    from agent.core.memory.store import get_memory_files, load_long_term_memory

    files = get_memory_files(settings.workdir)
    ui.info("长期记忆文件:")
    for label, path in files.items():
        exists = "✓" if path and path.exists() else "✗"
        ui.info(f"  [{label}] {exists} {path}")

    mem = load_long_term_memory(settings.workdir)
    if mem:
        ui.info("当前加载的记忆内容:")
        if ui._console:
            ui._console.print(mem)
        else:
            print(mem)
    else:
        ui.info("（暂无长期记忆。可手动创建上述文件写入需要记住的信息。）")


async def handle_memory(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /memory。"""
    _show_memory(ctx.ui, ctx.settings)
    return True
