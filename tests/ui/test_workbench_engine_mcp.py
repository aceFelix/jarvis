"""workbench/serve 引擎 MCP 接入回归测试。

背景：workbench/serve 装配路径（ChatEngine._ensure_session）曾漏掉 main.repl()
里的 MCP 连接步骤，导致桌面壳永远缺少 MCP 工具（如 mcp__amap-maps__*），
模型查天气等场景只能"念叨"计划而拿不到专业工具。
修复：_ensure_session 在系统提示词生成前 await _connect_mcp(registry)。
见 docs/fixlogs/serve-mcp-truncation-fix.md。

@author aceFelix
"""

from __future__ import annotations

import queue

import agent.core.extensions.mcp_client as mcp_mod
import agent.core.tool as tool_mod
from agent.config.settings import Settings
from agent.core.tool import ToolRegistry
from agent.ui.workbench.engine import ChatEngine


def _make_engine() -> tuple[ChatEngine, queue.Queue]:
    """构造不启动线程的引擎实例，直接取事件队列断言。"""
    event_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(Settings(), event_queue, queue.Queue())
    return engine, event_queue


def _drain(event_queue: queue.Queue) -> list[dict]:
    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    return events


class _FakeClient:
    """假 MCPClient：available 与 connect_all 结果可配。"""

    def __init__(self, available: bool = True, results: dict | None = None) -> None:
        self.available = available
        self._results = results or {}

    async def connect_all(self, config: dict) -> dict:
        return self._results


async def test_connect_mcp_registers_tools_and_keeps_client(monkeypatch) -> None:
    """连接成功：工具注册进 registry，client 引用保留防 GC，info 事件汇报数量。"""
    engine, event_queue = _make_engine()
    client = _FakeClient(results={"amap-maps": True})
    monkeypatch.setattr(mcp_mod, "MCPClient", lambda: client)
    monkeypatch.setattr(mcp_mod, "load_mcp_config", lambda: {"amap-maps": {"command": "x"}})

    captured: dict = {}

    def fake_register(registry, mcp_client=None, workdir=None):
        captured["registry"] = registry
        captured["client"] = mcp_client
        return 3

    monkeypatch.setattr(tool_mod, "register_dynamic_tools", fake_register)

    registry = ToolRegistry()
    await engine._connect_mcp(registry)

    assert captured["registry"] is registry
    assert captured["client"] is client
    assert engine._mcp_client is client  # 引用保留，防 GC 断连
    infos = [e["payload"] for e in _drain(event_queue) if e["type"] == "info"]
    assert any("注册 3 个工具" in t for t in infos)


async def test_connect_mcp_all_failed_skips_registration(monkeypatch) -> None:
    """全部 server 连接失败：不注册工具、不保留 client，info 提示失败名单。"""
    engine, event_queue = _make_engine()
    client = _FakeClient(results={"amap-maps": False, "tyc-mcp": False})
    monkeypatch.setattr(mcp_mod, "MCPClient", lambda: client)
    monkeypatch.setattr(mcp_mod, "load_mcp_config", lambda: {"amap-maps": {}, "tyc-mcp": {}})

    def boom(*args, **kwargs):
        raise AssertionError("全部连接失败时不应注册工具")

    monkeypatch.setattr(tool_mod, "register_dynamic_tools", boom)

    await engine._connect_mcp(ToolRegistry())

    assert engine._mcp_client is None
    infos = [e["payload"] for e in _drain(event_queue) if e["type"] == "info"]
    assert any("所有 server 连接失败" in t for t in infos)


async def test_connect_mcp_unavailable_or_no_config_silent(monkeypatch) -> None:
    """SDK 未安装或无配置：静默返回，无事件、无注册。"""
    engine, event_queue = _make_engine()
    monkeypatch.setattr(mcp_mod, "MCPClient", lambda: _FakeClient(available=False))
    monkeypatch.setattr(mcp_mod, "load_mcp_config", lambda: {"x": {}})

    await engine._connect_mcp(ToolRegistry())
    assert engine._mcp_client is None
    assert _drain(event_queue) == []

    # 有 SDK 但无配置：同样静默
    engine2, event_queue2 = _make_engine()
    monkeypatch.setattr(mcp_mod, "MCPClient", lambda: _FakeClient(available=True))
    monkeypatch.setattr(mcp_mod, "load_mcp_config", lambda: {})
    await engine2._connect_mcp(ToolRegistry())
    assert engine2._mcp_client is None
    assert _drain(event_queue2) == []


async def test_connect_mcp_exception_degrades_to_info(monkeypatch) -> None:
    """connect_all 抛异常：降级为 info 提示，不阻断引擎装配。"""
    engine, event_queue = _make_engine()

    class _BoomClient(_FakeClient):
        async def connect_all(self, config: dict) -> dict:
            raise OSError("spawn 失败")

    monkeypatch.setattr(mcp_mod, "MCPClient", lambda: _BoomClient(results={}))
    monkeypatch.setattr(mcp_mod, "load_mcp_config", lambda: {"amap-maps": {}})

    await engine._connect_mcp(ToolRegistry())  # 不抛异常即通过

    assert engine._mcp_client is None
    infos = [e["payload"] for e in _drain(event_queue) if e["type"] == "info"]
    assert any("MCP 接入异常" in t for t in infos)
