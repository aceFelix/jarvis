"""MCP 客户端 —— 连接外部 MCP server，获取其工具并转发调用。

MCP (Model Context Protocol) 是 Anthropic 开源的协议，让 AI 应用能连接
外部"server"进程获取额外工具/资源。比如连一个 filesystem server 获得
文件操作工具，连一个 git server 获得 git 操作工具。

本模块封装 stdio transport（最常见，起子进程通过 stdin/stdout JSON-RPC 通信）。
SSE/HTTP transport 后续按需加。

依赖: pip install mcp（mcp Python SDK）。未安装时所有 MCP 功能优雅降级（跳过）。

配置: ~/.jarvis/mcp.json
    {
      "mcpServers": {
        "filesystem": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        },
        "git": {
          "command": "uvx",
          "args": ["mcp-server-git", "--repository", "/path/to/repo"]
        }
      }
    }

密钥安全: env 和 args 中的字符串支持 ${VAR} 变量引用，运行时从系统环境变量取值，
mcp.json 不再需要写明文密钥。例如:
    "env": { "GITHUB_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}" }
    "args": ["--header", "Authorization: ${TYC_TOKEN}"]
支持默认值: "${VAR:-default}"。变量不存在时保留原样不展开。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---- 抑制 MCP stdio_client async generator GC 时的噪音 ----
# stdio_client 内部用 anyio.create_task_group()，cancel scope 必须在创建它的
# 同一 task 中退出。当连接在不同 task 中关闭（或 GC 回收 generator 时），
# anyio 抛出 "Attempted to exit cancel scope in a different task"。
# Python runtime 通过 warnings 系统 RuntimeWarning 打印完整 traceback
# ("an error occurred during closing of asynchronous generator")。这不影响功能
# （连接已在 _cleanup_connection 中尽力清理），只是 GC 噪音。
#
# 三层过滤：
# 1. warnings.filterwarnings —— 阻止 RuntimeWarning 打印（主要生效层）
# 2. sys.unraisablehook —— 兜底，过滤 unraisable exception
# 3. sys.setasyncgenhooks —— 兜底，自定义 async generator GC finalizer

warnings.filterwarnings(
    "ignore",
    message=r".*closing of asynchronous generator.*",
    category=RuntimeWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r".*Attempted to exit cancel scope in a different task.*",
    category=RuntimeWarning,
)

_orig_unraisablehook = getattr(sys, "unraisablehook", None)


def _filtered_unraisablehook(args, /):
    """过滤 stdio_client 相关的 unraisable exception，其余交给原 hook。"""
    err_msg = getattr(args, "err_msg", None) or ""
    if "closing of asynchronous generator" in err_msg:
        return
    exc = getattr(args, "exc", None)
    if exc is not None and "cancel scope" in str(exc):
        return
    if _orig_unraisablehook:
        _orig_unraisablehook(args)


sys.unraisablehook = _filtered_unraisablehook

# 自定义 async generator finalizer：GC 时静默关闭，不打印错误
_default_asyncgen_finalizer = None


def _silent_asyncgen_finalizer(agen):
    """GC 回收 async generator 时静默关闭，抑制 stdio_client 的 cleanup 错误。"""
    import asyncio as _asyncio
    try:
        coro = agen.aclose()
        # finalizer 在 GC 时调用，可能没有运行中的 event loop。
        # 尝试用现有 loop 调度 aclose()；若没有 loop 则同步关闭。
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(coro)
            else:
                loop.run_until_complete(coro)
        except RuntimeError:
            # 没有 event loop，直接忽略（generator 会被 Python 自动清理）
            coro.close()
    except BaseException:
        pass


# 安装 finalizer（仅对 Python 3.11+ 生效）
try:
    _default_asyncgen_finalizer = sys.getasyncgenhooks()[0]
    sys.setasyncgenhooks(finalizer=_silent_asyncgen_finalizer)
except (AttributeError, IndexError):
    pass


def _expand_vars(value: Any) -> Any:
    """递归展开字符串中的 ${VAR} 变量引用，从系统环境变量取值。

    支持: "${VAR}" / "prefix-${VAR}-suffix" / "${VAR:-default}"
    变量不存在时保留原样（不展开），让 server 报鉴权失败，用户能发现配置问题。
    递归处理 dict / list / str。
    """
    if isinstance(value, str):
        def _repl(m: re.Match) -> str:
            spec = m.group(1)
            # 支持 VAR 和 VAR:-default 两种形式
            if ":-" in spec:
                name, default = spec.split(":-", 1)
                return os.environ.get(name.strip(), default)
            return os.environ.get(spec.strip(), m.group(0))
        return re.sub(r"\$\{([^}]+)\}", _repl, value)
    if isinstance(value, dict):
        return {k: _expand_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_vars(v) for v in value]
    return value


def mcp_config_path() -> Path:
    """MCP 配置文件: ~/.jarvis/mcp.json"""
    return Path.home() / ".jarvis" / "mcp.json"


def load_mcp_config() -> dict[str, dict[str, Any]]:
    """加载 MCP 配置。返回 {server_name: {command, args, env?}} 字典。

    文件不存在或格式错误返回空字典。
    """
    path = mcp_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        return {}
    return servers


def save_mcp_config(config: dict[str, Any]) -> None:
    """写回 MCP 配置到 ~/.jarvis/mcp.json。config 格式: {"mcpServers": {...}}。"""
    path = mcp_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def merge_mcp_config(new_servers: dict[str, dict[str, Any]]) -> list[str]:
    """合并新 MCP server 到 ~/.jarvis/mcp.json。同名 server 跳过不覆盖。返回新增的 server 名列表。"""
    current = load_mcp_config()
    added: list[str] = []
    for name, cfg in new_servers.items():
        if name not in current:
            current[name] = cfg
            added.append(name)
    if added:
        save_mcp_config({"mcpServers": current})
    return added


def remove_mcp_config(server_names: list[str]) -> int:
    """从 ~/.jarvis/mcp.json 中移除指定 MCP server。返回实际移除数量。"""
    current = load_mcp_config()
    removed = 0
    for name in server_names:
        if name in current:
            del current[name]
            removed += 1
    if removed:
        save_mcp_config({"mcpServers": current})
    return removed


@dataclass
class McpToolDef:
    """一个 MCP server 暴露的工具定义。"""
    server_name: str       # 所属 server 名
    name: str              # 工具名（server 内唯一）
    description: str
    input_schema: dict[str, Any]


@dataclass
class McpConnection:
    """一个已连接的 MCP server 会话。"""
    name: str
    config: dict[str, Any]
    tools: list[McpToolDef] = field(default_factory=list)
    _session: Any = None        # mcp.ClientSession
    _stdio_ctx: Any = None      # stdio_client async context
    _session_ctx: Any = None    # ClientSession async context
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def connected(self) -> bool:
        return self._session is not None


def _is_mcp_available() -> bool:
    """检查 mcp SDK 是否已安装。"""
    try:
        import mcp  # noqa: F401
        return True
    except ImportError:
        return False


class MCPClient:
    """管理多个 MCP server 连接。

    用法:
        client = MCPClient()
        await client.connect_all(config)  # 连接所有配置的 server
        tools = client.list_tools()       # 获取所有 server 的工具
        result = await client.call_tool("server_name", "tool_name", args)
        await client.disconnect_all()
    """

    def __init__(self) -> None:
        self._connections: dict[str, McpConnection] = {}
        self._available = _is_mcp_available()
        # 熔断器：{server_name: (fail_count, first_fail_time)}
        self._breaker: dict[str, tuple[int, float]] = {}
        self._BREAKER_MAX_FAILS = 3
        self._BREAKER_COOLDOWN = 300  # 5 分钟冷却

    @property
    def available(self) -> bool:
        """mcp SDK 是否可用。"""
        return self._available

    async def connect(self, name: str, config: dict[str, Any]) -> McpConnection | None:
        """连接单个 MCP server。失败返回 None（不抛异常，调用方按需处理）。"""
        if not self._available:
            return None

        command = config.get("command")
        if not command:
            return None

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            return None

        args = _expand_vars(config.get("args", []))
        env = _expand_vars(config.get("env"))
        # 合并环境变量（继承当前进程环境 + server 配置的 env）
        full_env = dict(os.environ)
        if isinstance(env, dict):
            full_env.update(env)

        params = StdioServerParameters(
            command=command,
            args=list(args),
            env=full_env,
        )

        conn = McpConnection(name=name, config=config)

        try:
            # stdio_client 是 async context manager，需手动管理生命周期
            conn._stdio_ctx = stdio_client(params)
            read, write = await conn._stdio_ctx.__aenter__()

            conn._session_ctx = ClientSession(read, write)
            conn._session = await conn._session_ctx.__aenter__()

            await conn._session.initialize()

            # 列出工具
            result = await conn._session.list_tools()
            for tool in result.tools:
                conn.tools.append(McpToolDef(
                    server_name=name,
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema or {"type": "object", "properties": {}},
                ))

            self._connections[name] = conn
            return conn
        except Exception:
            # 连接失败: 清理半开的连接
            await self._cleanup_connection(conn)
            return None

    async def connect_all(self, config: dict[str, dict[str, Any]]) -> dict[str, bool]:
        """并发连接配置中的所有 server。返回 {server_name: 是否成功}。

        P-01 改进：原串行 for 循环改为 asyncio.gather 并发连接，
        7 个 MCP server 从串行 7×T 降到 max(T)，启动提速明显。

        @author aceFelix
        """
        async def _connect_one(name: str, cfg: dict[str, Any]) -> tuple[str, bool]:
            conn = await self.connect(name, cfg)
            return name, conn is not None

        tasks = [_connect_one(name, cfg) for name, cfg in config.items()]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        results: dict[str, bool] = {}
        for item in gathered:
            if isinstance(item, BaseException):
                continue  # 异常视为连接失败
            name, ok = item
            results[name] = ok
        return results

    def list_tools(self) -> list[McpToolDef]:
        """列出所有已连接 server 的全部工具。"""
        tools: list[McpToolDef] = []
        for conn in self._connections.values():
            tools.extend(conn.tools)
        return tools

    def list_connections(self) -> list[McpConnection]:
        """列出所有连接。"""
        return list(self._connections.values())

    async def call_tool(self, server_name: str, tool_name: str, args: dict[str, Any]) -> str:
        """调用某个 server 的某个工具。返回结果文本。

        熔断器：同一 server 连续失败 3 次后暂停 5 分钟，避免每次都卡超时。
        成功调用后重置计数器。

        Raises:
            KeyError: server 未连接或已熔断
            RuntimeError: 调用失败
        """
        # 熔断器检查
        import time as _time
        now = _time.time()
        if server_name in self._breaker:
            fail_count, first_fail = self._breaker[server_name]
            if fail_count >= self._BREAKER_MAX_FAILS:
                if now - first_fail < self._BREAKER_COOLDOWN:
                    raise KeyError(f"MCP server [{server_name}] 已熔断 ({fail_count} 次失败，"
                                   f"{(self._BREAKER_COOLDOWN - (now - first_fail)):.0f}s 后重试)")
                else:
                    # 冷却期过，重置
                    del self._breaker[server_name]

        conn = self._connections.get(server_name)
        if not conn or not conn._session:
            raise KeyError(f"MCP server 未连接: {server_name}")

        async with conn._lock:
            try:
                result = await conn._session.call_tool(tool_name, args)
            except Exception as e:
                # 记录失败
                if server_name not in self._breaker:
                    self._breaker[server_name] = (0, now)
                fc, first = self._breaker[server_name]
                self._breaker[server_name] = (fc + 1, first)
                raise RuntimeError(f"MCP 调用失败 [{server_name}.{tool_name}]: {e}") from e

        # 成功 → 重置熔断器
        self._breaker.pop(server_name, None)

        # 把结果 content 拼成文本
        parts: list[str] = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            elif hasattr(block, "type") and block.type == "text":
                parts.append(getattr(block, "text", ""))
            else:
                parts.append(str(block))
        return "\n".join(parts)

    async def disconnect(self, name: str) -> None:
        """断开单个 server。"""
        conn = self._connections.pop(name, None)
        if conn:
            await self._cleanup_connection(conn)

    async def disconnect_all(self) -> None:
        """断开所有 server。"""
        conns = list(self._connections.values())
        self._connections.clear()
        for conn in conns:
            await self._cleanup_connection(conn)

    async def _cleanup_connection(self, conn: McpConnection) -> None:
        """清理连接资源（关闭 session 和 stdio）。

        stdio_client 是 async generator，内部用 anyio.create_task_group()。
        优先用 __aexit__ 正常退出（让 generator 的 finally 块正常执行）。
        若 __aexit__ 抛错（如 cancel scope 跨 task），再用 aclose() 兜底。
        所有错误都吞掉——即使清理失败，连接已不可用，warnings 已被模块级
        filterwarnings 抑制。
        """
        # 1. 先关 session
        try:
            if conn._session_ctx:
                await conn._session_ctx.__aexit__(None, None, None)
        except BaseException:
            pass
        conn._session = None
        conn._session_ctx = None

        # 2. 再关 stdio_client（async generator）
        stdio_gen = conn._stdio_ctx
        conn._stdio_ctx = None  # 先置 None，避免 GC 时再次尝试关闭
        if stdio_gen is not None:
            # 优先 __aexit__（正常退出路径，让 finally 块运行）
            try:
                await stdio_gen.__aexit__(None, None, None)
                return
            except BaseException:
                pass
            # __aexit__ 失败，尝试 aclose() 强制关闭
            try:
                await stdio_gen.aclose()
            except BaseException:
                pass

