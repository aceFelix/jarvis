"""受阻信号的第一现场：编排器登记 denial，执行通路把它回报给驱动器。

盯的是 2026-10-22 实机走查那条谎报缺陷的上半段（驱动器侧的判定见
tests/core/test_driver_approval_blocked.py）：子代理"跑完没报错"从来不等于
"事情做成了"，被权限拦下的那一次调用必须在**发生的地方**留下结构化记录。

覆盖四条：
- DENY 落地时往宿主开的容器里追加 ``{"tool", "reason"}``；宿主没开容器就静默不记
  （主会话不该为了后台任务的能力多背一个列表）
- 只记工具名与原因，不记参数（参数可能含密钥/正文）
- ASK 被用户放行时**不记**：记了就会把一个成功的步骤误判成受阻
- ``default_spawn`` 把容器里的记录取回装进 ``StepOutcome.denials``，并为子代理备好
  宿主的权限规则与本轮任务级检查点

@author aceFelix
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent.core.context import ToolContext
from agent.core.message import ToolUseContent
from agent.core.orchestrator import (
    EXTRA_DENIALS,
    ToolOrchestrator,
    record_denial,
    take_denials,
)
from agent.core.result import PermissionResult, ToolResult
from agent.core.tasks import spawn as spawn_mod
from agent.core.tasks.spawn import default_spawn
from agent.core.tool import Tool, ToolRegistry


class _DenyTool(Tool):
    """恒被拒的工具桩：直接以 DENY 结尾，模拟"无 UI 时 ASK 落成拒绝"之后的结论。"""

    description = "测试工具"
    input_schema = {"type": "object", "properties": {}}

    name = "FileWrite"

    def is_read_only(self, args):
        return False

    def check_permissions(self, args, ctx):
        return PermissionResult.deny("需要用户确认但当前环境无 UI")

    async def call(self, args, ctx):
        raise AssertionError("被拒的工具不该被执行")


class _AskTool(_DenyTool):
    """ASK 工具：有 UI 时问人，用来验证"用户放行就不记"。"""

    def check_permissions(self, args, ctx):
        return PermissionResult.ask("文件写入需确认")


class _AllowTool(_DenyTool):
    name = "Glob"

    def check_permissions(self, args, ctx):
        return PermissionResult.allow("只读放行")

    async def call(self, args, ctx):
        return ToolResult.ok(data="ok")


class _UI:
    def __init__(self, answer: str = "y") -> None:
        self._answer = answer
        self.warns: list[str] = []

    def ask_user(self, question: str) -> str:
        return self._answer

    def warn(self, text: str) -> None:
        self.warns.append(text)

    def info(self, text: str) -> None:
        pass

    def tool_use(self, name, args, tool_use_id) -> None:
        pass

    def tool_result(self, name, tool_use_id, content, *, is_error=False) -> None:
        pass


class _Checker:
    """恒不干预的权限桩：判定交给工具自己的 check_permissions。"""

    def check(self, tool, args, ctx):
        return tool.check_permissions(args, ctx)


def _ctx(tool: Tool, *, with_container: bool = True, ui=None) -> ToolContext:
    extra: dict = {}
    if with_container:
        extra[EXTRA_DENIALS] = []
    return ToolContext(
        workdir=".", messages=[], permission_mode="default", ui=ui, extra=extra,
    )


def _run(ctx, *tools: Tool) -> list:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    orch = ToolOrchestrator(registry=reg, permission_checker=_Checker())
    calls = [
        ToolUseContent(id=f"t{i}", name=t.name, input={"file_path": "a.txt"})
        for i, t in enumerate(tools)
    ]
    return asyncio.run(orch.execute_calls(calls, ctx))


class TestDenialRegistry:
    """DENY 必须落在第一现场，且没开容器时不留痕。"""

    def test_deny_records_one_entry(self):
        ctx = _ctx(_DenyTool())
        _run(ctx, _DenyTool())
        denials = take_denials(ctx)
        assert len(denials) == 1
        assert denials[0]["tool"] == "FileWrite"
        assert "无 UI" in denials[0]["reason"]

    def test_no_container_no_record(self):
        ctx = _ctx(_DenyTool(), with_container=False)
        results = _run(ctx, _DenyTool())
        # 主会话路径没开容器：照常回绝结果，但不为此多背一个列表
        assert EXTRA_DENIALS not in ctx.extra
        assert results and results[0].is_error

    def test_allow_and_user_approved_not_recorded(self):
        ctx = _ctx(_AllowTool(), ui=_UI("y"))
        _run(ctx, _AllowTool())
        assert take_denials(ctx) == [], "放行的调用记成受阻会把好步骤误判成卡住"

    def test_approved_ask_not_recorded(self):
        # 同一个 ASK 工具，用户批了就不该记；下面那条“拒了才记”才是对照
        ctx = _ctx(_AskTool(), ui=_UI("y"))
        _run(ctx, _AskTool())
        assert take_denials(ctx) == []

    def test_user_rejection_recorded(self):
        ctx = _ctx(_AskTool(), ui=_UI("n"))
        _run(ctx, _AskTool())
        denials = take_denials(ctx)
        assert len(denials) == 1
        assert "用户拒绝" in denials[0]["reason"]

    def test_reason_truncated(self):
        ctx = _ctx(_DenyTool())
        record_denial(ctx, "Bash", "x" * 900)
        assert len(ctx.extra[EXTRA_DENIALS][0]["reason"]) <= 200

    def test_record_denial_tolerates_missing_extra(self):
        """ui=None 且 extra 未初始化时也不能抛：登记是附属信息，不该打挂执行。"""
        ctx = SimpleNamespace(extra=None)
        record_denial(ctx, "FileWrite", "原因")  # type: ignore[arg-type]
        assert take_denials(SimpleNamespace(extra=None)) == []  # type: ignore[arg-type]


class TestSpawnReportsDenials:
    """真实通路把容器的内容回报成 StepOutcome.denials。"""

    def _patch_subagent(self, monkeypatch, *, denials):
        async def fake_run(definition, prompt, **kw):
            # 模拟编排器在被拦下的现场往容器里追加
            bucket = kw["extra"].get(EXTRA_DENIALS)
            for d in denials:
                bucket.append(d)
            return SimpleNamespace(success=True, report="被拦下了", iterations=1, tool_calls=1)

        monkeypatch.setattr("agent.collaboration.subagent.run_subagent", fake_run)

    def test_denials_flow_back(self, monkeypatch):
        self._patch_subagent(
            monkeypatch, denials=[{"tool": "FileWrite", "reason": "无 UI"}])
        out = default_spawn(
            SimpleNamespace(), "写一个文件", provider=object(), workdir=".",
            task_id="g1",
        )
        assert out.success is True
        assert len(out.denials) == 1
        assert out.denials[0]["tool"] == "FileWrite"

    def test_clean_run_has_empty_denials(self, monkeypatch):
        self._patch_subagent(monkeypatch, denials=[])
        out = default_spawn(SimpleNamespace(), "列一下文件", provider=object(), workdir=".")
        assert out.denials == []

    def test_injected_settings_reaches_rules_lookup(self, monkeypatch):
        """settings=None 时不能崩：读不到规则就按默认判定跑（照实不放宽）。"""
        from agent.permissions.rules import RuleSet

        self._patch_subagent(monkeypatch, denials=[])
        assert isinstance(spawn_mod._host_rules(None), RuleSet)
        assert default_spawn(
            SimpleNamespace(), "x", provider=object(), workdir=".",
        ).denials == []

    def test_no_checkpoint_without_settings(self):
        # 没配置就没有检查点：照实返回空串，后台步骤不可回退而不是假装可回退
        assert spawn_mod._step_checkpoint(None, ".", "g1", "写文件") == ""
        assert spawn_mod._step_checkpoint(SimpleNamespace(), "", "g1", "写文件") == ""


def test_host_rules_empty_path_never_reads_file(monkeypatch):
    """没配 permissions_file 时根本不读盘，直接给空规则集。"""
    from agent.permissions.rules import RuleSet

    def boom(path):
        raise AssertionError("空路径不该去读文件")

    monkeypatch.setattr("agent.permissions.rules.load_rules", boom)
    for bad in (None, "", "   "):
        assert isinstance(spawn_mod._host_rules(SimpleNamespace(permissions_file=bad)), RuleSet)


def test_host_rules_falls_back_on_unreadable_file(monkeypatch):
    """规则文件读失败（坏 YAML/没权限）一律回落空集，不能把整步打挂。"""
    from agent.permissions.rules import RuleSet

    monkeypatch.setattr(
        "agent.permissions.rules.load_rules",
        lambda path: (_ for _ in ()).throw(RuntimeError("坏文件")),
    )
    got = spawn_mod._host_rules(SimpleNamespace(permissions_file="D:/nope.yaml"))
    assert isinstance(got, RuleSet)
