"""Skill 命令处理器。

包含 /skills 与 /<skill-name> 动态技能分发。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


def _list_skills(ui: Any, settings: Any) -> None:
    """/skills — 列出已加载的技能包。"""
    from agent.core.extensions.skills import load_skills, list_skill_files

    files = list_skill_files(settings.workdir)
    ui.info("技能包目录:")
    for label, path in files.items():
        exists = "✓" if path and path.exists() else "✗"
        count = len(list(path.iterdir())) if path.exists() else 0
        ui.info(f"  [{label}] {exists} {path} ({count} 个子目录)")

    skills = load_skills(settings.workdir)
    if not skills:
        ui.info("（暂无技能包。在上述目录创建 <name>/SKILL.md 即可添加。）")
        return

    if ui._console:
        from rich.table import Table
        table = Table(title="已加载技能包", show_lines=False)
        table.add_column("名称", style="cyan", no_wrap=True)
        table.add_column("来源", style="dim")
        table.add_column("描述")
        table.add_column("使用时机", style="dim")
        for s in skills:
            table.add_row(s.name, s.source, s.description, s.when_to_use)
        ui._console.print(table)
    else:
        for s in skills:
            print(f"  {s.name} [{s.source}] {s.description} — {s.when_to_use}")


async def _dispatch_skill(
    ui: Any,
    settings: Any,
    stripped: str,
    loop: Any,
    ctx: Any,
) -> bool:
    """如果 stripped = /<skill-name> [...args]，则把 skill 提示词 + 用户参数注入对话。

    Returns:
        True 表示已匹配并执行了 skill（调用方应 continue），False 表示不是 skill 命令。
    """
    from agent.core.extensions.skills import load_skills

    parts = stripped[1:].split(None, 1)
    if not parts:
        return False
    skill_name = parts[0].lower()
    user_arg = parts[1] if len(parts) > 1 else ""

    skills = load_skills(settings.workdir)
    matched = None
    for s in skills:
        if s.name.lower() == skill_name:
            matched = s
            break

    if matched is None:
        return False

    prompt = matched.content
    if user_arg:
        prompt = f"{user_arg}\n\n请运用以下技能来完成上述请求：\n\n{matched.content}"
    else:
        prompt = f"请运用以下技能来帮助我：\n\n{matched.content}"

    ui.info(f"调用技能: {matched.name}")
    try:
        stats = await loop.run(prompt, ctx)
        if settings.verbose:
            _cache_hint = f" cache={stats.usage.cache_read_tokens}" if stats.usage.cache_read_tokens else ""
            ui.info(
                f"[{matched.name}] iterations={stats.iterations} "
                f"tool_calls={stats.tool_calls} "
                f"tokens={stats.usage.input_tokens}+{stats.usage.output_tokens}{_cache_hint}]"
            )
    except Exception as e:
        ui.error(f"技能执行出错: {type(e).__name__}: {e}")
    return True


async def handle_skills(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /skills。"""
    _list_skills(ctx.ui, ctx.settings)
    return True


async def handle_dispatch_skill(ctx: "CommandContext", stripped: str) -> bool:
    """尝试将输入作为 /<skill-name> 进行动态匹配。"""
    return await _dispatch_skill(ctx.ui, ctx.settings, stripped, ctx.loop, ctx.ctx)
