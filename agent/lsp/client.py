"""LSP 客户端 —— JSON-RPC over stdio 通信。

对标 Claude Code 的 src/services/lsp/LSPClient.ts。

LSP 协议基于 JSON-RPC 2.0，通过子进程的 stdin/stdout 传输。
每条消息前有 `Content-Length: N\r\n\r\n` 头，body 是 N 字节的 JSON。

本模块管理：
- subprocess 启动 LSP server（如 pylsp, typescript-language-server）
- 异步读写 stdio
- send_request / send_notification
- initialize / shutdown 生命周期
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any


class LSPClientError(Exception):
    """LSP 客户端错误。"""


class LSPClient:
    """单个 LSP server 的客户端。

    用法::

        client = LSPClient("pylsp", cwd="/path/to/project")
        await client.start()
        await client.initialize()
        result = await client.send_request("textDocument/definition", {...})
        await client.shutdown()
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        name: str = "",
    ) -> None:
        self._command = command
        self._args = args or []
        self._cwd = cwd
        self._env = env
        self._name = name or command

        self._process: asyncio.subprocess.Process | None = None
        self._initialized = False
        self._stopping = False
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reader_task: asyncio.Task | None = None
        self._notification_handlers: dict[str, list] = {}
        self._stderr_buf = bytearray()

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def is_alive(self) -> bool:
        return (
            self._process is not None
            and self._process.returncode is None
            and self._initialized
        )

    async def start(self) -> None:
        """启动 LSP server 子进程。"""
        full_env = dict(os.environ)
        if self._env:
            full_env.update(self._env)

        try:
            self._process = await asyncio.create_subprocess_exec(
                self._command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env=full_env,
            )
        except FileNotFoundError as e:
            raise LSPClientError(
                f"LSP server '{self._command}' 未找到。请安装后重试。"
            ) from e

        # 启动读取循环
        self._reader_task = asyncio.create_task(self._read_loop())

    async def initialize(
        self,
        *,
        root_path: str | None = None,
        root_uri: str | None = None,
        workspace_folders: list[dict] | None = None,
        init_opts: dict | None = None,
    ) -> dict[str, Any]:
        """发送 initialize 请求。"""
        if not self._process:
            raise LSPClientError("LSP server 未启动")

        params: dict[str, Any] = {
            "processId": os.getpid(),
            "capabilities": {
                "textDocument": {
                    "synchronization": {
                        "didOpen": True,
                        "didChange": True,
                        "didSave": True,
                        "didClose": True,
                    },
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "definition": {"linkSupport": False},
                    "references": {},
                    "documentSymbol": {
                        "hierarchicalDocumentSymbolSupport": True,
                    },
                    "workspaceSymbol": {},
                    "implementation": {},
                    "callHierarchy": {"dynamicRegistration": False},
                },
                "workspace": {
                    "symbol": {},
                    "workspaceFolders": True,
                },
            },
        }
        if root_path:
            params["rootPath"] = root_path
        if root_uri:
            params["rootUri"] = root_uri
        if workspace_folders:
            params["workspaceFolders"] = workspace_folders
        if init_opts:
            params["initializationOptions"] = init_opts

        result = await self.send_request("initialize", params)
        self._initialized = True

        # 发送 initialized 通知
        await self.send_notification("initialized", {})

        return result

    async def send_request(
        self, method: str, params: Any = None, *, timeout: float = 30.0
    ) -> Any:
        """发送 JSON-RPC 请求，等待响应。"""
        if not self._process or not self._process.stdin:
            raise LSPClientError("LSP server 不可用")

        msg_id = self._next_id
        self._next_id += 1

        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        await self._write_message(message)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise LSPClientError(f"LSP 请求超时: {method} ({timeout}s)")

    async def send_notification(self, method: str, params: Any = None) -> None:
        """发送 JSON-RPC 通知（无响应）。"""
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            message["params"] = params
        await self._write_message(message)

    def on_notification(self, method: str, handler) -> None:
        """注册通知处理器。"""
        self._notification_handlers.setdefault(method, []).append(handler)

    async def shutdown(self) -> None:
        """优雅关闭 LSP server。"""
        if not self._process:
            return

        self._stopping = True

        try:
            await self.send_request("shutdown", {}, timeout=5.0)
        except Exception:
            pass

        try:
            await self.send_notification("exit", None)
        except Exception:
            pass

        try:
            self._process.terminate()
            await asyncio.wait_for(self._process.wait(), timeout=3.0)
        except Exception:
            try:
                self._process.kill()
            except Exception:
                pass

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except Exception:
                pass

        self._initialized = False
        self._process = None

    # ---- 内部实现 ----

    async def _write_message(self, message: dict) -> None:
        if not self._process or not self._process.stdin:
            raise LSPClientError("LSP server stdin 不可用")

        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")

        self._process.stdin.write(header + body)
        await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        """读取 LSP server stdout，解析 JSON-RPC 消息。"""
        if not self._process or not self._process.stdout:
            return

        reader = self._process.stdout

        try:
            while True:
                # 读取 header
                headers = {}
                while True:
                    line = await reader.readline()
                    if not line:
                        return  # EOF

                    line_str = line.decode("ascii", errors="replace").strip()
                    if not line_str:
                        break  # header 结束

                    if ":" in line_str:
                        key, _, val = line_str.partition(":")
                        headers[key.strip().lower()] = val.strip()

                content_length = int(headers.get("content-length", "0"))
                if content_length == 0:
                    continue

                # 读取 body
                body = await reader.readexactly(content_length)
                try:
                    message = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    continue

                await self._handle_message(message)

        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _handle_message(self, message: dict) -> None:
        """处理收到的 JSON-RPC 消息。"""
        if "id" in message and ("result" in message or "error" in message):
            # 响应
            msg_id = message["id"]
            future = self._pending.pop(msg_id, None)
            if future and not future.done():
                if "error" in message:
                    future.set_exception(
                        LSPClientError(f"LSP 错误: {message['error']}")
                    )
                else:
                    future.set_result(message.get("result"))
        elif "method" in message:
            # 通知或请求
            method = message["method"]
            params = message.get("params")

            handlers = self._notification_handlers.get(method, [])
            for h in handlers:
                try:
                    result = h(params)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass


def path_to_uri(path: str) -> str:
    """文件路径 → file:// URI。"""
    abs_path = os.path.abspath(path)
    # Windows: E:\path → file:///E:/path
    if sys.platform == "win32":
        abs_path = abs_path.replace("\\", "/")
        if not abs_path.startswith("/"):
            abs_path = "/" + abs_path
    return f"file://{abs_path}"


def uri_to_path(uri: str) -> str:
    """file:// URI → 文件路径。"""
    if uri.startswith("file://"):
        path = uri[7:]
        # Windows: /E:/path → E:/path
        if sys.platform == "win32" and len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
    else:
        path = uri

    from urllib.parse import unquote
    return unquote(path)
