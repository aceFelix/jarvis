"""UI 层。

对应原项目 components/（144 个组件）+ ink/。原版用 React+Ink 做了完整 TUI，
v0.1 用 Rich 做一个够用的命令行交互界面: 流式输出、工具调用展示、用户输入、
权限确认提示。后续可升级到 Textual 做更丰富的 TUI。
"""

from agent.ui.cli import RichCLI

__all__ = ["RichCLI"]
