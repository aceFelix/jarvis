"""ToolOrchestrator 的 W3.2 接入点测试：不可逆动作延时窗口 + 副作用记账。

覆盖三条不变量（都在守"敢放手"与"历史不能坏"）：
1. 被 hold 的调用**必须回一个非 error 的 tool_result**：本轮历史里已经留下了
   tool_use，缺对应 tool_result 会让这个会话之后所有请求被 API 拒收
   （docs/fixlogs/dangling-tool-use-fix.md）；
2. 窗口内真的没执行（工具一次都没被调），到点才按**账本里冻结的那一份 args**
   执行，模型后续改写参数、用户中断、/undo cancel 都不该把它发出去；
3. 只记"确实发生且没报错"的副作用：本地写引用本轮检查点（没检查点就不记，
   绝不记一个假的可撤销），外发标不可撤销。

测试策略：账本/延时窗口是进程级单例，conftest 已把路径钉到 tmp；本文件
用 SimpleNamespace 当 settings 精确控制 ``[actions]`` 字段，不依赖真实配置。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent.core.actions import (
    STATE_APPLIED,
    STATE_CANCELLED,
    STATE_FAILED,
    STATE_HELD,
    STATE_UNDONE,
    UNDO_CHECKPOINT,
    UNDO_NONE,
    get_journal,
    get_window,
)
from agent.core.context import ToolContext
from agent.core.message import Message, ToolUseContent
from agent.core.orchestrator import ToolOrchestrator
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import Tool, ToolRegistry


class FakeUI:
    """只记日志的 UI 桩（窗口提示与到点播报都经它）。"""

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warns: list[str] = []
        self.tool_uses: list[tuple] = []
        self.tool_results: list[tuple] = []

    def info(self, text: str) -> None:
        self.infos.append(text)

    def warn(self, text: str) -> None:
        self.warns.append(text)

    def tool_use(self, tool_name, tool_input, tool_use_id) -> None:
        self.tool_uses.append((tool_name, tool_input, tool_use_id))

    def tool_result(self, tool_name, tool_use_id, content, *, is_error=False) -> None:
        self.tool_results.append((tool_name, tool_use_id, content, is_error))


class FakeChecker:
    """恒 ALLOW 的权限桩：本文件只验证窗口与记账，不重测权限管线。"""

    def check(self, tool: Tool, args: dict, ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("测试放行")


class SpyTool(Tool):
    """可配置"是否外发/是否破坏性"的工具桩，记录每次真实调用。"""

    description = "测试工具"
    input_schema = {"type": "object", "properties": {}}

    def __init__(
        self,
        *,
        name: str = "SendEmail",
        irreversible: bool = True,
        destructive: bool = False,
        result: ToolResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.name = name
        self._irreversible = irreversible
        self._destructive = destructive
        self._result = result if result is not None else ToolResult.ok("已发送")
        self._error = error
        self.calls: list[tuple[dict, ToolContext]] = []

    async def call(self, args: dict, ctx: ToolContext) -> ToolResult:
        self.calls.append((dict(args), ctx))
        if self._error is not None:
            raise self._error
        return self._result

    def is_irreversible(self, args: dict) -> bool:
        return self._irreversible

    def is_destructive(self, args: dict) -> bool:
        return self._destructive

    def describe_irreversible(self, args: dict) -> str:
        return f"给 {args.get('to', '?')} 发一封信"


def _settings(**over) -> SimpleNamespace:
    """``[actions]`` 字段的安全缺省（与 orchestrator._actions_config 对齐）。"""
    base = {
        "actions_journal_enabled": True,
        "actions_undo_window_s": 30,
        "actions_track_local_writes": True,
        "actions_irreversible_tools": [],
        "workdir": ".",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(settings=None, ui=None, messages=None, extra=None, workdir=".") -> ToolContext:
    return ToolContext(
        workdir=workdir,
        messages=messages if messages is not None else [],
        ui=ui if ui is not None else FakeUI(),
        settings=settings if settings is not None else _settings(),
        extra=extra if extra is not None else {},
    )


def _tu(tu_id: str = "t1", name: str = "SendEmail", **input_: dict) -> ToolUseContent:
    return ToolUseContent(id=tu_id, name=name, input=input_ or {"to": "a@b.c"})


def _orch(tool: Tool) -> ToolOrchestrator:
    reg = ToolRegistry()
    reg.register(tool)
    return ToolOrchestrator(reg, FakeChecker())


async def _run_hold(tool: Tool, ctx: ToolContext, tu: ToolUseContent | None = None):
    """跑一轮编排，返回 (结果列表, orchestrator)。"""
    orch = _orch(tool)
    results = await orch.execute_calls([tu or _tu()], ctx)
    return results, orch


async def _run_one_call(tool: Tool, ctx: ToolContext) -> ToolOrchestrator:
    """把一个工具单独跑一轮（本地写侧的用例都长这样）。"""
    orch = _orch(tool)
    await orch.execute_calls([_tu(name=tool.name)], ctx)
    return orch


class TestHoldPath:
    """不可逆调用先入窗口，本轮不真执行。"""

    async def test_held_call_returns_non_error_result(self):
        """核心防线：被拦下也必须回一个正常 tool_result（悬空 tool_use 回归防线）。"""
        tool = SpyTool()
        ctx = _ctx(settings=_settings(actions_undo_window_s=0.5))
        results, _ = await _run_hold(tool, ctx)

        assert len(results) == 1
        r = results[0]
        assert r.tool_use_id == "t1"
        assert r.is_error is False, "被 hold 的结果不能是 error，否则模型会当失败重试"
        assert "待发" in r.content and "/undo cancel" in r.content
        # 工具一次都没被真调用
        assert tool.calls == []

    async def test_hold_text_forbids_equivalent_workaround(self):
        """给模型的结果文案必须明写「不要绕道」并举出等价路径。

        实机回归防线（2026-10-21）：文案只说「已排队、稍后自动执行」时，模型会
        自己判断「换个工具效果一样」而改用 Bash curl 把被拦下的动作做掉——窗口
        拦不住等价路径就等于没拦，见
        docs/fixlogs/w3-delay-window-bypass-fix.md。
        @author aceFelix
        """
        tool = SpyTool()
        ctx = _ctx(settings=_settings(actions_undo_window_s=30))
        results, _ = await _run_hold(tool, ctx)

        content = results[0].content
        assert "不要绕道" in content
        assert "curl" in content, "必须举例等价路径，空说一句「别绕道」拦不住换工具"
        assert tool.calls == [], "锁措辞的同时确认拦依然生效"
        get_window().clear()

    async def test_held_record_carries_frozen_args_and_preview(self):
        """账本里那条 held 记的是本次参数快照 + 可读预览。"""
        tool = SpyTool()
        ctx = _ctx(settings=_settings(actions_undo_window_s=30))
        ctx.extra["session_name"] = "session-x"
        await _run_hold(tool, ctx)

        rec = get_journal().last(STATE_HELD)
        assert rec is not None
        assert rec.state == STATE_HELD
        assert rec.args == {"to": "a@b.c"}
        assert rec.preview == "给 a@b.c 发一封信"
        assert rec.reversible is False
        assert rec.undo_spec["kind"] == UNDO_NONE
        assert rec.session_id == "session-x"
        assert rec.due_at > 0
        get_window().clear()

    async def test_normal_tool_is_executed_not_held(self):
        """没声明不可逆的工具照常执行、不进窗口。"""
        tool = SpyTool(name="FileRead", irreversible=False)
        reg = ToolRegistry()
        reg.register(tool)
        ctx = _ctx()
        orch = ToolOrchestrator(reg, FakeChecker())
        results = await orch.execute_calls([_tu(name="FileRead")], ctx)

        assert results[0].content == "已发送"
        assert len(tool.calls) == 1
        assert get_journal().last(STATE_HELD) is None

    async def test_zero_window_executes_immediately(self):
        """window_s<=0（用户关了延时）：不拦，执行完记一条"不可撤销"的 applied。"""
        tool = SpyTool()
        ctx = _ctx(settings=_settings(actions_undo_window_s=0))
        results, _ = await _run_hold(tool, ctx)

        assert len(tool.calls) == 1
        assert results[0].is_error is False
        rec = get_journal().last(STATE_APPLIED)
        assert rec is not None and rec.tool == "SendEmail"
        assert rec.reversible is False and rec.undo_spec["kind"] == UNDO_NONE

    async def test_journal_disabled_does_not_hold(self):
        """账本写不进（禁用/IO 失败）就退回原行为：无人能查的"延后执行"更糟。"""
        tool = SpyTool()
        ctx = _ctx(settings=_settings(actions_journal_enabled=False))
        results, _ = await _run_hold(tool, ctx)

        assert len(tool.calls) == 1
        assert "已发送" in results[0].content

    async def test_config_list_can_force_hold(self):
        """``actions_irreversible_tools`` 是应急口子：代码没声明也能按名字拉进窗口。"""
        tool = SpyTool(name="WebPost", irreversible=False)
        ctx = _ctx(settings=_settings(
            actions_undo_window_s=30, actions_irreversible_tools=["WebPost"],
        ))
        results, _ = await _run_hold(tool, ctx, _tu(name="WebPost"))

        assert tool.calls == []
        assert "待发" in results[0].content
        get_window().clear()

    async def test_broken_declaration_does_not_hold(self):
        """工具自己的 is_irreversible 抛异常时按"可逆"处理，不把正常调用拖进窗口。"""
        class BadTool(SpyTool):
            def is_irreversible(self, args: dict) -> bool:
                raise RuntimeError("声明位写坏了")

        tool = BadTool(name="BadTool", irreversible=True)
        ctx = _ctx()
        results, _ = await _run_hold(tool, ctx, _tu(name="BadTool"))

        assert len(tool.calls) == 1
        assert results[0].is_error is False

    async def test_hold_announces_to_ui(self):
        """窗口开启要当场说一声，并替刚开的工具卡片结个案（不能永远转圈）。"""
        ui = FakeUI()
        tool = SpyTool()
        ctx = _ctx(ui=ui, settings=_settings(actions_undo_window_s=30))
        await _run_hold(tool, ctx)

        assert any("待发窗口已开启" in m for m in ui.infos)
        assert len(ui.tool_results) == 1
        _name, tu_id, content, is_error = ui.tool_results[0]
        assert tu_id == "t1" and is_error is False and "待发" in content
        get_window().clear()

    async def test_hold_without_ui_still_returns_result(self):
        """无 UI 宿主（子代理/后台）也能拿到结果：不能因为没处说就不回话。"""
        tool = SpyTool()
        ctx = _ctx(ui=None, settings=_settings(actions_undo_window_s=30))
        results, _ = await _run_hold(tool, ctx)

        assert results[0].is_error is False and tool.calls == []
        get_window().clear()


class TestFirePath:
    """到点执行：只认账本里那份参数，且四种收尾都要留痕。"""

    async def _held_record(self, tool: SpyTool, ctx: ToolContext, window_s: float = 0.01):
        """走真实 hold 路径拿一条 held 记录（窗口给很小值便于到点）。"""
        orch = _orch(tool)
        await orch.execute_calls([_tu()], ctx)
        rec = get_journal().last(STATE_HELD)
        assert rec is not None
        return orch, rec

    async def test_fire_executes_frozen_args(self):
        """到点用的是冻结参数：模型/调用方随后改写了原 dict 也不受影响。"""
        args = {"to": "a@b.c"}
        tool = SpyTool()
        ctx = _ctx(settings=_settings(actions_undo_window_s=0.01))
        orch = _orch(tool)
        await orch.execute_calls([_tu(**args)], ctx)
        rec = get_journal().last(STATE_HELD)
        args["to"] = "hacker@evil.com"   # 事后改写原对象

        await orch._fire_irreversible(rec, ctx)
        assert tool.calls and tool.calls[0][0] == {"to": "a@b.c"}
        assert get_journal().get(rec.id).state == STATE_APPLIED

    async def test_fire_context_has_no_ui_and_empty_messages(self):
        """到点执行的上下文：不带 UI、不带对话历史，但沿用当时的权限模式。"""
        tool = SpyTool()
        ctx = _ctx(settings=_settings(actions_undo_window_s=0.01))
        ctx.permission_mode = "plan"
        orch = _orch(tool)
        await orch.execute_calls([_tu()], ctx)
        rec = get_journal().last(STATE_HELD)

        await orch._fire_irreversible(rec, ctx)
        fire_args, fire_ctx = tool.calls[0]
        assert fire_ctx.ui is None
        assert fire_ctx.messages == []
        assert fire_ctx.permission_mode == "plan"

    async def test_fire_skips_when_already_cancelled(self):
        """/undo cancel 之后到点什么都不做（邮件确实没发出去）。"""
        tool = SpyTool()
        ctx = _ctx(settings=_settings(actions_undo_window_s=30))
        orch = _orch(tool)
        await orch.execute_calls([_tu()], ctx)
        rec = get_journal().last(STATE_HELD)

        assert get_window().cancel(rec.id) is True
        get_journal().set_state(rec.id, STATE_CANCELLED, error="用户以 /undo cancel 撤回")
        await orch._fire_irreversible(rec, ctx)
        assert tool.calls == []
        assert get_journal().get(rec.id).state == STATE_CANCELLED

    async def test_fire_cancelled_when_aborted(self):
        """用户按了停止：预约动作按取消处理，绝不"人反悔了还替他发"。"""
        tool = SpyTool()
        ctx = _ctx(settings=_settings(actions_undo_window_s=30))
        orch = _orch(tool)
        await orch.execute_calls([_tu()], ctx)
        rec = get_journal().last(STATE_HELD)
        get_window().cancel(rec.id)

        ctx.abort_event.set()
        await orch._fire_irreversible(rec, ctx)
        assert tool.calls == []
        assert get_journal().get(rec.id).state == STATE_CANCELLED

    async def test_fire_records_failure_on_exception(self):
        """工具抛异常 → 状态 failed 并留原因，播报文案也说"执行失败"。"""
        ui = FakeUI()
        tool = SpyTool(error=RuntimeError("SMTP 掉了"))
        ctx = _ctx(ui=ui, settings=_settings(actions_undo_window_s=0.01))
        orch = _orch(tool)
        await orch.execute_calls([_tu()], ctx)
        rec = get_journal().last(STATE_HELD)

        await orch._fire_irreversible(rec, ctx)
        after = get_journal().get(rec.id)
        assert after.state == STATE_FAILED
        assert "SMTP 掉了" in after.error
        assert any("预约动作执行失败" in m for m in ui.infos)

    async def test_fire_records_failure_on_error_result(self):
        """工具返回 error 结果同样记 failed（不能报"已执行"）。"""
        tool = SpyTool(result=ToolResult.error("认证失败"))
        ctx = _ctx(settings=_settings(actions_undo_window_s=0.01))
        orch = _orch(tool)
        await orch.execute_calls([_tu()], ctx)
        rec = get_journal().last(STATE_HELD)

        await orch._fire_irreversible(rec, ctx)
        assert get_journal().get(rec.id).state == STATE_FAILED

    async def test_fire_when_tool_gone_marks_failed(self):
        """工具已不在注册表（插件卸载）：如实标 failed 而不是静默消失。"""
        tool = SpyTool()
        ctx = _ctx(settings=_settings(actions_undo_window_s=0.01))
        orch = _orch(tool)
        await orch.execute_calls([_tu()], ctx)
        rec = get_journal().last(STATE_HELD)
        get_window().cancel(rec.id)

        orch._registry = ToolRegistry()   # 模拟注册表里已经没有这个工具
        await orch._fire_irreversible(rec, ctx)
        assert tool.calls == []
        assert get_journal().get(rec.id).state == STATE_FAILED

    async def test_timer_actually_fires_the_call(self):
        """端到端：call_later 到点真的建 task 执行（不是只改了账本状态）。"""
        tool = SpyTool()
        ctx = _ctx(settings=_settings(actions_undo_window_s=0.02))
        orch = _orch(tool)
        await orch.execute_calls([_tu()], ctx)
        rec = get_journal().last(STATE_HELD)

        # 等到点 + 等建出来的 task 跑完
        for _ in range(20):
            await asyncio.sleep(0.02)
            if tool.calls:
                break
        assert len(tool.calls) == 1
        assert get_journal().get(rec.id).state == STATE_APPLIED


class TestRecordEffect:
    """本地写侧的记账：只记真发生的，且只在有检查点时声称可撤销。"""

    async def test_local_write_with_cid_is_undoable(self, tmp_path):
        """破坏性本地写 + 本轮有检查点 → 记成 checkpoint 类可撤销。"""
        tool = SpyTool(name="FileWrite", irreversible=False, destructive=True)
        ctx = _ctx(settings=_settings(actions_undo_window_s=0), workdir=str(tmp_path))
        ctx.extra["checkpoint_cid"] = "abc123"
        await _run_one_call(tool, ctx)

        rec = get_journal().last(STATE_APPLIED)
        assert rec is not None and rec.tool == "FileWrite"
        assert rec.reversible is True
        assert rec.undo_spec == {"kind": UNDO_CHECKPOINT, "cid": "abc123", "workdir": str(tmp_path)}
        assert rec.workdir == str(tmp_path)

    async def test_local_write_without_cid_is_not_recorded(self):
        """没有本轮检查点就不记：记了会让 /undo 列出一堆撤不动的条目。"""
        tool = SpyTool(name="FileWrite", irreversible=False, destructive=True)
        ctx = _ctx(settings=_settings(actions_undo_window_s=0))
        ctx.extra["checkpoint_cid"] = ""      # 键存在且为空=本轮确实没打检查点
        await _run_one_call(tool, ctx)

        assert get_journal().last(STATE_APPLIED) is None

    async def test_local_write_when_tracking_off(self):
        """``actions_track_local_writes=false`` 时本地写不进账（外发照样记）。"""
        tool = SpyTool(name="FileWrite", irreversible=False, destructive=True)
        ctx = _ctx(settings=_settings(
            actions_undo_window_s=0, actions_track_local_writes=False,
        ))
        ctx.extra["checkpoint_cid"] = "abc123"
        await _run_one_call(tool, ctx)

        assert get_journal().last(STATE_APPLIED) is None

    async def test_failed_call_is_not_recorded(self):
        """报错的调用不入账：本就没做成，记了只会让 /undo 面对假动作。"""
        tool = SpyTool(
            name="FileWrite", irreversible=False, destructive=True,
            result=ToolResult.error("磁盘满"),
        )
        ctx = _ctx(settings=_settings(actions_undo_window_s=0))
        ctx.extra["checkpoint_cid"] = "abc123"
        await _run_one_call(tool, ctx)

        assert get_journal().last(STATE_APPLIED) is None

    async def test_read_only_tool_is_not_recorded(self):
        """只读工具既不外发也不破坏，不该占用账本。"""
        tool = SpyTool(name="FileRead", irreversible=False, destructive=False)
        ctx = _ctx(settings=_settings(actions_undo_window_s=0))
        ctx.extra["checkpoint_cid"] = "abc123"
        await _run_one_call(tool, ctx)

        assert get_journal().last(STATE_APPLIED) is None


class TestTurnCheckpointId:
    """本轮 cid 的取值口径（拿错就会"多退一轮"）。"""

    def test_explicit_empty_extra_key_wins_over_messages(self):
        """键存在且为空 → 照实返回"无"，绝不去翻上一条用户消息（那是上一轮的 cid）。"""
        msg = Message(role="user", content="上一轮", extra={"checkpoint_id": "old-cid"})
        ctx = _ctx(messages=[msg], extra={"checkpoint_cid": ""})
        assert ToolOrchestrator._turn_checkpoint_id(ctx) == ""

    def test_falls_back_to_last_user_message(self):
        """键不存在（宿主没接这条线）→ 回退到最近一条用户消息上的 cid。"""
        old = Message(role="user", content="更早", extra={"checkpoint_id": "old"})
        newer = Message(role="user", content="本轮", extra={"checkpoint_id": "new"})
        ctx = _ctx(messages=[old, newer], extra={})
        assert ToolOrchestrator._turn_checkpoint_id(ctx) == "new"

    def test_no_message_no_cid(self):
        ctx = _ctx(messages=[], extra={})
        assert ToolOrchestrator._turn_checkpoint_id(ctx) == ""


class TestUndoFlowIntegration:
    """账本 → 撤销 的最小组合：/undo 撤上一步撤的是本地写。"""

    async def test_undo_applied_local_write(self, tmp_path, monkeypatch):
        """记进去的 checkpoint 条目能被 undo_record 撤掉（状态转 undone）。"""
        from agent.core.actions import journal as journal_mod
        from agent.core.actions import undo_record

        tool = SpyTool(name="FileWrite", irreversible=False, destructive=True)
        ctx = _ctx(settings=_settings(actions_undo_window_s=0), workdir=str(tmp_path))
        ctx.extra["checkpoint_cid"] = "cid-1"
        await _run_one_call(tool, ctx)
        rec = get_journal().last(STATE_APPLIED)
        assert rec is not None

        fake = type("M", (), {
            "available": lambda self: True,
            "has_checkpoint": lambda self, cid: True,
            "restore": lambda self, cid: (True, "工作区已恢复到该消息发出之前"),
        })()
        monkeypatch.setattr(journal_mod, "manager_for", lambda *a, **k: fake)
        ok, note = undo_record(rec, journal=get_journal())
        assert ok is True and "恢复" in note
        assert get_journal().get(rec.id).state == STATE_UNDONE


class TestTaskSideEffectBinding:
    """副作用归属接线：task_id 落账 + 账本 id 回扣给宿主（/undo --task 的两端）。

    实机走查暴露的链路断点（docs/fixlogs/w3-delay-window-bypass-fix.md §3）：驱动器
    从没往 ctx.extra 写 task_id，也不回收账本 id，于是 TaskStep.journal_ids 永远为空、
    ``/undo --task`` 端到端列不到任何条目单测却因手工构造记录一直是绿的。
    本类把"谁写、谁收"钉住，防止再退回只有测试造得起的状态。
    @author aceFelix
    """

    async def test_held_record_is_collected_and_tagged(self):
        """进窗口的动作：既带 task_id，也把 id 回报给宿主的 effect_ids。"""
        tool = SpyTool()
        ids: list[str] = []
        ctx = _ctx(settings=_settings(actions_undo_window_s=30))
        ctx.extra.update({"task_id": "goal-1", "effect_ids": ids})
        await _run_hold(tool, ctx)

        assert len(ids) == 1, "被 hold 的一步将来也要能按目标批量补偿"
        rec = get_journal().get(ids[0])
        assert rec is not None and rec.task_id == "goal-1"

    async def test_local_write_record_is_collected_and_tagged(self, tmp_path):
        """本地写同理：记成 checkpoint 的同时把 id 交回去。"""
        tool = SpyTool(name="FileWrite", irreversible=False, destructive=True)
        ids: list[str] = []
        ctx = _ctx(settings=_settings(actions_undo_window_s=0), workdir=str(tmp_path))
        ctx.extra.update({"checkpoint_cid": "cid-9", "task_id": "goal-2", "effect_ids": ids})
        await _run_one_call(tool, ctx)

        assert len(ids) == 1
        rec = get_journal().get(ids[0])
        assert rec is not None and rec.task_id == "goal-2"
        assert rec.undo_spec["cid"] == "cid-9"

    async def test_no_collector_is_silent(self, tmp_path):
        """普通对话没有 effect_ids：不该报错，也不该凭空造一个全局累加器。"""
        tool = SpyTool(name="FileWrite", irreversible=False, destructive=True)
        ctx = _ctx(settings=_settings(actions_undo_window_s=0), workdir=str(tmp_path))
        ctx.extra["checkpoint_cid"] = "cid-9"
        await _run_one_call(tool, ctx)

        assert get_journal().last(STATE_APPLIED) is not None
        assert "effect_ids" not in ctx.extra

    async def test_uncollectable_record_is_not_reported(self):
        """账本写不进就不回报 id：/undo --task 列出撤不动的条目比列不到更糟。"""
        tool = SpyTool()
        ids: list[str] = []
        ctx = _ctx(settings=_settings(actions_journal_enabled=False, actions_undo_window_s=30))
        ctx.extra.update({"task_id": "goal-3", "effect_ids": ids})
        await _run_hold(tool, ctx)

        assert ids == []
