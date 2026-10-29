"""空转防护两件套测试：截断续写熔断 + 同型调用循环检测。

背景（2026-10-14 贪吃蛇空转案）：agent 拿记忆里的假线索反复全盘检索、
每轮输出被 max_tokens 截断后又用散文续写代码，15 分钟零产出。交付两件
治本防护：
- query_loop 截断续写熔断：连续截断 >2 次即停，不再烧满迭代
- policy/loop_detect 循环检测器：同型调用窗口内 ≥3 次强制 ASK 拦刹车

覆盖：签名归一化（同型识别/异型区分）、检测器触发与静默配额、reset
语义、熔断终止与复位、orchestrator 集成（ALLOW 被升级 ASK）。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.core.policy.loop_detect import (
    LoopDetector,
    call_signature,
    check_tool_loop,
)
from agent.core.message import Message
from agent.llm.base import Stop, TextDelta, ThinkingDelta, ToolCall, ToolCallEnd, Usage

from tests._query_loop_fakes import (
    FakeOrchestrator,
    ScriptedProvider,
    make_ctx,
    make_loop,
    registry,
)


# 说明：全局检测器单例的逐用例复位由 tests/conftest.py autouse 夹具统一兜底

# ---------- 签名归一化 ----------

def test_ls_variants_share_signature():
    """不同姿势的 ls（大小写/多余空格/斜杠风格）应归一为同一签名。"""
    s1 = call_signature("Bash", {"command": "ls -la /E:/"})
    s2 = call_signature("Bash", {"command": "ls   -la  /e/"})
    assert s1 == s2 == "Bash:ls"


def test_find_different_depth_same_signature():
    """find 换 -maxdepth 姿势仍算同型（都在翻目录）。"""
    s1 = call_signature("Bash", {"command": "find /e -maxdepth 2 -iname '*snake*'"})
    s2 = call_signature("Bash", {"command": "find /e -maxdepth 5 -type d -iname snake"})
    assert s1 == s2 == "Bash:find"


def test_different_tools_distinct():
    """Bash 与 FileRead 即使路径文本相同也不应混为同型。"""
    assert call_signature("Bash", {"command": "ls x"}) != call_signature(
        "FileRead", {"path": "x"}
    )


def test_non_search_keeps_argument():
    """非翻目录类命令保留参数前缀，不误伤正常多样操作。"""
    s1 = call_signature("Bash", {"command": "echo hello"})
    s2 = call_signature("Bash", {"command": "echo world"})
    assert s1 != s2


# ---------- 检测器触发逻辑 ----------

def test_detector_fires_on_third_repeat():
    """同型第 3 次触发，前 2 次静默。"""
    d = LoopDetector()
    assert d.observe("Bash:ls") is False
    assert d.observe("Bash:ls") is False
    assert d.observe("Bash:ls") is True


def test_detector_quiet_after_fire_quota():
    """触发配额用尽后静默（用户放行后不再重复骚扰）。"""
    d = LoopDetector()
    fired = [d.observe("Bash:find") for _ in range(8)]
    # 第 3 次、第 4 次各触发一次（配额 2），之后全静默
    assert fired.count(True) == 2


def test_detector_window_evicts_old():
    """窗口滑动：异型调用挤掉旧签名后，同型计数重新累积。"""
    d = LoopDetector()
    d.observe("Bash:ls")
    d.observe("Bash:ls")
    for i in range(6):  # 用 6 个异型填满窗口，把旧签名挤出去
        d.observe(f"Bash:echo{i}")
    assert d.observe("Bash:ls") is False


def test_detector_reset_clears_state():
    """reset 清空窗口与配额。"""
    d = LoopDetector()
    for _ in range(3):
        d.observe("Bash:ls")
    d.reset()
    assert d.observe("Bash:ls") is False


def test_check_tool_loop_message_mentions_stall():
    """便捷入口：第 3 次同型调用返回含"原地打转"的提示语。"""
    for _ in range(2):
        assert check_tool_loop("Bash", {"command": "ls a"}) is None
    msg = check_tool_loop("Bash", {"command": "ls b"})
    assert msg and "原地打转" in msg


# ---------- query_loop 截断续写熔断 ----------

def _trunc_script(text: str) -> list:
    """构造一个"只有正文、被 max_tokens 截断"的事件脚本。"""
    return [TextDelta(text), Stop(reason="max_tokens", usage=Usage())]


@pytest.mark.asyncio
async def test_fuse_stops_after_two_continuations(registry):
    """连续 3 轮截断（续写 2 次封顶）→ 熔断停止并给用户建议。"""
    provider = ScriptedProvider([
        _trunc_script("半截代码一"),
        _trunc_script("半截代码二"),
        _trunc_script("半截代码三"),
        _trunc_script("不该被消费的第四轮"),
    ])
    orch = FakeOrchestrator()
    loop = make_loop(provider, orch, registry, max_iterations=10)
    ctx, _ = make_ctx()
    stats = await loop.run("写个贪吃蛇", ctx)
    assert stats.stopped_reason == "truncation_fuse"
    assert stats.iterations == 3
    assert provider.stream_calls == 3  # 第四轮没有被烧掉


@pytest.mark.asyncio
async def test_fuse_resets_when_tools_flow(registry):
    """截断一次后正常推进（工具调用）→ 计数复位，零星截断不受熔断惩罚。"""
    provider = ScriptedProvider([
        _trunc_script("半截一"),                       # 截断 #1 → 续写
        _trunc_script("半截二"),                       # 截断 #2 → 续写（已到封顶）
        [                                              # 正常轮：工具调用，复位计数
            TextDelta("我来调用工具"),
            ToolCall(id="t1", name="fake_tool", input={}),
            ToolCallEnd(id="t1"),
            Stop(reason="stop", usage=Usage()),
        ],
        _trunc_script("半截三"),                       # 截断 #1（复位后）→ 应续写而非熔断
        [TextDelta("完成"), Stop(reason="stop", usage=Usage())],
    ])
    orch = FakeOrchestrator()
    loop = make_loop(provider, orch, registry, max_iterations=10)
    ctx, _ = make_ctx()
    stats = await loop.run("做个任务", ctx)
    assert stats.stopped_reason == "stop"
    assert len(orch.calls) == 1  # 工具确实执行了


# ---------- orchestrator 集成：ALLOW 被空转检测升级 ASK ----------

@pytest.mark.asyncio
async def test_orchestrator_escalates_repetitive_allow_to_ask():
    """管线 ALLOW 的同型第 3 次调用被强制 ASK，用户拒绝则不执行。"""
    from agent.core.context import ToolContext
    from agent.core.message import ToolUseContent
    from agent.core.orchestrator import ToolOrchestrator
    from agent.core.result import PermissionBehavior, PermissionResult
    from agent.core.tool import Tool, ToolRegistry

    class EchoTool(Tool):
        name = "Bash"
        description = "test"
        input_schema = {"type": "object", "properties": {}}

        async def call(self, args, ctx):
            from agent.core.result import ToolResult
            return ToolResult.ok("executed")

    class FakeChecker:
        def check(self, tool, args, ctx):
            return PermissionResult.allow("沙箱自动放行")

    class AskUI:
        def __init__(self):
            self.questions = []

        def warn(self, t): pass
        def info(self, t): pass
        def error(self, t): pass
        def assistant_text(self, t): pass
        def assistant_thinking(self, t): pass
        def tool_use(self, *a, **k): pass
        def tool_result(self, *a, **k): pass
        def ask_user(self, prompt):
            self.questions.append(prompt)
            return "n"  # 用户拒绝继续空转

    reg = ToolRegistry()
    reg.register(EchoTool())
    ui = AskUI()
    ctx = ToolContext(workdir=".", messages=[Message(role="user", content=[])], ui=ui)
    orch = ToolOrchestrator(reg, FakeChecker())
    tu = ToolUseContent(id="t1", name="Bash", input={"command": "ls /e"})

    # 前两次：ALLOW 正常执行
    for i in range(2):
        res = await orch.execute_calls(
            [ToolUseContent(id=f"t{i}", name="Bash", input={"command": f"ls /e{i}"})], ctx)
        assert "executed" in res[0].content
    # 第三次：同型命中窗口阈值 → 升级 ASK → 用户答 n → 权限拒绝
    res = await orch.execute_calls([tu], ctx)
    assert "权限拒绝" in res[0].content
    assert ui.questions and "原地打转" in ui.questions[0]


# ---------- Ctrl+C 流式打断 ----------

class _AbortingProvider:
    """流式吐出若干思考增量，中途自行置 abort 标志，模拟用户按 Ctrl+C。

    语音模式的 SIGINT 处理器只置标志、不抛异常，_poll_interrupt 线程把
    stop_event 转成 ctx.abort_event；本桩用于验证 query_loop 能在流式过程
    中看到这个标志并立即收口。

    @author aceFelix
    """

    default_model = "fake-model"  # QueryLoop 未显式传 model 时的兜底来源

    def __init__(self, n_events: int, abort_after: int) -> None:
        self.n_events = n_events
        self.abort_after = abort_after
        self.abort_event = None  # 测试注入 ctx.abort_event
        self.stream_calls = 0
        self.consumed = 0
        self.closed = False

    async def stream(self, *, model, system, messages, tools,
                     max_tokens=4096, temperature=None, known_tool_names=None):
        """逐个产出思考增量；到达指定序号后置 abort，关闭时记 closed。"""
        self.stream_calls += 1
        try:
            for i in range(self.n_events):
                self.consumed += 1
                yield ThinkingDelta(text=f"思考{i}")
                if i + 1 == self.abort_after:
                    self.abort_event.set()
        finally:
            self.closed = True

    def set_thinking_enabled(self, enabled: bool) -> None:
        pass

    def is_thinking_enabled(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_abort_cuts_stream_mid_thinking(registry):
    """思考流中途置 abort → 当轮立即终止，不再消费后续事件。

    修复前：_stream_once 只在流自然结束后返回，abort_event 要到下一轮
    循环顶部才被检查，而深度思考一轮流式可跑数十秒——Ctrl+C 形同失效。
    """
    prov = _AbortingProvider(n_events=50, abort_after=2)
    ctx, _messages = make_ctx()
    prov.abort_event = ctx.abort_event
    loop = make_loop(prov, FakeOrchestrator(), registry)

    stats = await loop.run("明天中午提醒我面试", ctx)

    assert stats.stopped_reason == "aborted"
    # 第 3 个事件到达时才发现标志已置位，其余 47 个事件不再消费
    assert prov.stream_calls == 1
    assert prov.consumed == 3
    # 异步生成器被显式 aclose，底层连接不残留
    assert prov.closed is True


@pytest.mark.asyncio
async def test_abort_leaves_no_dangling_assistant_turn(registry):
    """被打断的残缺轮不得入历史，abort_event 需复位以便下一轮正常运行。

    残缺 assistant 轮可能带半截 tool_use，一旦入历史会让该会话后续所有
    请求被 API 拒收（悬空 tool_use）。
    """
    prov = _AbortingProvider(n_events=20, abort_after=1)
    ctx, messages = make_ctx()
    prov.abort_event = ctx.abort_event
    loop = make_loop(prov, FakeOrchestrator(), registry)

    stats = await loop.run("说点什么", ctx)

    assert stats.stopped_reason == "aborted"
    assert not any(m.role == "assistant" for m in messages)
    # run() 已重绑新的 abort_event，残留标志不会跳过后续轮次
    assert not ctx.abort_event.is_set()
