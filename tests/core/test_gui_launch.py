"""GUI 启动命令识别的单元测试。

同一份口径被两处使用：
- ``agent/tools/bash.py``：GUI 命令按后台处理，避免沙箱等 GUI 退出；
- ``agent/core/trace.py``：收尾清理保留这类命令打开的窗口（用户要看的结果）。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.core.gui_launch import is_gui_launch


@pytest.mark.parametrize(
    "command",
    [
        'cmd //c start "" "E:/work/snake/index.html"',
        'start "" "C:/Program Files/Mozilla Firefox/firefox.exe"',
        "Start-Process firefox",
        "cd /e/work && start index.html",
    ],
)
def test_gui_launch_detected(command: str) -> None:
    """常见 Windows GUI 启动写法都要识别出来。"""
    assert is_gui_launch(command)


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "python app.py",
        "echo start",
        "grep -i start file.txt",
        "",
    ],
)
def test_non_gui_launch_ignored(command: str) -> None:
    """普通命令不能被误判成 GUI 启动。"""
    assert not is_gui_launch(command)
