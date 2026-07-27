"""工具错误自愈单元测试。

覆盖分类器、策略、RecoveryExecutor 的核心路径。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.core.error_recovery import (
    ClassifiedError,
    RecoveryPolicy,
    ToolErrorCategory,
    ToolErrorClassifier,
    ToolRecoveryExecutor,
    DEFAULT_POLICIES,
)
from agent.core.result import ToolResult


class FakeUI:
    """用于测试的简单 UI 桩。"""

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.questions: list[str] = []
        self.answers: list[str] = []

    def info(self, msg: str) -> None:
        self.infos.append(msg)

    def warn(self, msg: str) -> None:
        self.infos.append(f"WARN: {msg}")

    def ask_user(self, question: str) -> str:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else "n"


class FakeContext:
    """用于测试的最小 ToolContext 桩。"""

    def __init__(self, ui: FakeUI | None = None) -> None:
        self.ui = ui
        self.abort_event = type("AbortEvent", (), {"is_set": lambda self: False})()
        self.messages = []
        self.workdir = "."


class TestToolErrorClassifier:
    def test_dependency_missing(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("Bash", ToolResult.error("command not found: foobar"))
        assert err.category == ToolErrorCategory.DEPENDENCY_MISSING
        assert err.recoverable is True

    def test_not_found(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("FileRead", ToolResult.error("No such file or directory: /tmp/x.txt"))
        assert err.category == ToolErrorCategory.NOT_FOUND
        assert err.recoverable is True

    def test_network_transient(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("DevServer", ToolResult.error("connection reset by peer"))
        assert err.category == ToolErrorCategory.NETWORK_TRANSIENT

    def test_rate_limit(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("WebFetch", ToolResult.error("429 Too Many Requests"))
        assert err.category == ToolErrorCategory.RATE_LIMIT

    def test_auth_missing(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("EmailTool", ToolResult.error("API key is missing"))
        assert err.category == ToolErrorCategory.AUTH_MISSING
        assert err.recoverable is False

    def test_permission_denied(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("FileWrite", ToolResult.error("Permission denied: /etc/x"))
        assert err.category == ToolErrorCategory.PERMISSION_DENIED

    def test_timeout_from_exception(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("Bash", None, asyncio.TimeoutError("timed out"))
        assert err.category == ToolErrorCategory.TIMEOUT


class TestToolRecoveryExecutor:
    @pytest.mark.asyncio
    async def test_success_no_recovery(self) -> None:
        executor = ToolRecoveryExecutor(global_enabled=True)
        ui = FakeUI()
        ctx = FakeContext(ui=ui)

        async def call_fn(args, ctx):
            return ToolResult.ok("ok")

        result = await executor.execute("Echo", call_fn, {"x": 1}, ctx)
        assert result.final_result.is_error is False
        assert result.final_result.data == "ok"

    @pytest.mark.asyncio
    async def test_retry_then_success(self) -> None:
        executor = ToolRecoveryExecutor(global_enabled=True)
        ui = FakeUI()
        ctx = FakeContext(ui=ui)
        attempts = 0

        async def call_fn(args, ctx):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return ToolResult.error("timeout connecting to server")
            return ToolResult.ok("ok")

        result = await executor.execute("Bash", call_fn, {}, ctx)
        assert result.final_result.is_error is False
        assert result.final_result.data == "ok"
        assert attempts == 3
        assert len(ui.infos) > 0

    @pytest.mark.asyncio
    async def test_timeout_auto_fix(self) -> None:
        executor = ToolRecoveryExecutor(global_enabled=True)
        ui = FakeUI()
        ctx = FakeContext(ui=ui)
        seen_args = []

        async def call_fn(args, ctx):
            seen_args.append(args.copy())
            if args.get("timeout", 120) < 200:
                return ToolResult.error("timeout")
            return ToolResult.ok("ok")

        result = await executor.execute("Bash", call_fn, {"command": "sleep 5", "timeout": 60}, ctx)
        assert result.final_result.is_error is False
        # 第一次失败后 auto_fix 把 timeout 从 60 提到 120，第二次成功
        assert any(a.get("timeout", 0) > 60 for a in seen_args)

    @pytest.mark.asyncio
    async def test_not_found_auto_create_parent(self, tmp_path: Path) -> None:
        executor = ToolRecoveryExecutor(global_enabled=True)
        ui = FakeUI()
        ctx = FakeContext(ui=ui)
        target = tmp_path / "nested" / "dir" / "file.txt"

        async def call_fn(args, ctx):
            path = Path(args["file_path"])
            if not path.parent.exists():
                return ToolResult.error(f"No such file or directory: {path}")
            return ToolResult.ok("parent exists")

        result = await executor.execute("FileWrite", call_fn, {"file_path": str(target)}, ctx)
        assert result.final_result.is_error is False
        assert target.parent.exists()

    @pytest.mark.asyncio
    async def test_disabled_no_recovery(self) -> None:
        executor = ToolRecoveryExecutor(global_enabled=False)
        ui = FakeUI()
        ctx = FakeContext(ui=ui)
        attempts = 0

        async def call_fn(args, ctx):
            nonlocal attempts
            attempts += 1
            return ToolResult.error("timeout")

        result = await executor.execute("Bash", call_fn, {}, ctx)
        assert result.final_result.is_error is True
        assert attempts == 1
