"""终端 UI 层 —— 纯命令行界面，只服务 REPL。

v0.1 用 Rich 做一个够用的命令行交互界面: 流式输出、工具调用展示、用户输入、
权限确认提示。后续可升级到 Textual 做更丰富的 TUI。

本包只做终端渲染，不包含任何图形化代码。``jarvis --serve`` 与 jarvis-desktop
共用的对话引擎层在 ``agent/serve/engine/``（原名 ``agent/ui/workbench/``，
2026-11 迁出）；三栏 pywebview 窗口壳及其 ``assets/`` 已于 2026-10 删除。
图形化界面不在本仓，在独立仓库 jarvis-desktop。
"""

from agent.ui.cli import RichCLI

__all__ = ["RichCLI"]
