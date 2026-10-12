"""DevServer 工具（启动 / 关闭）单元测试。

覆盖「把项目关了」修复引入的关闭能力，全部不真正启停进程：
- 元数据：非只读、并发安全、schema 含 stop 动作与 pid 字段
- 权限分流：stop 命中登记表 → ALLOW（避免语音模式阻塞确认），未命中 → ASK，start → ASK
- 活动描述：start / stop（含 pid / port）文案
- 目标定位：按 pid / port / project_dir / 最近启动 定位，且过滤已退出进程
- 关闭执行：无目标报错、显式 pid 终止、显式 port 未发现进程报错、终止失败进 failed 列表
- 入口分派：call 按 action 走 start / stop
- 启动登记：start 成功后把 pid / port 写入全局登记表

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior, ToolResult


@pytest.fixture
def dummy_ctx() -> ToolContext:
    """空 ToolContext（ui=None 走无界面分支）。"""
    return ToolContext(workdir=str(Path.cwd()), messages=[])


@pytest.fixture
def tool():
    """被测 DevServerTool 实例。"""
    from agent.tools.extensions.dev_server_tool import DevServerTool

    return DevServerTool()


def _patch_registry(monkeypatch, entries, *, alive=True):
    """把登记表读取与进程存活探测替换为可控桩。"""
    import agent.tools.extensions.dev_server_tool as dst

    monkeypatch.setattr(dst, "load_registry", lambda: entries)
    monkeypatch.setattr(dst, "pid_alive", lambda pid: alive)


class TestMetadata:
    """工具元数据与 schema。"""

    def test_not_read_only_but_concurrency_safe(self, tool):
        assert tool.is_read_only({}) is False
        assert tool.is_concurrency_safe({}) is True

    def test_schema_declares_stop_action_and_pid(self, tool):
        props = tool.input_schema["properties"]
        assert props["action"]["enum"] == ["start", "stop"]
        assert props["action"]["default"] == "start"
        assert props["pid"]["type"] == "integer"


class TestPermissions:
    """权限分流：stop 命中登记表直接放行，否则默认 ASK。"""

    def test_start_always_asks(self, tool, dummy_ctx):
        perm = tool.check_permissions({"action": "start"}, dummy_ctx)
        assert perm.behavior == PermissionBehavior.ASK

    def test_stop_registered_target_allows(self, tool, dummy_ctx, monkeypatch):
        """关闭 Jarvis 自己启动的服务器属安全收尾，直接 ALLOW。"""
        _patch_registry(
            monkeypatch,
            [{"pid": 4321, "port": 5174, "project_dir": ""}],
        )
        perm = tool.check_permissions({"action": "stop"}, dummy_ctx)
        assert perm.behavior == PermissionBehavior.ALLOW

    def test_stop_unregistered_target_asks(self, tool, dummy_ctx, monkeypatch):
        """未登记的关闭目标需要确认。"""
        _patch_registry(monkeypatch, [])
        perm = tool.check_permissions({"action": "stop"}, dummy_ctx)
        assert perm.behavior == PermissionBehavior.ASK


class TestActivityDescription:
    """活动描述文案区分启动 / 关闭。"""

    def test_start_variants(self, tool):
        assert tool.activity_description({"project_dir": "app"}) == "启动开发服务器: app"
        assert tool.activity_description({}) == "启动开发服务器: 当前目录"
        assert tool.activity_description(None) == "启动开发服务器"

    def test_stop_variants(self, tool):
        assert (
            tool.activity_description({"action": "stop", "pid": 12})
            == "关闭开发服务器 (PID 12)"
        )
        assert (
            tool.activity_description({"action": "stop", "port": 5174})
            == "关闭开发服务器 (端口 5174)"
        )
        assert tool.activity_description({"action": "stop"}) == "关闭开发服务器"


class TestFindRegisteredTargets:
    """登记表目标定位优先级与存活过滤。"""

    def test_by_pid(self, tool, dummy_ctx, monkeypatch):
        _patch_registry(
            monkeypatch,
            [{"pid": 1, "port": 5173, "project_dir": ""},
             {"pid": 2, "port": 3000, "project_dir": ""}],
        )
        targets = tool._find_registered_targets({"action": "stop", "pid": 2}, dummy_ctx)
        assert [t["pid"] for t in targets] == [2]

    def test_by_port(self, tool, dummy_ctx, monkeypatch):
        _patch_registry(
            monkeypatch,
            [{"pid": 1, "port": 5173, "project_dir": ""},
             {"pid": 2, "port": 3000, "project_dir": ""}],
        )
        targets = tool._find_registered_targets({"action": "stop", "port": 3000}, dummy_ctx)
        assert [t["pid"] for t in targets] == [2]

    def test_by_project_dir(self, tool, dummy_ctx, monkeypatch, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _patch_registry(
            monkeypatch,
            [{"pid": 7, "port": 5173, "project_dir": str(proj)}],
        )
        targets = tool._find_registered_targets(
            {"action": "stop", "project_dir": str(proj)}, dummy_ctx
        )
        assert [t["pid"] for t in targets] == [7]

    def test_latest_when_no_selector(self, tool, dummy_ctx, monkeypatch):
        """未指定条件时关闭最近启动（登记表最后一条）的那一个。"""
        _patch_registry(
            monkeypatch,
            [{"pid": 1, "port": 5173, "project_dir": ""},
             {"pid": 9, "port": 3000, "project_dir": ""}],
        )
        targets = tool._find_registered_targets({"action": "stop"}, dummy_ctx)
        assert [t["pid"] for t in targets] == [9]

    def test_dead_entries_are_filtered(self, tool, dummy_ctx, monkeypatch):
        _patch_registry(
            monkeypatch,
            [{"pid": 1, "port": 5173, "project_dir": ""}],
            alive=False,
        )
        assert tool._find_registered_targets({"action": "stop"}, dummy_ctx) == []


class TestStopServer:
    """关闭执行路径。"""

    def test_no_targets_errors(self, tool, dummy_ctx, monkeypatch):
        _patch_registry(monkeypatch, [])
        r = tool._stop_server({"action": "stop"}, dummy_ctx)
        assert r.is_error is True
        assert "没有" in r.data

    def test_explicit_pid_kills_and_unregisters(self, tool, dummy_ctx, monkeypatch):
        import agent.tools.extensions.dev_server_tool as dst

        _patch_registry(monkeypatch, [])
        killed: list[int] = []
        monkeypatch.setattr(
            dst, "kill_process_tree", lambda pid: (killed.append(pid) or (True, "已终止"))
        )
        unregistered: list[dict] = []
        monkeypatch.setattr(dst, "unregister_server", lambda **kw: unregistered.append(kw))

        r = tool._stop_server({"action": "stop", "pid": 999}, dummy_ctx)
        assert r.is_error is False
        assert killed == [999]
        assert unregistered == [{"pid": 999}]
        data = json.loads(r.data)
        assert data["action"] == "stop"
        assert data["stopped"][0]["pid"] == 999

    def test_explicit_port_without_process_errors(self, tool, dummy_ctx, monkeypatch):
        import agent.tools.extensions.dev_server_tool as dst

        _patch_registry(monkeypatch, [])
        monkeypatch.setattr(dst, "find_pids_by_port", lambda port: [])
        r = tool._stop_server({"action": "stop", "port": 5174}, dummy_ctx)
        assert r.is_error is True
        assert "5174" in r.data

    def test_kill_failure_goes_to_failed_list(self, tool, dummy_ctx, monkeypatch):
        import agent.tools.extensions.dev_server_tool as dst

        _patch_registry(monkeypatch, [{"pid": 55, "port": 5173, "project_dir": ""}])
        monkeypatch.setattr(dst, "kill_process_tree", lambda pid: (False, "拒绝访问"))
        monkeypatch.setattr(dst, "unregister_server", lambda **kw: None)

        r = tool._stop_server({"action": "stop"}, dummy_ctx)
        assert r.is_error is True
        data = json.loads(r.data)
        assert data["stopped"] == []
        assert data["failed"][0]["message"] == "拒绝访问"


class TestCallDispatch:
    """call 按 action 分派到启动 / 关闭。"""

    def test_dispatch_stop(self, tool, dummy_ctx, monkeypatch):
        seen = {}

        def fake_stop(args, ctx):
            seen["which"] = "stop"
            return ToolResult(data="stopped")

        def fake_start(args, ctx):
            seen["which"] = "start"
            return ToolResult(data="started")

        monkeypatch.setattr(tool, "_stop_server", fake_stop)
        monkeypatch.setattr(tool, "_start_server", fake_start)

        r = asyncio.run(tool.call({"action": "stop"}, dummy_ctx))
        assert seen["which"] == "stop"
        assert r.data == "stopped"

    def test_dispatch_default_start(self, tool, dummy_ctx, monkeypatch):
        seen = {}
        monkeypatch.setattr(tool, "_stop_server", lambda a, c: seen.setdefault("w", "stop"))
        monkeypatch.setattr(
            tool, "_start_server",
            lambda a, c: (seen.setdefault("w", "start"), ToolResult(data="s"))[1],
        )
        asyncio.run(tool.call({}, dummy_ctx))
        assert seen["w"] == "start"


class TestStartRegisters:
    """start 成功后把服务器写入全局登记表，供后续 stop 定位。"""

    def test_start_registers_server(self, tool, dummy_ctx, monkeypatch, tmp_path):
        import agent.tools.extensions.dev_server_tool as dst

        proj = tmp_path / "app"
        proj.mkdir()

        monkeypatch.setattr(dst, "is_port_in_use", lambda port: False)
        monkeypatch.setattr(
            dst, "_detect_project", lambda d: ("vite", "npm", "npm run dev")
        )
        monkeypatch.setattr(dst, "_prepare_log_file", lambda d: tmp_path / "run.log")

        class _FakeProc:
            pid = 4242

        monkeypatch.setattr(dst, "_start_process", lambda cmd, cwd, lf: _FakeProc())
        monkeypatch.setattr(dst, "_wait_for_port", lambda port, timeout=10: True)
        monkeypatch.setattr(dst, "_extract_url_from_log", lambda lf: None)

        captured: dict = {}
        monkeypatch.setattr(dst, "register_server", lambda e: captured.update(e))

        r = asyncio.run(
            tool.call({"action": "start", "project_dir": str(proj)}, dummy_ctx)
        )
        assert r.is_error is False
        data = json.loads(r.data)
        assert data["pid"] == 4242
        assert data["port"] == 5173  # vite 默认端口
        assert captured["pid"] == 4242
        assert captured["port"] == 5173
