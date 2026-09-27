"""QueryLoop 补充分支覆盖单元测试。

覆盖：多 Agent 邮箱注入（_inject_teammate_notifications）、hooks 系统整体
故障时的容错、on_assistant_text 回调、延迟工具加载、清理函数。

测试替身与工厂见 tests/_query_loop_fakes.py。

@author aceFelix
"""

from __future__ import annotations

from agent.core.context import ToolContext
from agent.core.message import (
    ImageContent,
    Message,
    TextContent,
    ToolResultContent,
)
from agent.core.query_loop import QueryLoop, _inject_teammate_notifications
from agent.llm.base import (
    ProviderError,
    Stop,
    TextDelta,
    ThinkingDelta,
)

from tests._query_loop_fakes import (
    FakeOrchestrator,
    FakeTool,
    FakeUI,
    ScriptedProvider,
    make_ctx,
    make_loop,
    registry,
)


# ---------------------------------------------------------------------------
# 团队邮箱注入
# ---------------------------------------------------------------------------


class TestInjectTeammateNotifications:
    """队友邮箱消息注入（多 Agent 团队）测试。"""

    def _make_msg(self, mtype, **kwargs):
        """构造一个简单的邮箱消息对象。"""
        defaults = dict(
            type=mtype, summary=None, from_name="队友A", task_subject=None,
            task_id=None, status=None, text=None, request_id=None,
            action=None, tool=None, approve=True,
        )
        defaults.update(kwargs)
        return type("MailMsg", (), defaults)()

    def test_inject_various_types(self, monkeypatch):
        """各类队友消息应渲染成文本注入对话。"""
        from agent.collaboration import mailbox as mailbox_mod
        from agent.collaboration import team as team_mod

        mgr = type("Mgr", (), {"active_team": "proj"})()
        monkeypatch.setattr(team_mod, "get_team_manager", lambda: mgr)

        messages = [
            self._make_msg("idle_notification", summary="空闲等待任务"),
            self._make_msg("task_claimed", task_subject="修复登录 bug", task_id=7),
            self._make_msg("task_completed", status="completed", summary="已完成重构", task_id=8),
            self._make_msg("plan_approval_request", text="计划详情", request_id="r1"),
            self._make_msg("permission_request", action="写文件", tool="FileWrite"),
            self._make_msg("shutdown_response", approve=False),
            self._make_msg("heartbeat"),  # 心跳不渲染
        ]
        monkeypatch.setattr(mailbox_mod, "read_mailbox", lambda *a, **k: messages)

        ctx = ToolContext(workdir=".", messages=[], ui=None)
        _inject_teammate_notifications(ctx)

        text = "".join(
            b.text for m in ctx.messages for b in m.content if isinstance(b, TextContent)
        )
        assert "空闲等待任务" in text
        assert "领取任务" in text and "#7" in text
        assert "[completed]" in text
        assert "请求审批计划" in text
        assert "请求权限" in text
        assert "拒绝关闭" in text
        # 心跳不出现
        assert "心跳" not in text and "heartbeat" not in text

    def test_no_active_team_returns(self, monkeypatch):
        """没有活跃团队时直接返回，不注入任何消息。"""
        from agent.collaboration import team as team_mod

        mgr = type("Mgr", (), {"active_team": None})()
        monkeypatch.setattr(team_mod, "get_team_manager", lambda: mgr)

        ctx = ToolContext(workdir=".", messages=[], ui=None)
        _inject_teammate_notifications(ctx)
        assert ctx.messages == []

    def test_no_messages_returns(self, monkeypatch):
        """邮箱为空时直接返回。"""
        from agent.collaboration import mailbox as mailbox_mod
        from agent.collaboration import team as team_mod

        mgr = type("Mgr", (), {"active_team": "proj"})()
        monkeypatch.setattr(team_mod, "get_team_manager", lambda: mgr)
        monkeypatch.setattr(mailbox_mod, "read_mailbox", lambda *a, **k: [])

        ctx = ToolContext(workdir=".", messages=[], ui=None)
        _inject_teammate_notifications(ctx)
        assert ctx.messages == []

    def test_inject_only_heartbeat_returns(self, monkeypatch):
        """邮箱里只有心跳消息（不渲染）→ 不注入任何文本。"""
        from agent.collaboration import mailbox as mailbox_mod
        from agent.collaboration import team as team_mod

        mgr = type("Mgr", (), {"active_team": "proj"})()
        monkeypatch.setattr(team_mod, "get_team_manager", lambda: mgr)
        monkeypatch.setattr(
            mailbox_mod, "read_mailbox", lambda *a, **k: [self._make_msg("heartbeat")]
        )

        ctx = ToolContext(workdir=".", messages=[], ui=None)
        _inject_teammate_notifications(ctx)
        assert ctx.messages == []

    def test_inject_import_error_returns(self, monkeypatch):
        """协作模块导入失败 → 静默返回，不注入。"""
        import sys

        monkeypatch.setitem(sys.modules, "agent.collaboration.team", None)

        ctx = ToolContext(workdir=".", messages=[], ui=None)
        _inject_teammate_notifications(ctx)
        assert ctx.messages == []


# ---------------------------------------------------------------------------
# 补充分支覆盖
# ---------------------------------------------------------------------------


class TestExtraCoverage:
    """补充分支覆盖（hooks 故障容错、on_assistant_text、延迟工具、清理函数）。"""

    class _BrokenHooks:
        """trigger 整体抛异常的 hooks 系统桩。"""

        async def trigger(self, *args, **kwargs):
            raise RuntimeError("hooks 系统故障")

    async def test_hooks_broken_do_not_break_run(self, registry, monkeypatch):
        """hooks 系统整体故障 → user_prompt / assistant_response 异常被吞，
        主流程照常完成。"""
        monkeypatch.setattr("agent.core.hooks.get_hooks", lambda: self._BrokenHooks())

        provider = ScriptedProvider([[TextDelta("正常回复"), Stop(reason="stop")]])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, msgs = make_ctx()

        stats = await loop.run("你好", ctx)

        assert stats.stopped_reason == "stop"
        assert len(msgs) == 2  # user + assistant 都保留

    async def test_on_assistant_text_callback(self, registry):
        """TextDelta 同时喂给 on_assistant_text 回调；回调异常被吞不影响主流程。"""
        received: list[str] = []

        def cb(text: str) -> None:
            received.append(text)

        provider = ScriptedProvider([[TextDelta("流式文本"), Stop(reason="stop")]])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ctx, _ = make_ctx()
        ctx.on_assistant_text = cb

        stats = await loop.run("hi", ctx)

        assert stats.stopped_reason == "stop"
        assert received == ["流式文本"]

        # 回调抛异常 → 被吞掉，不影响主流程
        def bad_cb(text: str) -> None:
            raise RuntimeError("tts 故障")

        ctx2, _ = make_ctx()
        ctx2.on_assistant_text = bad_cb
        provider2 = ScriptedProvider([[TextDelta("继续"), Stop(reason="stop")]])
        loop2 = make_loop(provider2, FakeOrchestrator(), registry)

        stats2 = await loop2.run("hi", ctx2)
        assert stats2.stopped_reason == "stop"

    async def test_ui_receives_thinking_deltas(self, registry):
        """带 UI 时 ThinkingDelta 实时推送给 ui.assistant_thinking。"""
        provider = ScriptedProvider([[ThinkingDelta("思考中"), TextDelta("回答"), Stop()]])
        loop = make_loop(provider, FakeOrchestrator(), registry)
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)

        await loop.run("分析", ctx)

        assert ui.thinkings == ["思考中"]

    async def test_failover_build_provider_fails(self, registry, monkeypatch):
        """构建备选 provider 失败 → 不故障转移，以 provider_error 结束。"""

        def _boom(*args, **kwargs):
            raise RuntimeError("构建 provider 失败")

        monkeypatch.setattr("agent.bootstrap._build_provider", _boom)

        provider = ScriptedProvider([ProviderError("api error")])
        loop = make_loop(
            provider,
            FakeOrchestrator(),
            registry,
            vendor_fallback="deepseek",
            custom_models={"ds-model": {"vendor": "deepseek", "base_url": "http://x", "api_key": "k"}},
        )
        ui = FakeUI()
        ctx, _ = make_ctx(ui=ui)

        stats = await loop.run("你好", ctx)

        assert stats.stopped_reason == "provider_error"
        assert loop._provider is provider  # 未切换
        assert any("LLM 调用失败" in e for e in ui.errors)

    async def test_deferred_tool_discovered(self, registry):
        """deferred_loading=True：核心工具始终携带，延迟工具仅在发现后携带。"""
        deferred_tool = FakeTool()
        deferred_tool.name = "lazy_tool"
        deferred_tool.deferred = True
        registry.register(deferred_tool)  # fake_tool（deferred=False）已注册

        loop = QueryLoop(
            provider=ScriptedProvider([]),
            registry=registry,
            orchestrator=FakeOrchestrator(),
            enable_compaction=False,
            deferred_loading=True,
            chat_detection=False,
        )
        ctx, _ = make_ctx()

        # 未发现延迟工具 → 只有核心工具
        assert {d.name for d in loop._build_tool_defs(ctx)} == {"fake_tool"}
        # 发现后 → 携带完整 schema
        ctx.extra["discovered_tools"] = {"lazy_tool"}
        assert {d.name for d in loop._build_tool_defs(ctx)} == {"fake_tool", "lazy_tool"}

    def test_evict_old_images_multiple(self):
        """多图消息：只保留最新一张，旧图替换为文字占位；
        非 user 消息与非 ToolResultContent block 跳过。"""
        from agent.core.query_loop import _evict_old_images

        img1 = ImageContent(data="fake1", media_type="image/jpeg")
        img2 = ImageContent(data="fake2", media_type="image/jpeg")
        msgs = [
            Message(role="user", content=[ToolResultContent(tool_use_id="c1", content="第一张", images=[img1])]),
            Message(role="assistant", content=[TextContent(text="assistant 消息")]),  # 非 user → 跳过
            Message(role="user", content=[TextContent(text="纯文本 user 消息")]),      # 非 tool_result → 跳过
            Message(role="user", content=[ToolResultContent(tool_use_id="c2", content="第二张", images=[img2])]),
        ]
        _evict_old_images(msgs)

        assert msgs[3].content[0].images == [img2]  # 最新保留
        assert msgs[0].content[0].images == []      # 旧图被清
        assert "截图已处理" in msgs[0].content[0].content
        # 非图片消息未被改动
        assert msgs[1].content[0].text == "assistant 消息"
        assert msgs[2].content[0].text == "纯文本 user 消息"

    def test_collapse_old_tool_results(self):
        """旧工具结果折叠为占位，最近 N 条保留完整。"""
        from agent.core.query_loop import _collapse_old_tool_results

        msgs = [
            Message(role="user", content=[ToolResultContent(tool_use_id="c1", content="r1")]),
            Message(role="user", content=[ToolResultContent(tool_use_id="c2", content="r2")]),
            Message(role="user", content=[ToolResultContent(tool_use_id="c3", content="r3")]),
        ]
        _collapse_old_tool_results(msgs, keep_recent=2)

        assert "已完成" in msgs[0].content[0].content
        assert msgs[1].content[0].content == "r2"
        assert msgs[2].content[0].content == "r3"
