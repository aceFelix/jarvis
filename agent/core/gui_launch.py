"""GUI 启动命令识别（Bash 与窗口收尾清理共用）。

为什么单独成模块：
- ``agent/tools/bash.py`` 需要它把 ``cmd //c start`` / ``Start-Process`` 这类
  "拉起 GUI 后立刻返回"的命令按后台处理，否则沙箱会一直等 GUI 退出；
- ``agent/core/trace.py`` 需要它判断"这一步是用户要看的结果窗口"，收尾清理时
  不能把用户要求打开的浏览器/编辑器关掉（2026-11-08 实机：火狐被误关）。
两处必须用同一份口径，避免一边认、一边不认。

@author aceFelix
"""

from __future__ import annotations

import re

#: Windows 常见 GUI 启动写法。命中即视为"拉起一个可见窗口后立刻返回"。
GUI_LAUNCH_PATTERN = re.compile(
    r"(?:cmd\s+//c\s+start|(?:^|[;&|]\s*)start\s+[\"']?|Start-Process\b)",
    re.IGNORECASE,
)


def is_gui_launch(command: str) -> bool:
    """命令里是否包含 GUI 启动写法。

    Args:
        command: 完整的 shell 命令文本。

    Returns:
        True 表示该命令会拉起可见窗口（浏览器/编辑器等）。
    """
    return bool(command and GUI_LAUNCH_PATTERN.search(command))
