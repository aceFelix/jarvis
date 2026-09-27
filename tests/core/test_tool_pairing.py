"""tool_use / tool_result 配对修复测试。

覆盖：
- ensure_tool_pairing 的修复规则：悬空补齐 / 孤儿丢弃 / 重复去重 /
  空消息清理 / 幂等 / 零拷贝快路径 / 不就地修改原消息
- 两个 provider 转换入口的出口兜底：OpenAI（role="tool" 紧跟 tool_calls）
  与 Anthropic（tool_result 块配对、role 交替、空 content 补占位）
- orchestrator 调度缺项时补占位（返回列表与输入等长）

背景：残缺历史会让 Anthropic 兼容端点（DeepSeek 等）在受理前拒收整个请求
（"tool_use ids were found without tool_result blocks immediately after"），
一旦被持久化，该会话每次请求都携带它 → 永久失败。详见
docs/fixlogs/dangling-tool-use-fix.md。

@author aceFelix
"""

from __future__ import annotations

from agent.core.context import ToolContext
from agent.core.message import (
    Message,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
)
from agent.core.orchestrator import ToolOrchestrator
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import Tool, ToolRegistry
from agent.core.tool_pairing import (
    REASON_ABORTED,
    ensure_tool_pairing,
    make_placeholder_result,
    make_placeholder_results,
)
from agent.llm.anthropic_provider import _messages_to_anthropic
from agent.llm.openai_provider import _messages_to_openai


def _use(uid: str = "c1", name: str = "Bash") -> ToolUseContent:
    """构造工具调用块。"""
    return ToolUseContent(id=uid, name=name, input={})


def _result(uid: str = "c1", content: str = "done") -> ToolResultContent:
    """构造工具结果块。"""
    return ToolResultContent(tool_use_id=uid, content=content)


def _result_ids(msgs: list[Message]) -> list[str]:
    """收集消息序列里所有 tool_result 的 tool_use_id（按出现顺序）。"""
    return [b.tool_use_id for m in msgs for b in m.content
            if isinstance(b, ToolResultContent)]


# ---------------------------------------------------------------------------
# ensure_tool_pairing
# ---------------------------------------------------------------------------


class TestEnsureToolPairing:
    """配对修复函数的规则覆盖。"""

    def test_healthy_history_returned_as_is(self) -> None:
        """完全配对的历史原样返回（同一列表对象，零拷贝）。"""
        msgs = [
            Message.user_text("现在几点"),
            Message(role="assistant", content=[_use("c1")]),
            Message(role="user", content=[_result("c1")]),
        ]
        assert ensure_tool_pairing(msgs) is msgs

    def test_empty_list_returned_as_is(self) -> None:
        """空列表直接返回。"""
        msgs: list[Message] = []
        assert ensure_tool_pairing(msgs) is msgs

    def test_dangling_tool_use_at_tail_padded(self) -> None:
        """历史以 assistant(tool_use) 结尾 → 追加占位结果消息。"""
        msgs = [Message(role="assistant", content=[_use("c1", "WPS")])]

        out = ensure_tool_pairing(msgs)

        assert len(out) == 2
        assert out[1].role == "user"
        assert _result_ids(out) == ["c1"]
        block = out[1].content[0]
        assert block.is_error is True
        assert "WPS" in block.content  # 文案带工具名，便于模型理解

    def test_dangling_padded_into_next_user_message(self) -> None:
        """悬空调用后紧跟用户文本 → 占位结果并入该消息且前置（标题数不变）。

        并入而非新建消息：新建会产生连续两条 user 消息，破坏 role 交替。
        """
        msgs = [
            Message(role="assistant", content=[_use("c1")]),
            Message.user_text("（这是用户的新问题）"),
        ]

        out = ensure_tool_pairing(msgs)

        assert len(out) == 2
        blocks = out[1].content
        assert isinstance(blocks[0], ToolResultContent)  # tool_result 前置
        assert isinstance(blocks[1], TextContent)

    def test_partial_results_padded(self) -> None:
        """2 个调用只回传 1 个结果 → 缺失的那个补占位，顺序与调用一致。"""
        msgs = [
            Message(role="assistant", content=[_use("c1"), _use("c2")]),
            Message(role="user", content=[_result("c1")]),
        ]

        out = ensure_tool_pairing(msgs)

        assert _result_ids(out) == ["c1", "c2"]
        blocks = out[1].content
        assert blocks[0].is_error is False  # 原有结果保留
        assert blocks[1].is_error is True   # 补的占位

    def test_dangling_then_assistant_gets_own_message(self) -> None:
        """悬空调用后紧跟 assistant → 插入独立的占位 user 消息。"""
        msgs = [
            Message(role="assistant", content=[_use("c1")]),
            Message(role="assistant", content=[TextContent(text="接着")]),
        ]

        out = ensure_tool_pairing(msgs)

        assert [m.role for m in out] == ["assistant", "user", "assistant"]
        assert _result_ids(out) == ["c1"]

    def test_orphan_result_dropped(self) -> None:
        """孤儿结果（引用了不存在的调用）被丢弃；消息因此变空则整体丢弃。"""
        msgs = [Message(role="user", content=[_result("ghost")])]

        out = ensure_tool_pairing(msgs)

        assert out == []

    def test_orphan_result_keeps_other_blocks(self) -> None:
        """孤儿结果被丢弃时，同消息内的文本块保留。"""
        msgs = [Message(role="user", content=[
            _result("ghost"),
            TextContent(text="保留我"),
        ])]

        out = ensure_tool_pairing(msgs)

        assert len(out) == 1
        assert out[0].content == [TextContent(text="保留我")]

    def test_duplicate_result_kept_once(self) -> None:
        """同一调用出现两次结果 → 只保留第一次（重复会被 API 拒绝）。"""
        msgs = [
            Message(role="assistant", content=[_use("c1")]),
            Message(role="user", content=[_result("c1", "第一次")]),
            Message(role="user", content=[_result("c1", "第二次")]),
        ]

        out = ensure_tool_pairing(msgs)

        blocks = [b for m in out for b in m.content if isinstance(b, ToolResultContent)]
        assert len(blocks) == 1
        assert blocks[0].content == "第一次"

    def test_originals_not_mutated(self) -> None:
        """修复不就地修改原消息对象（冻结区被 cache_control 锁定，改了就破缓存）。"""
        user_msg = Message.user_text("hi")
        assistant_msg = Message(role="assistant", content=[_use("c1")])
        msgs = [assistant_msg, user_msg]

        out = ensure_tool_pairing(msgs)

        # 原消息保持原样
        assert user_msg.content == [TextContent(text="hi")]
        # 返回的新序列里已补齐
        assert isinstance(out[1].content[0], ToolResultContent)
        # 重建的消息保留原 id / timestamp（便于日志与持久化比对）
        assert out[1].id == user_msg.id
        assert out[1].timestamp == user_msg.timestamp

    def test_idempotent(self) -> None:
        """修复一次后即为健康状态，再次调用零拷贝返回。"""
        msgs = [Message(role="assistant", content=[_use("c1")])]

        once = ensure_tool_pairing(msgs)
        twice = ensure_tool_pairing(once)

        assert twice is once

    def test_system_message_does_not_break_pairing(self) -> None:
        """中间夹着 system 消息时，配对不受影响（转换时会被单独提取）。"""
        msgs = [
            Message(role="assistant", content=[_use("c1")]),
            Message.system_text("中间系统消息"),
            Message(role="user", content=[_result("c1")]),
        ]
        assert ensure_tool_pairing(msgs) is msgs


# ---------------------------------------------------------------------------
# 占位结果工厂
# ---------------------------------------------------------------------------


class TestPlaceholderFactory:
    """占位结果的形状与批量顺序。"""

    def test_result_shape(self) -> None:
        """占位结果带工具名与原因，且标记为错误。"""
        block = make_placeholder_result(_use("c9", "WPS"), reason=REASON_ABORTED)

        assert block.tool_use_id == "c9"
        assert block.is_error is True
        assert "WPS" in block.content
        assert REASON_ABORTED in block.content

    def test_batch_order_preserved(self) -> None:
        """批量补齐保持输入顺序。"""
        blocks = make_placeholder_results(
            [_use("a"), _use("b")], reason=REASON_ABORTED
        )
        assert [b.tool_use_id for b in blocks] == ["a", "b"]


# ---------------------------------------------------------------------------
# provider 转换入口的出口兜底
# ---------------------------------------------------------------------------


class TestOpenAIProviderPairing:
    """OpenAI 协议：每个 tool_calls 后必须有配对的 role="tool" 消息。"""

    def test_dangling_history_gets_tool_message(self) -> None:
        msgs = [Message(role="assistant", content=[_use("c1")])]

        out = _messages_to_openai(msgs, "sys")

        assert [m["role"] for m in out] == ["system", "assistant", "tool"]
        assert out[1]["tool_calls"][0]["id"] == "c1"
        assert out[2]["tool_call_id"] == "c1"

    def test_partial_results_padded_in_order(self) -> None:
        msgs = [
            Message(role="assistant", content=[_use("c1"), _use("c2")]),
            Message(role="user", content=[_result("c1")]),
        ]

        out = _messages_to_openai(msgs, "sys")

        tool_msgs = [m for m in out if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]

    def test_orphan_result_not_sent(self) -> None:
        """孤儿结果不会变成悬空的 role="tool" 消息（tool_call_id 不存在会被拒）。"""
        msgs = [
            Message(role="assistant", content=[_use("c1")]),
            Message(role="user", content=[_result("c1")]),
            Message(role="user", content=[_result("ghost")]),
        ]

        out = _messages_to_openai(msgs, "sys")

        tool_ids = [m["tool_call_id"] for m in out if m["role"] == "tool"]
        assert tool_ids == ["c1"]

    def test_tool_message_precedes_user_text(self) -> None:
        """同一条消息含结果与文本时，tool 消息在 user 消息之前。"""
        msgs = [
            Message(role="assistant", content=[_use("c1")]),
            Message(role="user", content=[
                TextContent(text="补充说明"),
                _result("c1"),
            ]),
        ]

        out = _messages_to_openai(msgs, "sys")

        assert [m["role"] for m in out] == ["system", "assistant", "tool", "user"]

    def test_thinking_only_assistant_gets_empty_content(self) -> None:
        """只含 thinking 的 assistant 消息补空串，避免缺 content 字段被拒。"""
        msgs = [Message(role="assistant", content=[ThinkingContent(text="想")])]

        out = _messages_to_openai(msgs, "sys")

        assert out[1]["role"] == "assistant"
        assert out[1]["content"] == ""


class TestAnthropicProviderPairing:
    """Anthropic 协议：每个 tool_use 必须有配对的 tool_result，role 必须交替。"""

    def test_dangling_history_gets_tool_result(self) -> None:
        msgs = [Message(role="assistant", content=[_use("c1")])]

        _, api_msgs = _messages_to_anthropic(msgs)

        assert [m["role"] for m in api_msgs] == ["assistant", "user"]
        blocks = api_msgs[1]["content"]
        assert blocks[0]["type"] == "tool_result"
        assert blocks[0]["tool_use_id"] == "c1"
        assert blocks[0]["is_error"] is True

    def test_all_tool_uses_paired(self) -> None:
        """复合场景：2 个调用 1 个结果 → 输出里两个 tool_result。"""
        msgs = [
            Message(role="assistant", content=[_use("c1"), _use("c2")]),
            Message(role="user", content=[_result("c1")]),
        ]

        _, api_msgs = _messages_to_anthropic(msgs)

        blocks = api_msgs[1]["content"]
        ids = [b["tool_use_id"] for b in blocks if b["type"] == "tool_result"]
        assert ids == ["c1", "c2"]

    def test_roles_alternate_after_repair(self) -> None:
        """补齐后仍保持 user/assistant 交替（连续同 role 同样被拒收）。"""
        msgs = [
            Message.user_text("你好"),
            Message(role="assistant", content=[_use("c1")]),
            Message.user_text("新问题"),
        ]

        _, api_msgs = _messages_to_anthropic(msgs)

        assert [m["role"] for m in api_msgs] == ["user", "assistant", "user"]

    def test_thinking_only_message_not_empty(self) -> None:
        """只含 thinking 的消息过滤后为空 → 补占位文本（空 content 会被拒）。"""
        msgs = [Message(role="assistant", content=[ThinkingContent(text="想")])]

        _, api_msgs = _messages_to_anthropic(msgs)

        assert api_msgs[0]["content"] == [
            {"type": "text", "text": "(此消息不含可发送内容)"}
        ]


# ---------------------------------------------------------------------------
# orchestrator 等长保证
# ---------------------------------------------------------------------------


class _FakeTool(Tool):
    """最小工具桩。"""

    name = "fake_tool"
    description = "测试工具"
    input_schema = {"type": "object", "properties": {}}

    async def call(self, args, ctx):
        """返回固定成功结果。"""
        return ToolResult.ok("ok")


class _AllowChecker:
    """恒放行的权限检查器。"""

    def check(self, tool, tool_input, ctx):
        return PermissionResult.allow("测试放行")


class TestOrchestratorPadding:
    """调度缺项时补占位，保证返回列表与输入等长。"""

    async def test_missing_result_padded(self, monkeypatch) -> None:
        reg = ToolRegistry()
        reg.register(_FakeTool())
        orch = ToolOrchestrator(reg, _AllowChecker())

        async def _empty(pending, ctx):
            """模拟调度器没交出任何结果。"""
            return {}

        monkeypatch.setattr(orch, "_execute_pending", _empty)
        ctx = ToolContext(workdir=".", messages=[], ui=None)

        # 工具必须已注册：否则会先被「未知工具」分支拦截，测不到缺项补齐
        uses = [_use("c1", "fake_tool"), _use("c2", "fake_tool")]
        results = await orch.execute_calls(uses, ctx)

        assert [r.tool_use_id for r in results] == ["c1", "c2"]
        assert all(r.is_error for r in results)

    async def test_executed_results_pass_through(self) -> None:
        """正常路径不受影响：结果原样返回且等长。"""
        reg = ToolRegistry()
        reg.register(_FakeTool())
        orch = ToolOrchestrator(reg, _AllowChecker())
        ctx = ToolContext(workdir=".", messages=[], ui=None)

        results = await orch.execute_calls([_use("c1", "fake_tool")], ctx)

        assert len(results) == 1
        assert results[0].tool_use_id == "c1"
        assert results[0].is_error is False
