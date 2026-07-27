"""GUI 工具单元测试。

P1 升级：覆盖新增 GUI 工具的输入校验、权限、坐标转换等核心逻辑。
由于 GUI 工具依赖 pyautogui/pygetwindow 且会操作真实屏幕，
这里主要测试不依赖真实屏幕的部分；涉及屏幕调用的用 unittest.mock 模拟。

@author aceFelix
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior


@pytest.fixture
def dummy_ctx() -> ToolContext:
    """构造一个空 ToolContext 用于测试校验和权限方法。"""
    return ToolContext(
        workdir=str(Path.cwd()),
        messages=[],
    )


def _make_dummy_module(name: str, attrs: dict[str, Any]) -> ModuleType:
    """构造一个假模块，用于模拟 pyautogui / pygetwindow。"""
    mod = ModuleType(name)
    for key, val in attrs.items():
        setattr(mod, key, val)
    return mod


# -----------------------------------------------------------------------------
# MouseDragTool
# -----------------------------------------------------------------------------


def test_mouse_drag_validation_ok(dummy_ctx: ToolContext) -> None:
    """MouseDrag 合法输入应通过校验。"""
    from agent.tools.system.mouse import MouseDragTool

    tool = MouseDragTool()
    result = tool.validate_input(
        {"start_x": 100, "start_y": 200, "end_x": 300, "end_y": 400},
        dummy_ctx,
    )
    assert result.ok is True


def test_mouse_drag_validation_invalid_button(dummy_ctx: ToolContext) -> None:
    """MouseDrag 非法 button 应失败。"""
    from agent.tools.system.mouse import MouseDragTool

    tool = MouseDragTool()
    result = tool.validate_input(
        {
            "start_x": 100,
            "start_y": 200,
            "end_x": 300,
            "end_y": 400,
            "button": "invalid",
        },
        dummy_ctx,
    )
    assert result.ok is False
    assert "button" in result.message.lower()


def test_mouse_drag_permission(dummy_ctx: ToolContext) -> None:
    """MouseDrag 权限应为 ASK，并包含起点终点。"""
    from agent.tools.system.mouse import MouseDragTool

    tool = MouseDragTool()
    perm = tool.check_permissions(
        {"start_x": 100, "start_y": 200, "end_x": 300, "end_y": 400},
        dummy_ctx,
    )
    assert perm.behavior == PermissionBehavior.ASK
    assert perm.reason is not None
    assert "100" in perm.reason and "300" in perm.reason


async def test_mouse_drag_call_success(dummy_ctx: ToolContext) -> None:
    """MouseDrag 在模拟 pyautogui 下应成功执行。"""
    from agent.tools.system.mouse import MouseDragTool

    fake_pyautogui = MagicMock()
    fake_pyautogui.FailSafeException = Exception

    tool = MouseDragTool()
    with patch("agent.tools.system.mouse._import_pyautogui", return_value=fake_pyautogui):
        result = await tool.call(
            {
                "start_x": 10,
                "start_y": 20,
                "end_x": 30,
                "end_y": 40,
                "button": "left",
                "duration": 0.2,
            },
            dummy_ctx,
        )

    assert result.is_error is False
    assert "拖拽" in result.data
    fake_pyautogui.moveTo.assert_any_call(10, 20)
    fake_pyautogui.mouseDown.assert_called_once_with(button="left")
    fake_pyautogui.moveTo.assert_any_call(30, 40, duration=0.2)
    fake_pyautogui.mouseUp.assert_called_once_with(button="left")


async def test_mouse_drag_failsafe_releases_button(dummy_ctx: ToolContext) -> None:
    """MouseDrag 触发 FAILSAFE 时应释放鼠标按键。"""
    from agent.tools.system.mouse import MouseDragTool

    fake_pyautogui = MagicMock()
    fake_pyautogui.FailSafeException = type("FailSafeException", (Exception,), {})
    fake_pyautogui.moveTo.side_effect = fake_pyautogui.FailSafeException()

    tool = MouseDragTool()
    with patch("agent.tools.system.mouse._import_pyautogui", return_value=fake_pyautogui):
        result = await tool.call(
            {"start_x": 10, "start_y": 20, "end_x": 30, "end_y": 40},
            dummy_ctx,
        )

    assert result.is_error is True
    assert "FAILSAFE" in result.data
    fake_pyautogui.mouseUp.assert_called_once_with(button="left")


# -----------------------------------------------------------------------------
# WaitForTool
# -----------------------------------------------------------------------------


def test_wait_for_validation_region(dummy_ctx: ToolContext) -> None:
    """WaitFor region 参数校验。"""
    from agent.tools.system.screen import WaitForTool

    tool = WaitForTool()
    # 合法
    assert tool.validate_input(
        {"template_path": "/tmp/a.png", "region": [0, 0, 100, 100]},
        dummy_ctx,
    ).ok

    # 长度不对
    result = tool.validate_input(
        {"template_path": "/tmp/a.png", "region": [0, 0, 100]},
        dummy_ctx,
    )
    assert result.ok is False


async def test_wait_for_template_not_found(dummy_ctx: ToolContext) -> None:
    """WaitFor 模板文件不存在时应立即返回错误。"""
    from agent.tools.system.screen import WaitForTool

    tool = WaitForTool()
    with patch("agent.tools.system.screen._import_pyautogui", return_value=MagicMock()):
        result = await tool.call(
            {"template_path": "/definitely/not/exists.png", "timeout": 1, "interval": 0.1},
            dummy_ctx,
        )
    assert result.is_error is True
    assert "不存在" in result.data


# -----------------------------------------------------------------------------
# WindowRectTool / WindowClickTool
# -----------------------------------------------------------------------------


def _fake_window(left: int, top: int, width: int, height: int, title: str) -> MagicMock:
    """构造一个模拟窗口对象。"""
    w = MagicMock()
    w.left = left
    w.top = top
    w.width = width
    w.height = height
    w.right = left + width
    w.bottom = top + height
    w.title = title
    w.isMinimized = False
    return w


async def test_window_rect_call(dummy_ctx: ToolContext) -> None:
    """WindowRect 应返回窗口绝对坐标。"""
    from agent.tools.system.window import WindowRectTool

    fake_gw = MagicMock()
    fake_gw.getAllWindows.return_value = [
        _fake_window(100, 200, 800, 600, "Chrome"),
    ]
    fake_module = _make_dummy_module("pygetwindow", {"getAllWindows": fake_gw.getAllWindows})

    tool = WindowRectTool()
    with patch("agent.tools.system.window._import_pygetwindow", return_value=fake_module):
        result = await tool.call({"title": "Chrome"}, dummy_ctx)

    assert result.is_error is False
    assert "100" in result.data and "200" in result.data
    assert "800" in result.data and "600" in result.data


async def test_window_click_coordinate_conversion(dummy_ctx: ToolContext) -> None:
    """WindowClick 应把窗口相对坐标转换为屏幕绝对坐标后点击。"""
    from agent.tools.system.window import WindowClickTool

    fake_gw = MagicMock()
    fake_gw.getAllWindows.return_value = [
        _fake_window(100, 200, 800, 600, "Chrome"),
    ]
    fake_module = _make_dummy_module("pygetwindow", {"getAllWindows": fake_gw.getAllWindows})
    fake_pyautogui = MagicMock()
    fake_pyautogui.FailSafeException = Exception

    tool = WindowClickTool()
    with patch("agent.tools.system.window._import_pygetwindow", return_value=fake_module):
        with patch.dict(sys.modules, {"pyautogui": fake_pyautogui}):
            result = await tool.call({"title": "Chrome", "x": 50, "y": 60}, dummy_ctx)

    assert result.is_error is False
    assert "(150,260)" in result.data.replace(" ", "")
    fake_pyautogui.click.assert_called_once_with(
        x=150, y=260, button="left", clicks=1, duration=0.0
    )


# -----------------------------------------------------------------------------
# VisualClickTool
# -----------------------------------------------------------------------------


async def test_visual_click_template_missing(dummy_ctx: ToolContext) -> None:
    """VisualClick 模板不存在时应返回错误。"""
    from agent.tools.system.gui_vision import VisualClickTool

    tool = VisualClickTool()
    with patch("agent.tools.system.gui_vision._import_pyautogui", return_value=MagicMock()):
        result = await tool.call(
            {"template_path": "/definitely/not/exists.png"},
            dummy_ctx,
        )
    assert result.is_error is True
    assert "不存在" in result.data


def test_visual_click_validation(dummy_ctx: ToolContext) -> None:
    """VisualClick 输入校验。"""
    from agent.tools.system.gui_vision import VisualClickTool

    tool = VisualClickTool()
    assert tool.validate_input({"template_path": "/tmp/a.png"}, dummy_ctx).ok
    assert tool.validate_input(
        {"template_path": "/tmp/a.png", "button": "right"}, dummy_ctx
    ).ok

    result = tool.validate_input({"template_path": ""}, dummy_ctx)
    assert result.ok is False

    result = tool.validate_input({"template_path": "/tmp/a.png", "button": "foo"}, dummy_ctx)
    assert result.ok is False
