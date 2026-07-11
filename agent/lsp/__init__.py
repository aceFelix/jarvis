"""LSP 集成模块。

提供代码智能能力：跳转定义、查引用、悬停信息、文档符号、工作区符号等。

对标 Claude Code 的 src/services/lsp/ 和 src/tools/LSPTool/。
"""

from agent.lsp.client import LSPClient
from agent.lsp.manager import LSPServerManager, get_lsp_manager

__all__ = ["LSPClient", "LSPServerManager", "get_lsp_manager"]
