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
    RecoveryTelemetry,
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


# ── 补充：RecoveryTelemetry / 分类器边界 / 自动修复 / 询问用户 ──


class TestRecoveryTelemetry:
    """自愈遥测。"""

    def test_record_and_summary(self) -> None:
        t = RecoveryTelemetry()
        t.clear()
        t.record("Bash", ToolErrorCategory.NETWORK_TRANSIENT, True, 2, True, "重试后成功")
        t.record("FileRead", ToolErrorCategory.NOT_FOUND, True, 1, False, "未能自动恢复")
        summary = t.get_summary()
        assert summary["total_incidents"] == 2
        assert summary["resolved"] == 1
        assert summary["unresolved"] == 1
        assert summary["by_category"] == {"network_transient": 1, "not_found": 1}

    def test_get_recent_returns_latest(self) -> None:
        t = RecoveryTelemetry()
        t.clear()
        for i in range(5):
            t.record("Bash", ToolErrorCategory.TIMEOUT, True, 1, True)
        recent = t.get_recent(2)
        assert len(recent) == 2
        assert recent[-1].attempts == 1

    def test_top_category(self) -> None:
        t = RecoveryTelemetry()
        t.clear()
        assert t.top_category() is None
        t.record("Bash", ToolErrorCategory.TIMEOUT, True, 1, False)
        t.record("Bash", ToolErrorCategory.TIMEOUT, True, 1, False)
        t.record("Bash", ToolErrorCategory.NOT_FOUND, True, 1, False)
        assert t.top_category() == "timeout"

    def test_max_history_capped(self) -> None:
        t = RecoveryTelemetry()
        t.clear()
        for i in range(60):
            t.record("Bash", ToolErrorCategory.UNKNOWN, False, 0, False)
        assert len(t._incidents) <= 50

    def test_clear(self) -> None:
        t = RecoveryTelemetry()
        t.clear()
        t.record("Bash", ToolErrorCategory.TIMEOUT, True, 1, True)
        t.clear()
        assert t.get_summary()["total_incidents"] == 0


class TestClassifierMore:
    """分类器边界补充。"""

    def test_unknown_error(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("Bash", ToolResult.error("weird random message 123"))
        assert err.category == ToolErrorCategory.UNKNOWN
        assert err.recoverable is False

    def test_config_invalid(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("Bash", ToolResult.error("invalid config: bad key"))
        assert err.category == ToolErrorCategory.CONFIG_INVALID

    def test_chinese_not_found(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("FileRead", ToolResult.error("文件不存在: /tmp/x"))
        assert err.category == ToolErrorCategory.NOT_FOUND

    def test_chinese_dependency_missing(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("Bash", ToolResult.error("依赖缺失: ffmpeg 未安装"))
        assert err.category == ToolErrorCategory.DEPENDENCY_MISSING

    def test_chinese_permission_denied(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("FileWrite", ToolResult.error("无权访问: /etc/passwd"))
        assert err.category == ToolErrorCategory.PERMISSION_DENIED

    def test_rate_limit_via_text(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("WebFetch", ToolResult.error("Too Many Requests (429)"))
        assert err.category == ToolErrorCategory.RATE_LIMIT

    def test_timeout_via_exception_priority(self) -> None:
        """异常为 TimeoutError 时优先判超时（即使文本含其他关键词）。"""
        c = ToolErrorClassifier()
        err = c.classify("Bash", ToolResult.error("permission denied"), asyncio.TimeoutError("slow"))
        assert err.category == ToolErrorCategory.TIMEOUT

    def test_no_result_no_exception_is_unknown(self) -> None:
        c = ToolErrorClassifier()
        err = c.classify("Bash", None)
        assert err.category == ToolErrorCategory.UNKNOWN

    def test_default_policies_cover_all_categories(self) -> None:
        for category in ToolErrorCategory:
            if category == ToolErrorCategory.OK:
                continue
            assert category in DEFAULT_POLICIES, f"缺少策略: {category}"

    def test_policy_fields(self) -> None:
        p = DEFAULT_POLICIES[ToolErrorCategory.TIMEOUT]
        assert p.max_retries == 2
        assert p.auto_fix is True
        assert p.ask_user_on_fail is True


class TestAutoFix:
    """自动修复逻辑（直接调用 _try_auto_fix）。"""

    def _executor(self) -> ToolRecoveryExecutor:
        return ToolRecoveryExecutor(global_enabled=True)

    def test_timeout_increases(self) -> None:
        fixed, new_args, msg = self._executor()._try_auto_fix(
            "Bash",
            ClassifiedError(category=ToolErrorCategory.TIMEOUT, reason="超时", recoverable=True),
            {"command": "sleep 5", "timeout": 60},
            FakeContext(),
        )
        assert fixed is True
        assert new_args["timeout"] == 120  # max(60+60, 60*2)

    def test_not_found_creates_parent_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c.txt"
        fixed, new_args, msg = self._executor()._try_auto_fix(
            "FileWrite",
            ClassifiedError(category=ToolErrorCategory.NOT_FOUND, reason="不存在", recoverable=True),
            {"file_path": str(target)},
            FakeContext(),
        )
        assert fixed is True
        assert target.parent.exists()

    def test_dependency_missing_gives_suggestion(self) -> None:
        fixed, new_args, msg = self._executor()._try_auto_fix(
            "Bash",
            ClassifiedError(category=ToolErrorCategory.DEPENDENCY_MISSING, reason="缺依赖", recoverable=True),
            {"command": "ffmpeg"},
            FakeContext(),
        )
        assert fixed is False
        assert new_args is None

    def test_no_strategy(self) -> None:
        fixed, new_args, msg = self._executor()._try_auto_fix(
            "Bash",
            ClassifiedError(category=ToolErrorCategory.UNKNOWN, reason="未知", recoverable=False),
            {},
            FakeContext(),
        )
        assert fixed is False
        assert "暂无自动修复策略" in msg


class TestRecoveryExecutorMore:
    """执行器补充场景。"""

    @staticmethod
    def _fast_policies() -> dict:
        """深拷贝策略表并去掉退避等待（测试提速，保留原始重试次数）。

        注意必须 deepcopy：dict(DEFAULT_POLICIES) 是浅拷贝，改 backoff 会污染
        全局策略表（policy 对象共享），导致后续测试拿到被改过的策略。
        """
        import copy

        policies = copy.deepcopy(DEFAULT_POLICIES)
        for p in policies.values():
            p.backoff_base_seconds = 0.0
            p.backoff_max_seconds = 0.0
        return policies

    @staticmethod
    def _no_retry_policies() -> dict:
        """全部不重试（测询问用户分支时用，避免重试循环吞掉场景）。"""
        policies = TestRecoveryExecutorMore._fast_policies()
        for p in policies.values():
            p.max_retries = 0
        return policies

    @pytest.mark.asyncio
    async def test_auth_missing_not_asked(self) -> None:
        """认证类错误不询问用户，直接失败。"""
        executor = ToolRecoveryExecutor(
            global_enabled=True, policies=self._fast_policies()
        )
        ui = FakeUI()
        ctx = FakeContext(ui=ui)

        async def call_fn(args, ctx):
            return ToolResult.error("API key is missing")

        result = await executor.execute("EmailTool", call_fn, {}, ctx)
        assert result.final_result.is_error is True
        assert result.asked_user is False  # 认证类不询问
        assert ui.questions == []

    @pytest.mark.asyncio
    async def test_ask_user_yes_retries_and_succeeds(self) -> None:
        attempts = 0

        async def call_fn(args, ctx):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return ToolResult.error("connection reset by peer")
            return ToolResult.ok("ok")

        ui = FakeUI()
        ui.answers = ["y"]  # 用户选择重试
        executor = ToolRecoveryExecutor(global_enabled=True, policies=self._no_retry_policies())
        result = await executor.execute("WebFetch", call_fn, {}, FakeContext(ui=ui))
        assert result.final_result.is_error is False
        assert result.asked_user is True
        assert result.user_answer == "y"

    @pytest.mark.asyncio
    async def test_ask_user_no_gives_up(self) -> None:
        async def call_fn(args, ctx):
            return ToolResult.error("connection reset by peer")

        ui = FakeUI()
        ui.answers = ["n"]
        executor = ToolRecoveryExecutor(global_enabled=True, policies=self._no_retry_policies())
        result = await executor.execute("WebFetch", call_fn, {}, FakeContext(ui=ui))
        assert result.final_result.is_error is True
        assert result.asked_user is True
        assert result.user_answer == "n"

    @pytest.mark.asyncio
    async def test_no_ui_skips_asking(self) -> None:
        async def call_fn(args, ctx):
            return ToolResult.error("connection reset by peer")

        executor = ToolRecoveryExecutor(global_enabled=True, policies=self._no_retry_policies())
        result = await executor.execute("WebFetch", call_fn, {}, FakeContext(ui=None))
        assert result.final_result.is_error is True
        assert result.asked_user is False

    @pytest.mark.asyncio
    async def test_readonly_gets_extra_retry(self) -> None:
        """只读工具多一次重试。

        "timeout" 文本分类为 TIMEOUT（max_retries=2），只读 +1 → 3 次重试，
        加上初始调用共 4 次。
        """
        attempts = 0

        async def call_fn(args, ctx):
            nonlocal attempts
            attempts += 1
            return ToolResult.error("timeout")

        executor = ToolRecoveryExecutor(global_enabled=True, policies=self._fast_policies())
        result = await executor.execute(
            "FileRead", call_fn, {}, FakeContext(ui=None), tool_is_read_only=True
        )
        assert result.final_result.is_error is True
        assert attempts == 4

    @pytest.mark.asyncio
    async def test_call_fn_exception_classified(self) -> None:
        """call_fn 抛异常时按异常分类并返回错误结果。"""

        async def call_fn(args, ctx):
            raise asyncio.TimeoutError("hung")

        executor = ToolRecoveryExecutor(global_enabled=True, policies=self._fast_policies())
        result = await executor.execute("Bash", call_fn, {}, FakeContext(ui=None))
        assert result.final_result.is_error is True
        assert result.original_error.category == ToolErrorCategory.TIMEOUT

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self) -> None:
        """CancelledError 必须向上传播（不吞掉取消）。"""

        async def call_fn(args, ctx):
            raise asyncio.CancelledError()

        executor = ToolRecoveryExecutor(global_enabled=True)
        with pytest.raises(asyncio.CancelledError):
            await executor.execute("Bash", call_fn, {}, FakeContext(ui=None))

    def test_is_enabled(self) -> None:
        assert ToolRecoveryExecutor(global_enabled=True).is_enabled() is True
        assert ToolRecoveryExecutor(global_enabled=False).is_enabled() is False

    @pytest.mark.asyncio
    async def test_disabled_records_telemetry(self) -> None:
        RecoveryTelemetry().clear()
        async def call_fn(args, ctx):
            return ToolResult.error("timeout")

        executor = ToolRecoveryExecutor(global_enabled=False)
        await executor.execute("Bash", call_fn, {}, FakeContext(ui=None))
        summary = RecoveryTelemetry().get_summary()
        assert summary["total_incidents"] == 1
        RecoveryTelemetry().clear()
