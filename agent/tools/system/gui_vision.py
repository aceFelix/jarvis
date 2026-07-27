"""GUI 视觉定位工具 —— 基于模板匹配找到屏幕上的图标/按钮并点击。

P1 升级：让 Jarvis 摆脱对绝对坐标的依赖，实现"点击屏幕上这个图标"。
底层优先使用 pyautogui 内置的 locateOnScreen（依赖 Pillow），可选
opencv-python 时提供更稳定的匹配结果。

权限：点击操作默认 ASK。

@author aceFelix
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, PermissionMatcher, Tool


def _import_pyautogui():
    """延迟导入 pyautogui。"""
    import pyautogui  # type: ignore[import-untyped]
    return pyautogui


class VisualClickTool(Tool):
    """视觉点击工具 —— 用模板匹配在屏幕上找图标并点击。

    @author aceFelix
    """

    name = "VisualClick"
    description = (
        "在屏幕或指定区域内搜索目标图标/按钮图片，找到后自动点击其中心。"
        "用于点击文字/位置可能变化但图标不变的 UI 元素。"
        "默认会询问用户确认。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "template_path": {
                "type": "string",
                "description": "目标图标/按钮图片路径（PNG/JPEG）",
            },
            "region": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "可选：搜索区域 [left, top, width, height]",
                "minItems": 4,
                "maxItems": 4,
            },
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
            "confidence": {
                "type": "number",
                "description": "模板匹配置信度 0-1（默认 0.8）",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "timeout": {
                "type": "number",
                "description": "最长搜索秒数（默认 10）",
                "minimum": 0.5,
                "maximum": 60.0,
            },
            "interval": {
                "type": "number",
                "description": "轮询间隔秒数（默认 0.5）",
                "minimum": 0.1,
                "maximum": 5.0,
            },
            "move_duration": {
                "type": "number",
                "description": "光标移动到目标的耗时秒数（默认 0.0 瞬移）",
                "minimum": 0.0,
                "maximum": 5.0,
            },
        },
        "required": ["template_path"],
    }
    max_result_chars = 1_000

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        path = args.get("template_path", "")
        name = Path(path).name if path else "?"
        return PermissionResult.ask(f"点击图标: {name}")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        path = args.get("template_path")
        if not isinstance(path, str) or not path.strip():
            return ValidationResult.fail("template_path 必须是非空字符串")
        region = args.get("region")
        if region is not None:
            if not (isinstance(region, list) and len(region) == 4):
                return ValidationResult.fail("region 必须是长度 4 的整数数组 [left,top,width,height]")
            if not all(isinstance(v, int) and v >= 0 for v in region[:2]):
                return ValidationResult.fail("region 的 left/top 不能为负")
            if not all(isinstance(v, int) and v > 0 for v in region[2:]):
                return ValidationResult.fail("region 的 width/height 必须为正")
        btn = args.get("button", "left")
        if btn not in ("left", "right", "middle"):
            return ValidationResult.fail(f"button 非法: {btn}")
        return ValidationResult.pass_()

    def prepare_permission_matcher(self, args: dict[str, Any]) -> PermissionMatcher | None:
        path = args.get("template_path", "")
        return PermissionMatcher(tool_name="VisualClick", targets=[Path(path).name])

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            pyautogui = _import_pyautogui()
        except ImportError as e:
            return ToolResult.error(f"pyautogui 未安装: {e}")

        template_path = args["template_path"]
        region = args.get("region")
        region_tuple = tuple(region) if region else None
        button = args.get("button", "left")
        clicks = int(args.get("clicks", 1))
        confidence = float(args.get("confidence", 0.8))
        timeout = float(args.get("timeout", 10.0))
        interval = float(args.get("interval", 0.5))
        duration = float(args.get("move_duration", 0.0))

        if not Path(template_path).is_file():
            return ToolResult.error(f"模板图片不存在: {template_path}")

        # 第一步：等待并定位模板
        start = time.monotonic()
        attempts = 0
        box = None
        while time.monotonic() - start < timeout:
            attempts += 1
            try:
                box = pyautogui.locateOnScreen(
                    template_path, region=region_tuple, confidence=confidence
                )
            except TypeError:
                # 旧版 pyautogui 不支持 confidence
                try:
                    box = pyautogui.locateOnScreen(template_path, region=region_tuple)
                except Exception as e:
                    return ToolResult.error(f"视觉定位失败: {type(e).__name__}: {e}")
            except Exception as e:
                return ToolResult.error(f"视觉定位失败: {type(e).__name__}: {e}")

            if box is not None:
                break
            time.sleep(interval)

        if box is None:
            scope = f"区域 {region}" if region else "全屏"
            return ToolResult.error(
                f"视觉定位超时（{timeout}s）：未在{scope}找到 {template_path}"
            )

        center = pyautogui.center(box)
        x = int(center.x)
        y = int(center.y)

        # 第二步：点击中心坐标
        try:
            pyautogui.click(x=x, y=y, button=button, clicks=clicks, duration=duration)
        except pyautogui.FailSafeException:
            return ToolResult.error("触发 FAILSAFE，已中止")
        except Exception as e:
            return ToolResult.error(f"点击失败: {type(e).__name__}: {e}")

        return ToolResult.ok(
            f"已找到目标并点击，中心坐标 ({x},{y})，"
            f"匹配区域 ({box.left},{box.top},{box.width},{box.height})，"
            f"使用 {button}键 {clicks}次，搜索尝试 {attempts} 次"
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args:
            path = args.get("template_path", "")
            name = Path(path).name if path else "?"
            return f"视觉点击 {name}"
        return None
