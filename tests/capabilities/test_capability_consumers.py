"""能力自检层三个消费者的接线测试（S2）。

S1 只保证「表说得对、探测得准」，本文件保证结论真的**走到了模型眼前**：

1. ToolSearch —— 发现时就标出「当前不可用」，别让模型撞上去才发现；
2. 编排器调用前门 —— 必然失败的调用不执行、不进自愈、不进延时窗口；
3. system prompt 能力段 —— 交付自查依赖的能力缺位时，给出「不能说已验证」的依据。

另有两条**逃逸路径**必须一并钉住，否则门等于没装：不可逆动作到点真执行的那条，
以及实时语音自己拼工具调度的那条。

最后是一整组 fail-open 用例：自检层自己坏掉时绝不能拖垮工具发现与执行——
「看不清」和「没有」是两件事，这条纪律在 S1 已测，这里是它在消费者侧的镜像。

@author aceFelix
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import ClassVar

import pytest

from agent.capabilities import probe as probe_mod
from agent.capabilities.registry import ReqState, RequirementStatus
from agent.core.context import ToolContext
from agent.core.message import ToolUseContent
from agent.core.orchestrator import ToolOrchestrator
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import Tool, ToolRegistry

# 表中声明 blocks_selfcheck=True 的能力名（用真名，避免测试与表脱钩后自说自话）
_BROWSER_CAP = "browser.selfcheck"
_BROWSER_TOOL = "BrowserNavigate"


# ---------------------------------------------------------------------------
# 桩件
# ---------------------------------------------------------------------------


class FakeUI:
    """只记录 warn/info 的最小 UI 桩。"""

    def __init__(self) -> None:
        self.warns: list[str] = []
        self.infos: list[str] = []

    def warn(self, text: str) -> None:
        self.warns.append(text)

    def info(self, text: str) -> None:
        self.infos.append(text)

    def error(self, text: str) -> None:
        pass

    def tool_use(self, tool_name, tool_input, tool_use_id) -> None:
        pass

    def tool_result(self, tool_name, tool_use_id, content, *, is_error=False) -> None:
        pass


class FakeChecker:
    """权限恒放行：本文件只关心能力门，不重测权限管线。"""

    def check(self, tool: Tool, args: dict, ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow()


class SpyTool(Tool):
    """记录调用次数、可控不可逆声明的工具桩。"""

    # ClassVar：这两个是类级声明（工具协议要求），写成实例字段会被当成可变默认值
    description: ClassVar[str] = "测试工具"
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}
    max_result_chars = 20_000

    def __init__(
        self,
        *,
        name: str = _BROWSER_TOOL,
        deferred: bool = False,
        irreversible: bool = False,
    ) -> None:
        self.name = name
        self.deferred = deferred
        self._irreversible = irreversible
        self.calls: list[tuple[dict, ToolContext]] = []

    async def call(self, args: dict, ctx: ToolContext) -> ToolResult:
        self.calls.append((args, ctx))
        return ToolResult.ok("工具真的跑了")

    def is_read_only(self, args: dict) -> bool:
        return False

    def is_irreversible(self, args: dict) -> bool:
        return self._irreversible

    def describe_irreversible(self, args: dict) -> str:
        return "一次外发"


def _ctx(ui=None, settings=None, extra=None) -> ToolContext:
    return ToolContext(
        workdir=".",
        messages=[],
        ui=ui if ui is not None else FakeUI(),
        settings=settings,
        extra=extra if extra is not None else {},
    )


def _tu(name: str = _BROWSER_TOOL, tu_id: str = "t1") -> ToolUseContent:
    return ToolUseContent(id=tu_id, name=name, input={})


def _orch(tool: Tool) -> ToolOrchestrator:
    reg = ToolRegistry()
    reg.register(tool)
    return ToolOrchestrator(reg, FakeChecker())


def _drive_state(state: ReqState, *, optional: bool = False):
    """把「某能力当前处于某状态」注入探测层（唯一的探测出口）。

    只替 `_probe_all_requirements` 一个函数，聚合、缓存、文案拼装全走真实代码——
    替得越少，测到的接线越真。
    """
    req = RequirementStatus(
        kind="asset",
        label="playwright 浏览器内核",
        state=state,
        evidence="实测：%LOCALAPPDATA%\\ms-playwright 下没有 revision 1223 目录",
        fix_hint="python -m playwright install chromium --only-shell",
        optional=optional,
    )
    ok = RequirementStatus(kind="python", label="playwright", state=ReqState.OK, evidence="1.60.0")

    def _probe(meta):
        # 非目标能力一律「全好」，把干扰面收成一个点
        if meta.name != _BROWSER_CAP:
            return [ok]
        if state == ReqState.OK:
            return [ok]
        return [ok, req]

    return _probe


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    """每条用例前后清探测缓存与 prompt 快照缓存。

    两层缓存都会跨用例串味：探测缓存是进程级的，`_CAPABILITY_CACHE` 更是刻意
    「一进程一次」——不清就测不出 reload，也测不出真实的注入是否生效。
    """
    from agent.prompts import system as system_mod

    def _clean() -> None:
        probe_mod.reset_cache()
        system_mod.reload_capability_cache()

    _clean()
    yield
    _clean()


# ---------------------------------------------------------------------------
# 消费者 1：ToolSearch 的发现即告知
# ---------------------------------------------------------------------------


class TestToolSearchNote:
    """搜索结果里带能力状态，但不把不可用的工具摘掉。

    为什么不摘：内核装上就恢复可用，而注册期摘工具会让「装完还得重启」变成
    使用规则（本次事故的绕路里有相当一部分是重启带来的）。告知 + 调用前拦，
    比"看不见"更能让模型走对。
    """

    async def _search(self, tool_name: str, query: str) -> str:
        from agent.tools.tool_search import ToolSearchTool

        reg = ToolRegistry()
        reg.register(SpyTool(name=tool_name, deferred=True))
        ts = ToolSearchTool(reg)
        res = await ts.call({"query": query}, _ctx())
        assert isinstance(res.data, str)
        return res.data

    async def test_missing_capability_is_flagged_but_tool_still_listed(self, monkeypatch):
        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.MISSING))
        text = await self._search(_BROWSER_TOOL, "browser")

        assert "⚠ 当前不可用" in text
        assert "playwright 浏览器内核" in text
        # 仍然列出了工具本体：只告知，不隐藏
        assert f"## {_BROWSER_TOOL}" in text
        assert "已加载到可用列表" in text

    async def test_all_ok_emits_no_note(self, monkeypatch):
        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.OK))
        text = await self._search(_BROWSER_TOOL, "browser")

        assert "⚠" not in text and "○" not in text

    async def test_probe_failed_says_unconfirmed_not_missing(self, monkeypatch):
        """「看不清」的措辞必须区别于「没有」——这条纪律在发现侧同样生效。"""
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements", _drive_state(ReqState.PROBE_FAILED)
        )
        text = await self._search(_BROWSER_TOOL, "browser")

        assert "无法确认" in text
        assert "别把它当成缺件" in text

    async def test_degraded_lists_but_marks_partial(self, monkeypatch):
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements", _drive_state(ReqState.MISSING, optional=True)
        )
        text = await self._search(_BROWSER_TOOL, "browser")

        assert "可用但有缺项" in text

    async def test_undeclared_tool_gets_no_note(self, monkeypatch):
        """表里没写的工具（FileRead 之类）不能被标成异常。"""
        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.MISSING))
        text = await self._search("FileRead", "fileread")

        assert "⚠" not in text

    async def test_probe_layer_crash_does_not_break_discovery(self, monkeypatch):
        """自检层自己炸了也要正常返回结果：辅佐能力不得拖垮工具发现。"""
        import agent.capabilities as caps

        def _boom(_name: str) -> str:
            raise RuntimeError("capabilities 模块坏了")

        monkeypatch.setattr(caps, "tool_status_note", _boom)
        text = await self._search(_BROWSER_TOOL, "browser")

        assert f"## {_BROWSER_TOOL}" in text


# ---------------------------------------------------------------------------
# 消费者 2：编排器调用前门
# ---------------------------------------------------------------------------


class TestOrchestratorBlock:
    """缺件时不执行、不重试、不留副作用。"""

    async def test_missing_capability_blocks_call(self, monkeypatch):
        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.MISSING))
        tool = SpyTool()
        ui = FakeUI()

        results = await _orch(tool).execute_calls([_tu()], _ctx(ui=ui))

        assert tool.calls == [], "被拦下的调用绝不能真的跑一次"
        assert results[0].is_error is True
        assert f"({_BROWSER_CAP})" in results[0].content
        assert "python -m playwright install chromium --only-shell" in results[0].content
        assert any("能力缺失" in w for w in ui.warns)

    async def test_block_text_forbids_claiming_verification(self, monkeypatch):
        """本次事故里对用户不实陈述的那一步，要在文案层面直接堵死。"""
        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.MISSING))
        tool = SpyTool()

        results = await _orch(tool).execute_calls([_tu()], _ctx())

        content = results[0].content
        assert "本次调用没有执行" in content
        assert "禁止" in content and "已验证" in content
        assert "降级路径" in content

    async def test_probe_failed_does_not_block(self, monkeypatch):
        """看不清就放行：用一个编造的理由拒绝一次可能成功的调用，是本次事故的另一半。"""
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements", _drive_state(ReqState.PROBE_FAILED)
        )
        tool = SpyTool()

        results = await _orch(tool).execute_calls([_tu()], _ctx())

        assert len(tool.calls) == 1
        assert results[0].is_error is False

    async def test_degraded_does_not_block(self, monkeypatch):
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements", _drive_state(ReqState.MISSING, optional=True)
        )
        tool = SpyTool()
        await _orch(tool).execute_calls([_tu()], _ctx())

        assert len(tool.calls) == 1

    async def test_unsupported_blocks(self, monkeypatch):
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements", _drive_state(ReqState.UNSUPPORTED)
        )
        tool = SpyTool()
        results = await _orch(tool).execute_calls([_tu()], _ctx())

        assert tool.calls == [] and results[0].is_error is True

    async def test_undeclared_tool_passes(self, monkeypatch):
        """零回归防线：表里没声明的工具（含既有测试桩 fake_tool）一律放行。"""
        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.MISSING))
        tool = SpyTool(name="fake_tool")

        results = await _orch(tool).execute_calls([_tu(name="fake_tool")], _ctx())

        assert len(tool.calls) == 1
        assert results[0].content == "工具真的跑了"

    async def test_blocked_call_never_reaches_recovery_retry(self, monkeypatch):
        """门在自愈层前面：缺件失败天然不进重试循环（本次白撞两轮的那条路）。"""
        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.MISSING))
        tool = SpyTool()
        seen: list[str] = []

        class _RecoverySpy:
            """只用来证明自愈层一次都没被拉到（它一跑就会重调工具）。"""

            def is_enabled(self) -> bool:
                return True

            async def execute(self, **kwargs):
                seen.append(kwargs["tool_name"])
                result = await kwargs["call_fn"](kwargs["args"], kwargs["ctx"])
                return SimpleNamespace(final_result=result)

        orch = _orch(tool)
        orch._recovery = _RecoverySpy()

        await orch.execute_calls([_tu()], _ctx())

        assert seen == [] and tool.calls == []

    async def test_probe_crash_falls_open(self, monkeypatch):
        import agent.capabilities as caps

        def _boom(_name: str) -> str:
            raise RuntimeError("capabilities 模块坏了")

        monkeypatch.setattr(caps, "tool_block_reason", _boom)
        tool = SpyTool()

        results = await _orch(tool).execute_calls([_tu()], _ctx())

        assert len(tool.calls) == 1, "自检故障时宁可漏拦，不可误拦"
        assert results[0].is_error is False


# ---------------------------------------------------------------------------
# 逃逸路径 A：不可逆动作到点真执行
# ---------------------------------------------------------------------------


def _action_settings(**over) -> SimpleNamespace:
    base = {
        "actions_journal_enabled": True,
        "actions_undo_window_s": 30,
        "actions_track_local_writes": True,
        "actions_irreversible_tools": [],
        "workdir": ".",
    }
    base.update(over)
    return SimpleNamespace(**base)


class TestHeldActionGate:
    """到点执行那条路径也必须过门，否则外发动作留着绕开自检的后门。"""

    async def test_held_action_blocked_at_fire_time(self, monkeypatch):
        from agent.core.actions import STATE_FAILED, STATE_HELD, get_journal, get_window

        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.OK))
        tool = SpyTool(name=_BROWSER_TOOL, irreversible=True)
        ctx = _ctx(settings=_action_settings(actions_undo_window_s=30))
        ctx.extra["session_name"] = "s-cap"
        orch = _orch(tool)
        await orch.execute_calls([_tu(name=_BROWSER_TOOL)], ctx)

        rec = get_journal().last(STATE_HELD)
        assert rec is not None, "前置准备：动作已入窗口"
        assert tool.calls == []

        # 窗口里环境变了（用户刚卸载 / venv 刚切）：到点必须重新过门
        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.MISSING))
        await orch._fire_irreversible(rec, ctx)

        assert tool.calls == [], "到点执行绕开能力门就是后门"
        assert get_journal().get(rec.id).state == STATE_FAILED
        get_window().clear()

    async def test_gate_runs_before_hold(self, monkeypatch):
        """缺件时连“待发”都不该挂上：先排队再失败比当场说清缺什么更像事故。"""
        from agent.core.actions import STATE_HELD, get_journal, get_window

        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.MISSING))
        tool = SpyTool(name=_BROWSER_TOOL, irreversible=True)
        ctx = _ctx(settings=_action_settings(actions_undo_window_s=30))
        ctx.extra["session_name"] = "s-cap2"

        results = await _orch(tool).execute_calls([_tu(name=_BROWSER_TOOL)], ctx)

        assert get_journal().last(STATE_HELD) is None
        assert results[0].is_error is True and tool.calls == []
        get_window().clear()


# ---------------------------------------------------------------------------
# 逃逸路径 B：实时语音链路的工具分派
# ---------------------------------------------------------------------------


class TestRealtimeGate:
    """语音路径自己拼调度，不经编排器，所以要单独确认门在这里也生效。"""

    async def test_blocked_tool_returns_structured_error_without_executing(self, monkeypatch):
        from agent.voice import realtime_tools as rt

        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.MISSING))
        tool = SpyTool()

        raw = await rt.execute_tool(
            _BROWSER_TOOL,
            {},
            registry_tool_map={_BROWSER_TOOL: tool},
            workdir=".",
            ui=None,
        )
        payload = json.loads(raw)

        assert tool.calls == []
        assert _BROWSER_CAP in payload["error"]
        assert payload["retry"] is False
        assert "不要声称已执行" in payload["instruction"]

    async def test_probe_crash_does_not_block_voice_call(self, monkeypatch):
        import agent.capabilities as caps
        from agent.voice import realtime_tools as rt

        def _boom(_name: str) -> str:
            raise RuntimeError("capabilities 模块坏了")

        monkeypatch.setattr(caps, "tool_block_reason", _boom)
        tool = SpyTool()

        raw = await rt.execute_tool(
            _BROWSER_TOOL,
            {},
            registry_tool_map={_BROWSER_TOOL: tool},
            workdir=".",
            ui=None,
        )

        assert len(tool.calls) == 1, "语音链路同样 fail-open"
        assert raw == "工具真的跑了"


# ---------------------------------------------------------------------------
# 消费者 3：system prompt 能力段
# ---------------------------------------------------------------------------


class TestCapabilityPromptSection:
    """把「缺什么」摆进提示，是模型能说「我不能确认」的前提。"""

    def test_empty_when_everything_ok(self, monkeypatch):
        from agent.prompts.system import _capability_section

        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.OK))

        assert _capability_section() == "", "一切可用时不该给提示词增加一个字节"

    def test_missing_blocking_capability_is_described_with_hard_rule(self, monkeypatch):
        from agent.prompts.system import _capability_section

        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.MISSING))
        text = _capability_section()

        assert "浏览器自动化" in text
        assert "playwright 浏览器内核" in text
        assert "降级路径" in text
        # 硬约束：不许写"亲眼看过/已验证"，也不许把"看不清"当"没有"
        assert "亲眼看过" in text and "已验证" in text
        assert "探针报错不等于缺件" in text

    def test_degraded_others_get_one_brief_line(self, monkeypatch):
        from agent.prompts.system import _capability_section

        # 目标能力正常，另一项（vision.detect 之类）降级：只一行带过，不展开
        def _probe(meta):
            good = RequirementStatus(kind="python", label="x", state=ReqState.OK, evidence="有")
            if meta.blocks_selfcheck:
                return [good]
            return [
                good,
                RequirementStatus(
                    kind="python", label="mediapipe", state=ReqState.MISSING,
                    evidence="没装", optional=True,
                ),
            ]

        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _probe)
        text = _capability_section()

        assert "其它受限项" in text
        assert "硬约束" not in text, "不影响交付自查的降级不该上硬约束"

    def test_probed_once_per_process(self, monkeypatch):
        """进程内快照：system prompt 每轮都要拼，探测不能每轮都跑（前缀缓存 + 耗时）。"""
        from agent.prompts import system as system_mod

        calls: list[str] = []
        inner = _drive_state(ReqState.MISSING)

        def _counting(meta):
            calls.append(meta.name)
            return inner(meta)

        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _counting)
        first = system_mod._capability_section()
        before = len(calls)
        second = system_mod._capability_section()

        assert first == second and before > 0
        assert len(calls) == before, "第二次组装不该重跑探测"

        system_mod.reload_capability_cache()
        system_mod._capability_section()
        assert len(calls) > before, "reload 后要重探（修完组件得能立即反映进提示）"

    def test_probe_crash_yields_empty_section(self, monkeypatch):
        from agent.prompts import system as system_mod

        def _boom(_name: str) -> str:
            raise RuntimeError("capabilities 模块坏了")

        monkeypatch.setattr(system_mod, "_CAPABILITY_CACHE", None)
        import agent.capabilities as caps

        monkeypatch.setattr(caps, "build_capability_prompt_block", _boom)

        assert system_mod._capability_section() == ""
        # 失败也必须被缓存住：否则每轮拼提示都会重跑一次注定失败的探测
        assert system_mod._CAPABILITY_CACHE == ""

    def test_section_lands_before_tool_list_in_system_prompt(self, monkeypatch, tmp_path):
        """顺序是有意义的：先看见「哪些跑不通」，再看见工具备忘。"""
        from agent.prompts.system import build_system_prompt

        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _drive_state(ReqState.MISSING))
        reg = ToolRegistry()
        reg.register(SpyTool())

        prompt = build_system_prompt(str(tmp_path), reg)

        assert "# 你今天的实际能力" in prompt
        assert prompt.find("# 你今天的实际能力") < prompt.find("# 可用工具")
