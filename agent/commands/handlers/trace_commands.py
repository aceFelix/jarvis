"""`/trace` 命令处理器：回看最近一轮操作轨迹、管理开关与导出。

口径（2026-11 操作可见性）：
- 不带参数 = 看最近一轮（当前轮有活动时优先看当前轮）；
- 只读查询不改变运行状态；``on`` / ``off`` 是运行时开关，不写回 TOML；
- 导出只打印文件路径，不复制内容，避免把隐私数据再散一份。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.core.trace import get_recorder
from agent.ui.trace_view import render_run, safe_info

if TYPE_CHECKING:
    from agent.commands.router import CommandContext

_HELP = (
    "/trace            查看最近一轮操作轨迹",
    "/trace list       列出最近的轨迹文件",
    "/trace path       显示轨迹目录",
    "/trace export     显示当前/最近轨迹文件的完整路径",
    "/trace on|off     运行时开关（不写回 settings.toml）",
)


def handle_trace(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /trace 及其子命令。

    Args:
        ctx: REPL 共享状态（只用到 ui）。
        stripped: 原始命令文本。

    Returns:
        固定 True（命令已处理）。
    """
    recorder = get_recorder()
    if recorder is None:
        safe_info(ctx.ui, "操作轨迹未启用：observability 未装配或安装失败")
        return True

    parts = stripped.split(maxsplit=1)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if sub in ("", "last"):
        events = recorder.recent_events()
        if not events:
            safe_info(ctx.ui, "还没有可回看的操作轨迹（先发一条需要动手的指令）")
            return True
        for line in render_run(events):
            safe_info(ctx.ui, line)
        return True

    if sub == "list":
        runs = recorder.list_runs(limit=10)
        if not runs:
            safe_info(ctx.ui, "还没有轨迹文件")
            return True
        for path in runs:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            safe_info(ctx.ui, f"  {path.name}  ({size} bytes)")
        return True

    if sub == "path":
        safe_info(ctx.ui, f"轨迹目录：{recorder.base_dir}")
        return True

    if sub == "export":
        target = recorder.last_run_file
        if target is None and recorder.run_id:
            target = recorder._run_path()
        if target is None:
            safe_info(ctx.ui, "当前没有轨迹文件")
        else:
            safe_info(ctx.ui, f"轨迹文件：{target}")
        return True

    if sub in ("on", "off"):
        recorder.set_enabled(sub == "on")
        safe_info(ctx.ui, "操作轨迹与窗口跟随已开启" if sub == "on" else "操作轨迹与窗口跟随已关闭")
        return True

    for line in _HELP:
        safe_info(ctx.ui, line)
    return True
