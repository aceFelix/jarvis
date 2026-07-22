"""CLI-Anything harness 工具封装。

每个 harness 对应一个 ``CliAnythingTool`` 实例，名字为 ``cli_anything__<id>``。
执行时调用 harness 入口命令，返回 stdout / stderr / exit_code。

增强能力：
- 默认 ASK 权限（外部命令，不可信）。
- 不通过 shell 执行，避免注入。
- 超时强制终止。
- 自动识别 JSON 输出中的图片路径，作为 ``ImageContent`` 回传，
  让支持视觉的 LLM 直接看到 harness 生成的图像。

@author aceFelix
"""

from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path
from typing import Any

from agent.cli_anything.runner import run_harness
from agent.cli_anything.schema import Harness
from agent.core.context import ToolContext
from agent.core.message import ImageContent
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import JSONSchema, Tool

logger = logging.getLogger(__name__)

# 支持的图片扩展名（小写）
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 图片编码最长边限制（与截图工具保持一致）
_IMAGE_MAX_SIZE = 1280


def _build_input_schema(harness: Harness) -> JSONSchema:
    """根据 HarnessArg 生成 JSON Schema。"""
    properties: dict[str, JSONSchema] = {}
    required: list[str] = []

    for arg in harness.args:
        prop: JSONSchema = {
            "type": arg.type if arg.type in ("string", "integer", "number", "boolean", "array") else "string",
            "description": arg.description,
        }
        if arg.enum:
            prop["enum"] = arg.enum
        if arg.default is not None:
            prop["default"] = arg.default
        properties[arg.name] = prop
        if arg.required:
            required.append(arg.name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _extract_image_paths(obj: Any, workdir: str = "") -> list[Path]:
    """递归从 JSON 对象中提取可能的图片文件路径。

    只返回实际存在的文件路径，并按出现顺序去重。
    """
    candidates: list[str] = []

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for v in value.values():
                _walk(v)
        elif isinstance(value, list):
            for v in value:
                _walk(v)
        elif isinstance(value, str):
            lower = value.lower()
            if any(lower.endswith(ext) for ext in _IMAGE_EXTENSIONS):
                candidates.append(value)

    _walk(obj)

    seen: set[Path] = set()
    result: list[Path] = []
    wd = Path(workdir) if workdir else Path.cwd()
    for raw in candidates:
        path = Path(raw)
        if not path.is_absolute():
            path = wd / path
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            result.append(path)
    return result


def _encode_image_file(path: Path, max_size: int = _IMAGE_MAX_SIZE) -> ImageContent | None:
    """读取图片文件并编码为 ``ImageContent``。

    优先使用 Pillow 缩放；缺失 Pillow 时直接读取原文件 base64。
    """
    suffix = path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        media_type = "image/jpeg"
        pil_format = "JPEG"
    elif suffix == ".gif":
        media_type = "image/gif"
        pil_format = "GIF"
    elif suffix == ".webp":
        media_type = "image/webp"
        pil_format = "WEBP"
    elif suffix == ".bmp":
        media_type = "image/bmp"
        pil_format = "BMP"
    else:
        media_type = "image/png"
        pil_format = "PNG"

    try:
        from PIL import Image

        with Image.open(path) as img:
            img.thumbnail((max_size, max_size))
            buf = io.BytesIO()
            save_kwargs: dict[str, Any] = {}
            if pil_format == "JPEG":
                save_kwargs["quality"] = 85
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
            img.save(buf, format=pil_format, **save_kwargs)
            data = base64.b64encode(buf.getvalue()).decode("ascii")
            return ImageContent(data=data, media_type=media_type)
    except ImportError:
        # 无 Pillow，直接读取原文件
        try:
            raw = path.read_bytes()
            data = base64.b64encode(raw).decode("ascii")
            return ImageContent(data=data, media_type=media_type)
        except Exception as e:
            logger.warning("读取图片失败 %s: %s", path, e)
            return None
    except Exception as e:
        logger.warning("编码图片失败 %s: %s", path, e)
        return None


class CliAnythingTool(Tool):
    """CLI-Anything harness 的工具封装。

    Attributes:
        harness: 对应的 Harness 定义。
    """

    def __init__(self, harness: Harness) -> None:
        self.harness = harness
        self.name = f"cli_anything__{harness.id}"
        self.description = self._build_description(harness)
        self.input_schema = _build_input_schema(harness)
        self.max_result_chars = 20_000

    def _build_description(self, harness: Harness) -> str:
        """把 harness 元数据组装成给 LLM 看的描述。"""
        parts = [harness.description]
        if harness.when_to_use:
            parts.append(f"适用场景: {harness.when_to_use}")

        # 显式列出参数，防止 LLM 猜错参数名
        if harness.args:
            arg_lines = []
            for a in harness.args:
                req = "必填" if a.required else "可选"
                enum = f", 可选值: {a.enum}" if a.enum else ""
                default = f", 默认: {a.default}" if a.default is not None else ""
                pos = " (位置参数)" if a.positional else ""
                arg_lines.append(f"  - {a.name} ({req}, {a.type}{pos}{enum}{default}): {a.description}")
            parts.append("参数:\n" + "\n".join(arg_lines))

        if harness.examples:
            parts.append("示例: " + "; ".join(harness.examples))
        return "\n".join(parts)

    def is_read_only(self, args: dict[str, Any]) -> bool:
        """harness 默认视为可能修改状态，返回 False。"""
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        """harness 默认不可并发执行，返回 False。"""
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """外部 harness 命令默认需要用户确认。"""
        return PermissionResult.ask(f"执行外部 harness: {self.name}")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        """展示给用户的活动描述。"""
        return f"运行 {self.harness.name}"

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """执行 harness 命令并返回结果。

        如果 stdout 是 JSON，会尝试提取其中的图片路径并编码为 ``ImageContent``
        一并返回，供支持视觉的模型直接查看。
        """
        result = await run_harness(
            self.harness,
            args,
            timeout=120.0,
            workdir=ctx.workdir,
        )

        exit_code = result.get("exit_code", -1)
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        error = result.get("error", "")

        if exit_code != 0 or error:
            msg = f"{self.name} 执行失败"
            if error:
                msg += f" ({error})"
            if stderr:
                msg += f": {stderr}"
            elif stdout:
                msg += f": {stdout}"
            return ToolResult(data=msg, is_error=True)

        # 尝试把 stdout 当 JSON 解析，提升 LLM 可读性
        display = stdout
        parsed: Any = None
        try:
            parsed = json.loads(stdout)
            display = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            pass

        # 提取 JSON 中引用的图片并编码回传
        images: list[ImageContent] = []
        if parsed is not None:
            for img_path in _extract_image_paths(parsed, workdir=ctx.workdir):
                encoded = _encode_image_file(img_path)
                if encoded:
                    images.append(encoded)

        return ToolResult.ok(data=display, images=images)
