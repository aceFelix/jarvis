"""会话管理模块。

负责会话标题生成、自动保存、手动保存、加载、列表展示等会话生命周期管理。

@author aceFelix
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.message import Message, TextContent, ThinkingContent, ToolResultContent, ToolUseContent
from agent.ui.cli import RichCLI


__all__ = [
    "_sanitize_title",
    "_rename_session_file",
    "_generate_title_from_first_user",
    "_generate_session_title",
    "_auto_save",
    "_save_session",
    "_load_session",
    "_load_by_name",
    "_load_by_picker",
    "_render_session",
    "_list_sessions",
]


def _sanitize_title(title: str) -> str:
    """标题文件名安全化：去标点、换空格为连字符，截断到 15 字。"""
    title = title.strip()[:15]
    title = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', title).strip()
    title = re.sub(r'\s+', '-', title)
    return title


def _rename_session_file(old_name: str, title: str) -> str:
    """重命名会话文件。返回最终可用的新名称。

    目标文件名已存在时自动追加序号（-2、-3…），避免标题冲突时
    直接退回时间戳旧名导致标题生成失效。
    """
    from agent.core.memory.store import sessions_dir

    title = _sanitize_title(title)
    if not title:
        return old_name
    if title == old_name:
        return title

    old_path = sessions_dir() / f"{old_name}.json"
    if not old_path.exists():
        # 旧文件不存在（未落盘）：直接用新标题，后续保存会按新名创建
        return title

    new_path = sessions_dir() / f"{title}.json"
    if not new_path.exists():
        old_path.rename(new_path)
        return title

    # 目标已存在：追加序号 -2、-3…（最多尝试 99 次）
    for n in range(2, 100):
        candidate = f"{title}-{n}"
        candidate_path = sessions_dir() / f"{candidate}.json"
        if not candidate_path.exists():
            old_path.rename(candidate_path)
            return candidate

    # 极端情况兜底：保留原名称
    return old_name


async def _generate_title_from_first_user(
    ui: RichCLI, messages: list[Message], old_name: str
) -> str:
    """第 1 轮对话结束后：取用户第一条消息的前 15 字作为标题。"""
    try:
        first_user_text = ""
        for m in messages:
            if getattr(m, "role", "") == "user":
                text = m.get_text() if hasattr(m, "get_text") else ""
                text = text.strip()
                if text:
                    first_user_text = text
                    break

        if not first_user_text:
            return old_name

        # 取前 15 个字符（中英文混排按字符计）
        title = first_user_text[:15]
        title = _rename_session_file(old_name, title)
        if title != old_name:
            ui.info(f"📝 会话标题已生成: {title}")
        return title
    except Exception:
        return old_name


async def _generate_session_title(
    ui: RichCLI, provider, model: str, messages: list[Message], old_name: str
) -> str:
    """第 2 轮对话结束后：用 LLM 根据前两轮对话生成标题。返回新名称（失败则返回旧名）。

    只取前两轮 user/assistant 消息（最多 4 条）喂给 LLM，不污染上下文。
    标题限制 15 字以内，去标点，作文件名时安全截断。
    """
    try:
        # 取前两轮 user/assistant 消息（最多 4 条），跳过 tool 消息
        dialog_lines: list[str] = []
        for m in messages:
            role = getattr(m, "role", "")
            if role in ("user", "assistant"):
                text = m.get_text() if hasattr(m, "get_text") else ""
                text = text.strip()[:200]
                if text:
                    who = "用户" if role == "user" else "贾维斯"
                    dialog_lines.append(f"{who}: {text}")
            if len(dialog_lines) >= 4:
                break

        if not dialog_lines:
            return old_name

        dialog_text = "\n".join(dialog_lines)
        prompt = (
            "请根据以下对话内容，用15个字以内生成一个会话标题。\n"
            "要求：只输出标题文本，不要输出任何解释、标点、引号，"
            "不要重复题目或用户原话。\n\n"
            f"对话：\n{dialog_text}\n\n"
            "标题："
        )

        msgs = [Message(role="user", content=[TextContent(text=prompt)])]

        # 标题生成不需要深度思考，临时关闭避免模型输出冗余 reasoning/echo
        old_thinking = provider.is_thinking_enabled()
        title_text = ""
        try:
            provider.set_thinking_enabled(False)
            # 注意: system 不能传空字符串——DeepSeek 等 Anthropic 兼容端点
            # 对空 system 会静默返回空文本（实测 system='' 无输出）。
            # 传一句简短的角色说明即可规避。
            events = provider.stream(
                model=model,
                system="你是会话标题生成助手，只输出标题，不输出解释。",
                messages=msgs,
                tools=[],
                max_tokens=100,
                temperature=0.3,
            )
            async for event in events:
                if hasattr(event, "text") and event.text:
                    title_text += event.text
        finally:
            provider.set_thinking_enabled(old_thinking)

        # 去除模型可能带出的 "标题：" 前缀、引号等多余字符
        title_text = title_text.strip()
        for prefix in ("标题：", "标题:", "会话标题：", "会话标题:", "Title:", "Title："):
            if title_text.startswith(prefix):
                title_text = title_text[len(prefix):].strip()
        title_text = title_text.strip("'\"«»")

        title = _rename_session_file(old_name, title_text)
        if title != old_name:
            ui.info(f"📝 会话标题已生成: {title}")
        return title
    except Exception:
        return old_name


def _auto_save(
    ui: RichCLI,
    messages: list[Message],
    *,
    workdir: str = "",
    model: str = "",
    provider: str = "",
    session_name: str = "auto-latest",
    verbose: bool = True,
    dialog_count: int = 0,
    title_generated: bool = False,
) -> None:
    """保存会话到指定名称（增量刷新，每次对话后都会调用）。

    同时写入 auto-latest.json 确保重启时自动恢复。
    session_name 为空时使用时间戳自动命名。
    dialog_count/title_generated 随会话持久化，供 /load 恢复后续计。
    """
    if not messages:
        return
    try:
        from agent.core.memory.store import save_session, user_jarvis_dir
        jarvis_dir = user_jarvis_dir()
        jarvis_dir.mkdir(parents=True, exist_ok=True)
        # 写入独立的会话文件
        save_session(
            session_name, messages,
            workdir=workdir,
            model=model,
            provider=provider,
            dialog_count=dialog_count,
            title_generated=title_generated,
        )
        # 同时写入 auto-latest 作为恢复指针
        save_session(
            "auto-latest", messages,
            workdir=workdir,
            model=model,
            provider=provider,
            dialog_count=dialog_count,
            title_generated=title_generated,
        )
        if verbose:
            ui.info(f"会话已自动保存（{session_name[:40]}）")
    except Exception:
        pass


def _save_session(ui: RichCLI, settings: Any, cmd: str, messages: list[Message],
                  dialog_count: int = 0, title_generated: bool = False) -> None:
    """/save [name] — 保存当前会话（记录轮数与标题状态供恢复）。"""
    from agent.core.memory.store import save_session

    # 解析名字: /save myname → myname; /save → auto-<timestamp>
    parts = cmd.split(None, 1)
    if len(parts) > 1 and parts[1].strip():
        name = parts[1].strip()
    else:
        name = f"auto-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    if not messages:
        ui.warn("当前对话为空，无需保存")
        return

    path = save_session(
        name, messages,
        workdir=settings.workdir,
        model=settings.model or "",
        provider=settings.provider,
        dialog_count=dialog_count,
        title_generated=title_generated,
    )
    ui.info(f"会话已保存: {name}（{len(messages)} 条消息）→ {path}")


def _load_session(ui: RichCLI, settings: Any, cmd: str, messages: list[Message]) -> None:
    """已废弃，保留签名兼容。"""
    pass  # deprecated, 改用 _load_by_name / _load_by_picker


def _load_by_name(ui: RichCLI, settings: Any, name: str, messages: list[Message]) -> dict | None:
    """/load <name> — 直接加载指定会话。

    Returns:
        加载恢复信息 {session_name, dialog_count, title_generated}，
        供 REPL 恢复轮数计数与标题状态；失败返回 None。

    @author aceFelix
    """
    from agent.core.memory.store import load_session

    session = load_session(name)
    if not session:
        ui.error(f"会话不存在: {name}（用 /sessions 查看保存列表）")
        return None

    messages.clear()
    messages.extend(session.messages)
    ui.info(f"已加载会话: {name}（{len(session.messages)} 条消息，"
            f"保存于 {session.meta.workdir}）")
    _render_session(ui, session.messages)
    return {
        "session_name": session.meta.name,
        "dialog_count": session.meta.dialog_count,
        "title_generated": session.meta.title_generated,
    }


def _load_by_picker(ui: RichCLI, messages: list[Message]) -> dict | None:
    """/load（无参数）— 终端内联选择（↑↓ Enter Esc，不弹窗）。

    Returns:
        加载恢复信息 {session_name, dialog_count, title_generated}；取消/失败返回 None。

    @author aceFelix
    """
    from agent.core.memory.store import list_sessions, load_session

    sessions = list_sessions()
    if not sessions:
        ui.info("没有已保存的会话。用 /save [name] 保存当前会话。")
        return None

    # 转 pick_from_list 格式 [(value, label, description), ...]
    items = []
    for s in sessions:
        ts = ""
        try:
            ts = datetime.fromtimestamp(s.updated_at).strftime("%m-%d %H:%M")
        except Exception:
            pass
        items.append((
            s.name,
            s.name,
            f"{s.message_count}条消息  {ts}",
        ))

    from agent.ui.terminal_picker import pick_from_list
    picked = pick_from_list(items, title="加载会话")
    if picked is None:
        return None

    session = load_session(picked)
    if not session:
        ui.error(f"会话加载失败: {picked}")
        return None

    messages.clear()
    messages.extend(session.messages)
    ui.info(f"已加载会话: {picked}（{len(session.messages)} 条消息）")
    _render_session(ui, session.messages)
    return {
        "session_name": session.meta.name,
        "dialog_count": session.meta.dialog_count,
        "title_generated": session.meta.title_generated,
    }


def _render_session(ui: RichCLI, msgs: list[Message]) -> None:
    """回放已加载会话的消息到终端。"""
    for msg in msgs:
        if msg.role == "user":
            # 用户消息可能是 TextContent（用户输入）或 ToolResultContent（工具结果）
            text = "".join(
                b.text for b in msg.content if isinstance(b, TextContent)
            )
            tool_results = [b for b in msg.content if isinstance(b, ToolResultContent)]
            if text.strip():
                ui.info(f"👤 {text}")
            for tr in tool_results:
                ui.tool_result(
                    f"工具({tr.tool_use_id[:8]})",
                    tr.tool_use_id,
                    tr.content,
                    is_error=tr.is_error,
                )
        elif msg.role == "assistant":
            # 思考过程
            thinking = "".join(
                b.text for b in msg.content if isinstance(b, ThinkingContent)
            )
            if thinking:
                ui.assistant_thinking(thinking)
                ui._end_thinking()
            # 正式文本
            texts = [b for b in msg.content if isinstance(b, TextContent)]
            for t in texts:
                if t.text.strip():
                    ui.info(f"🤖 {t.text}")
            # 工具调用
            for b in msg.content:
                if isinstance(b, ToolUseContent):
                    ui.tool_use(b.name, b.input, b.id)


def _list_sessions(ui: RichCLI) -> None:
    """/sessions — 列出所有已保存会话。"""
    from agent.core.memory.store import list_sessions

    sessions = list_sessions()
    if not sessions:
        ui.info("没有已保存的会话。用 /save [name] 保存当前会话。")
        return

    if ui._console:
        from rich.table import Table
        table = Table(title="已保存会话", show_lines=False)
        table.add_column("名称", style="cyan", no_wrap=True)
        table.add_column("消息数", justify="right", style="green")
        table.add_column("更新时间", style="dim")
        table.add_column("工作目录", style="dim")
        for s in sessions:
            ts = datetime.fromtimestamp(s.updated_at).strftime("%Y-%m-%d %H:%M")
            table.add_row(s.name, str(s.message_count), ts, s.workdir)
        ui._console.print(table)
    else:
        for s in sessions:
            ts = datetime.fromtimestamp(s.updated_at).strftime("%Y-%m-%d %H:%M")
            print(f"  {s.name}  ({s.message_count} 条, {ts})  {s.workdir}")
