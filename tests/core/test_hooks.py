"""Hooks 钩子系统单元测试。

覆盖 AsyncHookRegistry 的注册（on 装饰器 / register）、触发（trigger）、
事件分发、返回值合并（None/bool/str/HookResult）、异常隔离、超时保护、
禁用/启用/卸载、全局单例等。

@author aceFelix
"""

from __future__ import annotations

import asyncio

import pytest

from agent.core.hooks import (
    HookEntry,
    HookEvent,
    HookRegistry,
    HookResult,
    get_hooks,
    reset_hooks,
)


class TestHookRegistryBasic:
    """注册与查询。"""

    def test_init_has_empty_entries_per_event(self) -> None:
        reg = HookRegistry()
        for event in HookEvent:
            assert reg._entries[event] == []

    def test_on_decorator_registers_and_returns_func(self) -> None:
        reg = HookRegistry()
        calls: list[dict] = []

        @reg.on(HookEvent.TOOL_AFTER, name="record")
        async def record(payload: dict) -> None:
            calls.append(payload)

        assert record.__name__ == "record"  # 装饰器返回原函数
        assert len(reg._entries[HookEvent.TOOL_AFTER]) == 1
        assert reg._entries[HookEvent.TOOL_AFTER][0].name == "record"

    def test_register_returns_name(self) -> None:
        reg = HookRegistry()

        def sync_hook(payload: dict) -> None:
            pass

        name = reg.register(HookEvent.USER_PROMPT, sync_hook)
        assert name == "sync_hook"

    def test_register_with_explicit_name(self) -> None:
        reg = HookRegistry()

        def hook(payload: dict) -> None:
            pass

        assert reg.register(HookEvent.ERROR, hook, name="my-hook") == "my-hook"

    def test_same_name_overwrites(self) -> None:
        reg = HookRegistry()

        def h1(payload: dict) -> None:
            pass

        def h2(payload: dict) -> None:
            pass

        reg.register(HookEvent.USER_PROMPT, h1, name="dup")
        reg.register(HookEvent.USER_PROMPT, h2, name="dup")
        entries = reg._entries[HookEvent.USER_PROMPT]
        assert len(entries) == 1
        assert entries[0].func is h2

    def test_priority_sorting(self) -> None:
        """priority 数值小先执行。"""
        reg = HookRegistry()
        order: list[str] = []

        reg.register(HookEvent.USER_PROMPT, lambda p: order.append("b"), name="b", priority=10)
        reg.register(HookEvent.USER_PROMPT, lambda p: order.append("a"), name="a", priority=-5)
        reg.register(HookEvent.USER_PROMPT, lambda p: order.append("c"), name="c", priority=0)
        assert [e.name for e in reg._entries[HookEvent.USER_PROMPT]] == ["a", "c", "b"]

    def test_unregister(self) -> None:
        reg = HookRegistry()

        def hook(payload: dict) -> None:
            pass

        reg.register(HookEvent.USER_PROMPT, hook, name="temp")
        assert reg.unregister("temp") is True
        assert reg.unregister("temp") is False  # 已卸载返回 False
        assert reg._entries[HookEvent.USER_PROMPT] == []

    def test_disable_enable(self) -> None:
        reg = HookRegistry()
        hits: list[str] = []

        def hook(payload: dict) -> None:
            hits.append("x")

        reg.register(HookEvent.USER_PROMPT, hook, name="flaky")
        reg.disable("flaky")
        assert "flaky" in reg._disabled
        reg.enable("flaky")
        assert "flaky" not in reg._disabled

    def test_rereregister_re_enables(self) -> None:
        """重注册同名钩子会从禁用集合中移除。"""
        reg = HookRegistry()
        reg.register(HookEvent.ERROR, lambda p: None, name="x")
        reg.disable("x")
        reg.register(HookEvent.ERROR, lambda p: None, name="x")
        assert "x" not in reg._disabled

    def test_list_hooks_excludes_disabled(self) -> None:
        reg = HookRegistry()
        reg.register(HookEvent.ERROR, lambda p: None, name="disabled-one")
        reg.register(HookEvent.ERROR, lambda p: None, name="enabled-one")
        reg.disable("disabled-one")
        names = {e.name for e in reg.list_hooks()}
        assert names == {"enabled-one"}

    def test_list_all_includes_disabled_state(self) -> None:
        reg = HookRegistry()
        reg.register(HookEvent.ERROR, lambda p: None, name="off")
        reg.register(HookEvent.ERROR, lambda p: None, name="on")
        reg.disable("off")
        pairs = dict((e.name, enabled) for e, enabled in reg.list_all())
        assert pairs == {"off": False, "on": True}


class TestHookTrigger:
    """触发与返回值合并。"""

    @pytest.mark.asyncio
    async def test_trigger_none_returns_continue(self) -> None:
        reg = HookRegistry()

        @reg.on(HookEvent.TOOL_BEFORE, name="noop")
        async def noop(payload: dict) -> None:
            return None

        result = await reg.trigger(HookEvent.TOOL_BEFORE, {"tool_name": "Bash"})
        assert result.allow is True

    @pytest.mark.asyncio
    async def test_trigger_sync_and_async_hooks(self) -> None:
        reg = HookRegistry()
        calls: list[str] = []

        def sync_hook(payload: dict) -> None:
            calls.append("sync")

        async def async_hook(payload: dict) -> None:
            calls.append("async")

        reg.register(HookEvent.USER_PROMPT, sync_hook, name="s")
        reg.register(HookEvent.USER_PROMPT, async_hook, name="a")
        await reg.trigger(HookEvent.USER_PROMPT, {})
        assert set(calls) == {"sync", "async"}

    @pytest.mark.asyncio
    async def test_trigger_bool_false_denies(self) -> None:
        reg = HookRegistry()

        @reg.on(HookEvent.TOOL_BEFORE, name="blocker")
        async def blocker(payload: dict) -> bool:
            return False

        result = await reg.trigger(HookEvent.TOOL_BEFORE, {})
        assert result.allow is False
        assert "blocker" in result.reason

    @pytest.mark.asyncio
    async def test_trigger_str_denies_with_reason(self) -> None:
        reg = HookRegistry()

        @reg.on(HookEvent.TOOL_BEFORE, name="say-no")
        async def say_no(payload: dict) -> str:
            return "不允许执行"

        result = await reg.trigger(HookEvent.TOOL_BEFORE, {})
        assert result.allow is False
        assert result.reason == "不允许执行"

    @pytest.mark.asyncio
    async def test_deny_breaks_subsequent_hooks(self) -> None:
        """deny 后不再调用后续钩子。"""
        reg = HookRegistry()
        after_deny: list[str] = []

        @reg.on(HookEvent.TOOL_BEFORE, name="first", priority=0)
        async def first(payload: dict) -> HookResult:
            return HookResult.deny("拒绝")

        @reg.on(HookEvent.TOOL_BEFORE, name="second", priority=1)
        async def second(payload: dict) -> None:
            after_deny.append("second ran")

        result = await reg.trigger(HookEvent.TOOL_BEFORE, {})
        assert result.allow is False
        assert after_deny == []

    @pytest.mark.asyncio
    async def test_modify_input_merged(self) -> None:
        """HookResult.modify_input 合并进最终结果。"""
        reg = HookRegistry()

        @reg.on(HookEvent.USER_PROMPT, name="rewrite")
        async def rewrite(payload: dict) -> HookResult:
            return HookResult(allow=True, modify_input={"command": "echo hi"})

        result = await reg.trigger(HookEvent.USER_PROMPT, {})
        assert result.allow is True
        assert result.modify_input == {"command": "echo hi"}

    @pytest.mark.asyncio
    async def test_exception_is_isolated(self) -> None:
        """钩子抛异常不影响后续钩子。"""
        reg = HookRegistry()
        calls: list[str] = []

        @reg.on(HookEvent.USER_PROMPT, name="boom")
        async def boom(payload: dict) -> None:
            raise RuntimeError("boom")

        @reg.on(HookEvent.USER_PROMPT, name="after")
        async def after(payload: dict) -> None:
            calls.append("after")

        result = await reg.trigger(HookEvent.USER_PROMPT, {})
        assert result.allow is True  # 主流程不受影响
        assert calls == ["after"]

    @pytest.mark.asyncio
    async def test_async_timeout_is_isolated(self) -> None:
        """异步钩子超时被取消，后续钩子继续执行。"""
        reg = HookRegistry()

        @reg.on(HookEvent.USER_PROMPT, name="slow", timeout=0.05)
        async def slow(payload: dict) -> None:
            await asyncio.sleep(5)

        hits: list[str] = []

        @reg.on(HookEvent.USER_PROMPT, name="fast", timeout=10)
        async def fast(payload: dict) -> None:
            hits.append("fast")

        result = await reg.trigger(HookEvent.USER_PROMPT, {})
        assert result.allow is True
        assert hits == ["fast"]

    @pytest.mark.asyncio
    async def test_sync_hook_too_slow_raises_timeout(self) -> None:
        """同步钩子执行超过 timeout 视为超时（不中断后续）。"""
        reg = HookRegistry()
        hits: list[str] = []

        def slow_sync(payload: dict) -> None:
            import time

            time.sleep(0.2)

        def quick_sync(payload: dict) -> None:
            hits.append("quick")

        reg.register(HookEvent.USER_PROMPT, slow_sync, name="slow-sync", timeout=0.02)
        reg.register(HookEvent.USER_PROMPT, quick_sync, name="quick-sync")
        result = await reg.trigger(HookEvent.USER_PROMPT, {})
        assert result.allow is True
        assert hits == ["quick"]

    @pytest.mark.asyncio
    async def test_disabled_hook_not_called(self) -> None:
        reg = HookRegistry()
        hits: list[str] = []

        @reg.on(HookEvent.USER_PROMPT, name="off")
        async def off(payload: dict) -> None:
            hits.append("off")

        reg.disable("off")
        await reg.trigger(HookEvent.USER_PROMPT, {})
        assert hits == []


class TestHookHelpers:
    """HookResult 与 HookEntry。"""

    def test_hook_result_continue(self) -> None:
        r = HookResult.continue_()
        assert r.allow is True
        assert r.reason == ""

    def test_hook_result_deny(self) -> None:
        r = HookResult.deny("因为")
        assert r.allow is False
        assert r.reason == "因为"

    def test_hook_entry_defaults(self) -> None:
        def f(payload: dict) -> None:
            pass

        e = HookEntry(name="n", event=HookEvent.ERROR, func=f)
        assert e.priority == 0
        assert e.timeout == 10.0

    def test_hook_event_values(self) -> None:
        assert HookEvent.SESSION_START.value == "session_start"
        assert HookEvent.FILE_CHANGED.value == "file_changed"


class TestGlobalSingleton:
    """全局单例。"""

    def test_get_hooks_returns_singleton(self) -> None:
        reset_hooks()
        a = get_hooks()
        b = get_hooks()
        assert a is b
        reset_hooks()  # 清理，避免污染其他测试

    def test_reset_hooks(self) -> None:
        reset_hooks()
        a = get_hooks()
        reset_hooks()
        b = get_hooks()
        assert a is not b
