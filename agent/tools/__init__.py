"""内置工具集合。
v0.1 实现 8 个核心工具 + 阶段二新增的
GUI 工具（鼠标/键盘/屏幕/窗口）+ P1 增强的 GUI 工具（拖拽/等待/窗口相对坐标/视觉定位）
+ 浏览器工具（导航/截图/点击/输入/取文本/关闭），
覆盖"对话 + 文件 + 命令 + 用户交互 + 电脑操作 + 网页操作"。

每个工具文件一个 class，继承 agent.core.tool.Tool，注册到 build_default_registry()。
GUI 工具依赖 pyautogui/pygetwindow，浏览器工具依赖 playwright，未安装时注册自动跳过。
"""

from agent.tools.ask_user import AskUserTool
from agent.tools.bash import BashTool
from agent.tools.file_ops.file_edit import FileEditTool
from agent.tools.file_ops.file_read import FileReadTool
from agent.tools.file_ops.file_write import FileWriteTool
from agent.tools.file_ops.glob import GlobTool
from agent.tools.file_ops.grep import GrepTool
from agent.tools.todo import TodoWriteTool
from agent.tools.location import LocationTool
from agent.tools.web.web import WebFetchTool, WebSearchTool

__all__ = [
    "AskUserTool",
    "BashTool",
    "FileEditTool",
    "FileReadTool",
    "FileWriteTool",
    "GlobTool",
    "GrepTool",
    "LocationTool",
    "TodoWriteTool",
    "WebFetchTool",
    "WebSearchTool",
]

# GUI 工具可选导入（依赖未安装时不影响基础工具）
try:
    from agent.tools.system.mouse import (
        MouseClickTool,
        MouseDragTool,
        MouseMoveTool,
        MouseScrollTool,
    )
    from agent.tools.system.keyboard import TypeTextTool, KeyTapTool
    from agent.tools.system.screen import GetScreenSizeTool, ScreenShotTool, WaitForTool
    from agent.tools.system.window import (
        WindowListTool,
        WindowFocusTool,
        WindowCloseTool,
        WindowMoveTool,
        WindowRectTool,
        WindowClickTool,
    )
    from agent.tools.system.gui_vision import VisualClickTool

    __all__ += [
        "MouseClickTool",
        "MouseDragTool",
        "MouseMoveTool",
        "MouseScrollTool",
        "TypeTextTool",
        "KeyTapTool",
        "GetScreenSizeTool",
        "ScreenShotTool",
        "WaitForTool",
        "WindowListTool",
        "WindowFocusTool",
        "WindowCloseTool",
        "WindowMoveTool",
        "WindowRectTool",
        "WindowClickTool",
        "VisualClickTool",
    ]
except ImportError:
    pass

# 浏览器工具可选导入（依赖 playwright，未安装时不影响基础工具）
try:
    from agent.tools.web.browser import (
        BrowserClickTool,
        BrowserCloseTool,
        BrowserGetTextTool,
        BrowserNavigateTool,
        BrowserScreenshotTool,
        BrowserTypeTool,
    )

    __all__ += [
        "BrowserNavigateTool",
        "BrowserScreenshotTool",
        "BrowserClickTool",
        "BrowserTypeTool",
        "BrowserGetTextTool",
        "BrowserCloseTool",
    ]
except ImportError:
    pass
