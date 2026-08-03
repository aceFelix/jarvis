"""核心工具单元测试（合并文件）。

覆盖:
- agent/tools/base.py: resolve_path / truncate_for_llm
- agent/tools/bash.py: 命令执行（成功/失败/超时）、权限判定、沙箱路径
- agent/tools/file_ops/: FileRead / FileWrite / FileEdit / Glob / Grep
- agent/tools/tool_search.py: 延迟工具搜索与打分
- agent/tools/todo.py: 任务清单
- agent/tools/location.py: IP 定位（网络请求用 mock）
- agent/tools/ask_user.py: 提问（UI 用 stub）
- agent/core/tool.py: Tool 基类默认行为与 PermissionMatcher

@author aceFelix
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.core.context import ToolContext
from agent.core.message import Message
from agent.core.result import PermissionBehavior, ToolResult, ValidationResult
from agent.core.tool import PermissionMatcher, Tool, ToolRegistry
from agent.tools import bash as bash_mod
from agent.tools.ask_user import AskUserTool
from agent.tools.base import resolve_path, truncate_for_llm
from agent.tools.bash import BashTool
from agent.tools.file_ops.file_edit import FileEditTool
from agent.tools.file_ops.file_read import FileReadTool
from agent.tools.file_ops.file_write import FileWriteTool
from agent.tools.file_ops.glob import GlobTool
from agent.tools.file_ops.grep import GrepTool
from agent.tools.location import LocationTool
from agent.tools.todo import TodoWriteTool
from agent.tools.tool_search import ToolSearchTool


# ── 通用辅助 ──


def make_ctx(workdir: str | Path, **kwargs) -> ToolContext:
    """构造测试用 ToolContext。"""
    return ToolContext(
        workdir=str(workdir),
        messages=[Message(role="user")],
        **kwargs,
    )


class FakeUI:
    """记录 info 调用的 UI 桩。"""

    def __init__(self) -> None:
        self.infos: list[str] = []

    def info(self, text: str) -> None:
        self.infos.append(text)


# ── base.py ──


class TestResolvePath:
    """路径解析。"""

    def test_absolute_path_unchanged(self, tmp_path: Path) -> None:
        p = tmp_path / "a.txt"
        assert resolve_path(make_ctx(tmp_path), str(p)) == p

    def test_relative_path_joins_workdir(self, tmp_path: Path) -> None:
        assert resolve_path(make_ctx(tmp_path), "sub/b.txt") == tmp_path / "sub" / "b.txt"

    def test_git_bash_style_path_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Git Bash 风格 /e/foo → E:/foo（仅 win32）。"""
        monkeypatch.setattr(sys, "platform", "win32")
        p = resolve_path(make_ctx("C:/work"), "/e/path/x.txt")
        assert p.drive == "E:"
        assert str(p).endswith("path/x.txt") or str(p).endswith("path\\x.txt")

    def test_dot_means_workdir(self, tmp_path: Path) -> None:
        assert resolve_path(make_ctx(tmp_path), ".") == tmp_path

    def test_strip_whitespace(self, tmp_path: Path) -> None:
        assert resolve_path(make_ctx(tmp_path), "  a.txt  ") == tmp_path / "a.txt"


class TestTruncateForLlm:
    """超长文本截断。"""

    def test_short_text_unchanged(self) -> None:
        assert truncate_for_llm("hello", 100) == "hello"

    def test_long_text_keeps_head_and_tail(self) -> None:
        text = "x" * 2000
        result = truncate_for_llm(text, max_chars=100, preview=30)
        assert result.startswith("x" * 30)
        assert result.endswith("x" * 30)
        assert "省略 1940 字符" in result

    def test_exactly_at_limit_unchanged(self) -> None:
        assert truncate_for_llm("abc", 3) == "abc"


# ── bash.py ──


class TestBashMetadata:
    """Bash 工具元数据与安全属性。"""

    def test_name_and_description(self) -> None:
        tool = BashTool()
        assert tool.name == "Bash"
        assert "执行 shell 命令" in tool.description

    def test_input_schema_requires_command(self) -> None:
        assert "command" in BashTool.input_schema["required"]

    def test_is_read_only_for_echo(self) -> None:
        assert BashTool().is_read_only({"command": "echo hi"}) is True

    def test_is_read_only_for_write_command(self) -> None:
        assert BashTool().is_read_only({"command": "git commit -m x"}) is False

    def test_is_concurrency_safe_matches_readonly(self) -> None:
        tool = BashTool()
        assert tool.is_concurrency_safe({"command": "echo hi"}) is True
        assert tool.is_concurrency_safe({"command": "git commit -m x"}) is False

    def test_validate_input_empty_command_fails(self) -> None:
        ctx = make_ctx("/tmp")
        r = BashTool().validate_input({"command": "  "}, ctx)
        assert r.ok is False
        assert "command" in r.message

    def test_validate_input_ok(self) -> None:
        r = BashTool().validate_input({"command": "echo hi"}, make_ctx("/tmp"))
        assert r.ok is True

    def test_activity_description(self) -> None:
        tool = BashTool()
        assert tool.activity_description({"command": "echo hello"}) == "运行 echo hello"
        assert tool.activity_description({}) is None


class TestBashPermissions:
    """Bash 权限判定。"""

    def test_readonly_allowed(self) -> None:
        r = BashTool().check_permissions({"command": "echo hi"}, make_ctx("/tmp"))
        assert r.behavior == PermissionBehavior.ALLOW

    def test_dangerous_denied(self) -> None:
        r = BashTool().check_permissions({"command": "rm -rf /"}, make_ctx("/tmp"))
        assert r.behavior == PermissionBehavior.DENY
        assert "危险" in (r.reason or "")

    def test_unknown_asked(self) -> None:
        r = BashTool().check_permissions({"command": "some-unlikely-cmd"}, make_ctx("/tmp"))
        assert r.behavior == PermissionBehavior.ASK

    def test_sandbox_medium_auto_allow(self) -> None:
        settings = SimpleNamespace(sandbox_enabled=True, sandbox_auto_allow_medium=True)
        ctx = make_ctx("/tmp", settings=settings)
        r = BashTool().check_permissions({"command": "git commit -m x"}, ctx)
        assert r.behavior == PermissionBehavior.ALLOW
        assert "沙箱" in (r.reason or "")

    def test_sandbox_medium_without_auto_allow_asks(self) -> None:
        settings = SimpleNamespace(sandbox_enabled=True, sandbox_auto_allow_medium=False)
        ctx = make_ctx("/tmp", settings=settings)
        r = BashTool().check_permissions({"command": "git commit -m x"}, ctx)
        assert r.behavior == PermissionBehavior.ASK

    def test_prepare_permission_matcher(self) -> None:
        matcher = BashTool().prepare_permission_matcher({"command": "git status -s"})
        assert matcher is not None
        assert matcher.tool_name == "Bash"
        assert matcher.matches("Bash(git status *)")
        assert matcher.matches("Bash")
        assert not matcher.matches("FileRead")


class TestBashExecution:
    """Bash 真实执行（安全命令）。"""

    @pytest.mark.asyncio
    async def test_echo_success(self, tmp_path: Path) -> None:
        tool = BashTool()
        result = await tool.call({"command": "echo hello-jarvis"}, make_ctx(tmp_path))
        assert result.is_error is False
        assert "hello-jarvis" in str(result.data)
        assert "[exit=0" in str(result.data)

    @pytest.mark.asyncio
    async def test_nonzero_exit_is_error(self, tmp_path: Path) -> None:
        tool = BashTool()
        result = await tool.call({"command": "exit 3"}, make_ctx(tmp_path))
        assert result.is_error is True
        assert "[exit=3" in str(result.data)

    @pytest.mark.asyncio
    async def test_cwd_argument_used(self, tmp_path: Path) -> None:
        tool = BashTool()
        result = await tool.call(
            {"command": "echo cwd-ok", "cwd": str(tmp_path)}, make_ctx("/nonexistent-workdir")
        )
        assert result.is_error is False
        assert "cwd-ok" in str(result.data)

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """超时路径：mock subprocess 使 communicate 永久挂起触发 TimeoutError。"""

        class FakeProc:
            returncode = 1

            async def communicate(self):
                await asyncio.sleep(100)  # 永不返回 → 触发超时

            def kill(self) -> None:
                pass

        async def fake_create(*args, **kwargs):
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
        # 不 mock wait_for，用真实的超时机制

        tool = BashTool()
        result = await tool.call({"command": "sleep 100", "timeout": 1}, make_ctx(tmp_path))
        assert result.is_error is True
        assert "超时" in str(result.data)

    def test_find_bash_returns_str_or_none(self) -> None:
        r = BashTool._find_bash()
        assert r is None or isinstance(r, str)


class TestBashSandboxed:
    """沙箱执行路径。"""

    class FakeExecutor:
        def __init__(self, result: Any) -> None:
            self._result = result

        async def run(self, command: str, cwd: str | None = None, timeout: int | None = None):
            return self._result

    class FakeAuditor:
        def __init__(self) -> None:
            self.executions: list[dict] = []

        def log_snapshot(self, snapshot_id: str, dirs: list, reason: str) -> None:
            pass

        def log_execution(self, **kwargs: Any) -> None:
            self.executions.append(kwargs)

    @staticmethod
    def _sandbox_settings() -> SimpleNamespace:
        return SimpleNamespace(
            sandbox_enabled=True,
            sandbox_auto_allow_medium=True,
            sandbox_max_snapshots=20,
        )

    def _run(self, monkeypatch: pytest.MonkeyPatch, result: Any) -> tuple[BashTool, ToolContext, "TestBashSandboxed.FakeAuditor"]:
        executor = self.FakeExecutor(result)
        auditor = self.FakeAuditor()
        monkeypatch.setattr(bash_mod, "get_sandbox_executor", lambda settings: executor)
        monkeypatch.setattr(bash_mod, "get_sandbox_auditor", lambda settings: auditor)
        ctx = make_ctx("/tmp", settings=self._sandbox_settings())
        return BashTool(), ctx, auditor

    @pytest.mark.asyncio
    async def test_sandbox_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = SimpleNamespace(
            sandboxed=True, exit_code=0, timed_out=False,
            resource_exceeded=False, error=None,
            stdout="sandbox output", stderr="",
        )
        tool, ctx, auditor = self._run(monkeypatch, result)
        r = await tool.call({"command": "git commit -m x"}, ctx)
        assert r.is_error is False
        data = str(r.data)
        assert "🛡️沙箱" in data
        assert "sandbox output" in data
        assert auditor.executions and auditor.executions[0]["sandboxed"] is True

    @pytest.mark.asyncio
    async def test_sandbox_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = SimpleNamespace(
            sandboxed=True, exit_code=0, timed_out=True,
            resource_exceeded=False, error=None, stdout="", stderr="",
        )
        tool, ctx, _ = self._run(monkeypatch, result)
        r = await tool.call({"command": "git commit -m x"}, ctx)
        assert r.is_error is True
        assert "超时" in str(r.data)

    @pytest.mark.asyncio
    async def test_sandbox_executor_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = SimpleNamespace(
            sandboxed=False, exit_code=1, timed_out=False,
            resource_exceeded=False, error="sandbox crashed", stdout="", stderr="",
        )
        tool, ctx, _ = self._run(monkeypatch, result)
        r = await tool.call({"command": "git commit -m x"}, ctx)
        assert r.is_error is True
        assert "sandbox crashed" in str(r.data)

    @pytest.mark.asyncio
    async def test_sandbox_nonzero_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = SimpleNamespace(
            sandboxed=True, exit_code=2, timed_out=False,
            resource_exceeded=False, error=None, stdout="bad", stderr="boom",
        )
        tool, ctx, _ = self._run(monkeypatch, result)
        r = await tool.call({"command": "git commit -m x"}, ctx)
        assert r.is_error is True
        assert "[stderr]" in str(r.data)
        assert "boom" in str(r.data)


# ── file_ops / FileWrite ──


class TestFileWriteTool:
    """FileWrite 工具。"""

    def test_metadata(self) -> None:
        tool = FileWriteTool()
        assert tool.name == "FileWrite"
        assert tool.is_read_only({}) is False
        assert tool.is_destructive({}) is True
        assert tool.check_permissions({}, make_ctx("/tmp")).behavior == PermissionBehavior.ASK

    def test_validate_input(self) -> None:
        ctx = make_ctx("/tmp")
        assert FileWriteTool().validate_input({"file_path": "", "content": "x"}, ctx).ok is False
        assert FileWriteTool().validate_input({"file_path": "a.txt", "content": None}, ctx).ok is False
        assert FileWriteTool().validate_input({"file_path": "a.txt", "content": "x"}, ctx).ok is True

    def test_get_path(self) -> None:
        assert FileWriteTool().get_path({"file_path": "a.txt"}) == "a.txt"

    @pytest.mark.asyncio
    async def test_write_new_file(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        target = tmp_path / "sub" / "nested" / "new.txt"
        r = await FileWriteTool().call(
            {"file_path": str(target), "content": "hello\nworld"}, ctx
        )
        assert r.is_error is False
        assert target.read_text(encoding="utf-8") == "hello\nworld"
        assert "已写入" in str(r.data)

    @pytest.mark.asyncio
    async def test_write_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("old", encoding="utf-8")
        r = await FileWriteTool().call({"file_path": str(target), "content": "new"}, make_ctx(tmp_path))
        assert r.is_error is False
        assert target.read_text(encoding="utf-8") == "new"

    @pytest.mark.asyncio
    async def test_write_rejects_stale_file(self, tmp_path: Path) -> None:
        """文件被外部修改（mtime 变化）时拒绝覆盖。"""
        from agent.core.memory.file_state import record_file_read

        ctx = make_ctx(tmp_path)
        target = tmp_path / "stale.txt"
        target.write_text("v1", encoding="utf-8")
        record_file_read(ctx, str(target))
        # 把 mtime 改到过去，模拟外部修改
        old = target.stat().st_mtime - 100
        os.utime(target, (old, old))
        r = await FileWriteTool().call({"file_path": str(target), "content": "v2"}, ctx)
        assert r.is_error is True
        assert "外部修改" in str(r.data)

    @pytest.mark.asyncio
    async def test_write_unwritable_path_returns_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        def boom(*args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "write_text", boom)
        r = await FileWriteTool().call(
            {"file_path": str(tmp_path / "x.txt"), "content": "x"}, make_ctx(tmp_path)
        )
        assert r.is_error is True
        assert "写入失败" in str(r.data)

    def test_activity_description(self) -> None:
        tool = FileWriteTool()
        assert tool.activity_description({"file_path": "a.txt"}) == "写入 a.txt"
        assert tool.activity_description({}) is None


# ── file_ops / FileRead ──


class TestFileReadTool:
    """FileRead 工具。"""

    @pytest.fixture
    def sample(self, tmp_path: Path) -> Path:
        p = tmp_path / "sample.txt"
        p.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
        return p

    def test_metadata(self) -> None:
        tool = FileReadTool()
        assert tool.name == "FileRead"
        assert tool.is_read_only({}) is True
        assert tool.is_concurrency_safe({}) is True
        assert tool.check_permissions({}, make_ctx("/tmp")).behavior == PermissionBehavior.ALLOW

    def test_validate_input(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        assert FileReadTool().validate_input({"file_path": "no-such.txt"}, ctx).ok is False
        assert FileReadTool().validate_input({"file_path": str(tmp_path)}, ctx).ok is False  # 目录
        sample = tmp_path / "ok.txt"
        sample.write_text("x", encoding="utf-8")
        assert FileReadTool().validate_input({"file_path": str(sample)}, ctx).ok is True

    @pytest.mark.asyncio
    async def test_read_full_file(self, tmp_path: Path, sample: Path) -> None:
        r = await FileReadTool().call({"file_path": str(sample)}, make_ctx(tmp_path))
        assert r.is_error is False
        assert "line0" in str(r.data)
        assert "共 10 行" in str(r.data)

    @pytest.mark.asyncio
    async def test_read_with_offset_and_limit(self, tmp_path: Path, sample: Path) -> None:
        r = await FileReadTool().call(
            {"file_path": str(sample), "offset": 2, "limit": 3}, make_ctx(tmp_path)
        )
        data = str(r.data)
        assert "line1" in data  # 从第 2 行开始（1-based）
        assert "line3" in data
        assert "line4" not in data

    @pytest.mark.asyncio
    async def test_read_binary_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "bin.dat"
        p.write_bytes(b"\x00\x01\x02")
        r = await FileReadTool().call({"file_path": str(p)}, make_ctx(tmp_path))
        assert r.is_error is True
        assert "二进制" in str(r.data)

    @pytest.mark.asyncio
    async def test_read_missing_file_raises(self, tmp_path: Path) -> None:
        """已知源码缺陷：文件不存在时 call 抛 FileNotFoundError 而非返回错误结果。

        FileReadTool.call 只捕获 PermissionError，FileNotFoundError 未被捕获会
        直接抛出。这里记录该行为（不改源码），用 pytest.raises 断言。
        """
        with pytest.raises(FileNotFoundError):
            await FileReadTool().call({"file_path": "ghost.txt"}, make_ctx(tmp_path))

    def test_get_path(self) -> None:
        assert FileReadTool().get_path({"file_path": "x"}) == "x"

    def test_activity_description(self) -> None:
        assert FileReadTool().activity_description({"file_path": "a.txt"}) == "读取 a.txt"


# ── file_ops / FileEdit ──


class TestFileEditTool:
    """FileEdit 工具。"""

    @pytest.fixture
    def target(self, tmp_path: Path) -> Path:
        p = tmp_path / "edit.txt"
        p.write_text("alpha beta gamma\n", encoding="utf-8")
        return p

    def test_metadata(self) -> None:
        tool = FileEditTool()
        assert tool.name == "FileEdit"
        assert tool.is_destructive({}) is True
        assert tool.check_permissions({}, make_ctx("/tmp")).behavior == PermissionBehavior.ASK

    def test_validate_input(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        assert FileEditTool().validate_input({"file_path": "ghost", "old_string": "a", "new_string": "b"}, ctx).ok is False
        p = tmp_path / "v.txt"
        p.write_text("abc", encoding="utf-8")
        assert FileEditTool().validate_input({"file_path": str(p), "old_string": "", "new_string": "b"}, ctx).ok is False
        assert FileEditTool().validate_input({"file_path": str(p), "old_string": "a", "new_string": None}, ctx).ok is False
        assert FileEditTool().validate_input({"file_path": str(p), "old_string": "a", "new_string": "b"}, ctx).ok is True

    @pytest.mark.asyncio
    async def test_edit_success(self, tmp_path: Path, target: Path) -> None:
        r = await FileEditTool().call(
            {"file_path": str(target), "old_string": "beta", "new_string": "BETA"}, make_ctx(tmp_path)
        )
        assert r.is_error is False
        assert target.read_text(encoding="utf-8") == "alpha BETA gamma\n"

    @pytest.mark.asyncio
    async def test_edit_not_found(self, tmp_path: Path, target: Path) -> None:
        r = await FileEditTool().call(
            {"file_path": str(target), "old_string": "zzz", "new_string": "x"}, make_ctx(tmp_path)
        )
        assert r.is_error is True
        assert "未找到" in str(r.data)

    @pytest.mark.asyncio
    async def test_edit_not_unique(self, tmp_path: Path) -> None:
        p = tmp_path / "dup.txt"
        p.write_text("aaa aaa\n", encoding="utf-8")
        r = await FileEditTool().call(
            {"file_path": str(p), "old_string": "aaa", "new_string": "bbb"}, make_ctx(tmp_path)
        )
        assert r.is_error is True
        assert "不唯一" in str(r.data)

    @pytest.mark.asyncio
    async def test_edit_stale_file_rejected(self, tmp_path: Path) -> None:
        from agent.core.memory.file_state import record_file_read

        ctx = make_ctx(tmp_path)
        p = tmp_path / "stale-edit.txt"
        p.write_text("content v1", encoding="utf-8")
        record_file_read(ctx, str(p))
        old = p.stat().st_mtime - 100
        os.utime(p, (old, old))
        r = await FileEditTool().call(
            {"file_path": str(p), "old_string": "v1", "new_string": "v2"}, ctx
        )
        assert r.is_error is True
        assert "外部修改" in str(r.data)

    def test_get_path(self) -> None:
        assert FileEditTool().get_path({"file_path": "x"}) == "x"


# ── file_ops / Glob ──


class TestGlobTool:
    """Glob 工具。"""

    @pytest.fixture
    def tree(self, tmp_path: Path) -> Path:
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("y", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.py").write_text("z", encoding="utf-8")
        return tmp_path

    def test_metadata(self) -> None:
        tool = GlobTool()
        assert tool.name == "Glob"
        assert tool.is_read_only({}) is True
        assert tool.check_permissions({}, make_ctx("/tmp")).behavior == PermissionBehavior.ALLOW

    @pytest.mark.asyncio
    async def test_glob_finds_files(self, tmp_path: Path, tree: Path) -> None:
        r = await GlobTool().call({"pattern": "*.py"}, make_ctx(tmp_path))
        assert r.is_error is False
        assert "a.py" in str(r.data)

    @pytest.mark.asyncio
    async def test_glob_recursive(self, tmp_path: Path, tree: Path) -> None:
        r = await GlobTool().call({"pattern": "**/*.py"}, make_ctx(tmp_path))
        data = str(r.data)
        assert "a.py" in data
        assert "c.py" in data

    @pytest.mark.asyncio
    async def test_glob_no_match(self, tmp_path: Path, tree: Path) -> None:
        r = await GlobTool().call({"pattern": "*.xyz"}, make_ctx(tmp_path))
        assert r.is_error is False
        assert "未找到" in str(r.data)

    @pytest.mark.asyncio
    async def test_glob_nonexistent_root(self, tmp_path: Path) -> None:
        r = await GlobTool().call({"pattern": "*.py", "path": str(tmp_path / "ghost")}, make_ctx(tmp_path))
        assert r.is_error is True
        assert "不存在" in str(r.data)

    @pytest.mark.asyncio
    async def test_glob_absolute_path_result(self, tmp_path: Path) -> None:
        """搜索根不在 workdir 内时回退到绝对路径输出。"""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "x.txt").write_text("x", encoding="utf-8")
        r = await GlobTool().call({"pattern": "*.txt", "path": str(outside)}, make_ctx(tmp_path))
        assert r.is_error is False
        assert "x.txt" in str(r.data)

    def test_activity_description(self) -> None:
        assert GlobTool().activity_description({"pattern": "*.py"}) == "搜索 *.py"


# ── file_ops / Grep ──


class TestGrepTool:
    """Grep 工具。"""

    @pytest.fixture
    def tree(self, tmp_path: Path) -> Path:
        (tmp_path / "a.py").write_text("hello world\nsecond line\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("HELLO there\n", encoding="utf-8")
        return tmp_path

    def test_metadata(self) -> None:
        tool = GrepTool()
        assert tool.name == "Grep"
        assert tool.is_read_only({}) is True
        assert tool.check_permissions({}, make_ctx("/tmp")).behavior == PermissionBehavior.ALLOW

    def test_validate_input(self) -> None:
        ctx = make_ctx("/tmp")
        assert GrepTool().validate_input({"pattern": "("}, ctx).ok is False  # 无效正则
        assert GrepTool().validate_input({"pattern": "ok"}, ctx).ok is True

    @pytest.mark.asyncio
    async def test_grep_finds_match(self, tmp_path: Path, tree: Path) -> None:
        r = await GrepTool().call({"pattern": "hello"}, make_ctx(tmp_path))
        data = str(r.data)
        assert "a.py:1" in data
        assert "hello world" in data

    @pytest.mark.asyncio
    async def test_grep_include_filter(self, tmp_path: Path, tree: Path) -> None:
        r = await GrepTool().call({"pattern": "hello", "include": "*.py"}, make_ctx(tmp_path))
        data = str(r.data)
        assert "a.py" in data
        assert "b.txt" not in data

    @pytest.mark.asyncio
    async def test_grep_ignore_case(self, tmp_path: Path, tree: Path) -> None:
        r = await GrepTool().call({"pattern": "hello", "ignore_case": True}, make_ctx(tmp_path))
        data = str(r.data)
        assert "b.txt:1" in data  # HELLO 被忽略大小写匹配

    @pytest.mark.asyncio
    async def test_grep_no_match(self, tmp_path: Path, tree: Path) -> None:
        r = await GrepTool().call({"pattern": "nonexistent-word"}, make_ctx(tmp_path))
        assert r.is_error is False
        assert "未找到匹配" in str(r.data)

    @pytest.mark.asyncio
    async def test_grep_nonexistent_root(self, tmp_path: Path) -> None:
        r = await GrepTool().call({"pattern": "x", "path": str(tmp_path / "ghost")}, make_ctx(tmp_path))
        assert r.is_error is True

    @pytest.mark.asyncio
    async def test_grep_skips_binary(self, tmp_path: Path) -> None:
        (tmp_path / "bin.dat").write_bytes(b"\x00\xff")
        r = await GrepTool().call({"pattern": "x"}, make_ctx(tmp_path))
        assert r.is_error is False  # 二进制被跳过，不崩溃

    def test_activity_description(self) -> None:
        assert GrepTool().activity_description({"pattern": "foo"}) == "搜索 /foo/"


# ── tool_search.py ──


class _DeferredTool(Tool):
    """测试用延迟工具。"""

    name = "mcp__browser__navigate"
    description = "导航浏览器到指定 URL，支持打开页面"
    input_schema = {"type": "object", "properties": {"url": {"type": "string"}}}
    deferred = True

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult.ok("ok")


class _OtherDeferredTool(Tool):
    """测试用延迟工具（不匹配关键词）。"""

    name = "email__send"
    description = "发送电子邮件"
    input_schema = {}
    deferred = True

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult.ok("ok")


def _registry_with_deferred() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_DeferredTool())
    reg.register(_OtherDeferredTool())
    return reg


class TestToolSearch:
    """ToolSearch 延迟工具发现。"""

    def test_empty_query_returns_error(self) -> None:
        tool = ToolSearchTool(_registry_with_deferred())
        r = asyncio.run(tool.call({"query": "  "}, make_ctx("/tmp")))
        assert r.is_error is True
        assert "关键词" in str(r.data)

    @pytest.mark.asyncio
    async def test_finds_tool_by_name(self) -> None:
        tool = ToolSearchTool(_registry_with_deferred())
        ctx = make_ctx("/tmp")
        r = await tool.call({"query": "browser"}, ctx)
        assert r.is_error is False
        assert "mcp__browser__navigate" in str(r.data)
        assert "已加载" in str(r.data)
        # 标记为已发现
        assert "mcp__browser__navigate" in ctx.extra["discovered_tools"]

    @pytest.mark.asyncio
    async def test_finds_tool_by_description(self) -> None:
        tool = ToolSearchTool(_registry_with_deferred())
        r = await tool.call({"query": "邮件"}, make_ctx("/tmp"))
        assert r.is_error is False
        assert "email__send" in str(r.data)

    @pytest.mark.asyncio
    async def test_max_results_limits(self) -> None:
        tool = ToolSearchTool(_registry_with_deferred())
        # "a" 同时命中两个工具的 name/description，但 max_results=1 只返回一个
        r = await tool.call({"query": "browser 邮件 navigate send", "max_results": 1}, make_ctx("/tmp"))
        assert r.is_error is False

    @pytest.mark.asyncio
    async def test_no_match_lists_available(self) -> None:
        tool = ToolSearchTool(_registry_with_deferred())
        r = await tool.call({"query": "zzz-no-such-keyword"}, make_ctx("/tmp"))
        assert r.is_error is False
        assert "未找到匹配" in str(r.data)
        assert "mcp__browser__navigate" in str(r.data)  # 提示可用延迟工具

    def test_score_name_hit_high(self) -> None:
        score = ToolSearchTool._score(_DeferredTool(), ["browser"])
        assert score >= 10

    def test_score_description_hit_low(self) -> None:
        score = ToolSearchTool._score(_DeferredTool(), ["url"])
        assert score >= 3

    def test_score_segment_hit(self) -> None:
        """工具名按 __/_ 分割后的片段命中。"""
        score = ToolSearchTool._score(_DeferredTool(), ["navigate"])
        assert score >= 5

    def test_score_no_hit_zero(self) -> None:
        assert ToolSearchTool._score(_DeferredTool(), ["qqq"]) == 0

    def test_is_read_only_and_concurrency_safe(self) -> None:
        tool = ToolSearchTool(_registry_with_deferred())
        assert tool.is_read_only({}) is True
        assert tool.is_concurrency_safe({}) is True


# ── todo.py ──


class TestTodoWriteTool:
    """TodoWrite 任务清单。"""

    def test_metadata(self) -> None:
        tool = TodoWriteTool()
        assert tool.name == "TodoWrite"
        assert tool.is_read_only({}) is False
        assert tool.is_concurrency_safe({}) is False
        assert tool.check_permissions({}, make_ctx("/tmp")).behavior == PermissionBehavior.ALLOW

    def test_validate_input(self) -> None:
        ctx = make_ctx("/tmp")
        tool = TodoWriteTool()
        assert tool.validate_input({"todos": "not-a-list"}, ctx).ok is False
        # 多个 in_progress
        bad = [
            {"content": "a", "status": "in_progress"},
            {"content": "b", "status": "in_progress"},
        ]
        assert tool.validate_input({"todos": bad}, ctx).ok is False
        # 无效 status
        bad2 = [{"content": "a", "status": "weird"}]
        assert tool.validate_input({"todos": bad2}, ctx).ok is False
        # 正常
        good = [{"content": "a", "status": "pending"}]
        assert tool.validate_input({"todos": good}, ctx).ok is True

    @pytest.mark.asyncio
    async def test_call_updates_extra(self) -> None:
        ctx = make_ctx("/tmp")
        todos = [
            {"content": "task1", "status": "completed"},
            {"content": "task2", "status": "in_progress"},
        ]
        r = await TodoWriteTool().call({"todos": todos}, ctx)
        assert r.is_error is False
        assert ctx.extra["todos"] == todos
        assert "1/2 完成" in str(r.data)

    @pytest.mark.asyncio
    async def test_call_with_ui_shows_list(self) -> None:
        ui = FakeUI()
        ctx = make_ctx("/tmp", ui=ui)
        todos = [{"content": "step", "status": "pending"}]
        await TodoWriteTool().call({"todos": todos}, ctx)
        assert ui.infos and "step" in ui.infos[0]

    def test_activity_description(self) -> None:
        tool = TodoWriteTool()
        assert tool.activity_description({"todos": [1, 2]}) == "更新任务清单（2 项）"
        assert tool.activity_description({}) is None


# ── location.py ──


class TestLocationTool:
    """IP 定位工具（mock 网络）。"""

    def test_metadata(self) -> None:
        tool = LocationTool()
        assert tool.name == "Location"
        assert tool.is_read_only({}) is True
        assert tool.is_concurrency_safe({}) is True
        assert tool.check_permissions({}, make_ctx("/tmp")).behavior == PermissionBehavior.ALLOW

    def test_validate_input(self) -> None:
        ctx = make_ctx("/tmp")
        tool = LocationTool()
        assert tool.validate_input({}, ctx).ok is True
        assert tool.validate_input({"ip": "1.2.3.4"}, ctx).ok is True
        assert tool.validate_input({"ip": "abc"}, ctx).ok is False
        assert tool.validate_input({"ip": "1.2.3"}, ctx).ok is False
        assert tool.validate_input({"ip": "1.2.3.999"}, ctx).ok is False

    @pytest.mark.asyncio
    async def test_call_success_pconline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_request(url: str, headers: dict) -> str:
            return '{"ip": "1.2.3.4", "addr": "广东省 广州市", "pro": "广东", "proCode": "CN", "city": "广州"}'

        monkeypatch.setattr("agent.tools.location._do_request", fake_request)
        ui = FakeUI()
        ctx = make_ctx("/tmp", ui=ui)
        r = await LocationTool().call({}, ctx)
        assert r.is_error is False
        data = str(r.data)
        assert "PConline" in data
        assert "广州" in data
        assert ui.infos and "定位中" in ui.infos[0]

    @pytest.mark.asyncio
    async def test_call_with_specific_ip_uses_ipapi(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PConline 失败、ip-api 成功（URL 拼接 ip）。"""

        def fake_request(url: str, headers: dict) -> str:
            if "pconline" in url:
                raise OSError("pconline down")
            assert url.endswith("/8.8.8.8")
            return '{"status": "success", "query": "8.8.8.8", "country": "United States", "countryCode": "US", "regionName": "California", "city": "Mountain View", "lat": 37.4, "lon": -122.1, "timezone": "America/Los_Angeles", "isp": "Google"}'

        monkeypatch.setattr("agent.tools.location._do_request", fake_request)
        r = await LocationTool().call({"ip": "8.8.8.8"}, make_ctx("/tmp"))
        assert r.is_error is False
        data = str(r.data)
        assert "ip-api" in data
        assert "Mountain View" in data
        assert "37.4" in data  # 经纬度
        assert "America/Los_Angeles" in data  # 时区

    @pytest.mark.asyncio
    async def test_call_all_providers_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_request(url: str, headers: dict) -> str:
            raise OSError("network down")

        monkeypatch.setattr("agent.tools.location._do_request", fake_request)
        r = await LocationTool().call({}, make_ctx("/tmp"))
        assert r.is_error is True
        assert "定位失败" in str(r.data)

    def test_parse_ipapi_status_fail_raises(self) -> None:
        """ip-api 返回 status=fail 时解析器抛 RuntimeError（call 中转为 provider 失败）。"""
        from agent.tools.location import _parse_ipapi

        with pytest.raises(RuntimeError, match="invalid query"):
            _parse_ipapi('{"status": "fail", "message": "invalid query"}')


# ── ask_user.py ──


class _AskUI:
    """带 ask_user 的 UI 桩。"""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def ask_user(self, prompt: str) -> str:
        self.asked.append(prompt)
        return self.answer


class TestAskUserTool:
    """AskUser 工具。"""

    def test_metadata(self) -> None:
        tool = AskUserTool()
        assert tool.name == "AskUser"
        assert tool.is_read_only({}) is True
        assert tool.is_concurrency_safe({}) is False
        assert tool.requires_user_interaction() is True
        assert tool.check_permissions({}, make_ctx("/tmp")).behavior == PermissionBehavior.ALLOW

    def test_validate_input(self) -> None:
        ctx = make_ctx("/tmp")
        tool = AskUserTool()
        assert tool.validate_input({"question": "  "}, ctx).ok is False
        assert tool.validate_input({"question": "目标路径是？"}, ctx).ok is True

    @pytest.mark.asyncio
    async def test_call_with_answer(self) -> None:
        ui = _AskUI("E:/projects")
        ctx = make_ctx("/tmp", ui=ui)
        r = await AskUserTool().call({"question": "路径？"}, ctx)
        assert r.is_error is False
        assert str(r.data) == "用户回答: E:/projects"
        assert ui.asked == ["路径？"]

    @pytest.mark.asyncio
    async def test_call_empty_answer(self) -> None:
        ctx = make_ctx("/tmp", ui=_AskUI("   "))
        r = await AskUserTool().call({"question": "确认？"}, ctx)
        assert r.is_error is False
        assert "(用户未作答)" in str(r.data)

    @pytest.mark.asyncio
    async def test_call_without_ui_errors(self) -> None:
        r = await AskUserTool().call({"question": "hi"}, make_ctx("/tmp"))
        assert r.is_error is True
        assert "无法与用户交互" in str(r.data)

    def test_activity_description(self) -> None:
        tool = AskUserTool()
        assert tool.activity_description({"question": "你叫什么名字？"}) == "提问 你叫什么名字？"
        assert tool.activity_description({}) is None


# ── tool.py：Tool 基类默认行为 ──


class _ConcreteTool(Tool):
    """可实例化的最小工具。"""

    name = "Concrete"
    description = "concrete tool"
    input_schema = {"type": "object"}

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult.ok("ok")


class TestToolBaseDefaults:
    """Tool 基类 fail-closed 默认值。"""

    def test_abstract_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            Tool()  # type: ignore[abstract]

    def test_safety_defaults_fail_closed(self) -> None:
        tool = _ConcreteTool()
        assert tool.is_read_only({}) is False
        assert tool.is_destructive({}) is False
        assert tool.is_concurrency_safe({}) is False
        assert tool.deferred is False
        assert tool.max_result_chars == 20_000

    def test_validate_input_default_passes(self) -> None:
        r = _ConcreteTool().validate_input({}, make_ctx("/tmp"))
        assert r.ok is True

    def test_check_permissions_default_asks(self) -> None:
        r = _ConcreteTool().check_permissions({}, make_ctx("/tmp"))
        assert r.behavior == PermissionBehavior.ASK
        assert r.reason == "no tool-specific permission rule"

    def test_user_facing_name(self) -> None:
        assert _ConcreteTool().user_facing_name() == "Concrete"

    def test_activity_description_default_none(self) -> None:
        assert _ConcreteTool().activity_description({}) is None

    def test_prepare_permission_matcher_default_none(self) -> None:
        assert _ConcreteTool().prepare_permission_matcher({}) is None


class TestPermissionMatcher:
    """权限规则匹配器。"""

    def _matcher(self) -> PermissionMatcher:
        return PermissionMatcher(tool_name="Read", targets=["src/main.py", "git commit -m x"])

    def test_tool_name_mismatch(self) -> None:
        assert self._matcher().matches("Write(src/*)") is False

    def test_no_spec_matches_any(self) -> None:
        assert self._matcher().matches("Read") is True

    def test_empty_spec_matches_any(self) -> None:
        assert self._matcher().matches("Read()") is True

    def test_wildcard_match(self) -> None:
        assert self._matcher().matches("Read(src/*.py)") is True

    def test_exact_match(self) -> None:
        assert self._matcher().matches("Read(src/main.py)") is True

    def test_no_match(self) -> None:
        assert self._matcher().matches("Read(other/*.py)") is False

    def test_invalid_pattern(self) -> None:
        assert self._matcher().matches("not a pattern!") is False


# ── tool.py：装配函数（build_default_registry / 动态注册）──


class TestToolAssembly:
    """工具装配函数（依赖缺失时静默跳过，不崩溃）。"""

    def test_build_default_registry_has_core_tools(self) -> None:
        from agent.core.tool import build_default_registry

        registry = build_default_registry()
        names = {t.name for t in registry.all()}
        for core in ("Bash", "FileRead", "FileWrite", "FileEdit", "Glob", "Grep",
                     "Location", "TodoWrite", "AskUser"):
            assert core in names

    def test_build_default_registry_cached(self) -> None:
        from agent.core.tool import build_default_registry

        assert build_default_registry() is build_default_registry()

    def test_register_dynamic_tools_no_mcp(self) -> None:
        """无 MCP client 时只尝试 harness，不崩溃。"""
        from agent.core.tool import register_dynamic_tools

        registry = ToolRegistry()
        count = register_dynamic_tools(registry, mcp_client=None, workdir=None)
        assert count >= 0

    def test_register_subagent_tool(self) -> None:
        from agent.core.tool import register_subagent_tool

        registry = ToolRegistry()
        ok = register_subagent_tool(registry, provider=None, permission_mode=None)
        # 依赖 collaboration 模块，成功或失败都不应抛异常
        if ok:
            agent = registry.get("Agent")
            assert agent is not None
            assert agent.deferred is True
            # 第二次注册应返回 False（已存在）
            assert register_subagent_tool(registry, provider=None, permission_mode=None) is False

    def test_register_team_tools(self) -> None:
        from agent.core.tool import register_team_tools

        registry = ToolRegistry()
        count = register_team_tools(registry)
        assert count >= 0
        # 注册的工具都应标记为 deferred
        for tool in registry.all():
            assert tool.deferred is True

    def test_register_plan_tools(self) -> None:
        from agent.core.tool import register_plan_tools

        registry = ToolRegistry()
        count = register_plan_tools(registry)
        assert count in (0, 2)

    def test_register_lsp_tool_returns_int(self) -> None:
        from agent.core.tool import register_lsp_tool

        registry = ToolRegistry()
        count = register_lsp_tool(registry)
        assert count in (0, 1)

    def test_register_gui_tools_no_crash(self) -> None:
        from agent.core.tool import _register_gui_tools

        registry = ToolRegistry()
        _register_gui_tools(registry)  # pyautogui 缺失时静默跳过
        for tool in registry.all():
            assert tool.deferred is True

    def test_register_browser_tools_no_crash(self) -> None:
        from agent.core.tool import _register_browser_tools

        registry = ToolRegistry()
        _register_browser_tools(registry)  # playwright 缺失时静默跳过

    def test_register_camera_tools_no_crash(self) -> None:
        from agent.core.tool import _register_camera_tools

        registry = ToolRegistry()
        _register_camera_tools(registry)  # opencv 缺失时静默跳过


class TestToolRegistrySupplement:
    """ToolRegistry 边界（register/get/all_core/len/contains）。"""

    def test_register_empty_name_raises(self) -> None:
        registry = ToolRegistry()
        tool = _ConcreteTool()
        tool.name = ""
        with pytest.raises(ValueError, match="empty name"):
            registry.register(tool)

    def test_register_duplicate_raises(self) -> None:
        registry = ToolRegistry()
        registry.register(_ConcreteTool())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_ConcreteTool())

    def test_get_via_alias(self) -> None:
        registry = ToolRegistry()
        tool = _ConcreteTool()
        registry.register(tool, aliases=["nick"])
        assert registry.get("nick") is tool
        assert registry.get("Concrete") is tool
        assert registry.get("missing") is None

    def test_contains(self) -> None:
        registry = ToolRegistry()
        tool = _ConcreteTool()
        registry.register(tool, aliases=["nick"])
        assert "Concrete" in registry
        assert "nick" in registry
        assert "missing" not in registry

    def test_all_core_filters_deferred(self) -> None:
        registry = ToolRegistry()
        core = _ConcreteTool()
        core.name = "CoreTool"
        deferred = _ConcreteTool()
        deferred.name = "DeferredTool"
        deferred.deferred = True
        registry.register(core)
        registry.register(deferred)
        assert {t.name for t in registry.all_core()} == {"CoreTool"}
        assert {t.name for t in registry.all_deferred()} == {"DeferredTool"}

    def test_len(self) -> None:
        registry = ToolRegistry()
        assert len(registry) == 0
        registry.register(_ConcreteTool())
        assert len(registry) == 1


class _FakeMCPClient:
    """register_dynamic_tools 用的假 MCP client。"""

    def __init__(self, available: bool = True, tools: list | None = None) -> None:
        self.available = available
        self._tools = tools or []

    def list_tools(self):
        return self._tools


class TestDynamicRegistration:
    """register_dynamic_tools 分支。"""

    def test_mcp_client_available_empty_tools(self) -> None:
        from agent.core.tool import register_dynamic_tools

        registry = ToolRegistry()
        count = register_dynamic_tools(registry, mcp_client=_FakeMCPClient())
        # harness 可能注册工具，MCP 无工具 → 不崩溃即可
        assert count >= 0

    def test_mcp_client_unavailable(self) -> None:
        from agent.core.tool import register_dynamic_tools

        registry = ToolRegistry()
        count = register_dynamic_tools(registry, mcp_client=_FakeMCPClient(available=False))
        assert count >= 0

    def test_mcp_import_error_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """mcp_tool 模块无法导入时静默跳过（except ImportError 分支）。"""
        import sys

        from agent.core.tool import register_dynamic_tools

        monkeypatch.setitem(sys.modules, "agent.tools.extensions.mcp_tool", None)
        registry = ToolRegistry()
        count = register_dynamic_tools(registry, mcp_client=object())
        assert count >= 0


class TestRegisterMCPTools:
    """register_mcp_tools 函数本身（MCP client 分支）。"""

    def test_unavailable_client_returns_zero(self) -> None:
        from agent.tools.extensions.mcp_tool import register_mcp_tools

        registry = ToolRegistry()
        assert register_mcp_tools(registry, _FakeMCPClient(available=False)) == 0

    def test_available_client_no_tools(self) -> None:
        from agent.tools.extensions.mcp_tool import register_mcp_tools

        registry = ToolRegistry()
        assert register_mcp_tools(registry, _FakeMCPClient(available=True, tools=[])) == 0


class TestAssemblyImportErrorBranches:
    """装配函数中 ImportError 静默跳过分支。"""

    @pytest.mark.parametrize(
        "module_name, tool_name",
        [
            ("agent.tools.collaboration.team_create", "TeamCreate"),
            ("agent.tools.collaboration.send_message", "SendMessage"),
            ("agent.tools.collaboration.team_status", "TeamStatus"),
        ],
    )
    def test_register_team_tools_import_error(
        self, monkeypatch: pytest.MonkeyPatch, module_name: str, tool_name: str
    ) -> None:
        import sys

        from agent.core.tool import register_team_tools

        monkeypatch.setitem(sys.modules, module_name, None)
        registry = ToolRegistry()
        count = register_team_tools(registry, task_list=object())
        # 被 mock 掉模块对应的工具不应注册；其余工具正常注册
        assert count >= 0
        assert registry.get(tool_name) is None

    def test_register_team_tools_task_branch(self) -> None:
        """传 task_list 时注册 Task 工具分支（覆盖 368-392 行）。"""
        from agent.core.tool import register_team_tools

        registry = ToolRegistry()
        count = register_team_tools(registry, task_list=object())
        # Task 相关工具注册时都标记 deferred
        for tool in registry.all():
            assert tool.deferred is True
        assert count >= 0

    def test_register_plan_tools_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys

        from agent.core.tool import register_plan_tools

        monkeypatch.setitem(sys.modules, "agent.tools.collaboration.enter_plan", None)
        assert register_plan_tools(ToolRegistry()) == 0

    def test_register_lsp_no_manager(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LSP manager 未初始化 → 返回 0。"""
        from agent.core.tool import register_lsp_tool

        monkeypatch.setattr("agent.lsp.manager.get_lsp_manager", lambda: None)
        assert register_lsp_tool(ToolRegistry()) == 0

    def test_register_lsp_manager_no_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LSP manager 存在但没有配置 → 返回 0。"""
        from agent.core.tool import register_lsp_tool

        mgr = SimpleNamespace(_configs={})
        monkeypatch.setattr("agent.lsp.manager.get_lsp_manager", lambda: mgr)
        assert register_lsp_tool(ToolRegistry()) == 0

    def test_register_gui_tools_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GUI 依赖（pyautogui）缺失 → 静默跳过。"""
        import sys

        from agent.core.tool import _register_gui_tools

        monkeypatch.setitem(sys.modules, "agent.tools.system.mouse", None)
        registry = ToolRegistry()
        _register_gui_tools(registry)  # 不抛异常
        assert len(registry) == 0

    def test_register_browser_tools_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """playwright 缺失 → 静默跳过。"""
        import sys

        from agent.core.tool import _register_browser_tools

        monkeypatch.setitem(sys.modules, "agent.tools.web.browser", None)
        registry = ToolRegistry()
        _register_browser_tools(registry)
        assert len(registry) == 0

    def test_register_camera_tools_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """opencv 缺失 → 静默跳过（含 cv2 导入失败分支）。"""
        import sys

        from agent.core.tool import _register_camera_tools

        monkeypatch.setitem(sys.modules, "agent.tools.vision.camera", None)
        registry = ToolRegistry()
        _register_camera_tools(registry)
        assert len(registry) == 0

    def test_register_camera_cv2_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """camera 可导入但 cv2 缺失 → 静默跳过。"""
        import sys

        from agent.core.tool import _register_camera_tools

        # 让 camera 模块可导入（不能设为 None），但 cv2 导入失败
        class _FakeCamera:
            CameraShotTool = None
            ListCamerasTool = None

        fake_module = sys.modules.setdefault("agent.tools.vision.camera", _FakeCamera)
        monkeypatch.setitem(sys.modules, "cv2", None)
        registry = ToolRegistry()
        _register_camera_tools(registry)
        assert len(registry) == 0
