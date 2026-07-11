"""LSP Server Manager —— 多 server 路由 + 文件追踪。

对标 Claude Code 的 src/services/lsp/LSPServerManager.ts。

按文件扩展名路由到对应的 LSP server（如 .py → pylsp, .ts → tsserver）。
管理文件打开/关闭状态，把工具调用转发到正确的 server。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent.lsp.client import LSPClient, LSPClientError, path_to_uri, uri_to_path


class LSPServerConfig:
    """单个 LSP server 配置。"""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        extensions: list[str] | None = None,
        env: dict[str, str] | None = None,
        init_options: dict | None = None,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.extensions = [e.lower().lstrip(".") for e in (extensions or [])]
        self.env = env
        self.init_options = init_options


class LSPServerManager:
    """管理多个 LSP server，按文件扩展名路由。"""

    def __init__(self, root_path: str = "") -> None:
        self._root_path = root_path or os.getcwd()
        self._root_uri = path_to_uri(self._root_path)
        self._configs: dict[str, LSPServerConfig] = {}
        self._ext_to_server: dict[str, str] = {}
        self._clients: dict[str, LSPClient] = {}
        self._opened_files: set[str] = set()

    @property
    def root_path(self) -> str:
        return self._root_path

    def add_server(self, config: LSPServerConfig) -> None:
        """注册一个 LSP server 配置。"""
        self._configs[config.name] = config
        for ext in config.extensions:
            self._ext_to_server[ext] = config.name

    def get_server_name_for_file(self, file_path: str) -> str | None:
        """根据文件扩展名找到对应的 server 名。"""
        ext = Path(file_path).suffix.lower().lstrip(".")
        return self._ext_to_server.get(ext)

    def get_client(self, name: str) -> LSPClient | None:
        """获取已启动的 server client。"""
        return self._clients.get(name)

    def is_file_open(self, file_path: str) -> bool:
        """检查文件是否已在 LSP server 中打开。"""
        return path_to_uri(file_path) in self._opened_files

    async def ensure_server_started(self, file_path: str) -> LSPClient | None:
        """确保文件对应的 LSP server 已启动。返回 client 或 None。"""
        server_name = self.get_server_name_for_file(file_path)
        if not server_name:
            return None

        client = self._clients.get(server_name)
        if client and client.is_alive:
            return client

        # 启动新 server
        config = self._configs[server_name]
        client = LSPClient(
            command=config.command,
            args=config.args,
            cwd=self._root_path,
            env=config.env,
            name=config.name,
        )

        try:
            await client.start()
            await client.initialize(
                root_path=self._root_path,
                root_uri=self._root_uri,
                init_opts=config.init_options,
            )
        except LSPClientError:
            return None

        self._clients[server_name] = client
        return client

    async def open_file(self, file_path: str, content: str | None = None) -> None:
        """在 LSP server 中打开文件（发送 didOpen）。"""
        client = await self.ensure_server_started(file_path)
        if not client:
            return

        uri = path_to_uri(file_path)

        if content is None:
            try:
                content = Path(file_path).read_text(encoding="utf-8")
            except Exception:
                return

        ext = Path(file_path).suffix.lstrip(".")
        language_id = _guess_language_id(ext)

        await client.send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": language_id,
                "version": 1,
                "text": content,
            }
        })
        self._opened_files.add(uri)

    async def change_file(self, file_path: str, content: str) -> None:
        """通知 LSP server 文件内容已变更（didChange）。"""
        client = await self.ensure_server_started(file_path)
        if not client:
            return

        uri = path_to_uri(file_path)
        await client.send_notification("textDocument/didChange", {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{"text": content}],
        })

    async def close_file(self, file_path: str) -> None:
        """通知 LSP server 文件已关闭（didClose）。"""
        uri = path_to_uri(file_path)
        if uri not in self._opened_files:
            return

        server_name = self.get_server_name_for_file(file_path)
        if not server_name:
            return

        client = self._clients.get(server_name)
        if not client:
            return

        await client.send_notification("textDocument/didClose", {
            "textDocument": {"uri": uri}
        })
        self._opened_files.discard(uri)

    async def send_request(
        self, file_path: str, method: str, params: Any
    ) -> Any:
        """向文件对应的 LSP server 发送请求。"""
        client = await self.ensure_server_started(file_path)
        if not client:
            return None

        # 确保文件已打开
        if not self.is_file_open(file_path):
            await self.open_file(file_path)

        try:
            return await client.send_request(method, params, timeout=30.0)
        except LSPClientError:
            return None

    def on_notification(self, method: str, handler) -> None:
        """注册通知处理器到所有已启动的 server。"""
        for client in self._clients.values():
            client.on_notification(method, handler)

    async def shutdown_all(self) -> None:
        """关闭所有 LSP server。"""
        for client in list(self._clients.values()):
            try:
                await client.shutdown()
            except Exception:
                pass
        self._clients.clear()
        self._opened_files.clear()


# ---- 语言 ID 映射 ----

_LANGUAGE_IDS = {
    "py": "python",
    "ts": "typescript",
    "tsx": "typescriptreact",
    "js": "javascript",
    "jsx": "javascriptreact",
    "go": "go",
    "rs": "rust",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "h": "c",
    "hpp": "cpp",
    "rb": "ruby",
    "php": "php",
    "swift": "swift",
    "kt": "kotlin",
    "scala": "scala",
    "sh": "shellscript",
    "bash": "shellscript",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "html": "html",
    "css": "css",
    "scss": "scss",
    "sql": "sql",
    "md": "markdown",
    "xml": "xml",
    "toml": "toml",
}


def _guess_language_id(ext: str) -> str:
    return _LANGUAGE_IDS.get(ext.lower(), ext)


# ---- 全局单例 ----

_global_manager: LSPServerManager | None = None


def init_lsp_manager(root_path: str, configs: list[LSPServerConfig]) -> LSPServerManager:
    """初始化全局 LSP manager。"""
    global _global_manager
    _global_manager = LSPServerManager(root_path=root_path)
    for config in configs:
        _global_manager.add_server(config)
    return _global_manager


def get_lsp_manager() -> LSPServerManager | None:
    """获取全局 LSP manager（未初始化返回 None）。"""
    return _global_manager


def load_lsp_config(settings) -> list[LSPServerConfig]:
    """从 settings 加载 LSP 配置。"""
    configs: list[LSPServerConfig] = []

    lsp_settings = getattr(settings, "lsp_servers", None) or {}
    if not lsp_settings:
        # 默认配置：Python 用 pylsp（如果安装了）
        # 用户可在 settings.toml [lsp.servers] 自定义
        return configs

    for name, cfg in lsp_settings.items():
        if not cfg.get("command"):
            continue
        configs.append(LSPServerConfig(
            name=name,
            command=cfg["command"],
            args=cfg.get("args", []),
            extensions=cfg.get("extensions", []),
            env=cfg.get("env"),
            init_options=cfg.get("init_options"),
        ))

    return configs
