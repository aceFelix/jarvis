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
    _save_session(ctx.ui, ctx.settings, stripped, ctx.messages,
                  dialog_count=ctx.dialog_count, title_generated=ctx.title_generated)
    return True


async def handle_load(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /load <prefix>：前缀匹配加载会话。

    加载后把恢复信息（轮数/标题状态）存到 ctx.last_load_info，
    由 REPL 主循环应用到 _dialog_count/_title_generated/_session_name。
    """
    parts = stripped.split(None, 1)
    if len(parts) > 1 and parts[1].strip():
        want = parts[1].strip().lower()
        from agent.core.memory.store import list_sessions

        sessions = list_sessions()
        exact = next((s for s in sessions if s.name.lower() == want), None)
        if exact:
            info = _load_by_name(ctx.ui, ctx.settings, exact.name, ctx.messages)
            if info:
                ctx.last_load_info = info
        else:
            matches = [s for s in sessions if s.name.lower().startswith(want)]
            if not matches:
                ctx.ui.warn(f"无匹配会话: {want}（用 /sessions 查看保存列表）")
            elif len(matches) == 1:
                info = _load_by_name(ctx.ui, ctx.settings, matches[0].name, ctx.messages)
                if info:
                    ctx.last_load_info = info
            else:
                match_items = [
                    (s.name, s.name, f"{s.message_count} 条消息 | {s.workdir or '(无)'}")
                    for s in matches
                ]
                picked = pick_from_list(match_items, title=f"「{want}」匹配 {len(matches)} 个会话")
                if picked:
                    info = _load_by_name(ctx.ui, ctx.settings, picked, ctx.messages)
                    if info:
                        ctx.last_load_info = info
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


def _show_memory_file(ui, settings) -> None:
    """/memory file — 查看长期记忆文件（MEMORY.md）。"""
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


def _show_profile(ui, settings) -> None:
    """/memory — 画像记忆列表（Phase 1a）。"""
    from agent.core.memory.profile_store import ProfileStore, CATEGORY_LABELS

    store = ProfileStore()
    entries = store.entries()
    if not entries:
        ui.info(
            "画像记忆为空。和贾维斯聊聊你的习惯与偏好（如\"我习惯熬夜写代码\"），"
            "会话结束后会自动提炼入库；也可用 /memory add <内容> 手动添加。"
        )
        return

    limit = getattr(settings, "profile_inject_token_limit", 300)
    ui.info(f"画像记忆（{len(entries)} 条，按置信度排序；注入限额 {limit} token）:")
    for e in entries:
        label = CATEGORY_LABELS.get(e.category, e.category)
        src = e.source_session or "-"
        ui.info(
            f"  [{e.id}] {label} | {e.content}\n"
            f"      置信度 {e.confidence:.2f} | 来源 {src}"
        )


def _rebuild_system_prompt(ctx) -> None:
    """画像增删后重建 system prompt，让变更在当前会话立即生效。"""
    try:
        from agent.prompts.system import build_system_prompt, reload_profile_cache

        reload_profile_cache()
        enable_thinking = getattr(ctx.settings, "enable_thinking", True)
        new_system = build_system_prompt(
            ctx.settings.workdir, ctx.registry,
            enable_thinking=enable_thinking, settings=ctx.settings,
        )
        if ctx.settings.system_prompt_append:
            new_system = new_system + "\n\n" + ctx.settings.system_prompt_append
        ctx.loop._system = new_system
    except Exception:
        pass  # 重建失败不影响命令本身


async def handle_memory(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /memory 系列命令。

    /memory              查看画像记忆（Phase 1a 自动提炼的用户偏好/习惯）
    /memory add <文本>    手动添加画像条目
    /memory del <id>     删除指定条目
    /memory clear yes    清空全部画像（需 yes 二次确认）
    /memory refine       立即提炼当前会话（不等自动节流）
    /memory file         查看长期记忆文件（MEMORY.md，旧功能保留）
    """
    from agent.core.memory.profile_store import ProfileStore

    parts = stripped.split(None, 1)
    sub = parts[1].strip() if len(parts) > 1 else ""

    if not sub:
        _show_profile(ctx.ui, ctx.settings)
        return True

    action, _, rest = sub.partition(" ")
    action = action.lower()
    rest = rest.strip()

    if action == "file":
        _show_memory_file(ctx.ui, ctx.settings)
        return True

    if action == "refine":
        if not getattr(ctx.settings, "profile_enabled", False):
            ctx.ui.warn("画像记忆未开启（settings.toml [memory] profile_enabled）")
            return True
        if len(ctx.messages) < 2:
            ctx.ui.warn("当前会话消息太少，先和贾维斯聊聊再提炼")
            return True
        import threading
        from agent.core.memory.profile_refiner import refine_session

        msgs = list(ctx.messages)

        def _worker() -> None:
            refine_session(msgs, "manual-refine", ctx.settings)

        threading.Thread(target=_worker, name="profile-refiner-manual", daemon=True).start()
        ctx.ui.info("已开始后台提炼当前会话（用 /memory 查看结果，稍等十几秒）")
        return True

    if action == "add":
        if not rest:
            ctx.ui.warn("用法: /memory add <要记住的内容>（如 /memory add 习惯用 GLM 写代码）")
            return True
        from agent.core.memory.profile_store import ProfileEntry

        entry = ProfileEntry.new(rest, "other", confidence=0.99, source_session="manual")
        ProfileStore().upsert(entry)
        _rebuild_system_prompt(ctx)
        ctx.ui.info(f"已记住: {rest}（[{entry.id}]）当前会话即生效")
        return True

    if action == "del":
        if not rest:
            ctx.ui.warn("用法: /memory del <id>（id 用 /memory 查看）")
            return True
        if ProfileStore().delete(rest):
            _rebuild_system_prompt(ctx)
            ctx.ui.info(f"已删除画像条目 {rest}")
        else:
            ctx.ui.warn(f"未找到条目: {rest}（id 用 /memory 查看）")
        return True

    if action == "clear":
        if rest.lower() != "yes":
            ctx.ui.warn("清空全部画像不可恢复。确认请输入: /memory clear yes")
            return True
        n = ProfileStore().clear()
        _rebuild_system_prompt(ctx)
        ctx.ui.info(f"已清空画像记忆（{n} 条）")
        return True

    ctx.ui.warn(
        "用法: /memory [add <内容> | del <id> | clear yes | refine | file]\n"
        "  /memory              查看画像\n"
        "  /memory add <文本>    手动添加\n"
        "  /memory del <id>     删除条目\n"
        "  /memory clear yes    清空全部\n"
        "  /memory refine       立即提炼当前会话\n"
        "  /memory file         查看 MEMORY.md 长期记忆文件"
    )
    return True
