"""UI 层。

v0.1 用 Rich 做一个够用的命令行交互界面: 流式输出、工具调用展示、用户输入、
权限确认提示。后续可升级到 Textual 做更丰富的 TUI。
"""

from agent.ui.cli import RichCLI

__all__ = ["RichCLI"]
