"""插件市场模块单元测试。

覆盖 agent/core/extensions/plugins.py 的 PluginManager:
- Marketplace: fetch_marketplace / search / _load_local_marketplace
- 安装: install（本地/远程 github 分支）/ _install_from_dir
- 卸载: uninstall
- 启用/禁用: enable / disable / is_disabled / list_disabled
- 脚手架: create_plugin / validate_plugin
- 内部: _load / _save / _load_disabled_state / _save_disabled_state

说明:
- 用 tmp_path 构造隔离的插件目录/市场目录，并通过 monkeypatch Path.home()
  把 ~/.jarvis 重定向到临时目录，避免污染真实用户目录。
- 网络（fetch_marketplace）与 git clone（subprocess.run）均用 monkeypatch 模拟。
- 不修改被测源码。

@author aceFelix
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.core.extensions import plugins as plugins_mod
from agent.core.extensions.plugins import PluginManager


@pytest.fixture
def plugin_env(tmp_path, monkeypatch):
    """构造隔离的插件环境：假 home + 本地市场目录 + 临时安装目录。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    market = tmp_path / "market"
    market.mkdir()
    install_base = tmp_path / "install"
    manager = PluginManager(
        marketplace_url="http://fake.invalid/marketplace.json",
        marketplace_local=str(market),
        install_base=install_base,
    )
    return SimpleNamespace(
        home=home, market=market, install=install_base, manager=manager,
    )


def _make_local_plugin(root: Path, name: str, skills=None, mcp_servers=None,
                       extra: dict | None = None):
    """在本地市场目录下生成一个插件目录（含 plugin.json 与 skills/）。"""
    pdir = root / name
    (pdir / "skills").mkdir(parents=True, exist_ok=True)
    for skill in skills or []:
        (pdir / "skills" / skill).mkdir(parents=True, exist_ok=True)
        (pdir / "skills" / skill / "SKILL.md").write_text(f"# {skill}", encoding="utf-8")
    manifest = {
        "name": name,
        "version": "0.1.0",
        "description": f"{name} 插件",
        "skills": skills or [],
        "mcp_servers": mcp_servers or [],
        **(extra or {}),
    }
    (pdir / "plugin.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return pdir


# ---- Marketplace ----

class TestMarketplace:
    """市场拉取与搜索测试。"""

    def test_fetch_marketplace_ok(self, monkeypatch):
        """远程市场拉取成功。"""
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"plugins": [{"name": "a", "version": "1"}]}'

        monkeypatch.setattr(
            "urllib.request.urlopen", lambda req, timeout=0, context=None: FakeResp(),
        )
        m = PluginManager(marketplace_url="http://fake.invalid/mp.json")
        assert m.fetch_marketplace() == [{"name": "a", "version": "1"}]

    def test_fetch_marketplace_error(self, monkeypatch):
        """网络错误 / JSON 错误均返回空列表。"""
        def boom(req, timeout=0, context=None):
            raise OSError("network down")
        monkeypatch.setattr("urllib.request.urlopen", boom)
        m = PluginManager(marketplace_url="http://fake.invalid/mp.json")
        assert m.fetch_marketplace() == []

        def bad_json(req, timeout=0, context=None):
            class Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return b"{not json"

            return Resp()
        monkeypatch.setattr("urllib.request.urlopen", bad_json)
        assert m.fetch_marketplace() == []

    def test_search_empty_keyword(self, plugin_env, monkeypatch):
        """空关键字返回全部（远程 + 本地）。"""
        _make_local_plugin(plugin_env.market, "hello")
        monkeypatch.setattr(
            plugin_env.manager, "fetch_marketplace",
            lambda: [{"name": "remote-plugin", "description": "远程插件"}],
        )
        merged = plugin_env.manager.search()
        names = {p["name"] for p in merged}
        assert names == {"hello", "remote-plugin"}

    def test_search_filter_keyword(self, plugin_env, monkeypatch):
        """关键字过滤 name 与 description。"""
        _make_local_plugin(plugin_env.market, "git-helper", extra={"description": "Git 版本控制专家"})
        _make_local_plugin(plugin_env.market, "weather")
        monkeypatch.setattr(plugin_env.manager, "fetch_marketplace", lambda: [])
        assert len(plugin_env.manager.search("git")) == 1
        assert len(plugin_env.manager.search("版本控制")) == 1
        assert plugin_env.manager.search("nope") == []

    def test_search_local_overrides_remote(self, plugin_env, monkeypatch):
        """本地插件覆盖远程同名插件。"""
        _make_local_plugin(plugin_env.market, "hello", extra={"version": "1.0"})
        monkeypatch.setattr(
            plugin_env.manager, "fetch_marketplace",
            lambda: [{"name": "hello", "version": "9.9", "description": "远程"},
                     {"name": "only-remote", "version": "1"}],
        )
        merged = plugin_env.manager.search()
        by_name = {p["name"]: p for p in merged}
        assert by_name["hello"]["version"] == "1.0"  # 本地优先
        assert by_name["hello"]["_is_local"] is True
        assert "only-remote" in by_name

    def test_load_local_marketplace_flat_and_nested(self, plugin_env):
        """本地市场支持扁平布局与 plugins/ 子目录布局。"""
        _make_local_plugin(plugin_env.market, "flat-plugin")
        nested = plugin_env.market / "plugins"
        _make_local_plugin(nested, "nested-plugin")
        plugins = plugin_env.manager._load_local_marketplace()
        names = {p["name"] for p in plugins}
        assert names == {"flat-plugin", "nested-plugin"}
        flat = next(p for p in plugins if p["name"] == "flat-plugin")
        assert flat["_is_local"] is True
        assert flat["source"]["type"] == "local"

    def test_load_local_marketplace_marketplace_json_mapping(self, plugin_env):
        """本地 marketplace.json 中 github-subdir 条目映射为本地源。"""
        sub = plugin_env.market / "plugins" / "mapped"
        (sub / "skills").mkdir(parents=True)
        (sub / "plugin.json").write_text(
            json.dumps({"name": "mapped", "skills": []}), encoding="utf-8",
        )
        (plugin_env.market / "marketplace.json").write_text(
            json.dumps({"plugins": [
                {"name": "mapped", "source": {"type": "github-subdir", "subdir": "plugins/mapped"}},
                {"name": "remote-only", "source": {"type": "github", "repo": "a/b"}},
            ]}),
            encoding="utf-8",
        )
        plugins = plugin_env.manager._load_local_marketplace()
        by_name = {p["name"]: p for p in plugins}
        assert by_name["mapped"]["source"]["type"] == "local"  # 已映射
        assert by_name["remote-only"]["source"]["repo"] == "a/b"  # 保留远程源

    def test_load_local_marketplace_skip_broken(self, plugin_env):
        """坏 plugin.json 与坏 marketplace.json 被跳过。"""
        bad = plugin_env.market / "bad"
        bad.mkdir()
        (bad / "plugin.json").write_text("{broken", encoding="utf-8")
        (plugin_env.market / "marketplace.json").write_text("{broken", encoding="utf-8")
        assert plugin_env.manager._load_local_marketplace() == []

    def test_load_local_marketplace_empty(self, plugin_env):
        """marketplace_local 为空或目录不存在返回空。"""
        m = PluginManager(marketplace_url="", marketplace_local="", install_base=plugin_env.install)
        assert m._load_local_marketplace() == []
        m2 = PluginManager(marketplace_url="", marketplace_local=str(plugin_env.market / "nope"),
                           install_base=plugin_env.install)
        assert m2._load_local_marketplace() == []


# ---- 安装 ----

class TestInstall:
    """插件安装测试。"""

    def test_install_not_found(self, plugin_env, monkeypatch):
        """市场中没有该插件。"""
        monkeypatch.setattr(plugin_env.manager, "fetch_marketplace", lambda: [])
        ok, msg = plugin_env.manager.install("ghost")
        assert ok is False
        assert "不在市场" in msg

    def test_install_local_plugin(self, plugin_env, monkeypatch):
        """本地插件安装：复制 skills、写 installed.json。"""
        _make_local_plugin(plugin_env.market, "hello", skills=["hello-skill"])
        monkeypatch.setattr(plugin_env.manager, "fetch_marketplace", lambda: [])
        ok, msg = plugin_env.manager.install("hello")
        assert ok is True
        assert "hello-skill" in msg
        # skills 复制到 ~/.jarvis/skills/
        assert (plugin_env.home / ".jarvis" / "skills" / "hello-skill" / "SKILL.md").exists()
        installed = plugin_env.manager.list_installed()
        assert installed["plugins"]["hello"]["version"] == "0.1.0"
        assert installed["plugins"]["hello"]["skills"] == ["hello-skill"]

    def test_install_local_dir_missing(self, plugin_env, monkeypatch):
        """本地插件目录不存在。"""
        monkeypatch.setattr(
            plugin_env.manager, "fetch_marketplace",
            lambda: [{"name": "x", "_is_local": True,
                      "_local_path": str(plugin_env.market / "gone")}],
        )
        ok, msg = plugin_env.manager.install("x")
        assert ok is False
        assert "本地插件目录不存在" in msg

    def test_install_unsupported_source(self, plugin_env, monkeypatch):
        """不支持的插件源类型。"""
        monkeypatch.setattr(
            plugin_env.manager, "fetch_marketplace",
            lambda: [{"name": "x", "source": {"type": "ftp"}}],
        )
        ok, msg = plugin_env.manager.install("x")
        assert ok is False
        assert "不支持的插件源类型" in msg

    def test_install_missing_repo(self, plugin_env, monkeypatch):
        """github 源缺少 repo 信息。"""
        monkeypatch.setattr(
            plugin_env.manager, "fetch_marketplace",
            lambda: [{"name": "x", "source": {"type": "github"}}],
        )
        ok, msg = plugin_env.manager.install("x")
        assert ok is False
        assert "缺少 repo" in msg

    def test_install_no_git(self, plugin_env, monkeypatch):
        """未找到 git 命令。"""
        monkeypatch.setattr(
            plugin_env.manager, "fetch_marketplace",
            lambda: [{"name": "x", "source": {"type": "github", "repo": "a/b"}}],
        )
        monkeypatch.setattr(plugins_mod, "_find_git", lambda: "")
        ok, msg = plugin_env.manager.install("x")
        assert ok is False
        assert "未找到 git" in msg

    def test_install_git_clone_failure(self, plugin_env, monkeypatch):
        """git clone 返回非零退出码。"""
        monkeypatch.setattr(
            plugin_env.manager, "fetch_marketplace",
            lambda: [{"name": "x", "source": {"type": "github", "repo": "a/b"}}],
        )
        monkeypatch.setattr(plugins_mod, "_find_git", lambda: "git")

        def fake_run(args, **kw):
            return subprocess.CompletedProcess(args, 1, stderr="auth failed")

        monkeypatch.setattr(subprocess, "run", fake_run)
        ok, msg = plugin_env.manager.install("x")
        assert ok is False
        assert "git clone 失败" in msg

    def test_install_git_clone_timeout(self, plugin_env, monkeypatch):
        """git clone 超时。"""
        monkeypatch.setattr(
            plugin_env.manager, "fetch_marketplace",
            lambda: [{"name": "x", "source": {"type": "github", "repo": "a/b"}}],
        )
        monkeypatch.setattr(plugins_mod, "_find_git", lambda: "git")

        def fake_run(args, **kw):
            raise subprocess.TimeoutExpired(args, 60)

        monkeypatch.setattr(subprocess, "run", fake_run)
        ok, msg = plugin_env.manager.install("x")
        assert ok is False
        assert "git clone 超时" in msg

    def test_install_git_clone_exception(self, plugin_env, monkeypatch):
        """git clone 其他异常。"""
        monkeypatch.setattr(
            plugin_env.manager, "fetch_marketplace",
            lambda: [{"name": "x", "source": {"type": "github", "repo": "a/b"}}],
        )
        monkeypatch.setattr(plugins_mod, "_find_git", lambda: "git")

        def fake_run(args, **kw):
            raise OSError("boom")

        monkeypatch.setattr(subprocess, "run", fake_run)
        ok, msg = plugin_env.manager.install("x")
        assert ok is False
        assert "git clone 异常" in msg

    def test_install_git_clone_success(self, plugin_env, monkeypatch):
        """远程 github 插件安装成功（clone 模拟 + 子目录定位 + 安装）。"""
        monkeypatch.setattr(
            plugin_env.manager, "fetch_marketplace",
            lambda: [{"name": "remote", "source": {"type": "github", "repo": "a/b", "ref": "main"}}],
        )
        monkeypatch.setattr(plugins_mod, "_find_git", lambda: "git")

        def fake_run(args, **kw):
            # args[-1] 是 git clone 的目标临时目录（仓库根），
            # plugin.json 与 skills/ 直接位于仓库根（单插件仓库布局）
            tdir = Path(args[-1])
            (tdir / "skills" / "r-skill").mkdir(parents=True)
            (tdir / "skills" / "r-skill" / "SKILL.md").write_text("# r", encoding="utf-8")
            (tdir / "plugin.json").write_text(
                json.dumps({"name": "remote", "version": "0.1.0", "description": "d",
                            "skills": ["r-skill"], "mcp_servers": []}),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        ok, msg = plugin_env.manager.install("remote")
        assert ok is True
        assert (plugin_env.home / ".jarvis" / "skills" / "r-skill" / "SKILL.md").exists()
        assert "remote" in plugin_env.manager.list_installed()["plugins"]

    def test_install_git_subdir_missing(self, plugin_env, monkeypatch):
        """github-subdir 指定的子目录不存在。"""
        monkeypatch.setattr(
            plugin_env.manager, "fetch_marketplace",
            lambda: [{"name": "x", "source": {"type": "github-subdir",
                                              "repo": "a/b", "subdir": "plugins/nope"}}],
        )
        monkeypatch.setattr(plugins_mod, "_find_git", lambda: "git")
        monkeypatch.setattr(
            subprocess, "run",
            lambda args, **kw: subprocess.CompletedProcess(args, 0),
        )
        ok, msg = plugin_env.manager.install("x")
        assert ok is False
        assert "插件子目录不存在" in msg

    def test_install_from_dir_missing_manifest(self, plugin_env):
        """目录缺少 plugin.json。"""
        d = plugin_env.market / "empty"
        d.mkdir()
        ok, msg = plugin_env.manager._install_from_dir(d)
        assert ok is False
        assert "缺少 plugin.json" in msg

    def test_install_from_dir_bad_json(self, plugin_env):
        """plugin.json 格式错误。"""
        d = plugin_env.market / "bad"
        d.mkdir()
        (d / "plugin.json").write_text("{bad", encoding="utf-8")
        ok, msg = plugin_env.manager._install_from_dir(d)
        assert ok is False
        assert "plugin.json 格式错误" in msg

    def test_install_from_dir_with_mcp(self, plugin_env):
        """合并插件 .mcp.json 到 ~/.jarvis/mcp.json。"""
        d = plugin_env.market / "mcpplugin"
        (d / "skills").mkdir(parents=True)
        (d / "plugin.json").write_text(
            json.dumps({"name": "mcpplugin", "skills": [], "mcp_servers": ["fs"]}),
            encoding="utf-8",
        )
        (d / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["-y"]}}}),
            encoding="utf-8",
        )
        ok, msg = plugin_env.manager._install_from_dir(d)
        assert ok is True
        assert "fs" in msg
        mcp_json = plugin_env.home / ".jarvis" / "mcp.json"
        assert json.loads(mcp_json.read_text(encoding="utf-8"))["mcpServers"]["fs"]["command"] == "npx"
        entry = plugin_env.manager.list_installed()["plugins"]["mcpplugin"]
        assert entry["mcp_servers"] == ["fs"]

    def test_install_from_dir_mcp_error(self, plugin_env):
        """MCP 配置合并失败返回错误。"""
        d = plugin_env.market / "badmcp"
        d.mkdir()
        (d / "plugin.json").write_text(
            json.dumps({"name": "badmcp", "skills": [], "mcp_servers": []}),
            encoding="utf-8",
        )
        (d / ".mcp.json").write_text(json.dumps({"mcpServers": 123}), encoding="utf-8")
        ok, msg = plugin_env.manager._install_from_dir(d)
        assert ok is False
        assert "MCP 配置合并失败" in msg


# ---- 卸载 / 启用 / 禁用 ----

class TestLifecycle:
    """卸载、启用、禁用测试。"""

    def _install_hello(self, env):
        _make_local_plugin(env.market, "hello", skills=["hello-skill"])
        env.manager.fetch_marketplace = lambda: []
        ok, _ = env.manager.install("hello")
        assert ok is True
        return ok

    def test_uninstall(self, plugin_env):
        """卸载：删除 skills、移除 MCP、更新 installed.json。"""
        self._install_hello(plugin_env)
        skill_dir = plugin_env.home / ".jarvis" / "skills" / "hello-skill"
        assert skill_dir.exists()
        ok, msg = plugin_env.manager.uninstall("hello")
        assert ok is True
        assert not skill_dir.exists()
        assert "hello" not in plugin_env.manager.list_installed()["plugins"]

    def test_uninstall_not_installed(self, plugin_env):
        """卸载未安装的插件。"""
        ok, msg = plugin_env.manager.uninstall("ghost")
        assert ok is False
        assert "未安装" in msg

    def test_disable_enable_cycle(self, plugin_env):
        """禁用把 skills 移入禁用目录，启用移回，状态同步。"""
        self._install_hello(plugin_env)
        skill_dir = plugin_env.home / ".jarvis" / "skills" / "hello-skill"
        # 禁用
        ok, msg = plugin_env.manager.disable("hello")
        assert ok is True
        assert plugin_env.manager.is_disabled("hello") is True
        assert plugin_env.manager.list_disabled() == ["hello"]
        assert not skill_dir.exists()
        assert (plugin_env.install / "disabled" / "hello" / "skills" / "hello-skill").exists()
        # 重复禁用
        ok, msg = plugin_env.manager.disable("hello")
        assert ok is True
        assert "已是禁用状态" in msg
        # 启用
        ok, msg = plugin_env.manager.enable("hello")
        assert ok is True
        assert plugin_env.manager.is_disabled("hello") is False
        assert skill_dir.exists()
        assert not (plugin_env.install / "disabled" / "hello").exists()
        # 重复启用
        ok, msg = plugin_env.manager.enable("hello")
        assert ok is True
        assert "已是启用状态" in msg

    def test_disable_not_installed(self, plugin_env):
        """禁用未安装的插件。"""
        ok, msg = plugin_env.manager.disable("ghost")
        assert ok is False
        assert "未安装" in msg


# ---- 脚手架与校验 ----

class TestScaffold:
    """create_plugin / validate_plugin 测试。"""

    def test_create_plugin_ok(self, plugin_env):
        """合法插件名生成脚手架。"""
        ok, msg = plugin_env.manager.create_plugin(
            "my plugin", description="测试插件", output_dir=plugin_env.market,
        )
        assert ok is True
        target = Path(msg)
        assert (target / "plugin.json").exists()
        assert (target / "skills").is_dir()
        assert (target / "README.md").exists()
        manifest = json.loads((target / "plugin.json").read_text(encoding="utf-8"))
        assert manifest["name"] == "my-plugin"
        assert manifest["version"] == "0.1.0"
        assert manifest["description"] == "测试插件"

    def test_create_plugin_default_output_dir(self, plugin_env):
        """未指定 output_dir 时写入系统临时目录。"""
        import uuid
        name = f"tmp-plugin-{uuid.uuid4().hex[:8]}"
        ok, msg = plugin_env.manager.create_plugin(name)
        assert ok is True
        assert f"jarvis-plugin-{name}" in msg

    def test_create_plugin_invalid_name(self, plugin_env):
        """非法插件名（含特殊字符）。"""
        ok, msg = plugin_env.manager.create_plugin("bad name!")
        assert ok is False
        assert "不合法" in msg

    def test_create_plugin_dir_exists(self, plugin_env):
        """目标目录已存在。"""
        ok, _ = plugin_env.manager.create_plugin("dup", output_dir=plugin_env.market)
        assert ok is True
        ok, msg = plugin_env.manager.create_plugin("dup", output_dir=plugin_env.market)
        assert ok is False
        assert "目录已存在" in msg

    def test_validate_plugin_ok(self, plugin_env):
        """合法插件通过校验。"""
        d = plugin_env.market / "good"
        d.mkdir()
        (d / "plugin.json").write_text(
            json.dumps({"name": "good", "version": "1.0", "description": "d",
                        "source": {"type": "github", "repo": "a/b"}}),
            encoding="utf-8",
        )
        ok, errs = plugin_env.manager.validate_plugin(d)
        assert ok is True
        assert errs == []
        # 直接传 plugin.json 文件路径
        ok, _ = plugin_env.manager.validate_plugin(d / "plugin.json")
        assert ok is True

    def test_validate_plugin_dir_without_manifest(self, plugin_env):
        """目录中缺少 plugin.json。"""
        d = plugin_env.market / "empty"
        d.mkdir()
        ok, errs = plugin_env.manager.validate_plugin(d)
        assert ok is False
        assert "未找到 plugin.json" in errs[0]

    def test_validate_plugin_bad_path_and_type(self, plugin_env):
        """路径不存在 / 非 plugin.json 文件。"""
        ok, _ = plugin_env.manager.validate_plugin(plugin_env.market / "nope")
        assert ok is False
        f = plugin_env.market / "readme.md"
        f.write_text("x", encoding="utf-8")
        ok, errs = plugin_env.manager.validate_plugin(f)
        assert ok is False
        assert "只支持 plugin.json" in errs[0]

    def test_validate_plugin_bad_json(self, plugin_env):
        """JSON 格式错误。"""
        d = plugin_env.market / "bad"
        d.mkdir()
        (d / "plugin.json").write_text("{", encoding="utf-8")
        ok, errs = plugin_env.manager.validate_plugin(d)
        assert ok is False
        assert "JSON 格式错误" in errs[0]

    def test_validate_plugin_missing_fields(self, plugin_env):
        """缺少必填字段。"""
        d = plugin_env.market / "nofields"
        d.mkdir()
        (d / "plugin.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")
        ok, errs = plugin_env.manager.validate_plugin(d)
        assert ok is False
        assert any("缺少必填字段" in e for e in errs)

    def test_validate_plugin_bad_source(self, plugin_env):
        """非法 source.type / github 缺 repo / 非法 name。"""
        cases = [
            {"name": "x", "version": "1", "description": "d",
             "source": {"type": "ftp"}},
            {"name": "x", "version": "1", "description": "d",
             "source": {"type": "github"}},
            {"name": "bad name!", "version": "1", "description": "d"},
        ]
        for i, manifest in enumerate(cases):
            d = plugin_env.market / f"case{i}"
            d.mkdir()
            (d / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
            ok, errs = plugin_env.manager.validate_plugin(d)
            assert ok is False
            assert errs


# ---- 内部状态 ----

class TestInternal:
    """_load / _save / 禁用状态持久化测试。"""

    def test_load_save_installed(self, plugin_env):
        """installed.json 读写与损坏兜底。"""
        manager = plugin_env.manager
        manager._save({"plugins": {"a": {"name": "a"}}})
        assert manager._load()["plugins"]["a"]["name"] == "a"
        # 损坏文件 → 默认空结构
        manager._installed_path.write_text("{broken", encoding="utf-8")
        assert manager._load() == {"plugins": {}}
        # 文件不存在 → 默认空结构
        manager._installed_path.unlink()
        assert manager._load() == {"plugins": {}}

    def test_load_save_disabled_state(self, plugin_env):
        """disabled.json 读写与损坏兜底。"""
        manager = plugin_env.manager
        manager._save_disabled_state({"disabled": ["x"]})
        assert manager._load_disabled_state() == {"disabled": ["x"]}
        path = manager._install_base / "disabled.json"
        path.write_text("{broken", encoding="utf-8")
        assert manager._load_disabled_state() == {"disabled": []}
        path.unlink()
        assert manager._load_disabled_state() == {"disabled": []}

    def test_default_install_base(self, tmp_path, monkeypatch):
        """未指定 install_base 时使用 ~/.jarvis/plugins。"""
        home = tmp_path / "h"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        m = PluginManager(marketplace_url="")
        assert m._install_base == home / ".jarvis" / "plugins"
        assert m._skills_base == home / ".jarvis" / "skills"
