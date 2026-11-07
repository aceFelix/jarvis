"""长期记忆读写 API（设置面板 / MemoryUpdate 工具 / serve RPC）单测。

覆盖 2026-11 新增的三层：

1. ``memory.store``：``read_memory_scope`` / ``save_memory_scope`` /
   ``append_memory_entry`` 的读写、覆盖前备份、长度上限与去重；
2. ``MemoryUpdateTool``：参数校验、追加成功、重复不写、项目级缺 workdir；
3. ``memory.get`` / ``memory.save`` RPC：快照形状与保存后通知引擎。

所有路径都用 monkeypatch 把 ``~/.jarvis`` 重定向到 tmp_path，不碰真实用户目录。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.core.context import ToolContext
from agent.core.memory import store
from agent.core.memory.store import (
    MEMORY_MAX_CHARS,
    append_memory_entry,
    project_memory_path,
    read_memory_scope,
    save_memory_scope,
    user_memory_path,
)
from agent.serve.rpc_memory import MemoryRpcMixin
from agent.tools.memory.memory_update import MemoryUpdateTool


@pytest.fixture
def jarvis_home(tmp_path, monkeypatch):
    """把 store 的 user_jarvis_dir 重定向到隔离目录。"""
    home = tmp_path / ".jarvis"
    monkeypatch.setattr(store, "user_jarvis_dir", lambda: home)
    return home


@pytest.fixture
def workdir(tmp_path):
    """独立项目目录（与 ~/.jarvis 不重叠，避免两个 scope 指向同一文件）。"""
    d = tmp_path / "proj"
    d.mkdir()
    return str(d)


def _ctx(workdir: str) -> ToolContext:
    """构造只带 workdir 的最小工具上下文。"""
    return ToolContext(workdir=workdir, messages=[])


class TestReadMemoryScope:
    """读取快照：首次打开、正常内容、非法参数。"""

    def test_missing_file_is_empty_not_error(self, jarvis_home, workdir) -> None:
        snap = read_memory_scope(workdir, "project")
        assert snap["exists"] is False
        assert snap["content"] == ""
        assert snap["chars"] == 0
        assert snap["updated_at"] == 0
        assert snap["path"].endswith("MEMORY.md")

    def test_existing_file_returns_content_and_chars(self, jarvis_home, workdir) -> None:
        path = project_memory_path(workdir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 长期记忆\n\n- 用 pytest（2026-11-06）\n", encoding="utf-8")
        snap = read_memory_scope(workdir, "project")
        assert snap["exists"] is True
        assert "- 用 pytest" in snap["content"]
        assert snap["chars"] == len(snap["content"])
        assert snap["updated_at"] > 0

    def test_unknown_scope_raises(self, jarvis_home, workdir) -> None:
        with pytest.raises(ValueError):
            read_memory_scope(workdir, "global")

    def test_project_scope_requires_workdir(self, jarvis_home) -> None:
        with pytest.raises(ValueError):
            read_memory_scope("", "project")


class TestSaveMemoryScope:
    """整体覆盖：原子落盘、备份、长度上限、空内容。"""

    def test_save_creates_file_and_roundtrips(self, jarvis_home, workdir) -> None:
        snap = save_memory_scope(workdir, "project", "# 长期记忆\n\n- 记住 A")
        assert snap["exists"] is True
        assert "- 记住 A" in snap["content"]
        assert snap["content"].endswith("\n")  # 统一补尾换行，注入时不必再拼
        assert project_memory_path(workdir).is_file()

    def test_save_keeps_single_backup(self, jarvis_home, workdir) -> None:
        path = project_memory_path(workdir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("旧内容", encoding="utf-8")
        save_memory_scope(workdir, "project", "新内容")
        bak = path.with_suffix(path.suffix + ".bak")
        assert bak.is_file()
        assert "旧内容" in bak.read_text(encoding="utf-8")
        # 只有一份 .bak，不生成 .bak.1
        assert not path.with_suffix(path.suffix + ".bak.1").exists()

    def test_save_empty_content_writes_empty_file(self, jarvis_home, workdir) -> None:
        path = project_memory_path(workdir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("待清空", encoding="utf-8")
        snap = save_memory_scope(workdir, "project", "   \n")
        assert snap["exists"] is True
        assert snap["content"] == ""
        assert path.read_text(encoding="utf-8") == ""

    def test_save_over_limit_rejected(self, jarvis_home, workdir) -> None:
        with pytest.raises(ValueError):
            save_memory_scope(workdir, "user", "x" * (MEMORY_MAX_CHARS + 1))
        assert not user_memory_path().is_file()

    def test_save_user_scope_goes_to_home(self, jarvis_home, workdir) -> None:
        save_memory_scope(workdir, "user", "- 喜欢中文回答")
        assert user_memory_path().is_file()
        assert "喜欢中文回答" in user_memory_path().read_text(encoding="utf-8")


class TestAppendMemoryEntry:
    """模型追加路径：去重、日期、空文件头、单条上限。"""

    def test_first_entry_writes_header(self, jarvis_home, workdir) -> None:
        result = append_memory_entry(workdir, "project", "项目用 Python 3.12", today="2026-11-06")
        assert result["added"] is True
        assert result["content"].startswith("# 长期记忆")
        assert "- 项目用 Python 3.12（2026-11-06）" in result["content"]

    def test_duplicate_entry_skipped(self, jarvis_home, workdir) -> None:
        first = append_memory_entry(workdir, "project", "使用 pytest", today="2026-11-06")
        second = append_memory_entry(workdir, "project", "  使用   pytest ", today="2026-11-07")
        assert second["added"] is False
        assert second["content"] == first["content"]

    def test_whitespace_normalized(self, jarvis_home, workdir) -> None:
        result = append_memory_entry(workdir, "user", "用户   喜欢\n简洁回答")
        assert result["entry"] == "用户 喜欢 简洁回答"

    def test_empty_entry_raises(self, jarvis_home, workdir) -> None:
        with pytest.raises(ValueError):
            append_memory_entry(workdir, "project", "   ")

    def test_too_long_entry_raises(self, jarvis_home, workdir) -> None:
        with pytest.raises(ValueError):
            append_memory_entry(workdir, "project", "x" * 2_001)


class TestMemoryUpdateTool:
    """工具层：校验与追加，不覆盖已有内容。"""

    def test_append_to_project(self, jarvis_home, workdir) -> None:
        import asyncio

        tool = MemoryUpdateTool()
        result = asyncio.run(
            tool.call({"content": "本仓库的测试命令是 pytest", "scope": "project"}, _ctx(workdir))
        )
        assert result.is_error is False
        assert "已写入项目级长期记忆" in result.data
        path = project_memory_path(workdir)
        assert "本仓库的测试命令是 pytest" in path.read_text(encoding="utf-8")

    def test_duplicate_reports_not_added(self, jarvis_home, workdir) -> None:
        import asyncio

        tool = MemoryUpdateTool()
        args = {"content": "偏好中文", "scope": "user"}
        asyncio.run(tool.call(args, _ctx(workdir)))
        second = asyncio.run(tool.call(args, _ctx(workdir)))
        assert "已存在相同内容" in second.data
        # 只写了一行
        assert user_memory_path().read_text(encoding="utf-8").count("偏好中文") == 1

    def test_validate_rejects_empty_content(self, workdir) -> None:
        v = MemoryUpdateTool().validate_input({"content": "  ", "scope": "user"}, _ctx(workdir))
        assert v.ok is False

    def test_validate_rejects_bad_scope(self, workdir) -> None:
        v = MemoryUpdateTool().validate_input({"content": "x", "scope": "global"}, _ctx(workdir))
        assert v.ok is False

    def test_validate_rejects_project_without_workdir(self) -> None:
        v = MemoryUpdateTool().validate_input({"content": "x", "scope": "project"}, _ctx(""))
        assert v.ok is False

    def test_tool_permission_is_allow(self, workdir) -> None:
        from agent.core.result import PermissionBehavior

        result = MemoryUpdateTool().check_permissions({}, _ctx(workdir))
        assert result.behavior == PermissionBehavior.ALLOW


class _FakeSettings:
    """只带 workdir 的 settings 替身。"""

    def __init__(self, workdir: str) -> None:
        self.workdir = workdir


class _FakeApi:
    """记录 reload_memory 调用的引擎门面替身。"""

    def __init__(self) -> None:
        self.reloaded: list[str] = []

    def reload_memory(self, scope: str) -> None:
        self.reloaded.append(scope)


class _FakeServer(MemoryRpcMixin):
    """把 RPC mixin 挂到最小宿主上。"""

    def __init__(self, workdir: str, api: _FakeApi | None = None) -> None:
        self._settings = _FakeSettings(workdir)
        self._api = api or _FakeApi()


class TestMemoryRpc:
    """serve 的 memory.get / memory.save。"""

    def test_get_returns_user_and_project(self, jarvis_home, workdir) -> None:
        srv = _FakeServer(workdir)
        payload = srv._rpc_memory_get({})
        assert payload["workdir"] == workdir
        assert [e["scope"] for e in payload["entries"]] == ["user", "project"]

    def test_get_without_workdir_only_user(self, jarvis_home) -> None:
        payload = _FakeServer("")._rpc_memory_get({})
        assert payload["workdir"] == ""
        assert [e["scope"] for e in payload["entries"]] == ["user"]

    def test_save_writes_and_notifies_engine(self, jarvis_home, workdir) -> None:
        api = _FakeApi()
        srv = _FakeServer(workdir, api)
        out = srv._rpc_memory_save({"scope": "project", "content": "- 项目约定：先写测试"})
        assert out["scope"] == "project"
        assert api.reloaded == ["project"]
        assert "先写测试" in project_memory_path(workdir).read_text(encoding="utf-8")

    def test_save_rejects_unknown_scope(self, jarvis_home, workdir) -> None:
        with pytest.raises(ValueError):
            _FakeServer(workdir)._rpc_memory_save({"scope": "global", "content": "x"})

    def test_save_rejects_non_string_content(self, jarvis_home, workdir) -> None:
        with pytest.raises(ValueError):
            _FakeServer(workdir)._rpc_memory_save({"scope": "user", "content": 123})
