"""ToolOrchestrator 工具编排器单元测试。

覆盖核心调度逻辑：
- 正常执行（单个 / 多个 / 顺序对齐 / 结果序列化 str/dict/None）
- 权限路径（ALLOW / DENY / ASK 询问 y/n/a / 无 UI fail-closed）
- 未知工具、工具抛异常、工具返回错误结果
- 并发安全分组（safe 并行 / unsafe 串行）与 abort_event 中断
- hooks（tool_before 拒绝 / 修改输入 / 钩子异常回退）
- recovery_executor 自愈（重试成功 / 重试耗尽 / 自愈关闭）
- 超长结果截断落盘、file_changed 钩子、真实 PermissionChecker 集成

测试策略：
- FakeChecker 精确控制权限三态结果；FakeTool 可配置返回结果 / 抛异常 /
  并发安全 / 只读 / max_result_chars，并监控并发度。
- hooks 相关用例通过 monkeypatch `agent.core.hooks.get_hooks` 隔离全局单例，
  避免影响其它测试。

@author aceFelix
"""

from __future__ import annotations

import asyncio

import pytest

from agent.core.context import ToolContext
from agent.core.error_recovery import (
    RecoveryPolicy,
    ToolErrorCategory,
    ToolRecoveryExecutor,
)
from agent.core.hooks import HookEvent, HookRegistry, HookResult
from agent.core.message import ToolUseContent
from agent.core.orchestrator import ToolOrchestrator
from agent.core.result import PermissionBehavior, PermissionResult, ToolResult
from agent.core.tool import Tool, ToolRegistry


class FakeUI:
    """记录 UI 回调的桩（ask_user 默认返回 'n'）。"""

    def __init__(self) -> None:
        self.warns: list[str] = []
        self.infos: list[str] = []
        self.questions: list[str] = []
        self.answers: list[str] = ["n"]
        self.tool_use_calls: list[tuple] = []
        self.tool_result_calls: list[tuple] = []

    def warn(self, text: str) -> None:
        self.warns.append(text)

    def info(self, text: str) -> None:
        self.infos.append(text)

    def error(self, text: str) -> None:
        pass

    def assistant_text(self, text: str) -> None:
        pass

    def assistant_thinking(self, text: str) -> None:
        pass

    def tool_use(self, tool_name, tool_input, tool_use_id) -> None:
        self.tool_use_calls.append((tool_name, tool_input, tool_use_id))

    def tool_result(self, tool_name, tool_use_id, content, *, is_error=False) -> None:
        self.tool_result_calls.append((tool_name, tool_use_id, content, is_error))

    def ask_user(self, prompt: str) -> str:
        self.questions.append(prompt)
        return self.answers.pop(0) if self.answers else "n"


class FakeChecker:
    """可控的权限检查器：check() 恒返回预设结果。"""

    def __init__(self, result: PermissionResult) -> None:
        self.result = result

    def check(self, tool: Tool, args: dict, ctx: ToolContext) -> PermissionResult:
        return self.result


class _ConcurrencyCounter:
    """共享并发监控计数器。"""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    def enter(self) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)

    def exit(self) -> None:
        self.active -= 1


class FakeTool(Tool):
    """可配置的工具桩。

    - results: 结果队列，call 依次弹出（最后一个重复使用）
    - error: 非 None 时 call 直接抛该异常
    - concurrency_safe / read_only / max_result_chars: 对应安全属性
    - counter: 共享并发计数器（验证并行/串行）
    """

    description = "测试工具"
    input_schema = {"type": "object", "properties": {}}
    max_result_chars: int = 20_000

    def __init__(
        self,
        *,
        name: str = "fake_tool",
        results: list[ToolResult] | None = None,
        error: BaseException | None = None,
        concurrency_safe: bool = False,
        read_only: bool = False,
        max_result_chars: int = 20_000,
        delay: float = 0.0,
        counter: _ConcurrencyCounter | None = None,
    ) -> None:
        self.name = name
        self._results = results if results is not None else [ToolResult.ok("默认结果")]
        self._error = error
        self._concurrency_safe = concurrency_safe
        self._read_only = read_only
        self.max_result_chars = max_result_chars
        self.delay = delay
        self.counter = counter
        self.calls: list[tuple[dict, ToolContext]] = []

    async def call(self, args: dict, ctx: ToolContext) -> ToolResult:
        """按队列返回结果，并监控并发度。"""
        self.calls.append((args, ctx))
        if self.counter:
            self.counter.enter()
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self._error is not None:
                raise self._error
            result = self._results[0]
            if len(self._results) > 1:
                self._results.pop(0)
            return result
        finally:
            if self.counter:
                self.counter.exit()

    def is_concurrency_safe(self, args: dict) -> bool:
        return self._concurrency_safe

    def is_read_only(self, args: dict) -> bool:
        return self._read_only


@pytest.fixture
def registry():
    """空工具注册表。"""
    return ToolRegistry()


def make_ctx(ui=None, workdir: str = ".", abort: bool = False) -> ToolContext:
    """构造 ToolContext，可选预置 abort。"""
    ctx = ToolContext(workdir=workdir, messages=[], ui=ui)
    if abort:
        ctx.abort_event.set()
    return ctx


def make_tu(tool_id: str = "t1", name: str = "fake_tool", input_: dict | None = None) -> ToolUseContent:
    """快捷构造 ToolUseContent。"""
    return ToolUseContent(id=tool_id, name=name, input=input_ if input_ is not None else {})


def _build(reg, tool, result: PermissionResult = PermissionResult.allow(), **kwargs) -> ToolOrchestrator:
    """注册工具并构造 orchestrator。"""
    reg.register(tool)
    return ToolOrchestrator(reg, FakeChecker(result), **kwargs)


def make_recovery(*, enabled: bool = True) -> ToolRecoveryExecutor:
    """构造不 sleep 的 recovery（backoff=0），便于测试自愈路径。"""
    policy = RecoveryPolicy(
        category=ToolErrorCategory.NETWORK_TRANSIENT,
        max_retries=1,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
    )
    return ToolRecoveryExecutor(
        policies={ToolErrorCategory.NETWORK_TRANSIENT: policy},
        global_enabled=enabled,
    )


# ---------------------------------------------------------------------------
# 正常执行
# ---------------------------------------------------------------------------


class TestExecuteNormal:
    """正常执行路径。"""

    async def test_empty_tool_uses_returns_empty(self, registry):
        """空 tool_use 列表直接返回空列表。"""
        orch = ToolOrchestrator(registry, FakeChecker(PermissionResult.allow()))
        assert await orch.execute_calls([], make_ctx()) == []

    async def test_single_tool_success(self, registry):
        """单个工具成功执行，结果按 tool_use_id 对齐。"""
        tool = FakeTool(results=[ToolResult.ok("data")])
        orch = _build(registry, tool)
        ui = FakeUI()
        ctx = make_ctx(ui=ui)

        results = await orch.execute_calls([make_tu()], ctx)

        assert len(results) == 1
        r = results[0]
        assert r.tool_use_id == "t1"
        assert r.content == "data"
        assert r.is_error is False
        # UI 收到 tool_use / tool_result 通知
        assert ui.tool_use_calls and ui.tool_use_calls[0][0] == "fake_tool"
        assert ui.tool_result_calls and ui.tool_result_calls[0][1] == "t1"

    async def test_multiple_preserves_input_order(self, registry):
        """多个工具结果按输入顺序对齐（含未知工具与错误结果混排）。"""
        ok_tool = FakeTool(name="ok_tool", results=[ToolResult.ok("good")])
        err_tool = FakeTool(name="err_tool", results=[ToolResult.error("boom")])
        registry.register(ok_tool)
        registry.register(err_tool)
        orch = ToolOrchestrator(registry, FakeChecker(PermissionResult.allow()))

        tus = [
            make_tu("1", "ok_tool"),
            make_tu("2", "no_such_tool"),  # 未知工具
            make_tu("3", "err_tool"),      # 错误结果
        ]
        results = await orch.execute_calls(tus, make_ctx())

        assert [r.tool_use_id for r in results] == ["1", "2", "3"]
        assert results[0].is_error is False and results[0].content == "good"
        assert results[1].is_error and "未知工具" in results[1].content
        assert results[2].is_error and "boom" in results[2].content

    async def test_result_none_serialized(self, registry):
        """ToolResult.ok(None) → 序列化为 '(无输出)'。"""
        tool = FakeTool(results=[ToolResult.ok(None)])
        orch = _build(registry, tool)
        results = await orch.execute_calls([make_tu()], make_ctx())
        assert results[0].content == "(无输出)"
        assert results[0].is_error is False

    async def test_result_dict_serialized(self, registry):
        """ToolResult.ok(dict) → JSON 序列化。"""
        tool = FakeTool(results=[ToolResult.ok({"a": 1, "b": [2, 3]})])
        orch = _build(registry, tool)
        results = await orch.execute_calls([make_tu()], make_ctx())
        assert '"a": 1' in results[0].content
        assert '"b"' in results[0].content

    async def test_result_unserializable_input_format(self, registry):
        """_format_ask 中 input 不可 JSON 序列化时回退 str()。"""
        checker = FakeChecker(PermissionResult.ask("确认一下"))
        ui = FakeUI()
        ui.answers = ["y"]
        tool = FakeTool(results=[ToolResult.ok("done")])
        registry.register(tool)
        orch = ToolOrchestrator(registry, checker)

        # input 含不可序列化对象 → _format_ask 走 except 分支
        tu = make_tu(input_={"weird": object()})
        results = await orch.execute_calls([tu], make_ctx(ui=ui))

        assert not results[0].is_error
        assert ui.questions  # 确实走了 ASK 询问流程


# ---------------------------------------------------------------------------
# 权限路径
# ---------------------------------------------------------------------------


class TestPermissionPaths:
    """权限校验三态与 ASK 询问。"""

    async def test_permission_denied(self, registry):
        """DENY：返回权限拒绝结果，工具不执行，UI 收到警告。"""
        tool = FakeTool()
        orch = _build(registry, tool, PermissionResult.deny("规则命中: Bash(rm)"))
        ui = FakeUI()

        results = await orch.execute_calls([make_tu()], make_ctx(ui=ui))

        assert results[0].is_error
        assert "权限拒绝" in results[0].content
        assert "规则命中" in results[0].content
        assert tool.calls == []  # 工具未执行
        assert ui.warns and "拒绝执行" in ui.warns[0]

    async def test_ask_yes_allows(self, registry):
        """ASK + 用户回答 y → 放行执行。"""
        tool = FakeTool()
        orch = _build(registry, tool, PermissionResult.ask("可以吗"))
        ui = FakeUI()
        ui.answers = ["y"]

        results = await orch.execute_calls([make_tu()], make_ctx(ui=ui))

        assert not results[0].is_error
        assert len(tool.calls) == 1

    async def test_ask_always_allows_session(self, registry):
        """ASK + 用户回答 a（总是）→ 会话内放行。"""
        tool = FakeTool()
        orch = _build(registry, tool, PermissionResult.ask("可以吗"))
        ui = FakeUI()
        ui.answers = ["a"]

        results = await orch.execute_calls([make_tu()], make_ctx(ui=ui))

        assert not results[0].is_error
        assert len(tool.calls) == 1

    async def test_ask_no_denies(self, registry):
        """ASK + 用户回答 n → 拒绝执行。"""
        tool = FakeTool()
        orch = _build(registry, tool, PermissionResult.ask("可以吗"))
        ui = FakeUI()
        ui.answers = ["n"]

        results = await orch.execute_calls([make_tu()], make_ctx(ui=ui))

        assert results[0].is_error
        assert "用户拒绝" in results[0].content
        assert tool.calls == []

    async def test_ask_no_ui_fail_closed(self, registry):
        """ASK 但无 UI → fail-closed 拒绝。"""
        tool = FakeTool()
        orch = _build(registry, tool, PermissionResult.ask("可以吗"))

        results = await orch.execute_calls([make_tu()], make_ctx(ui=None))

        assert results[0].is_error
        assert "无 UI" in results[0].content
        assert tool.calls == []


# ---------------------------------------------------------------------------
# 异常与未知工具
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """工具异常与未知工具。"""

    async def test_tool_exception_without_recovery_propagates(self, registry):
        """无 recovery 时工具抛异常 → 封装为 is_error 结果回传 LLM。

        曾经 orchestrator.py 未导入 ToolResult，此分支会 NameError 向外传播；
        已修复（补全 import），现在应返回封装后的错误结果。
        """
        tool = FakeTool(error=ValueError("boom"))
        orch = _build(registry, tool)

        results = await orch.execute_calls([make_tu()], make_ctx())

        assert results[0].is_error
        assert "工具执行异常" in results[0].content

    async def test_tool_exception_with_recovery_classified(self, registry):
        """配置 recovery 时工具抛异常 → 被分类为错误结果（正常封装路径）。"""
        tool = FakeTool(error=ValueError("connection reset by peer"))
        orch = _build(registry, tool, recovery_executor=make_recovery())

        results = await orch.execute_calls([make_tu()], make_ctx())

        assert results[0].is_error
        assert "工具执行异常" in results[0].content

    async def test_tool_returns_error_result(self, registry):
        """工具返回 ToolResult.error → is_error 标记。"""
        tool = FakeTool(results=[ToolResult.error("文件不存在")])
        orch = _build(registry, tool)

        results = await orch.execute_calls([make_tu()], make_ctx())

        assert results[0].is_error
        assert results[0].content == "文件不存在"

    async def test_tool_raises_cancelled_error_propagates(self, registry):
        """工具抛 asyncio.CancelledError → 原样传播（不封装为错误）。"""
        tool = FakeTool(error=asyncio.CancelledError())
        orch = _build(registry, tool)

        with pytest.raises(asyncio.CancelledError):
            await orch.execute_calls([make_tu()], make_ctx())

    async def test_unknown_tool_warns(self, registry):
        """未知工具：错误结果 + UI 警告。"""
        orch = ToolOrchestrator(registry, FakeChecker(PermissionResult.allow()))
        ui = FakeUI()

        results = await orch.execute_calls([make_tu(name="ghost_tool")], make_ctx(ui=ui))

        assert results[0].is_error
        assert "未知工具 'ghost_tool'" in results[0].content
        assert ui.warns and "未知工具" in ui.warns[0]


# ---------------------------------------------------------------------------
# 并发与中断
# ---------------------------------------------------------------------------


class TestConcurrency:
    """并发安全分组与 abort_event 中断。"""

    async def test_concurrency_safe_parallel(self, registry):
        """并发安全工具并行执行。"""
        counter = _ConcurrencyCounter()
        t1 = FakeTool(name="p1", concurrency_safe=True, delay=0.05, counter=counter)
        t2 = FakeTool(name="p2", concurrency_safe=True, delay=0.05, counter=counter)
        registry.register(t1)
        registry.register(t2)
        orch = ToolOrchestrator(registry, FakeChecker(PermissionResult.allow()))

        results = await orch.execute_calls(
            [make_tu("a", "p1"), make_tu("b", "p2")], make_ctx()
        )

        assert {r.tool_use_id for r in results} == {"a", "b"}
        assert counter.max_active >= 2  # 并行

    async def test_unsafe_serial(self, registry):
        """非并发安全工具串行执行。"""
        counter = _ConcurrencyCounter()
        t1 = FakeTool(name="s1", concurrency_safe=False, delay=0.03, counter=counter)
        t2 = FakeTool(name="s2", concurrency_safe=False, delay=0.03, counter=counter)
        registry.register(t1)
        registry.register(t2)
        orch = ToolOrchestrator(registry, FakeChecker(PermissionResult.allow()))

        await orch.execute_calls([make_tu("a", "s1"), make_tu("b", "s2")], make_ctx())

        assert counter.max_active == 1  # 串行

    async def test_abort_event_cancels_pending(self, registry):
        """abort_event 置位 → 待执行工具标记为已取消。"""
        safe_tool = FakeTool(name="safe_t", concurrency_safe=True)
        unsafe_tool = FakeTool(name="unsafe_t", concurrency_safe=False)
        registry.register(safe_tool)
        registry.register(unsafe_tool)
        orch = ToolOrchestrator(registry, FakeChecker(PermissionResult.allow()))

        results = await orch.execute_calls(
            [make_tu("a", "safe_t"), make_tu("b", "unsafe_t")], make_ctx(abort=True)
        )

        for r in results:
            assert r.is_error
            assert "已取消" in r.content
        assert safe_tool.calls == [] and unsafe_tool.calls == []


# ---------------------------------------------------------------------------
# hooks
# ---------------------------------------------------------------------------


class TestHooks:
    """tool_before / file_changed 钩子。"""

    async def test_hook_before_denies(self, registry, monkeypatch):
        """tool_before 钩子拒绝 → 工具不执行，UI 收到警告。"""
        reg = HookRegistry()

        async def deny_hook(payload):
            return HookResult.deny("安全策略不允许")

        reg.register(HookEvent.TOOL_BEFORE, deny_hook, name="deny")
        monkeypatch.setattr("agent.core.hooks.get_hooks", lambda: reg)

        tool = FakeTool()
        orch = _build(registry, tool)
        ui = FakeUI()

        results = await orch.execute_calls([make_tu()], make_ctx(ui=ui))

        assert results[0].is_error
        assert "钩子拒绝" in results[0].content
        assert tool.calls == []
        assert ui.warns and "钩子拒绝执行" in ui.warns[0]

    async def test_hook_before_modifies_input(self, registry, monkeypatch):
        """tool_before 钩子修改输入 → 工具收到修改后的参数。"""
        reg = HookRegistry()

        async def modify_hook(payload):
            return HookResult(allow=True, modify_input={"mode": "safe"})

        reg.register(HookEvent.TOOL_BEFORE, modify_hook, name="mod")
        monkeypatch.setattr("agent.core.hooks.get_hooks", lambda: reg)

        tool = FakeTool()
        orch = _build(registry, tool)

        results = await orch.execute_calls([make_tu(input_={"orig": 1})], make_ctx())

        assert not results[0].is_error
        assert tool.calls[0][0] == {"mode": "safe"}

    async def test_hook_before_exception_falls_back(self, registry, monkeypatch):
        """tool_before 钩子抛异常 → 不影响工具执行，使用原输入。"""
        reg = HookRegistry()

        async def boom_hook(payload):
            raise RuntimeError("钩子崩了")

        reg.register(HookEvent.TOOL_BEFORE, boom_hook, name="boom")
        monkeypatch.setattr("agent.core.hooks.get_hooks", lambda: reg)

        tool = FakeTool()
        orch = _build(registry, tool)

        results = await orch.execute_calls([make_tu(input_={"orig": 1})], make_ctx())

        assert not results[0].is_error
        assert tool.calls[0][0] == {"orig": 1}

    async def test_file_tool_triggers_file_changed(self, registry, monkeypatch):
        """文件类工具执行后触发 FILE_CHANGED 钩子。"""
        reg = HookRegistry()
        events: list[dict] = []

        async def on_file_changed(payload):
            events.append(payload)

        reg.register(HookEvent.FILE_CHANGED, on_file_changed, name="fc")
        monkeypatch.setattr("agent.core.hooks.get_hooks", lambda: reg)

        tool = FakeTool(name="file_write_doc", results=[ToolResult.ok("ok")])
        registry.register(tool)
        orch = ToolOrchestrator(registry, FakeChecker(PermissionResult.allow()))

        await orch.execute_calls(
            [make_tu(name="file_write_doc", input_={"file_path": "docs/a.md"})], make_ctx()
        )

        assert events and events[0]["path"] == "docs/a.md"
        assert events[0]["operation"] == "write"


# ---------------------------------------------------------------------------
# recovery 自愈
# ---------------------------------------------------------------------------


class TestRecovery:
    """recovery_executor 自愈路径。"""

    def _make_recovery(self, *, enabled: bool = True) -> ToolRecoveryExecutor:
        """复用模块级工厂构造 recovery。"""
        return make_recovery(enabled=enabled)

    async def test_recovery_heals_on_retry(self, registry):
        """首次失败（网络错误）→ 自愈重试成功。"""
        tool = FakeTool(results=[
            ToolResult.error("connection reset by peer"),
            ToolResult.ok("恢复成功"),
        ])
        orch = _build(registry, tool, recovery_executor=self._make_recovery())

        results = await orch.execute_calls([make_tu()], make_ctx())

        assert not results[0].is_error
        assert results[0].content == "恢复成功"
        assert len(tool.calls) == 2  # 失败一次 + 重试一次

    async def test_recovery_gives_up(self, registry):
        """自愈重试耗尽 → 返回最终错误结果。"""
        tool = FakeTool(results=[
            ToolResult.error("connection reset by peer"),
            ToolResult.error("connection reset by peer"),
        ])
        orch = _build(registry, tool, recovery_executor=self._make_recovery())

        results = await orch.execute_calls([make_tu()], make_ctx())

        assert results[0].is_error
        assert len(tool.calls) == 2

    async def test_recovery_disabled_uses_raw_call(self, registry):
        """自愈关闭 → 直接走原始 tool.call（只调用一次）。"""
        tool = FakeTool(results=[ToolResult.error("boom")])
        orch = _build(registry, tool, recovery_executor=self._make_recovery(enabled=False))

        results = await orch.execute_calls([make_tu()], make_ctx())

        assert results[0].is_error
        assert len(tool.calls) == 1


# ---------------------------------------------------------------------------
# 结果截断 / 真实权限系统集成
# ---------------------------------------------------------------------------


class TestResultTruncationAndIntegration:
    """超长结果截断落盘与真实 PermissionChecker 集成。"""

    async def test_long_result_truncated_and_persisted(self, registry, tmp_path):
        """超长结果截断预览并落盘到 .jarvis 目录。"""
        tool = FakeTool(results=[ToolResult.ok("x" * 5000)], max_result_chars=100)
        orch = _build(registry, tool)

        results = await orch.execute_calls([make_tu()], make_ctx(workdir=str(tmp_path)))

        r = results[0]
        assert "[结果超长" in r.content
        assert "已保存到" in r.content
        jarvis_dir = tmp_path / ".jarvis"
        assert jarvis_dir.is_dir()
        assert len(list(jarvis_dir.iterdir())) == 1

    async def test_real_checker_yolo_allows(self, registry):
        """真实 PermissionChecker：YOLO 模式放行只读工具。"""
        from agent.permissions import PermissionChecker
        from agent.permissions.modes import PermissionMode

        tool = FakeTool(name="read_file", read_only=True, results=[ToolResult.ok("内容")])
        registry.register(tool)
        orch = ToolOrchestrator(registry, PermissionChecker(mode=PermissionMode.YOLO))

        results = await orch.execute_calls(
            [make_tu(name="read_file", input_={"file_path": "a.txt"})], make_ctx()
        )

        assert not results[0].is_error
        assert results[0].content == "内容"

    async def test_real_checker_plan_denies_write(self, registry):
        """真实 PermissionChecker：PLAN 模式拒绝非白名单写操作。"""
        from agent.permissions import PermissionChecker
        from agent.permissions.modes import PermissionMode

        tool = FakeTool(name="write_file", read_only=False, results=[ToolResult.ok("w")])
        registry.register(tool)
        ui = FakeUI()
        orch = ToolOrchestrator(registry, PermissionChecker(mode=PermissionMode.PLAN))

        results = await orch.execute_calls([make_tu(name="write_file")], make_ctx(ui=ui))

        assert results[0].is_error
        assert "权限拒绝" in results[0].content
        assert "plan 模式禁止写操作" in results[0].content


# ---------------------------------------------------------------------------
# 补充覆盖：hooks 故障 / 审计失败 / 序列化与落盘异常
# ---------------------------------------------------------------------------


class TestExtraCoverage:
    """容错分支补充覆盖。"""

    class _BrokenHooks:
        """trigger 整体抛异常的 hooks 系统桩。"""

        async def trigger(self, *args, **kwargs):
            raise RuntimeError("hooks 系统故障")

    async def test_hook_system_broken_falls_back(self, registry, monkeypatch):
        """hooks 系统整体故障 → 回退原输入，工具正常执行。"""
        monkeypatch.setattr("agent.core.hooks.get_hooks", lambda: self._BrokenHooks())

        tool = FakeTool()
        orch = _build(registry, tool)

        results = await orch.execute_calls([make_tu(input_={"orig": 1})], make_ctx())

        assert not results[0].is_error
        assert tool.calls[0][0] == {"orig": 1}  # 回退原输入

    async def test_audit_failure_ignored(self, registry, monkeypatch):
        """审计日志抛异常 → 不影响工具执行与结果。"""

        class _BadAuditor:
            def log_call(self, **kwargs):
                raise RuntimeError("audit 故障")

        monkeypatch.setattr(
            "agent.core.audit.tool_auditor.get_tool_auditor", lambda: _BadAuditor()
        )

        tool = FakeTool(results=[ToolResult.ok("data")])
        orch = _build(registry, tool)

        results = await orch.execute_calls([make_tu()], make_ctx())

        assert not results[0].is_error
        assert results[0].content == "data"

    async def test_cyclic_result_data_serialized_fallback(self, registry):
        """结果数据循环引用 → json.dumps 失败回退 str()。"""
        cyclic: list = []
        cyclic.append(cyclic)
        tool = FakeTool(results=[ToolResult.ok(cyclic)])
        orch = _build(registry, tool)

        results = await orch.execute_calls([make_tu()], make_ctx())

        assert results[0].content.startswith("[")  # str(cyclic) 的表示

    async def test_persist_failure_truncates_without_path(self, registry, monkeypatch):
        """超大结果落盘失败 → 截断预览中不含保存路径。"""

        def _boom(*args, **kwargs):
            raise OSError("磁盘只读")

        monkeypatch.setattr("agent.core.orchestrator._persist_result", _boom)

        tool = FakeTool(results=[ToolResult.ok("x" * 5000)], max_result_chars=100)
        orch = _build(registry, tool)

        results = await orch.execute_calls([make_tu()], make_ctx())

        assert "[结果超长" in results[0].content
        assert "已保存到" not in results[0].content

    async def test_file_changed_hook_broken_ignored(self, registry, monkeypatch):
        """FILE_CHANGED 钩子故障 → 工具执行结果不受影响。"""
        monkeypatch.setattr("agent.core.hooks.get_hooks", lambda: self._BrokenHooks())

        tool = FakeTool(name="file_write_doc", results=[ToolResult.ok("ok")])
        registry.register(tool)
        orch = ToolOrchestrator(registry, FakeChecker(PermissionResult.allow()))

        results = await orch.execute_calls(
            [make_tu(name="file_write_doc", input_={"file_path": "a.txt"})], make_ctx()
        )

        assert not results[0].is_error
        assert results[0].content == "ok"
