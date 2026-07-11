"""屏幕感知工具 —— 让模型"看见"屏幕。

阶段二「电脑操作能力」的眼睛。没有它，模型只能瞎点坐标；有了它，模型能先看清
屏幕布局再决定点哪里、输入什么。

包含两个工具:
- GetScreenSize: 返回屏幕分辨率。让模型知道坐标范围（x∈[0,width), y∈[0,height)）。
- ScreenShot: 全屏截图。**多模态视觉已接入**——截图会同时作为 image content block
  回传给支持视觉的 LLM（Claude / GPT-4o），模型能真正"看"到屏幕内容，而不只是
  拿到一个文件路径。默认缩放到最长边 1280px 并转 JPEG（quality 85）以节省 token，
  可通过 format/max_size 参数调整。

权限: 两者都是只读操作（不改任何状态），自动放行。
"""

from __future__ import annotations

import base64
import io
import platform
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.context import ToolContext
from agent.core.message import ImageContent
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool


def _import_pyautogui():
    import pyautogui  # type: ignore[import-untyped]
    return pyautogui


def _shots_dir() -> Path:
    """截图保存目录: 系统临时目录下的 jarvis-shots。"""
    d = Path(tempfile.gettempdir()) / "jarvis-shots"
    d.mkdir(parents=True, exist_ok=True)
    return d


class GetScreenSizeTool(Tool):
    name = "GetScreenSize"
    description = (
        "返回主屏幕分辨率（宽 高）。操作鼠标前先调用本工具，确认坐标范围: "
        "x∈[0, 宽), y∈[0, 高)。只读，自动放行。"
    )
    input_schema: JSONSchema = {"type": "object", "properties": {}}
    max_result_chars = 500

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
            pyautogui = _import_pyautogui()
        except ImportError as e:
            return ToolResult.error(f"pyautogui 未安装: {e}")

        try:
            size = pyautogui.size()  # Size(width, height)
            w, h = int(size.width), int(size.height)
        except Exception as e:
            return ToolResult.error(f"获取分辨率失败: {type(e).__name__}: {e}")

        return ToolResult.ok(
            f"主屏幕分辨率: {w} x {h}\n"
            f"坐标范围: x∈[0,{w}), y∈[0,{h})\n"
            f"原点(0,0)在左上角"
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        return "查询屏幕分辨率"


class ScreenShotTool(Tool):
    name = "ScreenShot"
    description = (
        "全屏截图。截图会作为图片直接回传给你（多模态），你能真正'看到'屏幕内容——"
        "按钮、文字、弹窗、窗口布局都一目了然，据此判断该点哪里、输入什么。"
        "操作电脑前务必先截图看清当前屏幕。只读，自动放行。"
        "可选参数 region 截取局部区域 [left, top, width, height]。"
        "可选参数 format（jpeg/png，默认 jpeg 省 token）、max_size（最长边像素，默认 1280，0=不缩放）。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "region": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "局部截图区域 [left, top, width, height]（不填=全屏）",
                "minItems": 4,
                "maxItems": 4,
            },
            "path": {
                "type": "string",
                "description": "保存路径（默认存临时目录 jarvis-shots/screenshot_时间戳.png）",
            },
            "format": {
                "type": "string",
                "enum": ["jpeg", "png"],
                "description": "回传给模型的图片格式。jpeg 体积小省 token（默认），png 无损。",
            },
            "max_size": {
                "type": "integer",
                "description": "回传图片的最长边像素，超出则等比缩放（默认 1280，兼顾清晰度与成本；设 0 表示不缩放）",
            },
        },
    }
    max_result_chars = 1_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读操作")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        region = args.get("region")
        if region is not None:
            if not (isinstance(region, list) and len(region) == 4):
                return ValidationResult.fail("region 必须是长度 4 的整数数组 [left,top,width,height]")
            if not all(isinstance(v, int) and v >= 0 for v in region[:2]):
                return ValidationResult.fail("region 的 left/top 不能为负")
            if not all(isinstance(v, int) and v > 0 for v in region[2:]):
                return ValidationResult.fail("region 的 width/height 必须为正")
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            pyautogui = _import_pyautogui()
        except ImportError as e:
            return ToolResult.error(f"pyautogui 未安装: {e}")

        region = args.get("region")
        region_tuple = tuple(region) if region else None

        # 保存路径
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if args.get("path"):
            save_path = Path(args["path"])
            save_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            save_path = _shots_dir() / f"screenshot_{ts}.png"

        try:
            img = pyautogui.screenshot(region=region_tuple)
            img.save(str(save_path), format="PNG")
        except Exception as e:
            return ToolResult.error(f"截图失败: {type(e).__name__}: {e}")

        w, h = img.size
        scope = "局部" if region else "全屏"

        # 多模态: 把截图编码为图片块回传给支持视觉的 LLM，模型能直接"看到"屏幕。
        fmt = args.get("format", "jpeg")
        max_size = args.get("max_size", 1280)
        images: list[ImageContent] = []
        encode_note = ""
        try:
            images = [self._encode_image(img, fmt, max_size)]
        except Exception as e:
            # 编码失败不致命: 降级为纯路径文本模式（模型仍可凭路径 + 用户描述操作）
            encode_note = f"\n（图片回传失败，降级为纯路径模式: {type(e).__name__})"

        summary = (
            f"已{scope}截图\n"
            f"路径: {save_path}\n"
            f"尺寸: {w} x {h}\n"
            f"时间: {ts}\n"
            f"OS: {platform.system()}\n"
            f"图片已回传: {'是' if images else '否'}"
            f"（{fmt}, 最长边<={max_size or '原图'}）{encode_note}"
        )
        return ToolResult.ok(summary, images=images)

    @staticmethod
    def _encode_image(img: Any, fmt: str, max_size: int) -> ImageContent:
        """把 PIL Image 缩放并编码为 base64 图片块。

        - 默认缩放到最长边 max_size（保持比例），兼顾清晰度与 token 成本。
          Claude 建议图片长边 <= 1568px，1280 是稳妥默认值。
        - JPEG 体积远小于 PNG（屏幕内容偏照片式），默认用 JPEG quality 85。
        - JPEG 不支持透明通道，RGBA/LA/P 模式自动转 RGB。

        Args:
            img: pyautogui.screenshot 返回的 PIL.Image.Image。
            fmt: "jpeg" 或 "png"。
            max_size: 最长边像素上限，0 表示不缩放。
        """
        work = img.copy()
        if max_size and max(work.size) > max_size:
            work.thumbnail((max_size, max_size))

        if fmt == "png":
            media_type = "image/png"
            pil_format = "PNG"
        else:
            media_type = "image/jpeg"
            pil_format = "JPEG"
            if work.mode in ("RGBA", "LA", "P"):
                work = work.convert("RGB")

        buf = io.BytesIO()
        save_kwargs: dict[str, Any] = {}
        if pil_format == "JPEG":
            save_kwargs["quality"] = 85
        work.save(buf, format=pil_format, **save_kwargs)
        data = base64.b64encode(buf.getvalue()).decode("ascii")
        return ImageContent(data=data, media_type=media_type)

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        return "截图" + ("（局部）" if args and args.get("region") else "（全屏）")
