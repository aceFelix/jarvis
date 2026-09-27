"""QueryLoop 单元测试共享的测试替身与工厂。

从原 tests/test_query_loop_run.py 抽出（该文件超 800 行上限后按职责拆分）：
- ScriptedProvider：按预设脚本输出 LLMEvent（支持异常脚本），完全控制 LLM
  流式行为，并可记录每次调用收到的 messages/tools
- FakeOrchestrator：记录工具调用并返回固定工具结果（可配置抛异常）
- FakeTool / FakeUI：最小可用工具与 UI 回调桩
- registry / make_loop / make_ctx / _tool_script：常用 fixture 与构造快捷方式

用法：`from tests._query_loop_fakes import ScriptedProvider, make_loop, ...`
（与 tests/daemon/_fakes.py 的共享方式保持一致）

@author aceFelix
"""

from __future__ import annotations

import asyncio

import pytest

from agent.core.context import ToolContext
from agent.core.message import Message, ToolResultContent, ToolUseContent
from agent.core.query_loop import QueryLoop
from agent.core.result import ToolResult
from agent.core.tool import Tool, ToolRegistry
from agent.llm.base import (
    LLMEvent,
    LLMProvider,
    ProviderError,
    Stop,
    TextDelta,
    ToolCall,
    ToolCallEnd,
    Usage,
)


class ScriptedProvider(LLMProvider):
    """按预设脚本输出事件序列的可控 provider。

    每次 stream() 调用消费脚本列表中的下一个元素：
    - 事件序列（list[LLMEvent]）：逐个 yield
    - 异常实例（BaseException）：直接 raise（模拟 ProviderError / CancelledError）
    - 可调用对象：先以 messages 为参调用，再按其返回值输出
    """

    name = "scripted"
    default_model = "scripted-1"

    def __init__(self, scripts) -> None:
        self.scripts = list(scripts)
        self.stream_calls = 0
        self.last_messages: list[Message] = []
        self.last_tools: list = []
        self._thinking = False

    async def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        tools: list,
        max_tokens: int = 4096,
        temperature: float | None = None,
    ):
        """每次调用消费一个脚本元素。"""
        self.stream_calls += 1
        self.last_messages = list(messages)
        self.last_tools = list(tools)
        if not self.scripts:
            yield Stop(reason="stop", usage=Usage())
            return
        script = self.scripts.pop(0)
        if isinstance(script, BaseException):
            raise script
        if callable(script):
            script = script(messages)
        for ev in script:
            yield ev

    def set_thinking_enabled(self, enabled: bool) -> None:
        """记录思考模式开关。"""
        self._thinking = enabled

    def is_thinking_enabled(self) -> bool:
        """返回当前思考模式状态。"""
        return self._thinking


class FakeOrchestrator:
    """记录工具调用并返回固定工具结果的假编排器。"""

    def __init__(self, result_content: str = "工具执行结果") -> None:
        self.calls: list[list[ToolUseContent]] = []
        self.result_content = result_content
        self.raise_cancelled = False
        self.raise_error: Exception | None = None

    async def execute_calls(self, tool_uses, ctx):
        """按输入顺序为每个 tool_use 生成一条固定结果（可配置抛异常）。"""
        self.calls.append(list(tool_uses))
        if self.raise_cancelled:
            raise asyncio.CancelledError()
        if self.raise_error is not None:
            raise self.raise_error
        return [
            ToolResultContent(tool_use_id=tu.id, content=self.result_content)
            for tu in tool_uses
        ]


class FakeTool(Tool):
    """最小可用工具，供注册表使用。"""

    name = "fake_tool"
    description = "测试工具"
    input_schema = {"type": "object", "properties": {}}

    async def call(self, args, ctx):
        """返回固定成功结果。"""
        return ToolResult.ok("fake_result")


class FakeUI:
    """记录 UI 回调的桩。"""

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warns: list[str] = []
        self.errors: list[str] = []
        self.thinkings: list[str] = []

    def info(self, text: str) -> None:
        self.infos.append(text)

    def warn(self, text: str) -> None:
        self.warns.append(text)

    def error(self, text: str) -> None:
        self.errors.append(text)

    def assistant_text(self, text: str) -> None:
        pass

    def assistant_thinking(self, text: str) -> None:
        self.thinkings.append(text)

    def tool_use(self, tool_name, tool_input, tool_use_id) -> None:
        pass

    def tool_result(self, tool_name, tool_use_id, content, *, is_error=False) -> None:
        pass

    def ask_user(self, prompt: str) -> str:
        """默认拒绝。"""
        return "n"


async def _noop_sleep(*args, **kwargs):
    """替代 asyncio.sleep 的 no-op，加速网络重试测试。"""
    return None


@pytest.fixture
def registry():
    """注册了一个 FakeTool 的工具注册表。"""
    reg = ToolRegistry()
    reg.register(FakeTool())
    return reg


def make_loop(provider, orchestrator, reg, **kwargs):
    """快捷构造 QueryLoop，默认关闭压缩/延迟加载/聊天检测以专注主流程。

    kwargs 可覆盖默认值（如 enable_compaction=True）。
    """
    defaults = {
        "enable_compaction": False,
        "deferred_loading": False,
        "chat_detection": False,
    }
    defaults.update(kwargs)
    return QueryLoop(
        provider=provider,
        registry=reg,
        orchestrator=orchestrator,
        **defaults,
    )


def make_ctx(ui=None) -> tuple[ToolContext, list[Message]]:
    """构造 ToolContext，messages 由外部持有（模拟 repl 调用方）。"""
    messages: list[Message] = []
    ctx = ToolContext(workdir=".", messages=messages, ui=ui)
    return ctx, messages


def _tool_script(tool_id: str = "t1", tool_name: str = "fake_tool") -> list[LLMEvent]:
    """构造一个"发起工具调用"的事件脚本。"""
    return [
        TextDelta("我来调用工具"),
        ToolCall(id=tool_id, name=tool_name, input={}),
        ToolCallEnd(id=tool_id),
        Stop(reason="stop", usage=Usage(input_tokens=10, output_tokens=20)),
    ]
