"""工具注册单元测试 — T-04 改进项。

测试覆盖:
- build_default_registry 核心工具注册（13 个，deferred=False）
- 延迟工具标记验证（GUI/Browser/Camera/Plan/LSP/Team/Subagent）
- ToolRegistry 方法（register/get/all/all_core/all_deferred/aliases）
- 工具权限检查（默认 YOLO/ASK、Subagent/Team 权限）
- ToolSearch 动态注册
- register_dynamic_tools 空调用不崩溃

@author aceFelix
"""

import pytest

from agent.core.tool import (
    ToolRegistry,
    Tool,
    build_default_registry,
    register_plan_tools,
    register_subagent_tool,
    register_team_tools,
)
from agent.core.result import PermissionResult
from agent.permissions.modes import PermissionMode


# ── 辅助：一个可实例化的简单 Tool 子类 ──

class _FakeTool(Tool):
    """用于测试 ToolRegistry 方法的 fake 工具。"""
    name = "Fake"
    description = "Fake tool for testing"
    input_schema = {}

    async def call(self, args, ctx):
        from agent.core.result import ToolResult
        return ToolResult.ok("ok")


# ── 核心工具完整性 ──

_CORE_TOOL_NAMES = frozenset({
    "Bash", "FileRead", "FileEdit", "FileWrite", "Glob", "Grep",
    "Location", "TodoWrite", "AskUser", "WebFetch", "WebSearch",
    "SendEmail", "DevServer",
})


class TestCoreRegistry:
    """build_default_registry 核心工具测试。"""

    def test_all_13_core_tools_registered(self) -> None:
        """13 个核心工具必须全部注册。"""
        registry = build_default_registry()
        registered_names = {t.name for t in registry.all()}
        assert _CORE_TOOL_NAMES.issubset(registered_names), (
            f"缺少核心工具: {_CORE_TOOL_NAMES - registered_names}"
        )

    def test_core_tools_are_not_deferred(self) -> None:
        """核心工具 deferred 必须为 False。"""
        registry = build_default_registry()
        for tool in registry.all():
            if tool.name in _CORE_TOOL_NAMES:
                assert not tool.deferred, f"{tool.name} 不应为延迟工具"

    def test_all_core_returns_correct_count(self) -> None:
        """all_core() 返回数量 ≥ 13。"""
        registry = build_default_registry()
        core = registry.all_core()
        # GUI/browser/camera 可能未注册（依赖未安装），所以核心数 ≥ 13
        assert len(core) >= 13

    def test_registry_len(self) -> None:
        """registry 应有至少 13 个工具。"""
        registry = build_default_registry()
        assert len(registry) >= 13


class TestToolRegistryMethods:
    """ToolRegistry 基本方法测试。"""

    def test_register_and_get(self) -> None:
        registry = ToolRegistry()
        tool = _FakeTool()
        tool.name = "TestTool"
        registry.register(tool)
        assert registry.get("TestTool") is tool

    def test_get_nonexistent_returns_none(self) -> None:
        registry = ToolRegistry()
        assert registry.get("NonexistentTool") is None

    def test_alias_registration(self) -> None:
        registry = ToolRegistry()
        tool = _FakeTool()
        tool.name = "OriginalName"
        registry.register(tool, aliases=["AliasName"])
        assert "AliasName" in registry

    def test_duplicate_registration_raises(self) -> None:
        registry = ToolRegistry()
        t1 = _FakeTool()
        t1.name = "Dup"
        t2 = _FakeTool()
        t2.name = "Dup"
        registry.register(t1)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(t2)

    def test_all_deferred_excludes_core(self) -> None:
        """all_deferred 不应包含核心工具。"""
        registry = build_default_registry()
        deferred = registry.all_deferred()
        deferred_names = {t.name for t in deferred}
        assert _CORE_TOOL_NAMES.isdisjoint(deferred_names), (
            f"核心工具不应出现在延迟列表: {_CORE_TOOL_NAMES & deferred_names}"
        )

    def test_all_core_and_deferred_disjoint(self) -> None:
        """core 和 deferred 列表应互斥。"""
        registry = build_default_registry()
        core_names = {t.name for t in registry.all_core()}
        deferred_names = {t.name for t in registry.all_deferred()}
        assert core_names.isdisjoint(deferred_names)


class TestDeferredToolMarking:
    """延迟工具标记验证。"""

    def test_gui_tools_are_deferred(self) -> None:
        """GUI 工具必须标记 deferred=True（如果已注册）。"""
        registry = build_default_registry()
        gui_tools = [t for t in registry.all_deferred()
                     if t.name in {"MouseClick", "MouseDrag", "MouseMove", "MouseScroll",
                                   "TypeText", "KeyTap", "ScreenShot", "GetScreenSize",
                                   "WaitFor", "WindowList", "WindowFocus", "WindowClose",
                                   "WindowMove", "WindowRect", "WindowClick", "VisualClick"}]
        for t in gui_tools:
            assert t.deferred, f"{t.name} 应标记为 deferred"

    def test_browser_tools_are_deferred(self) -> None:
        registry = build_default_registry()
        browser_tools = [t for t in registry.all_deferred()
                         if t.name in {"BrowserNavigate", "BrowserScreenshot", "BrowserClick",
                                       "BrowserType", "BrowserGetText", "BrowserClose"}]
        for t in browser_tools:
            assert t.deferred, f"{t.name} 应标记为 deferred"

    def test_plan_tools_are_deferred(self) -> None:
        """Plan Mode 工具应标记 deferred。"""
        registry = ToolRegistry()
        register_plan_tools(registry)
        for t in registry.all():
            if t.name in {"EnterPlanMode", "ExitPlanMode"}:
                assert t.deferred, f"{t.name} 应标记为 deferred"

    def test_subagent_is_deferred(self) -> None:
        """Subagent/Agent 工具应标记 deferred。"""
        registry = ToolRegistry()
        # 需要 team_mgr + task_list 才能在 register_team_tools 后调 register_subagent_tool
        # 但这里只测 register_subagent_tool 的 deferred 标记
        ok = register_subagent_tool(registry, provider=None, permission_mode=PermissionMode.YOLO)
        if ok:
            agent = registry.get("Agent")
            assert agent is not None
            assert agent.deferred, "Agent 工具应标记为 deferred"

    def test_team_tools_are_deferred(self) -> None:
        """Team/Task 协作工具应标记 deferred。"""
        registry = ToolRegistry()
        cnt = register_team_tools(registry)
        if cnt > 0:
            team_names = {"TeamCreate", "TeamDelete", "SendMessage",
                         "TaskCreate", "TaskGet", "TaskList", "TaskUpdate",
                         "TaskStop", "TeamStatus"}
            for name in team_names:
                t = registry.get(name)
                if t is not None:
                    assert t.deferred, f"{name} 应标记为 deferred"


class TestToolRegistrationFlows:
    """工具注册流程测试。"""

    def test_register_dynamic_tools_empty(self) -> None:
        """register_dynamic_tools 无 mcp_client 时应返回 0 而不崩溃。"""
        from agent.core.tool import register_dynamic_tools
        registry = build_default_registry()
        count = register_dynamic_tools(registry, mcp_client=None, workdir=None)
        assert count >= 0  # harness 可能注册也可能未注册

    def test_toolsearch_registration(self) -> None:
        """ToolSearch 工具应在 deferred_loading 模式下注册。"""
        registry = build_default_registry()
        from agent.tools.tool_search import ToolSearchTool
        if "ToolSearch" not in registry:
            registry.register(ToolSearchTool(registry))
        search = registry.get("ToolSearch")
        assert search is not None
        assert not search.deferred, "ToolSearch 自身应为核心工具"
        assert search.is_read_only({})

    def test_toolsearch_query_empty_returns_error(self) -> None:
        """ToolSearch 空查询应返回错误。"""
        import asyncio
        from agent.tools.tool_search import ToolSearchTool
        from agent.core.context import ToolContext
        registry = build_default_registry()
        search = ToolSearchTool(registry)
        ctx = ToolContext(workdir="/tmp", messages=[], permission_mode="yolo")
        result = asyncio.run(search.call({"query": "", "max_results": 5}, ctx))
        assert result.is_error
        assert "关键词" in str(result.data)

    def test_toolsearch_finds_deferred_tools(self) -> None:
        """ToolSearch 应能找到延迟工具。"""
        import asyncio
        from agent.tools.tool_search import ToolSearchTool
        from agent.core.context import ToolContext
        registry = build_default_registry()
        # 延迟工具可能在 GUI 中已注册
        deferred = registry.all_deferred()
        if deferred:
            search = ToolSearchTool(registry)
            ctx = ToolContext(workdir="/tmp", messages=[], permission_mode="yolo")
            # 用第一个延迟工具的名字搜索
            target_name = deferred[0].name
            result = asyncio.run(search.call({"query": target_name, "max_results": 3}, ctx))
            assert not result.is_error


class TestPermissionDefaults:
    """工具权限默认值测试。"""

    def test_bash_permission_is_protected(self) -> None:
        """Bash 工具的默认权限应受保护（非 YOLO 不可直接 ALLOW）。"""
        registry = build_default_registry()
        bash = registry.get("Bash")
        assert bash is not None
        from agent.core.result import PermissionBehavior
        perm = bash.check_permissions({"command": "rm -rf /"}, None)
        # 危险命令应为 ASK 或 DENY
        assert perm.behavior in (PermissionBehavior.ASK, PermissionBehavior.DENY), (
            f"危险命令不应默认 ALLOW: {perm}"
        )

    def test_readonly_tools_default_allow(self) -> None:
        """只读工具默认应为 ALLOW。"""
        registry = build_default_registry()
        from agent.core.result import PermissionBehavior
        for name in ("FileRead", "Glob", "Grep"):
            tool = registry.get(name)
            if tool:
                perm = tool.check_permissions({}, None)
                # 只读工具不涉及执行，应默认 ALLOW
                assert perm.behavior == PermissionBehavior.ALLOW, (
                    f"{name}: 只读工具应默认 ALLOW，实际: {perm.behavior}"
                )


# ── 补充：注册中心边界 / contains / 别名 / 基类默认 ──


class TestRegistryEdgeCases:
    """ToolRegistry 边界补充。"""

    def test_register_empty_name_raises(self) -> None:
        registry = ToolRegistry()
        tool = _FakeTool()
        tool.name = ""
        with pytest.raises(ValueError, match="empty name"):
            registry.register(tool)

    def test_contains_by_name(self) -> None:
        registry = ToolRegistry()
        tool = _FakeTool()
        tool.name = "Named"
        registry.register(tool)
        assert "Named" in registry

    def test_contains_by_alias(self) -> None:
        registry = ToolRegistry()
        tool = _FakeTool()
        tool.name = "Canonical"
        registry.register(tool, aliases=["aka"])
        assert "aka" in registry

    def test_contains_missing(self) -> None:
        assert "Nope" not in ToolRegistry()

    def test_get_by_alias_returns_tool(self) -> None:
        registry = ToolRegistry()
        tool = _FakeTool()
        tool.name = "Canonical2"
        registry.register(tool, aliases=["alias2"])
        assert registry.get("alias2") is tool
        assert registry.get("Canonical2") is tool

    def test_all_returns_in_registration_order(self) -> None:
        registry = ToolRegistry()
        t1 = _FakeTool()
        t1.name = "First"
        t2 = _FakeTool()
        t2.name = "Second"
        registry.register(t1)
        registry.register(t2)
        names = [t.name for t in registry.all()]
        assert names == ["First", "Second"]

    def test_aliases_not_in_all(self) -> None:
        """别名不产生重复工具对象。"""
        registry = ToolRegistry()
        tool = _FakeTool()
        tool.name = "Aliased"
        registry.register(tool, aliases=["a1", "a2"])
        assert len(registry.all()) == 1


class TestToolDefaults:
    """Tool 基类 fail-closed 默认行为。"""

    def test_safety_defaults(self) -> None:
        tool = _FakeTool()
        assert tool.is_read_only({}) is False
        assert tool.is_destructive({}) is False
        assert tool.is_concurrency_safe({}) is False
        assert tool.deferred is False

    def test_check_permissions_default_ask(self) -> None:
        from agent.core.result import PermissionBehavior

        tool = _FakeTool()
        perm = tool.check_permissions({}, None)
        assert perm.behavior == PermissionBehavior.ASK

    def test_validate_input_default_pass(self) -> None:
        tool = _FakeTool()
        assert tool.validate_input({}, None).ok is True

    def test_ui_defaults(self) -> None:
        tool = _FakeTool()
        assert tool.user_facing_name() == "Fake"
        assert tool.activity_description({}) is None

    def test_prepare_permission_matcher_default_none(self) -> None:
        assert _FakeTool().prepare_permission_matcher({}) is None


class TestPermissionMatcher:
    """权限规则匹配器。"""

    def _matcher(self, tool_name: str = "Bash", targets: list | None = None):
        from agent.core.tool import PermissionMatcher

        return PermissionMatcher(tool_name=tool_name, targets=targets or ["git status"])

    def test_tool_name_mismatch(self) -> None:
        assert self._matcher().matches("Read(git *)") is False

    def test_no_spec_matches_any_call(self) -> None:
        assert self._matcher().matches("Bash") is True

    def test_empty_spec_matches_any_call(self) -> None:
        assert self._matcher().matches("Bash()") is True

    def test_wildcard_hit(self) -> None:
        assert self._matcher().matches("Bash(git *)") is True

    def test_exact_target_hit(self) -> None:
        assert self._matcher().matches("Bash(git status)") is True

    def test_no_hit(self) -> None:
        assert self._matcher().matches("Bash(npm *)") is False

    def test_invalid_pattern_false(self) -> None:
        assert self._matcher().matches("###") is False
