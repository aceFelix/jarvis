"""操作轨迹的终端渲染（纯函数，不依赖 Rich）。

为什么不直接用 Rich：
- trace 事件来自工具入参与窗口标题，是**不可控文本**，必须 escape 后才能交给
  ``RichCLI.info``（该方法不做 escape）；本模块统一在输出口做转义；
- 纯函数便于单测：输入事件 dict，输出一行字符串，不碰终端、不碰文件。

@author aceFelix
"""

from __future__ import annotations

import os
from typing import Any

_MAX_REASON = 80


def safe_info(ui: Any, text: str) -> None:
    """把纯文本安全地交给 ``ui.info``（转义 Rich 标记，防 MarkupError）。"""
    if ui is None:
        return
    try:
        from rich.markup import escape

        ui.info(escape(text))
    except Exception:
        ui.info(text)


def _fmt_duration(ms: float) -> str:
    """毫秒 → 人读时长：<1s 显示 ms，≥1s 显示秒。"""
    if ms < 1000:
        return f"{int(ms)}ms"
    return f"{ms / 1000:.1f}s"


def _basename(path: str) -> str:
    return os.path.basename(path.rstrip("/\\")) or path


def describe_target(target: dict[str, Any] | None) -> str:
    """把 target 描述成一行可读文本。"""
    if not target:
        return ""
    kind = target.get("kind")
    if kind == "window":
        return f"「{target.get('title', '')}」"
    if kind == "file":
        return _basename(str(target.get("path", "")))
    return str(target.get("path") or target.get("title") or "")


def render_event(event: dict[str, Any]) -> str | None:
    """渲染单条事件；不需要上屏的事件返回 None。

    Args:
        event: ``agent/core/trace.py`` 写入的事件 dict。

    Returns:
        一行终端文本；``run_start`` 等不渲染的事件返回 None。
    """
    etype = event.get("type")
    if etype == "tool_start":
        target = describe_target(event.get("target"))
        suffix = f"({target})" if target else ""
        return f"▶ {event.get('tool', '?')}{suffix}"
    if etype == "tool_end":
        duration = _fmt_duration(float(event.get("duration_ms") or 0))
        if event.get("ok"):
            return f"✓ {duration}"
        reason = str(event.get("error") or "").strip().splitlines()[0][:_MAX_REASON]
        return f"✗ {duration}" + (f" · {reason}" if reason else "")
    if etype == "file_change":
        diff = event.get("diff") or {}
        added = diff.get("added")
        removed = diff.get("removed")
        name = _basename(str(event.get("path", "")))
        if added is None and removed is None:
            return f"✎ {name}"
        return f"✎ {name}  +{added or 0} / -{removed or 0}"
    if etype == "window_action":
        return f"🪟 {event.get('message', '')}"
    if etype == "run_end":
        total = _fmt_duration(float(event.get("duration_ms") or 0))
        return (
            f"✓ 完成 · 共 {event.get('steps', 0)} 步 · "
            f"文件工具 {event.get('files_changed', 0)} 次 · "
            f"{event.get('failed', 0)} 失败 · {total}"
        )
    return None


def render_run(events: list[dict[str, Any]]) -> list[str]:
    """把一轮事件渲染成多行（供 ``/trace`` 回看）。"""
    lines: list[str] = []
    for event in events:
        line = render_event(event)
        if line:
            lines.append(line)
    return lines
