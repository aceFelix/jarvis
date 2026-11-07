"""规划模式工具与权限覆盖单测（2026-11）。

覆盖三件事：
1. ``EnterPlanMode`` 把 ctx 切成 plan 后，权限层真的拒绝写操作、放行只读工具
   （历史问题是 checker 只看构造时的模式，ctx 改了等于没改）；
2. ``ExitPlanMode`` 落盘 ``.jarvis/PLAN.md``、恢复原权限模式；
3. 桌面 UI 钩子 ``plan_submitted`` 被调用（终端没有该钩子时静默跳过）。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.core.context import ToolContext
from agent.core.message import Message, TextContent
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import JSONSchema, Tool
from agent.permissions.checker import PermissionChecker
from agent.permissions.modes import PermissionMode, parse_mode
from agent.tools.collaboration.enter_plan import EnterPlanModeTool
from agent.tools.collaboration.exit_plan import ExitPlanModeTool


class _ReadTool(Tool):
    """最小只读工具替身（名字走 plan 白名单）。"""

    name = "FileRead"
    description = "read"
    input_schema: JSONSchema = {"type": "object", "properties": {}}

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("read")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(data="ok")


class _WriteTool(Tool):
    """最小写工具替身。"""

    name = "FileWrite"
    description = "write"
    input_schema: JSONSchema = {"type": "object", "properties": {}}

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("write")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(data="ok")


def _ctx(tmp_path: Path, mode: str = "default") -> ToolContext:
    return ToolContext(
        workdir=str(tmp_path),
        messages=[
            Message(role="assistant", content=[TextContent(text="# 方案\n1. 先读代码\n2. 再改")])
        ],
        permission_mode=mode,
    )


@pytest.mark.asyncio
async def test_enter_plan_denies_writes_through_checker(tmp_path: Path) -> None:
    """ctx 切成 plan 后，把模式传给 checker 才会真的拦写操作。"""
    ctx = _ctx(tmp_path)
    await EnterPlanModeTool().call({"reason": "先调研"}, ctx)
    assert ctx.permission_mode == "plan"

    checker = PermissionChecker(mode=PermissionMode.DEFAULT)
    effective = parse_mode(ctx.permission_mode)
    denied = checker.check(_WriteTool(), {}, ctx, mode=effective)
    assert denied.behavior.name == "DENY"
    assert "plan 模式禁止写操作" in denied.reason

    allowed = checker.check(_ReadTool(), {}, ctx, mode=effective)
    assert allowed.behavior.name == "ALLOW"


@pytest.mark.asyncio
async def test_exit_plan_saves_restores_mode_and_emits(tmp_path: Path) -> None:
    """ExitPlanMode 落盘 PLAN.md、恢复原模式，并把预览推给桌面钩子。"""
    captured: list[tuple[str, str, str]] = []

    class _PlanUI:
        def plan_submitted(self, content: str, summary: str, path: str) -> None:
            captured.append((content, summary, path))

    ctx = _ctx(tmp_path, mode="default")
    ctx.ui = _PlanUI()  # type: ignore[assignment]
    await EnterPlanModeTool().call({"reason": "先看结构"}, ctx)
    result = await ExitPlanModeTool().call({"summary": "两步走"}, ctx)

    plan_path = tmp_path / ".jarvis" / "PLAN.md"
    assert plan_path.is_file()
    assert "先读代码" in plan_path.read_text(encoding="utf-8")
    assert ctx.permission_mode == "default"  # 恢复进入前的模式
    assert ".jarvis" in str(result.data)
    assert captured and captured[0][1] == "两步走"
    assert captured[0][2].endswith("PLAN.md")


@pytest.mark.asyncio
async def test_exit_plan_without_ui_hook_is_silent(tmp_path: Path) -> None:
    """没有 plan_submitted 的宿主（终端）不应因钩子缺失而失败。"""
    ctx = _ctx(tmp_path)
    ctx.ui = None  # type: ignore[assignment]
    await EnterPlanModeTool().call({}, ctx)
    result = await ExitPlanModeTool().call({"summary": "无 UI"}, ctx)
    assert ".jarvis" in str(result.data)
