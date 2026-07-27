"""窗口管理工具 —— 列出、激活、关闭、移动窗口。

阶段二「电脑操作能力」的窗口层。让模型能"帮我把记事本关了"
"切到浏览器窗口""把这个窗口挪到左边"。

基于 pygetwindow（pyautogui 的依赖，Windows/macOS 可用；Linux 支持有限）。
pywinauto 留给后续 UI 元素级操作（找控件、点按钮内部）。

工具:
- WindowList: 列出所有可见、有标题的顶层窗口。只读，自动放行。
- WindowFocus: 按标题模糊匹配激活（前置）窗口。ASK。
- WindowClose: 按标题模糊匹配关闭窗口。ASK（destructive）。
- WindowMove: 移动/调整窗口位置和大小。ASK。

标题匹配: 默认模糊包含匹配（大小写不敏感）。精确匹配用 exact=true。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, PermissionMatcher, Tool


def _import_pygetwindow():
    """延迟导入 pygetwindow。"""
    import pygetwindow as gw  # type: ignore[import-untyped]
    return gw


def _find_windows(gw, title: str, exact: bool) -> list:
    """按标题查找窗口。返回匹配的窗口对象列表。

    exact=True: 精确匹配；False: 包含匹配（大小写不敏感）。
    只返回有标题的窗口。
    """
    all_wins = gw.getAllWindows()
    if exact:
        return [w for w in all_wins if w.title == title]
    title_lower = title.lower()
    return [w for w in all_wins if w.title and title_lower in w.title.lower()]


def _fmt_window(w) -> str:
    """格式化窗口信息为一行。"""
    state_parts = []
    if w.isActive:
        state_parts.append("active")
    if w.isMinimized:
        state_parts.append("minimized")
    if w.isMaximized:
        state_parts.append("maximized")
    state = ",".join(state_parts) if state_parts else "normal"
    return (
        f"  - {w.title!r}  "
        f"rect=({w.left},{w.top},{w.right},{w.bottom}) "
        f"size={w.width}x{w.height}  [{state}]"
    )


class WindowListTool(Tool):
    name = "WindowList"
    description = (
        "列出当前所有可见、有标题的顶层窗口（标题/位置/尺寸/状态）。"
        "用于操作前了解屏幕上有哪些窗口。只读，自动放行。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "description": "可选: 只返回标题包含此串的窗口（不填=全部）",
            },
        },
    }
    max_result_chars = 6_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读操作")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            gw = _import_pygetwindow()
        except ImportError as e:
            return ToolResult.error(f"pygetwindow 未安装: {e}")

        try:
            all_wins = gw.getAllWindows()
        except Exception as e:
            return ToolResult.error(f"枚举窗口失败: {type(e).__name__}: {e}")

        # 过滤: 有标题
        wins = [w for w in all_wins if w.title and w.title.strip()]

        flt = args.get("filter")
        if flt:
            flt_lower = flt.lower()
            wins = [w for w in wins if flt_lower in w.title.lower()]

        if not wins:
            return ToolResult.ok("没有匹配的窗口")

        lines = [f"共 {len(wins)} 个窗口:"]
        for w in wins:
            lines.append(_fmt_window(w))

        body = "\n".join(lines)
        from agent.tools.base import truncate_for_llm
        return ToolResult.ok(truncate_for_llm(body, self.max_result_chars))

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        return "列出窗口"


class WindowFocusTool(Tool):
    name = "WindowFocus"
    description = (
        "按标题激活（前置）窗口。默认模糊包含匹配；精确匹配用 exact=true。"
        "若匹配到多个窗口，激活第一个。默认会询问用户确认。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "窗口标题（模糊匹配）"},
            "exact": {"type": "boolean", "description": "是否精确匹配标题（默认 false 模糊）"},
        },
        "required": ["title"],
    }
    max_result_chars = 1_000

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.ask(f"激活窗口: {args.get('title')!r}")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        if not args.get("title", "").strip():
            return ValidationResult.fail("title 不能为空")
        return ValidationResult.pass_()

    def prepare_permission_matcher(self, args: dict[str, Any]) -> PermissionMatcher | None:
        return PermissionMatcher(tool_name="WindowFocus", targets=[args.get("title", "")])

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            gw = _import_pygetwindow()
        except ImportError as e:
            return ToolResult.error(f"pygetwindow 未安装: {e}")

        title = args["title"]
        exact = bool(args.get("exact", False))

        matches = _find_windows(gw, title, exact)
        if not matches:
            return ToolResult.error(f"没找到标题匹配 {title!r} 的窗口")
        if len(matches) > 1:
            # 多个匹配，列出供模型参考，激活第一个
            pass

        w = matches[0]
        try:
            if w.isMinimized:
                w.restore()
            w.activate()
        except Exception as e:
            return ToolResult.error(f"激活窗口失败: {type(e).__name__}: {e}")

        extra = f"（共匹配 {len(matches)} 个，已激活第一个）" if len(matches) > 1 else ""
        return ToolResult.ok(f"已激活窗口: {w.title!r}{extra}")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args:
            return f"激活窗口 {args.get('title')!r}"
        return None


class WindowCloseTool(Tool):
    name = "WindowClose"
    description = (
        "按标题关闭窗口。默认模糊匹配。关闭是不可逆操作，默认会询问用户确认。"
        "若匹配到多个窗口，关闭第一个。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "窗口标题（模糊匹配）"},
            "exact": {"type": "boolean", "description": "是否精确匹配（默认 false）"},
        },
        "required": ["title"],
    }
    max_result_chars = 1_000

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.ask(f"关闭窗口: {args.get('title')!r}")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        if not args.get("title", "").strip():
            return ValidationResult.fail("title 不能为空")
        return ValidationResult.pass_()

    def prepare_permission_matcher(self, args: dict[str, Any]) -> PermissionMatcher | None:
        return PermissionMatcher(tool_name="WindowClose", targets=[args.get("title", "")])

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            gw = _import_pygetwindow()
        except ImportError as e:
            return ToolResult.error(f"pygetwindow 未安装: {e}")

        title = args["title"]
        exact = bool(args.get("exact", False))

        matches = _find_windows(gw, title, exact)
        if not matches:
            return ToolResult.error(f"没找到标题匹配 {title!r} 的窗口")

        w = matches[0]
        try:
            w.close()
        except Exception as e:
            return ToolResult.error(f"关闭窗口失败: {type(e).__name__}: {e}")

        extra = f"（共匹配 {len(matches)} 个，已关闭第一个）" if len(matches) > 1 else ""
        return ToolResult.ok(f"已关闭窗口: {w.title!r}{extra}")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args:
            return f"关闭窗口 {args.get('title')!r}"
        return None


class WindowRectTool(Tool):
    """获取窗口矩形区域工具 —— 返回窗口的屏幕绝对坐标与尺寸。

    P1 升级新增：为多窗口协调和窗口相对坐标点击提供基础数据。

    @author aceFelix
    """

    name = "WindowRect"
    description = (
        "获取指定窗口的屏幕绝对坐标和尺寸（left, top, width, height）。"
        "用于把窗口内相对坐标转换为屏幕绝对坐标。只读，自动放行。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "窗口标题（模糊匹配）"},
            "exact": {"type": "boolean", "description": "是否精确匹配标题（默认 false 模糊）"},
        },
        "required": ["title"],
    }
    max_result_chars = 1_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读操作")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        if not args.get("title", "").strip():
            return ValidationResult.fail("title 不能为空")
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            gw = _import_pygetwindow()
        except ImportError as e:
            return ToolResult.error(f"pygetwindow 未安装: {e}")

        title = args["title"]
        exact = bool(args.get("exact", False))

        matches = _find_windows(gw, title, exact)
        if not matches:
            return ToolResult.error(f"没找到标题匹配 {title!r} 的窗口")

        w = matches[0]
        try:
            rect = {
                "left": int(w.left),
                "top": int(w.top),
                "width": int(w.width),
                "height": int(w.height),
                "right": int(w.right),
                "bottom": int(w.bottom),
                "title": w.title,
            }
        except Exception as e:
            return ToolResult.error(f"获取窗口信息失败: {type(e).__name__}: {e}")

        extra = f"（共匹配 {len(matches)} 个，返回第一个）" if len(matches) > 1 else ""
        return ToolResult.ok(
            f"窗口 {w.title!r} 矩形信息: {rect}{extra}"
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        return f"获取窗口 {args.get('title')!r} 区域" if args else None


class WindowClickTool(Tool):
    """窗口内点击工具 —— 基于窗口相对坐标点击。

    P1 升级新增：解决窗口移动后绝对坐标失效的问题。
    先获取窗口绝对坐标，再把相对坐标转换为屏幕坐标后点击。

    @author aceFelix
    """

    name = "WindowClick"
    description = (
        "在指定窗口内部按相对坐标点击。窗口标题模糊匹配，"
        "x/y 是相对于窗口左上角的坐标。窗口移动后仍可正确点击。"
        "默认会询问用户确认。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "窗口标题（模糊匹配）"},
            "x": {"type": "integer", "description": "窗口内相对 x 坐标", "minimum": 0},
            "y": {"type": "integer", "description": "窗口内相对 y 坐标", "minimum": 0},
            "exact": {"type": "boolean", "description": "是否精确匹配标题（默认 false）"},
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "鼠标按键，默认 left",
            },
            "clicks": {
                "type": "integer",
                "description": "点击次数，1=单击 2=双击",
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
        "required": ["title", "x", "y"],
    }
    max_result_chars = 1_000

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.ask(
            f"在窗口 {args.get('title')!r} 内点击 ({args.get('x')},{args.get('y')})"
        )

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        if not args.get("title", "").strip():
            return ValidationResult.fail("title 不能为空")
        for key in ("x", "y"):
            val = args.get(key)
            try:
                int(val)
            except (TypeError, ValueError):
                return ValidationResult.fail(f"{key} 必须是整数: {val!r}")
        btn = args.get("button", "left")
        if btn not in ("left", "right", "middle"):
            return ValidationResult.fail(f"button 非法: {btn}")
        return ValidationResult.pass_()

    def prepare_permission_matcher(self, args: dict[str, Any]) -> PermissionMatcher | None:
        return PermissionMatcher(
            tool_name="WindowClick",
            targets=[args.get("title", ""), f"{args.get('x')},{args.get('y')}"],
        )

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            gw = _import_pygetwindow()
            import pyautogui  # type: ignore[import-untyped]
        except ImportError as e:
            return ToolResult.error(f"依赖未安装: {e}")

        title = args["title"]
        exact = bool(args.get("exact", False))
        rel_x = int(args["x"])
        rel_y = int(args["y"])
        button = args.get("button", "left")
        clicks = int(args.get("clicks", 1))
        duration = float(args.get("move_duration", 0.0))

        matches = _find_windows(gw, title, exact)
        if not matches:
            return ToolResult.error(f"没找到标题匹配 {title!r} 的窗口")

        w = matches[0]
        try:
            if w.isMinimized:
                w.restore()
            abs_x = int(w.left) + rel_x
            abs_y = int(w.top) + rel_y
            pyautogui.click(x=abs_x, y=abs_y, button=button, clicks=clicks, duration=duration)
        except pyautogui.FailSafeException:
            return ToolResult.error("触发 FAILSAFE，已中止")
        except Exception as e:
            return ToolResult.error(f"窗口内点击失败: {type(e).__name__}: {e}")

        return ToolResult.ok(
            f"已在窗口 {w.title!r} 的相对坐标 ({rel_x},{rel_y}) "
            f"对应屏幕坐标 ({abs_x},{abs_y}) 执行 {button}键 {clicks}次点击"
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args:
            return f"在窗口 {args.get('title')!r} 内点击 ({args.get('x')},{args.get('y')})"
        return None


class WindowMoveTool(Tool):
    name = "WindowMove"
    description = (
        "移动窗口到指定位置，可同时调整大小。按标题模糊匹配。"
        "默认会询问用户确认。坐标系: 原点在屏幕左上角。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "窗口标题（模糊匹配）"},
            "x": {"type": "integer", "description": "窗口左上角目标 x 坐标", "minimum": -32000},
            "y": {"type": "integer", "description": "窗口左上角目标 y 坐标", "minimum": -32000},
            "width": {"type": "integer", "description": "可选: 新宽度（不填=保持原宽）", "minimum": 1},
            "height": {"type": "integer", "description": "可选: 新高度（不填=保持原高）", "minimum": 1},
            "exact": {"type": "boolean", "description": "是否精确匹配标题（默认 false）"},
        },
        "required": ["title", "x", "y"],
    }
    max_result_chars = 1_000

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.ask(
            f"移动窗口 {args.get('title')!r} 到 ({args.get('x')},{args.get('y')})"
        )

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        if not args.get("title", "").strip():
            return ValidationResult.fail("title 不能为空")
        if not isinstance(args.get("x"), int) or not isinstance(args.get("y"), int):
            return ValidationResult.fail("x/y 必须是整数")
        return ValidationResult.pass_()

    def prepare_permission_matcher(self, args: dict[str, Any]) -> PermissionMatcher | None:
        return PermissionMatcher(
            tool_name="WindowMove",
            targets=[args.get("title", ""), f"{args.get('x')},{args.get('y')}"],
        )

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            gw = _import_pygetwindow()
        except ImportError as e:
            return ToolResult.error(f"pygetwindow 未安装: {e}")

        title = args["title"]
        exact = bool(args.get("exact", False))
        x = int(args["x"])
        y = int(args["y"])
        width = args.get("width")
        height = args.get("height")

        matches = _find_windows(gw, title, exact)
        if not matches:
            return ToolResult.error(f"没找到标题匹配 {title!r} 的窗口")

        w = matches[0]
        try:
            if w.isMinimized or w.isMaximized:
                w.restore()
            if width is not None and height is not None:
                w.moveTo(x, y)
                w.resizeTo(int(width), int(height))
            else:
                w.moveTo(x, y)
        except Exception as e:
            return ToolResult.error(f"移动窗口失败: {type(e).__name__}: {e}")

        size_info = f" 尺寸={w.width}x{w.height}" if (width and height) else ""
        return ToolResult.ok(
            f"已移动窗口 {w.title!r} 到 ({x},{y}){size_info}"
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args:
            return f"移动窗口 {args.get('title')!r}"
        return None
