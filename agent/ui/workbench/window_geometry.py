"""工作台窗口几何工具：主屏工作区（不含任务栏）获取。

无边框透明窗口"铺满"的统一口径是工作区而非全屏——真全屏会盖住
桌面任务栏（用户实测反馈），故建窗与"全屏"切换共用此函数。

@author aceFelix
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes


def work_area() -> tuple[int, int, int, int]:
    """主屏工作区（不含任务栏）：(x, y, 宽, 高)。

    无边框窗口铺满它 = 等效最大化且保留任务栏。
    不能用 WinForms 的 maximized：无边框最大化会盖住任务栏，
    且最大化窗口与像素级透明（AllowTransparency）互斥会抛异常。
    非 Windows / 调用失败时降级为常规窗口尺寸。
    """
    try:
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        return 0, 0, 1280, 800
