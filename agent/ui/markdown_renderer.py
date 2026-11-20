"""Markdown 富文本渲染模块。

设计目标:
1. 用 rich 库渲染 Markdown 文本（代码块、表格、列表、标题、加粗、链接）
2. 提供:
   - render_markdown(text, console)        —— 一次性渲染整段 Markdown
   - render_code_block(code, lang, console) —— 仅渲染代码块（带语法高亮）
   - render_diff(diff_text, console)       —— 渲染 unified diff（红绿配色）
   - render_table(rows, headers, console)  —— 渲染表格
3. 流式输出时不渲染（保持现有 assistant_text 流式体验）
4. /diff /context /doctor 等命令输出 + 工具结果包含 markdown 时使用

依赖: rich（已是项目依赖）
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

#: emoji 与变体选择符：cmd（cp936/legacy 字体）里会变成 ?/空白占位。
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")


def _plain_title(text: Any) -> str:
    """legacy 终端下去掉 emoji，保留中文与文字（只用于标题，不改内容）。"""
    return _EMOJI_RE.sub("", str(text or "")).strip()


def _get_console(console: Any | None) -> Any | None:
    """获取 Rich Console 实例，None 表示不可用。"""
    if console is not None:
        return console
    # 只捕获 ImportError：以前的 `except Exception` 会把「符号不存在」也吞掉，
    # 于是命令输出静默绕过 120 列封顶（2026-11-19 实修：该符号当时根本没定义）。
    # @author aceFelix
    try:
        from agent.ui.cli import _global_console  # type: ignore
    except ImportError:
        _global_console = None
    if _global_console is not None:
        return _global_console
    try:
        from rich.console import Console
        return Console()
    except Exception:
        return None


def render_markdown(text: str, console: Any | None = None) -> None:
    """渲染整段 Markdown 文本。

    支持: 标题 / 段落 / 列表 / 代码块 / 表格 / 加粗 / 斜体 / 链接 / 引用块。
    """
    if not text:
        return
    c = _get_console(console)
    if c is None:
        print(text)
        return
    try:
        from rich.markdown import Markdown
        c.print(Markdown(text, code_theme="monokai", inline_code_theme="monokai"))
    except Exception:
        # rich 不可用或解析失败，退化为纯文本
        c.print(text)


def render_code_block(
    code: str,
    language: str = "text",
    *,
    console: Any | None = None,
    line_numbers: bool = False,
    theme: str = "monokai",
) -> None:
    """渲染带语法高亮的代码块。"""
    if not code:
        return
    c = _get_console(console)
    if c is None:
        print(f"```{language}\n{code}\n```")
        return
    try:
        from rich.syntax import Syntax
        from rich.panel import Panel
        syntax = Syntax(
            code, language, theme=theme,
            line_numbers=line_numbers,
            word_wrap=True, padding=(0, 1),
        )
        c.print(Panel(syntax, border_style="dim #4a5a6a", padding=(0, 0)))
    except Exception:
        c.print(f"```{language}\n{code}\n```")


def render_diff(diff_text: str, *, console: Any | None = None) -> None:
    """渲染 unified diff 文本（红绿配色）。

    输入是 git diff 或 unified diff 格式文本，按行着色:
    - `+++`/`@@` 行: 加粗
    - `+` 行: 绿色
    - `-` 行: 红色
    - ` ` 行: 默认
    """
    if not diff_text:
        return
    c = _get_console(console)
    if c is None:
        print(diff_text)
        return
    try:
        from rich.text import Text
        from rich.panel import Panel
        text = Text()
        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
                text.append(line + "\n", style="bold cyan")
            elif line.startswith("+"):
                text.append(line + "\n", style="green")
            elif line.startswith("-"):
                text.append(line + "\n", style="red")
            else:
                text.append(line + "\n", style="dim")
        c.print(Panel(text, border_style="dim #4a5a6a", title="diff", title_align="left"))
    except Exception:
        c.print(diff_text)


def render_table(
    rows: Sequence[Sequence[Any]],
    *,
    headers: Sequence[str] | None = None,
    console: Any | None = None,
    title: str | None = None,
) -> None:
    """渲染表格。

    Args:
        rows: 行数据，每行是单元格值列表
        headers: 表头（None 表示无表头）
        title: 表格标题
    """
    if not rows:
        return
    c = _get_console(console)
    if c is None:
        # 纯文本退化: 对齐打印
        if headers:
            print(" | ".join(str(h) for h in headers))
            print("-" * 40)
        for row in rows:
            print(" | ".join(str(c) for c in row))
        return
    try:
        from rich import box as rich_box
        from rich.table import Table

        # legacy 控制台（cmd/conhost）下制表符可能整行不可见；显式改用 ASCII
        # 边框（+ - |），并把每一列改为单行省略，避免长路径把表格撑破。
        # 非 legacy（Git Bash / Windows Terminal）保持原有圆角框与折行。
        # @author aceFelix
        plain = bool(getattr(c, "legacy_windows", False))
        table = Table(
            title=_plain_title(title) if plain else title,
            show_header=headers is not None,
            border_style="dim #4a5a6a",
            box=rich_box.ASCII if plain else rich_box.ROUNDED,
        )
        if headers:
            for h in headers:
                if plain:
                    table.add_column(str(h), overflow="ellipsis", no_wrap=True)
                else:
                    table.add_column(str(h), overflow="fold")
        for row in rows:
            table.add_row(*[str(cell) for cell in row])
        c.print(table)
    except Exception:
        if headers:
            print(" | ".join(str(h) for h in headers))
        for row in rows:
            print(" | ".join(str(c) for c in row))


def render_panel(
    content: str,
    *,
    title: str | None = None,
    console: Any | None = None,
    style: str = "cyan",
) -> None:
    """渲染面板（带边框的内容区，用于命令输出）。"""
    if not content:
        return
    c = _get_console(console)
    if c is None:
        if title:
            print(f"=== {title} ===")
        print(content)
        return
    try:
        from rich import box as rich_box
        from rich.markdown import Markdown
        from rich.panel import Panel

        # 内容当 Markdown 渲染（支持代码块、加粗等）；legacy 下换 ASCII 边框。
        plain = bool(getattr(c, "legacy_windows", False))
        c.print(
            Panel(
                Markdown(content),
                title=_plain_title(title) if plain else title,
                title_align="left",
                border_style=style,
                box=rich_box.ASCII if plain else rich_box.ROUNDED,
            )
        )
    except Exception:
        c.print(content)


def render_tree(
    root_label: str,
    children: Iterable[tuple[str, Iterable[str] | None]],
    *,
    console: Any | None = None,
) -> None:
    """渲染树形结构（如 /context 展示对话历史）。

    Args:
        root_label: 根节点标签
        children: 子节点列表，每项 (label, grand_children_or_None)
    """
    c = _get_console(console)
    if c is None:
        print(root_label)
        for label, grand in children:
            print(f"  ├─ {label}")
            if grand:
                for g in grand:
                    print(f"  │   ├─ {g}")
        return
    try:
        from rich.tree import Tree
        from rich.text import Text
        root = Tree(root_label, guide_style="dim")
        for label, grand in children:
            branch = root.add(label)
            if grand:
                for g in grand:
                    branch.add(Text(g, style="dim"))
        c.print(root)
    except Exception:
        print(root_label)
        for label, grand in children:
            print(f"  └─ {label}")
            if grand:
                for g in grand:
                    print(f"      └─ {g}")
