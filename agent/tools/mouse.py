"""鼠标控制工具 —— 操作鼠标光标。

阶段二「电脑操作能力」的核心工具之一。让模型能点击屏幕上的按钮、图标，
实现"帮我把这个窗口关了""点那个按钮"这类指令。

基于 pyautogui 实现。坐标系统: 原点(0,0)在屏幕左上角，x 向右增，y 向下增。
pyautogui 的 FAILSAFE 默认开启: 鼠标移到左上角会抛异常，作为失控保护。

权限: 所有鼠标操作默认 ASK（操作 GUI 属于不可逆副作用）。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, PermissionMatcher, Tool


def _import_pyautogui():
    """延迟导入 pyautogui。未安装时抛 ImportError，由调用方转成 ToolResult.error。"""
    import pyautogui  # type: ignore[import-untyped]
    return pyautogui


def _validate_xy(x: Any, y: Any) -> str | None:
    """校验坐标: 非负整数。返回错误信息或 None。"""
    try:
        xi, yi = int(x), int(y)
    except (TypeError, ValueError):
        return f"坐标必须是整数: x={x!r}, y={y!r}"
    if xi < 0 or yi < 0:
        return f"坐标不能为负: x={xi}, y={yi}"
    return None


class MouseClickTool(Tool):
    name = "MouseClick"
    description = (
        "在屏幕指定坐标点击鼠标。坐标原点(0,0)在左上角，x向右增，y向下增。"
        "默认左键单击；可设双击、右键。操作前请先用 ScreenShot 看清屏幕布局。"
        "默认会询问用户确认。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "点击的 x 坐标（屏幕像素）", "minimum": 0},
            "y": {"type": "integer", "description": "点击的 y 坐标（屏幕像素）", "minimum": 0},
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "鼠标按键，默认 left",
            },
            "clicks": {
                "type": "integer",
                "description": "点击次数，1=单击(默认) 2=双击",
                "minimum": 1,
                "maximum": 3,
            },
            "move_duration": {
                "type": "number",
                "description": "光标移动到目标的耗时秒数（默认 0.0 瞬移）",
                "minimum": 0.0,
                "maximum": 5.0,
            },
        },
        "required": ["x", "y"],
    }
    max_result_chars = 2_000

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        # GUI 操作绝不并行（会抢光标）
        return False

    def is_destructive(self, args: dict[str, Any]) -> bool:
        # 点击可能触发任意动作，视为不可逆
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        # 操作电脑一律询问
        return PermissionResult.ask(f"点击 ({args.get('x')},{args.get('y')}) {args.get('button', 'left')}键")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        err = _validate_xy(args.get("x"), args.get("y"))
        if err:
            return ValidationResult.fail(err)
        btn = args.get("button", "left")
        if btn not in ("left", "right", "middle"):
            return ValidationResult.fail(f"button 非法: {btn}")
        return ValidationResult.pass_()

    def prepare_permission_matcher(self, args: dict[str, Any]) -> PermissionMatcher | None:
        # 让规则 "MouseClick" 能命中；坐标作为 target 便于细粒度规则
        return PermissionMatcher(
            tool_name="MouseClick",
            targets=[f"{args.get('x')},{args.get('y')}"],
        )

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            pyautogui = _import_pyautogui()
        except ImportError as e:
            return ToolResult.error(f"pyautogui 未安装: {e}")

        x = int(args["x"])
        y = int(args["y"])
        button = args.get("button", "left")
        clicks = int(args.get("clicks", 1))
        duration = float(args.get("move_duration", 0.0))

        try:
            pyautogui.click(x=x, y=y, button=button, clicks=clicks, duration=duration)
        except pyautogui.FailSafeException:
            return ToolResult.error("触发 FAILSAFE（光标触达左上角），已中止")
        except Exception as e:
            return ToolResult.error(f"点击失败: {type(e).__name__}: {e}")

        return ToolResult.ok(
            f"已在 ({x},{y}) 执行 {button}键 {clicks}次点击"
            + (f"，移动耗时 {duration}s" if duration else "")
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args:
            return f"点击 ({args.get('x')},{args.get('y')})"
        return None


class MouseMoveTool(Tool):
    name = "MouseMove"
    description = (
        "把鼠标光标移动到屏幕指定坐标（不点击）。"
        "用于悬停查看提示、或为后续点击定位。默认会询问用户确认。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "目标 x 坐标", "minimum": 0},
            "y": {"type": "integer", "description": "目标 y 坐标", "minimum": 0},
            "duration": {
                "type": "number",
                "description": "移动耗时秒数（默认 0.0 瞬移；设 0.3 可见平滑移动）",
                "minimum": 0.0,
                "maximum": 5.0,
            },
        },
        "required": ["x", "y"],
    }
    max_result_chars = 1_000

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.ask(f"移动光标到 ({args.get('x')},{args.get('y')})")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        err = _validate_xy(args.get("x"), args.get("y"))
        if err:
            return ValidationResult.fail(err)
        return ValidationResult.pass_()

    def prepare_permission_matcher(self, args: dict[str, Any]) -> PermissionMatcher | None:
        return PermissionMatcher(tool_name="MouseMove", targets=[f"{args.get('x')},{args.get('y')}"])

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            pyautogui = _import_pyautogui()
        except ImportError as e:
            return ToolResult.error(f"pyautogui 未安装: {e}")

        x = int(args["x"])
        y = int(args["y"])
        duration = float(args.get("duration", 0.0))

        try:
            pyautogui.moveTo(x, y, duration=duration)
        except pyautogui.FailSafeException:
            return ToolResult.error("触发 FAILSAFE，已中止")
        except Exception as e:
            return ToolResult.error(f"移动失败: {type(e).__name__}: {e}")

        return ToolResult.ok(f"光标已移至 ({x},{y})")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args:
            return f"移动光标到 ({args.get('x')},{args.get('y')})"
        return None


class MouseScrollTool(Tool):
    name = "MouseScroll"
    description = (
        "滚动鼠标滚轮。clicks 为正向上滚，为负向下滚。"
        "可在当前光标位置滚，也可指定坐标。默认会询问用户确认。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "clicks": {
                "type": "integer",
                "description": "滚动量，正=向上 负=向下（如 3 或 -5）",
            },
            "x": {"type": "integer", "description": "可选: 在此 x 坐标滚（不填=当前位置）", "minimum": 0},
            "y": {"type": "integer", "description": "可选: 在此 y 坐标滚（不填=当前位置）", "minimum": 0},
        },
        "required": ["clicks"],
    }
    max_result_chars = 1_000

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.ask(f"滚动 {args.get('clicks')} 格")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        try:
            c = int(args.get("clicks"))
        except (TypeError, ValueError):
            return ValidationResult.fail(f"clicks 必须是整数: {args.get('clicks')!r}")
        if c == 0:
            return ValidationResult.fail("clicks 不能为 0")
        return ValidationResult.pass_()

    def prepare_permission_matcher(self, args: dict[str, Any]) -> PermissionMatcher | None:
        return PermissionMatcher(tool_name="MouseScroll", targets=[str(args.get("clicks"))])

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            pyautogui = _import_pyautogui()
        except ImportError as e:
            return ToolResult.error(f"pyautogui 未安装: {e}")

        clicks = int(args["clicks"])
        x = args.get("x")
        y = args.get("y")
        kw: dict[str, Any] = {}
        if x is not None and y is not None:
            kw["x"] = int(x)
            kw["y"] = int(y)

        try:
            pyautogui.scroll(clicks, **kw)
        except pyautogui.FailSafeException:
            return ToolResult.error("触发 FAILSAFE，已中止")
        except Exception as e:
            return ToolResult.error(f"滚动失败: {type(e).__name__}: {e}")

        direction = "上" if clicks > 0 else "下"
        return ToolResult.ok(f"向{direction}滚动 {abs(clicks)} 格")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args:
            return f"滚动 {args.get('clicks')} 格"
        return None
