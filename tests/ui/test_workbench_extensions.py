"""桌面「扩展服务」数据源（extensions_api）测试。

覆盖：
- run_extension：非法分类 / 缺 action → ValueError（serve 转 ok=false）
- mcp：list 快照、add/update 落盘与同名拒绝、remove、enable/disable 禁用名单
- tools：registry 清单（含空 registry 降级）
- skills：目录 + 技能清单结构
- plugins：list（含禁用标记）、search、install/uninstall/enable/disable
- harness：list、market、install/uninstall/enable/disable

外部依赖（MCP 配置文件、PluginManager、cli_anything 市场）全程 monkeypatch /
tmp_path，不触真实网络与用户目录。

@author aceFelix
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.config.settings import Settings
from agent.core.extensions import mcp_client
from agent.core.extensions import plugins as plugins_mod
from agent.ui.workbench import extensions_api


# ---- 公共替身 ----


class _FakeTool:
    """工具替身：仅暴露 name/description（tools list 所需字段）。"""

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description


class _FakeRegistry:
    """ToolRegistry 替身：all() 返回给定工具列表。"""

    def __init__(self, tools: list) -> None:
        self._tools = tools

    def all(self) -> list:
        return self._tools


def _engine(registry=None, mcp_status=None, mcp_client=None) -> SimpleNamespace:
    """最小引擎替身（仅提供 extensions_api 读取的字段）。"""
    return SimpleNamespace(
        _active_registry=registry,
        mcp_status=mcp_status,
        _mcp_client=mcp_client,
    )


@pytest.fixture()
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def mcp_paths(monkeypatch, tmp_path):
    """把 MCP 配置/禁用名单文件重定向到 tmp（不触碰真实 ~/.jarvis）。"""
    cfg = tmp_path / "mcp.json"
    dis = tmp_path / "mcp_disabled.json"
    monkeypatch.setattr(mcp_client, "mcp_config_path", lambda: cfg)
    monkeypatch.setattr(mcp_client, "mcp_disabled_path", lambda: dis)
    return cfg, dis


def _call(settings, engine, category, action, **params):
    return extensions_api.run_extension(
        settings, engine, {"category": category, "action": action, **params}
    )


# ---- 分发守卫 ----

def test_unknown_category_and_action_raise(settings):
    """非法分类 / 缺 action / 分类不支持的动作 → ValueError。"""
    engine = _engine()
    with pytest.raises(ValueError):
        _call(settings, engine, "nope", "list")
    with pytest.raises(ValueError):
        extensions_api.run_extension(settings, engine, {"category": "mcp"})
    with pytest.raises(ValueError):
        _call(settings, engine, "tools", "install")
    with pytest.raises(ValueError):
        _call(settings, engine, "skills", "remove")


# ---- MCP ----

def test_mcp_list_empty(settings, mcp_paths):
    """无配置：servers 为空，返回配置路径与全局开关。"""
    result = _call(settings, _engine(), "mcp", "list")
    assert result["ok"] is True
    assert result["data"]["servers"] == []
    assert result["data"]["config_path"].endswith("mcp.json")
    assert result["data"]["enabled"] is True


def test_mcp_add_list_then_reject_duplicate(settings, mcp_paths):
    """add 落盘后可 list 到；同名再 add 被拒绝（提示用编辑覆盖）。"""
    engine = _engine()
    add = _call(
        settings,
        engine,
        "mcp",
        "add",
        name="filesystem",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        env="TOKEN=abc\n# comment\n",
    )
    assert add["ok"] is True

    data = _call(settings, engine, "mcp", "list")["data"]["servers"]
    assert len(data) == 1
    assert data[0]["name"] == "filesystem"
    assert data[0]["command"] == "npx"
    assert data[0]["args"][0] == "-y"
    assert data[0]["env"] == {"TOKEN": "abc"}
    assert data[0]["disabled"] is False
    assert data[0]["connected"] is False

    dup = _call(settings, engine, "mcp", "add", name="filesystem", command="npx")
    assert dup["ok"] is False and "已存在" in dup["message"]


def test_mcp_add_validates_required_fields(settings, mcp_paths):
    """缺 name / 缺 command → ok=false（不落盘）。"""
    engine = _engine()
    assert _call(settings, engine, "mcp", "add", name="  ", command="npx")["ok"] is False
    assert _call(settings, engine, "mcp", "add", name="x", command=" ")["ok"] is False
    assert _call(settings, engine, "mcp", "list")["data"]["servers"] == []


def test_mcp_update_overwrites(settings, mcp_paths):
    """update 覆盖已存在 server 的 command/args。"""
    engine = _engine()
    _call(settings, engine, "mcp", "add", name="git", command="uvx", args=["a"])
    upd = _call(settings, engine, "mcp", "update", name="git", command="npx", args=["b", "c"])
    assert upd["ok"] is True
    servers = {s["name"]: s for s in _call(settings, engine, "mcp", "list")["data"]["servers"]}
    assert servers["git"]["command"] == "npx"
    assert servers["git"]["args"] == ["b", "c"]


def test_mcp_disable_enable_and_remove(settings, mcp_paths):
    """disable/enable 写禁用名单；remove 删除配置并清理禁用残留。"""
    engine = _engine()
    _call(settings, engine, "mcp", "add", name="srv", command="cmd")

    assert _call(settings, engine, "mcp", "disable", name="srv")["ok"] is True
    assert mcp_client.load_mcp_disabled() == {"srv"}
    listed = _call(settings, engine, "mcp", "list")["data"]["servers"][0]
    assert listed["disabled"] is True

    assert _call(settings, engine, "mcp", "enable", name="srv")["ok"] is True
    assert mcp_client.load_mcp_disabled() == set()

    assert _call(settings, engine, "mcp", "remove", name="srv")["ok"] is True
    assert _call(settings, engine, "mcp", "list")["data"]["servers"] == []
    assert _call(settings, engine, "mcp", "remove", name="srv")["ok"] is False


def test_mcp_disable_unknown_server_rejected(settings, mcp_paths):
    """禁用/启用一个不存在的 server → ok=false。"""
    assert _call(settings, _engine(), "mcp", "disable", name="ghost")["ok"] is False


def test_mcp_list_reports_connection_and_tools(settings, mcp_paths):
    """连接快照 + 运行中 client 的工具名用于回填 connected/tools。"""
    _call(settings, _engine(), "mcp", "add", name="live", command="cmd")
    tool = SimpleNamespace(name="read_file")
    conn = SimpleNamespace(name="live", connected=True, tools=[tool])
    client = SimpleNamespace(available=True, list_connections=lambda: [conn])
    engine = _engine(mcp_status={"connected": ["live"], "failed": [], "tools": 1}, mcp_client=client)
    server = _call(settings, engine, "mcp", "list")["data"]["servers"][0]
    assert server["connected"] is True
    assert server["tools"] == ["read_file"]


# ---- 内置工具 ----

def test_tools_list(settings):
    """registry 工具清单按名称排序，描述取首行。"""
    reg = _FakeRegistry([
        _FakeTool("bash", "执行命令\n第二行"),
        _FakeTool("Read", "读文件"),
    ])
    result = _call(settings, _engine(registry=reg), "tools", "list")
    assert result["ok"] is True
    assert [t["name"] for t in result["data"]] == ["Read", "bash"]
    assert result["data"][1]["description"] == "执行命令"


def test_tools_list_without_registry(settings):
    """registry 未装配（None）→ 空清单降级，不抛异常。"""
    result = _call(settings, _engine(registry=None), "tools", "list")
    assert result["ok"] is True and result["data"] == []


# ---- 技能 ----

def test_skills_list_structure(settings):
    """技能分类返回 user/project 目录条目 + 技能清单（结构断言，不依赖真实目录内容）。"""
    result = _call(settings, _engine(), "skills", "list")
    assert result["ok"] is True
    dirs = result["data"]["dirs"]
    assert {d["label"] for d in dirs} == {"user", "project"}
    assert isinstance(result["data"]["skills"], list)


# ---- 插件 ----


class _FakePluginManager:
    """PluginManager 替身：固定已装/禁用/搜索结果，写操作恒成功。"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def list_installed(self) -> dict:
        return {"plugins": {
            "p1": {"name": "p1", "version": "1.0", "source": "remote",
                   "installed_at": "2026-01-01T00:00:00Z"},
        }}

    def list_disabled(self) -> list[str]:
        return ["p2"]

    def search(self, keyword: str = "") -> list[dict]:
        return [{"name": "p1", "version": "1.0", "description": "d", "_is_local": False}]

    def install(self, name: str): return (True, f"installed {name}")
    def uninstall(self, name: str): return (True, f"removed {name}")
    def enable(self, name: str): return (True, f"enabled {name}")
    def disable(self, name: str): return (True, f"disabled {name}")


@pytest.fixture()
def fake_plugins(monkeypatch):
    monkeypatch.setattr(plugins_mod, "PluginManager", _FakePluginManager)


def test_plugins_list_and_search(settings, fake_plugins):
    """list 展开插件条目并标记禁用；search 标注已装状态与来源。"""
    listed = _call(settings, _engine(), "plugins", "list")
    assert listed["ok"] is True
    assert listed["data"][0]["name"] == "p1"
    assert listed["data"][0]["disabled"] is False

    found = _call(settings, _engine(), "plugins", "search", keyword="p")
    assert found["ok"] is True
    assert found["data"][0]["installed"] is True
    assert found["data"][0]["source"] == "远程"


def test_plugins_write_actions(settings, fake_plugins):
    """install/uninstall/enable/disable 透传 PluginManager 结果；缺 name 拒绝。"""
    engine = _engine()
    for action in ("install", "uninstall", "enable", "disable"):
        res = _call(settings, engine, "plugins", action, name="p1")
        assert res["ok"] is True and res["message"]
    assert _call(settings, engine, "plugins", "install", name=" ")["ok"] is False


# ---- harness ----

def test_harness_list_and_market(settings, monkeypatch):
    """harness 分类：list 带禁用标记；market 带来源与已装状态。"""
    from agent import cli_anything
    from agent.cli_anything import market

    monkeypatch.setattr(cli_anything, "list_installed",
                        lambda workdir=None: [{"id": "blender", "name": "Blender", "description": "3D"}])
    monkeypatch.setattr(cli_anything, "list_market",
                        lambda **kw: [{"id": "blender", "name": "Blender", "description": "3D",
                                       "requires": "blender", "installed": "是",
                                       "source": "CLI_Anything官方"}])
    monkeypatch.setattr(market, "is_harness_disabled", lambda hid: hid == "blender")

    listed = _call(settings, _engine(), "harness", "list")
    assert listed["ok"] is True
    assert listed["data"][0]["id"] == "blender"
    assert listed["data"][0]["disabled"] is True

    market_data = _call(settings, _engine(), "harness", "market")
    assert market_data["ok"] is True
    assert market_data["data"][0]["installed"] is True
    assert market_data["data"][0]["source"] == "CLI_Anything官方"


def test_harness_write_actions(settings, monkeypatch):
    """install/uninstall/enable/disable 透传市场结果；缺 id 拒绝。"""
    from agent import cli_anything
    from agent.cli_anything import market

    monkeypatch.setattr(extensions_api, "_temp_registry", lambda: None)
    monkeypatch.setattr(cli_anything, "install_harness",
                        lambda *a, **k: {"success": True, "message": "installed"})
    monkeypatch.setattr(cli_anything, "uninstall_harness",
                        lambda *a, **k: {"success": True, "message": "removed"})
    monkeypatch.setattr(market, "enable_harness", lambda hid: {"success": True, "message": "enabled"})
    monkeypatch.setattr(market, "disable_harness", lambda hid: {"success": True, "message": "disabled"})

    engine = _engine()
    for action in ("install", "uninstall", "enable", "disable"):
        res = _call(settings, engine, "harness", action, id="blender")
        assert res["ok"] is True and res["message"]
    assert _call(settings, engine, "harness", "install", id=" ")["ok"] is False


def test_harness_write_failure_propagates_message(settings, monkeypatch):
    """市场写操作失败 → ok=false 且保留原文 message（不回灌异常）。"""
    from agent import cli_anything

    monkeypatch.setattr(extensions_api, "_temp_registry", lambda: None)
    monkeypatch.setattr(cli_anything, "install_harness",
                        lambda *a, **k: {"success": False, "message": "下载失败"})
    res = _call(settings, _engine(), "harness", "install", id="blender")
    assert res["ok"] is False and res["message"] == "下载失败"