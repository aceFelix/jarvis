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


def _fmt_bytes(n: Any) -> str:
    """字节数 → 人读大小；缺失或非法值返回空串。"""
    if n is None:
        return ""
    try:
        value = int(n)
    except (TypeError, ValueError):
        return ""
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f}MB"
    if value >= 1024:
        return f"{int(value / 1024)}KB"
    return f"{value}B"


def _render_web_fetch(event: dict[str, Any]) -> str:
    """``web_fetch`` → ``🌐 GET url 200 · 12KB · 320ms``；缺失字段自动省略。"""
    head = f"🌐 GET {event.get('url') or '?'}"
    if event.get("http_status") is not None:
        head += f" {event.get('http_status')}"
    extras: list[str] = []
    size = _fmt_bytes(event.get("bytes"))
    if size:
        extras.append(size)
    duration = _fmt_duration(float(event.get("duration_ms") or 0))
    if duration:
        extras.append(duration)
    if event.get("status") == "error":
        extras.append("失败")
    if extras:
        head += " · " + " · ".join(extras)
    return head


def _render_web_extract(event: dict[str, Any]) -> str:
    """``web_extract`` → ``📋 url · 字段 3 个``。"""
    count = event.get("field_count")
    if count is None:
        count = len(event.get("fields") or {})
    return f"📋 {event.get('url') or '?'} · 字段 {count} 个"


def _render_crawl_stats(event: dict[str, Any]) -> str:
    """``crawl_stats`` → ``🕸 队列 12 · 完成 8 · 失败 1 · 去重 3``；缺失项省略。"""
    parts = ["🕸"]
    for label, key in (
        ("队列", "queued"),
        ("完成", "done"),
        ("失败", "failed"),
        ("去重", "dedup_hits"),
    ):
        if event.get(key) is not None:
            parts.append(f"{label} {event.get(key)}")
    return " · ".join(parts)


def _render_image_show(event: dict[str, Any]) -> str:
    """``image_show`` → ``🖼 ScreenShot · 2560×1440 · 180KB``；缺失项省略。"""
    parts = ["🖼"]
    source = str(event.get("source") or "")
    if source:
        parts.append(source)
    width = event.get("width")
    height = event.get("height")
    if width and height:
        parts.append(f"{width}×{height}")
    size = _fmt_bytes(event.get("bytes"))
    if size:
        parts.append(size)
    return " · ".join(parts)


def render_event(event: dict[str, Any], *, for_review: bool = False) -> str | None:
    """渲染单条事件；不需要上屏的事件返回 None。

    Args:
        event: ``agent/core/trace.py`` 写入的事件 dict。
        for_review: True 表示 ``/trace`` 回看。``web_fetch`` / ``web_extract`` /
            ``crawl_stats`` 只在回看时渲染——实时滚动区已由驾驶舱汇总，
            重复打印会污染输出（计划 §4.3）。

    Returns:
        一行终端文本；``run_start`` 等不渲染的事件返回 None。
    """
    etype = event.get("type")

    def _agent_tag(line: str) -> str:
        """``/trace`` 回看时标注子代理来源；实时区不重复标。"""
        name = str(event.get("agent_name") or "")
        if for_review and name:
            return f"{line} · @{name}"
        return line

    if etype == "tool_start":
        target = describe_target(event.get("target"))
        suffix = f"({target})" if target else ""
        return _agent_tag(f"▶ {event.get('tool', '?')}{suffix}")
    if etype == "tool_end":
        duration = _fmt_duration(float(event.get("duration_ms") or 0))
        if event.get("ok"):
            return _agent_tag(f"✓ {duration}")
        # 空错误文本时 splitlines() 返回 []：必须先判空再取首行，
        # 否则"失败但无错误摘要"的事件会把渲染路径抛成 IndexError。
        error_lines = str(event.get("error") or "").strip().splitlines()
        reason = error_lines[0][:_MAX_REASON] if error_lines else ""
        return _agent_tag(f"✗ {duration}" + (f" · {reason}" if reason else ""))
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
    if etype == "web_fetch":
        return _agent_tag(_render_web_fetch(event)) if for_review else None
    if etype == "web_extract":
        return _agent_tag(_render_web_extract(event)) if for_review else None
    if etype == "crawl_stats":
        return _render_crawl_stats(event) if for_review else None
    # 图片：实时区不刷屏（图片窗自己展示），只在 /trace 回看时留一行记录
    if etype == "image_show":
        return _agent_tag(_render_image_show(event)) if for_review else None
    return None


def render_run(events: list[dict[str, Any]]) -> list[str]:
    """把一轮事件渲染成多行（供 ``/trace`` 回看）。"""
    lines: list[str] = []
    for event in events:
        line = render_event(event, for_review=True)
        if line:
            lines.append(line)
    return lines
