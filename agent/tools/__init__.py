"""内置工具集合。

对应原项目 src/tools/（42 个工具目录）。v0.1 实现 8 个核心工具 + 阶段二新增的
GUI 工具（鼠标/键盘/屏幕/窗口）+ 浏览器工具（导航/截图/点击/输入/取文本/关闭），
覆盖"对话 + 文件 + 命令 + 用户交互 + 电脑操作 + 网页操作"。

每个工具文件一个 class，继承 agent.core.tool.Tool，注册到 build_default_registry()。
GUI 工具依赖 pyautogui/pygetwindow，浏览器工具依赖 playwright，未安装时注册自动跳过。
"""

from agent.tools.ask_user import AskUserTool
from agent.tools.bash import BashTool
from agent.tools.file_edit import FileEditTool
from agent.tools.file_read import FileReadTool
from agent.tools.file_write import FileWriteTool
from agent.tools.glob import GlobTool
from agent.tools.grep import GrepTool
from agent.tools.todo import TodoWriteTool
from agent.tools.location import LocationTool
from agent.tools.marketplace_tool import MarketSearchTool
from agent.tools.web import WebFetchTool, WebSearchTool

__all__ = [
    "AskUserTool",
    "BashTool",
    "FileEditTool",
    "FileReadTool",
    "FileWriteTool",
    "GlobTool",
    "GrepTool",
    "LocationTool",
    "MarketSearchTool",
    "TodoWriteTool",
    "WebFetchTool",
    "WebSearchTool",
]

# GUI 工具可选导入（依赖未安装时不影响基础工具）
try:
    from agent.tools.mouse import MouseClickTool, MouseMoveTool, MouseScrollTool
    from agent.tools.keyboard import TypeTextTool, KeyTapTool
    from agent.tools.screen import GetScreenSizeTool, ScreenShotTool
    from agent.tools.window import (
        WindowListTool,
        WindowFocusTool,
        WindowCloseTool,
        WindowMoveTool,
    )

    __all__ += [
        "MouseClickTool",
        "MouseMoveTool",
        "MouseScrollTool",
        "TypeTextTool",
        "KeyTapTool",
        "GetScreenSizeTool",
        "ScreenShotTool",
        "WindowListTool",
        "WindowFocusTool",
        "WindowCloseTool",
        "WindowMoveTool",
    ]
except ImportError:
    pass

# 浏览器工具可选导入（依赖 playwright，未安装时不影响基础工具）
try:
    from agent.tools.browser import (
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
