"""MCP 客户端模块单元测试。

覆盖 agent/core/extensions/mcp_client.py:
- _expand_vars: ${VAR} / ${VAR:-default} 变量展开（含 dict/list 递归）
- 配置读写: mcp_config_path / load_mcp_config / save_mcp_config /
  merge_mcp_config / remove_mcp_config
- MCPClient: connect / connect_all / list_tools / list_connections /
  call_tool（含熔断器）/ disconnect / disconnect_all / _cleanup_connection
- 模块级噪音抑制: _filtered_unraisablehook / _silent_asyncgen_finalizer

说明:
- 通过向 sys.modules 注入假 mcp SDK 模块模拟真实 MCP 连接，
  不启动真实子进程、不依赖网络。
- 配置读写通过 monkeypatch Path.home() 重定向到临时目录。
- 不修改被测源码。

@author aceFelix
"""

from __future__ import annotations

import builtins
import json
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from agent.core.extensions import mcp_client as mcp


# =====================================================================
# 假 mcp SDK
# =====================================================================

class FakeTool:
    """模拟 mcp 的 Tool 对象。"""

    def __init__(self, name: str, description: str = "", inputSchema=None):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema or {"type": "object", "properties": {}}


class FakeMCPWorld:
    """测试世界状态：控制假 session 的行为。"""

    def __init__(self) -> None:
        self.tools: list[FakeTool] = []
        self.call_result = None
        self.call_error: Exception | None = None
        self.initialize_error: Exception | None = None
        self.sessions: list = []
        self.stdio_params: list = []
        self.initialize_calls = 0


def make_session_class(world: FakeMCPWorld):
    """按 world 状态生成假 ClientSession 类。"""

    class FakeSession:
        def __init__(self, read, write):
            self.read = read
            self.write = write
            world.sessions.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def initialize(self):
            world.initialize_calls += 1
            if world.initialize_error:
                raise world.initialize_error

        async def list_tools(self):
            return NS(tools=list(world.tools))

        async def call_tool(self, name, args):
            if world.call_error:
                raise world.call_error
            return world.call_result

    return FakeSession


def make_stdio_class(world: FakeMCPWorld):
    """按 world 状态生成假 stdio_client 上下文管理器。"""

    class FakeStdioClient:
        def __init__(self, params):
            self.params = params
            self.aexit_error: Exception | None = None
            self.aclosed = False
            world.stdio_params.append(params)

        async def __aenter__(self):
            return ("read-stream", "write-stream")

        async def __aexit__(self, *a):
            if self.aexit_error:
                raise self.aexit_error
            return False

        async def aclose(self):
            self.aclosed = True

    return FakeStdioClient


@pytest.fixture
def mcp_world(monkeypatch):
    """向 sys.modules 注入假 mcp SDK，测试结束后恢复原模块。"""
    saved = {k: sys.modules.get(k) for k in ("mcp", "mcp.client", "mcp.client.stdio")}
    world = FakeMCPWorld()
    mcp_mod = types.ModuleType("mcp")
    mcp_mod.ClientSession = make_session_class(world)
    mcp_mod.StdioServerParameters = NS
    client_pkg = types.ModuleType("mcp.client")
    stdio_mod = types.ModuleType("mcp.client.stdio")
    stdio_mod.stdio_client = make_stdio_class(world)
    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.client"] = client_pkg
    sys.modules["mcp.client.stdio"] = stdio_mod
    yield world
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """把 Path.home() 重定向到临时目录。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


# =====================================================================
# 变量展开
# =====================================================================

class TestExpandVars:
    """${VAR} 变量展开测试。"""

    def test_expand_basic(self, monkeypatch):
        """基本 ${VAR} 展开（含前缀后缀）。"""
        monkeypatch.setenv("MY_TOKEN", "secret123")
        assert mcp._expand_vars("${MY_TOKEN}") == "secret123"
        assert mcp._expand_vars("prefix-${MY_TOKEN}-suffix") == "prefix-secret123-suffix"

    def test_expand_missing_kept(self, monkeypatch):
        """变量不存在时保留原样。"""
        monkeypatch.delenv("NOT_EXIST_VAR", raising=False)
        assert mcp._expand_vars("${NOT_EXIST_VAR}") == "${NOT_EXIST_VAR}"

    def test_expand_default(self, monkeypatch):
        """${VAR:-default} 默认值语法。"""
        monkeypatch.delenv("NO_DEFAULT_VAR", raising=False)
        assert mcp._expand_vars("${NO_DEFAULT_VAR:-fallback}") == "fallback"
        monkeypatch.setenv("HAS_VAR", "real")
        assert mcp._expand_vars("${HAS_VAR:-fallback}") == "real"

    def test_expand_recursive(self, monkeypatch):
        """dict / list 递归展开。"""
        monkeypatch.setenv("TOKEN", "abc")
        assert mcp._expand_vars({"a": "${TOKEN}", "b": ["${TOKEN}"]}) == {
            "a": "abc", "b": ["abc"],
        }
        # 非字符串原样返回
        assert mcp._expand_vars(42) == 42
        assert mcp._expand_vars(None) is None


# =====================================================================
# 配置读写
# =====================================================================

class TestConfigIO:
    """MCP 配置文件读写测试。"""

    def test_mcp_config_path(self, fake_home):
        """配置路径为 ~/.jarvis/mcp.json。"""
        assert mcp.mcp_config_path() == fake_home / ".jarvis" / "mcp.json"

    def test_load_missing(self, fake_home):
        """文件不存在返回空字典。"""
        assert mcp.load_mcp_config() == {}

    def test_load_bad_json(self, fake_home):
        """格式错误返回空字典。"""
        cfg = fake_home / ".jarvis" / "mcp.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("{bad json", encoding="utf-8")
        assert mcp.load_mcp_config() == {}

    def test_load_not_dict_servers(self, fake_home):
        """mcpServers 不是 dict 返回空字典。"""
        cfg = fake_home / ".jarvis" / "mcp.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"mcpServers": []}), encoding="utf-8")
        assert mcp.load_mcp_config() == {}

    def test_load_ok(self, fake_home):
        """正常加载返回 server 字典。"""
        cfg = fake_home / ".jarvis" / "mcp.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["-y"]}}}),
            encoding="utf-8",
        )
        servers = mcp.load_mcp_config()
        assert servers["fs"]["command"] == "npx"

    def test_save_and_merge(self, fake_home):
        """save / merge：新增、同名跳过、写入文件。"""
        mcp.save_mcp_config({"mcpServers": {"a": {"command": "x"}}})
        assert "a" in mcp.load_mcp_config()
        added = mcp.merge_mcp_config({
            "b": {"command": "y"},
            "a": {"command": "new"},  # 已存在 → 跳过不覆盖
        })
        assert added == ["b"]
        servers = mcp.load_mcp_config()
        assert servers["b"]["command"] == "y"
        assert servers["a"]["command"] == "x"  # 未被覆盖
        # 无新增时不应写文件
        added2 = mcp.merge_mcp_config({"a": {"command": "z"}})
        assert added2 == []
        assert mcp.load_mcp_config()["a"]["command"] == "x"

    def test_remove(self, fake_home):
        """remove：移除指定 server，返回实际移除数。"""
        mcp.save_mcp_config({"mcpServers": {"a": {}, "b": {}}})
        assert mcp.remove_mcp_config(["a", "c"]) == 1
        servers = mcp.load_mcp_config()
        assert "a" not in servers
        assert "b" in servers


# =====================================================================
# 模块级噪音抑制
# =====================================================================

class TestNoiseSuppression:
    """warnings / unraisablehook 过滤逻辑测试。"""

    def test_filtered_unraisablehook(self, monkeypatch):
        """generator 关闭与 cancel scope 消息被过滤，其余转发原 hook。"""
        calls: list = []

        class Args:
            def __init__(self, err_msg="", exc=None):
                self.err_msg = err_msg
                self.exc = exc

        monkeypatch.setattr(mcp, "_orig_unraisablehook", lambda args: calls.append(1))
        mcp._filtered_unraisablehook(Args(err_msg="an error occurred during closing of asynchronous generator"))
        mcp._filtered_unraisablehook(Args(exc=RuntimeError("Attempted to exit cancel scope in a different task")))
        assert calls == []
        mcp._filtered_unraisablehook(Args(err_msg="other error"))
        assert len(calls) == 1

    def test_filtered_unraisablehook_no_orig(self, monkeypatch):
        """无原 hook 时静默处理。"""
        monkeypatch.setattr(mcp, "_orig_unraisablehook", None)
        mcp._filtered_unraisablehook(NS(err_msg="whatever", exc=None))  # 不抛异常

    def test_silent_asyncgen_finalizer(self):
        """async generator GC finalizer 不抛异常。"""
        async def gen():
            yield 1

        ag = gen()
        mcp._silent_asyncgen_finalizer(ag)  # 不抛异常
        mcp._silent_asyncgen_finalizer(object())  # 非 generator 也不抛


# =====================================================================
# MCPClient
# =====================================================================

class TestMCPClient:
    """MCPClient 连接与工具注册测试。"""

    def test_available_true_with_fake_mcp(self, mcp_world):
        """注入假 mcp 后 available 为 True。"""
        client = mcp.MCPClient()
        assert client.available is True

    def test_is_mcp_available_import_error(self, monkeypatch):
        """mcp SDK 未安装时 _is_mcp_available 返回 False。"""
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "mcp" or name.startswith("mcp."):
                raise ImportError("no mcp installed")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert mcp._is_mcp_available() is False

    @pytest.mark.asyncio
    async def test_connect_not_available(self, mcp_world):
        """SDK 不可用时 connect 返回 None。"""
        client = mcp.MCPClient()
        client._available = False
        assert await client.connect("s", {"command": "x"}) is None

    @pytest.mark.asyncio
    async def test_connect_no_command(self, mcp_world):
        """配置缺少 command 时返回 None。"""
        client = mcp.MCPClient()
        assert await client.connect("s", {"args": []}) is None

    @pytest.mark.asyncio
    async def test_connect_import_failure(self, monkeypatch):
        """mcp import 失败时返回 None（sys.modules 置 None 模拟）。"""
        monkeypatch.setitem(sys.modules, "mcp", None)
        client = mcp.MCPClient()
        client._available = True
        assert await client.connect("s", {"command": "x"}) is None

    @pytest.mark.asyncio
    async def test_connect_success_registers_tools(self, mcp_world, monkeypatch):
        """连接成功：initialize + list_tools 注册工具。"""
        monkeypatch.setenv("MY_TOKEN", "tok")
        mcp_world.tools = [
            FakeTool("read_file", "读取文件"),
            FakeTool("write_file", "写入", {"type": "object", "properties": {"p": {}}}),
        ]
        client = mcp.MCPClient()
        conn = await client.connect("fs", {
            "command": "npx",
            "args": ["-y", "--token=${MY_TOKEN}"],
            "env": {"K": "${MY_TOKEN}"},
        })
        assert conn is not None
        assert conn.name == "fs"
        assert conn.connected is True
        assert len(conn.tools) == 2
        assert conn.tools[0].name == "read_file"
        assert conn.tools[0].server_name == "fs"
        assert conn.tools[0].description == "读取文件"
        # 变量已展开、env 合并
        params = mcp_world.stdio_params[0]
        assert params.args == ["-y", "--token=tok"]
        assert params.env["K"] == "tok"
        assert params.env["PATH"]  # 继承进程环境
        # 连接已登记
        assert client.list_connections() == [conn]
        tools = client.list_tools()
        assert {t.name for t in tools} == {"read_file", "write_file"}

    @pytest.mark.asyncio
    async def test_connect_failure_cleanup(self, mcp_world):
        """initialize 失败时清理半开连接并返回 None。"""
        mcp_world.initialize_error = RuntimeError("connection refused")
        client = mcp.MCPClient()
        conn = await client.connect("bad", {"command": "x"})
        assert conn is None
        assert client.list_connections() == []

    @pytest.mark.asyncio
    async def test_connect_all_mixed(self, mcp_world):
        """connect_all 并发连接：成功与失败并存。"""
        mcp_world.tools = [FakeTool("t1")]

        class FlakySession:
            def __init__(self, read, write):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def initialize(self):
                # 第 1 个连接的 initialize 成功，其余失败
                mcp_world.initialize_calls += 1
                if mcp_world.initialize_calls >= 2:
                    raise RuntimeError("refused")

            async def list_tools(self):
                return NS(tools=list(mcp_world.tools))

        mcp_mod = sys.modules["mcp"]
        mcp_mod.ClientSession = FlakySession
        client = mcp.MCPClient()
        results = await client.connect_all({
            "a": {"command": "x"},
            "b": {"command": "y"},
        })
        # 并发顺序不确定，但应恰好一个成功一个失败
        assert sum(results.values()) == 1
        assert len(client.list_connections()) == 1

    @pytest.mark.asyncio
    async def test_call_tool_not_connected(self, mcp_world):
        """未连接的 server 调用抛 KeyError。"""
        client = mcp.MCPClient()
        with pytest.raises(KeyError, match="未连接"):
            await client.call_tool("ghost", "t", {})

    @pytest.mark.asyncio
    async def test_call_tool_success(self, mcp_world):
        """成功调用：文本块拼接。"""
        mcp_world.tools = [FakeTool("t1")]
        mcp_world.call_result = NS(content=[
            NS(text="line1"),
            NS(type="text", text="line2"),
            "raw-block",
        ])
        client = mcp.MCPClient()
        await client.connect("fs", {"command": "x"})
        text = await client.call_tool("fs", "t1", {"a": 1})
        assert text == "line1\nline2\nraw-block"
        # 成功后无熔断记录
        assert "fs" not in client._breaker

    @pytest.mark.asyncio
    async def test_call_tool_breaker(self, mcp_world):
        """连续失败 3 次后熔断，第 4 次抛 KeyError。"""
        mcp_world.tools = [FakeTool("t1")]
        mcp_world.call_error = RuntimeError("remote error")
        client = mcp.MCPClient()
        await client.connect("fs", {"command": "x"})
        for _ in range(3):
            with pytest.raises(RuntimeError, match="MCP 调用失败"):
                await client.call_tool("fs", "t1", {})
        assert client._breaker["fs"][0] == 3
        with pytest.raises(KeyError, match="已熔断"):
            await client.call_tool("fs", "t1", {})

    @pytest.mark.asyncio
    async def test_breaker_cooldown_reset(self, mcp_world, monkeypatch):
        """冷却期过后熔断重置，重新尝试调用。"""
        mcp_world.tools = [FakeTool("t1")]
        mcp_world.call_error = RuntimeError("boom")
        client = mcp.MCPClient()
        await client.connect("fs", {"command": "x"})
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await client.call_tool("fs", "t1", {})
        # 模拟 400 秒后（超过 300s 冷却）
        real_time = time.time
        base = real_time()
        monkeypatch.setattr(time, "time", lambda: base + 400)
        with pytest.raises(RuntimeError, match="MCP 调用失败"):
            await client.call_tool("fs", "t1", {})  # 熔断已重置，重新尝试并再次失败

    @pytest.mark.asyncio
    async def test_disconnect(self, mcp_world):
        """断开单个连接。"""
        client = mcp.MCPClient()
        await client.connect("fs", {"command": "x"})
        await client.disconnect("fs")
        assert client.list_connections() == []
        assert client.list_tools() == []
        await client.disconnect("ghost")  # 断开不存在的连接不抛异常

    @pytest.mark.asyncio
    async def test_disconnect_all(self, mcp_world):
        """断开全部连接。"""
        client = mcp.MCPClient()
        await client.connect("a", {"command": "x"})
        await client.connect("b", {"command": "y"})
        await client.disconnect_all()
        assert client.list_connections() == []

    @pytest.mark.asyncio
    async def test_cleanup_connection_aexit_failure(self):
        """__aexit__ 抛错时用 aclose 兜底。"""
        client = mcp.MCPClient()
        conn = mcp.McpConnection(name="x", config={})

        class BadStdioCtx:
            def __init__(self):
                self.aclosed = False

            async def __aexit__(self, *a):
                raise RuntimeError("cancel scope")

            async def aclose(self):
                self.aclosed = True

        ctx = BadStdioCtx()
        conn._stdio_ctx = ctx
        await client._cleanup_connection(conn)
        assert ctx.aclosed is True
        assert conn._session is None
        assert conn._stdio_ctx is None

    @pytest.mark.asyncio
    async def test_cleanup_connection_session_aexit_error(self):
        """session_ctx 的 __aexit__ 抛错被吞掉。"""
        client = mcp.MCPClient()
        conn = mcp.McpConnection(name="x", config={})

        class BadSessionCtx:
            async def __aexit__(self, *a):
                raise RuntimeError("session teardown")

        conn._session_ctx = BadSessionCtx()
        conn._stdio_ctx = None
        await client._cleanup_connection(conn)  # 不抛异常
        assert conn._session is None

    def test_connection_property(self):
        """McpConnection.connected 属性。"""
        conn = mcp.McpConnection(name="n", config={})
        assert conn.name == "n"
        assert conn.connected is False
        conn._session = object()
        assert conn.connected is True

    def test_mcp_tool_def_fields(self):
        """McpToolDef 字段默认值。"""
        tool = mcp.McpToolDef(server_name="s", name="t", description="d", input_schema={})
        assert tool.server_name == "s"
        assert tool.name == "t"
