"""命令路由集成测试。

验证 agent.commands.router.dispatch_command 对常见 REPL 斜杠命令的识别与分发，
所有外部依赖（UI、Settings、Provider、QueryLoop）均使用 mock，不调用真实 API。

@author aceFelix
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from agent.commands.router import CommandContext, dispatch_command


@pytest.fixture
def cmd_ctx():
    """构造一个带 mock 依赖的命令上下文。"""
    ui = MagicMock()
    ui._console = MagicMock()

    settings = MagicMock()
    settings.api_format = "openai"
    settings.provider = "openai"
    settings.workdir = "/tmp"
    settings.permission_mode = MagicMock()
    settings.permission_mode.value = "default"
    settings.max_iterations = 10
    settings.max_tokens = 4096
    settings.temperature = 0.7
    settings.context_compaction = False
    settings.compaction_threshold = 8000
    settings.keep_recent_messages = 4
    settings.vendor_fallback = True
    settings.custom_models = {}
    settings.models = {"deepseek-chat": "DeepSeek Chat", "gpt-4o": "GPT-4o"}
    settings.system_prompt_append = ""
    settings.verbose = False

    provider = MagicMock()
    provider.name = "OpenAIProvider"
    provider.default_model = "gpt-4o"
    provider.is_thinking_enabled.return_value = True

    registry = MagicMock()
    registry.all.return_value = []

    checker = MagicMock()
    recovery = MagicMock()
    orchestrator = MagicMock()

    loop = MagicMock()
    loop._system = "system prompt"

    ctx = MagicMock()
    ctx.extra = {}
    ctx.permission_mode = "default"
    ctx.messages = []

    team_mgr = MagicMock()
    task_list = MagicMock()
    mcp_client = MagicMock()

    return CommandContext(
        ui=ui,
        settings=settings,
        provider=provider,
        registry=registry,
        checker=checker,
        recovery=recovery,
        orchestrator=orchestrator,
        loop=loop,
        ctx=ctx,
        model="gpt-4o",
        messages=[],
        dialog_count=0,
        team_mgr=team_mgr,
        task_list=task_list,
        mcp_client=mcp_client,
        session_name="session-test",
        title_generated=False,
        system_prompt="system prompt",
        should_exit=False,
    )


@pytest.mark.asyncio
async def test_exit_command_sets_exit_flag(cmd_ctx):
    """/exit 应设置 should_exit 并返回 True。"""
    result = await dispatch_command(cmd_ctx, "/exit")

    assert result is True
    assert cmd_ctx.should_exit is True


@pytest.mark.asyncio
async def test_help_command_prints_help(cmd_ctx):
    """/help 应调用帮助输出。"""
    result = await dispatch_command(cmd_ctx, "/help")

    assert result is True
    cmd_ctx.ui._console.print.assert_called_once()
    printed = cmd_ctx.ui._console.print.call_args[0][0]
    assert "/exit" in printed
    assert "/help" in printed


@pytest.mark.asyncio
async def test_models_command_switches_model(cmd_ctx):
    """/models 选择模型后应切换当前模型与 loop。"""
    new_provider = MagicMock()
    new_loop = MagicMock()

    with patch("agent.commands.handlers.model_commands.pick_from_grouped_list", return_value="deepseek-chat"), \
         patch("agent.commands.handlers.model_commands._switch_model", return_value=(new_provider, new_loop, "deepseek-chat")):
        result = await dispatch_command(cmd_ctx, "/models")

    assert result is True
    assert cmd_ctx.model == "deepseek-chat"
    assert cmd_ctx.provider is new_provider
    assert cmd_ctx.loop is new_loop


@pytest.mark.asyncio
async def test_model_prefix_switches_model(cmd_ctx):
    """/model <prefix> 前缀匹配应切换模型。"""
    new_provider = MagicMock()
    new_loop = MagicMock()

    with patch("agent.commands.handlers.model_commands._switch_model", return_value=(new_provider, new_loop, "deepseek-chat")):
        result = await dispatch_command(cmd_ctx, "/model deep")

    assert result is True
    assert cmd_ctx.model == "deepseek-chat"
    assert cmd_ctx.loop is new_loop


@pytest.mark.asyncio
async def test_unknown_command_returns_false(cmd_ctx):
    """未知斜杠命令应返回 False，让 repl 走普通对话。"""
    with patch("agent.commands.handlers.skill_commands._dispatch_skill", return_value=False):
        result = await dispatch_command(cmd_ctx, "/not-a-real-command")

    assert result is False


@pytest.mark.asyncio
async def test_normal_input_returns_false(cmd_ctx):
    """普通非斜杠输入应返回 False。"""
    result = await dispatch_command(cmd_ctx, "hello jarvis")

    assert result is False


# ---- /think 参数模糊前缀匹配（免打全 low|medium|high） @author aceFelix ----


@pytest.mark.asyncio
async def test_think_fuzzy_prefix_single_hit(cmd_ctx):
    """/think l → low；/think med → medium；均经 loop 统一入口落地。"""
    with patch("agent.commands.handlers.core_commands.build_system_prompt", return_value="sys"):
        assert await dispatch_command(cmd_ctx, "/think l") is True
        cmd_ctx.loop.set_thinking_effort.assert_called_with("low")
        assert cmd_ctx.settings.thinking_effort == "low"
        assert cmd_ctx.settings.enable_thinking is True

        assert await dispatch_command(cmd_ctx, "/think med") is True
        cmd_ctx.loop.set_thinking_effort.assert_called_with("medium")


@pytest.mark.asyncio
async def test_think_numeric_shorthand(cmd_ctx):
    """/think 1/2/3 速记 low/medium/high；/think 0 仍为 off。"""
    with patch("agent.commands.handlers.core_commands.build_system_prompt", return_value="sys"):
        for arg, expect in (("1", "low"), ("2", "medium"), ("3", "high")):
            await dispatch_command(cmd_ctx, f"/think {arg}")
            cmd_ctx.loop.set_thinking_effort.assert_called_with(expect)

        await dispatch_command(cmd_ctx, "/think 0")
        cmd_ctx.loop.set_thinking_effort.assert_called_with("off")
        assert cmd_ctx.settings.enable_thinking is False


@pytest.mark.asyncio
async def test_think_ambiguous_prefix_warns(cmd_ctx):
    """/think o 同时命中 on/off → 弹用法提示，不下发设置。"""
    await dispatch_command(cmd_ctx, "/think o")

    cmd_ctx.loop.set_thinking_effort.assert_not_called()
    assert any("用法" in str(c) for c in cmd_ctx.ui.warn.call_args_list)


@pytest.mark.asyncio
async def test_think_invalid_arg_warns(cmd_ctx):
    """/think xyz 无任何命中 → 弹用法提示，不下发设置。"""
    await dispatch_command(cmd_ctx, "/think xyz")

    cmd_ctx.loop.set_thinking_effort.assert_not_called()
    cmd_ctx.ui.warn.assert_called_once()


def test_think_tab_completion():
    """_SlashCompleter 对 /think 前缀弹出档位补全（/think l 只命中 low）。"""
    pytest.importorskip("prompt_toolkit")
    from prompt_toolkit.document import Document

    from agent.ui.cli import _HAS_PT, _SlashCompleter

    if not _HAS_PT:
        pytest.skip("prompt_toolkit 未安装时哑元 Completer 不可用")
    comp = _SlashCompleter()

    doc = Document("/think ", cursor_position=7)
    names = [c.text for c in comp.get_completions(doc, MagicMock())]
    assert names == ["on", "off", "low", "medium", "high"]

    doc = Document("/think l", cursor_position=8)
    names = [c.text for c in comp.get_completions(doc, MagicMock())]
    assert names == ["low"]

