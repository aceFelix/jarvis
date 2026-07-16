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

from typing import Any, Iterable, Sequence


def _get_console(console: Any | None) -> Any | None:
    """获取 Rich Console 实例，None 表示不可用。"""
    if console is not None:
        return console
    try:
        from agent.ui.cli import _global_console  # type: ignore
        if _global_console is not None:
            return _global_console
    except Exception:
        pass
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
        from rich.table import Table
        table = Table(title=title, show_header=headers is not None, border_style="dim #4a5a6a")
        if headers:
            for h in headers:
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
        from rich.panel import Panel
        from rich.markdown import Markdown
        # 内容当 Markdown 渲染（支持代码块、加粗等）
        c.print(Panel(Markdown(content), title=title, title_align="left", border_style=style))
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
