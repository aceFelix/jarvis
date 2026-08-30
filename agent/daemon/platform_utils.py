"""平台 / 依赖检测纯函数层（作者：aceFelix）。

集中管理跨平台判断与可选依赖检测，不持有任何状态。
原模块中的解释器定位 / 终端模拟器搜索 / detached 子进程启动等
函数随"无窗口 daemon"架构一并下线，仅保留全局热键所需的最小集合。
"""

from __future__ import annotations

import sys


# ---------------------------------------------------------------------------
# 平台判断
# ---------------------------------------------------------------------------

def _is_windows() -> bool:
    """是否运行在 Windows 平台。"""
    return sys.platform == "win32"


def _is_macos() -> bool:
    """是否运行在 macOS 平台。"""
    return sys.platform == "darwin"


# ---------------------------------------------------------------------------
# 可选依赖检测
# ---------------------------------------------------------------------------

def _has_keyboard() -> bool:
    """keyboard 库是否可用（语音打断/热键回退后端）。"""
    try:
        import keyboard  # noqa: F401
        return True
    except ImportError:
        return False


def _has_pynput() -> bool:
    """pynput 是否可用（macOS 热键首选后端，无需 root）。"""
    try:
        import pynput  # noqa: F401
        return True
    except ImportError:
        return False
