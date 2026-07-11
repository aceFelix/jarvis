"""工具公共基类与辅助函数。

提供路径解析、结果落盘、JSON schema 构造等通用能力，避免每个工具重复实现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.core.context import ToolContext


def resolve_path(ctx: ToolContext, raw: str) -> Path:
    """把 raw 路径解析为绝对路径（相对 ctx.workdir，展开 ~）。

    所有文件类工具都用这个，保证路径处理一致。
    兼容 Git Bash 风格路径（/e/... /c/...）→ Windows 盘符。
    """
    import re
    import sys

    raw = raw.strip()

    # Windows: Git Bash 风格路径 /e/path → E:\path
    if sys.platform == "win32":
        m = re.match(r'^/([a-zA-Z])/(.*)$', raw)
        if m:
            drive = m.group(1).upper()
            rest = m.group(2)
            return Path(f"{drive}:/{rest}")

    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path(ctx.workdir) / p
    return p


def truncate_for_llm(text: str, max_chars: int, *, preview: int = 500) -> str:
    """超长文本截断，保留首尾 + 中间省略提示。

    对应原项目工具结果落盘逻辑的简化版（v0.1 不落盘，直接截断回传）。
    """
    if len(text) <= max_chars:
        return text
    head = text[: preview]
    tail = text[-preview:]
    omitted = len(text) - 2 * preview
    return f"{head}\n\n... [省略 {omitted} 字符] ...\n\n{tail}"
