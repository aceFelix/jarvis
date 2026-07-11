"""摄像头工具 —— 让贾维斯"睁眼看世界"。

阶段五扩展能力。复用 ScreenShot 的多模态视觉架构，把摄像头画面作为
image content block 回传给支持视觉的 LLM（qwen3.7-plus / Claude / GPT-4o），
模型能直接"看见"现实世界——人脸、物体、场景、文字、姿态等。

包含两个工具:
- **CameraShot**: 拍一张照片。OpenCV VideoCapture 读一帧 → JPEG → ImageContent。
  LLM 调用后直接看到画面，回答"这是什么""几个人""桌上有什么"等问题。
- **ListCameras**: 列出可用摄像头索引。多摄像头机器可选 front/back。

设计要点:
- **复用多模态架构**: 与 ScreenShot 同款 ImageContent + ToolResult.images 通道，
  provider 层无需改动
- **OpenCV 轻量**: cv2.VideoCapture 读单帧约 200ms，即开即拍即关，不持续占用
- **隐私**: 每次调用都显式拍一张，不持续录像。权限默认 ASK（拍用户需要用户确认）
- **优雅降级**: cv2 未装或无摄像头时返回明确错误
- **JPEG 压缩**: 默认 quality 85 + 最长边 1280，兼顾清晰度与 token 成本

依赖: opencv-python（pip install opencv-python）。numpy 随 cv2 自动装。
"""

from __future__ import annotations

import base64
import io
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.context import ToolContext
from agent.core.message import ImageContent
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool


def _import_cv2():
    """延迟导入 cv2。未装时抛 ImportError。"""
    import cv2  # type: ignore[import-untyped]
    return cv2


def _import_numpy():
    """延迟导入 numpy。"""
    import numpy  # type: ignore[import-untyped]
    return numpy


def _import_pil():
    """延迟导入 PIL.Image，用于缩放和编码。"""
    from PIL import Image  # type: ignore[import-untyped]
    return Image


def _cam_shots_dir() -> Path:
    """摄像头照片保存目录: 系统临时目录下的 jarvis-cam。"""
    d = Path.home() / ".jarvis" / "cam"
    d.mkdir(parents=True, exist_ok=True)
    return d


class CameraShotTool(Tool):
    """用摄像头拍一张照片，回传给 LLM 视觉理解。

    LLM 调用此工具后，照片作为 image content block 回传，支持视觉的模型
    （qwen3.7-plus / Claude / GPT-4o）能直接"看见"画面内容，回答关于
    画面的问题——识别物体、人数、场景、文字、姿态等。
    """

    name = "CameraShot"
    description = (
        "用电脑摄像头拍一张照片，照片会作为图片直接回传给你（多模态），"
        "你能真正'看到'摄像头画面——识别人脸/物体/场景/文字/姿态等。"
        "用于用户说'你看看''帮我看看我桌上有什么''这是什么'等需要视觉的场景。"
        "可选参数 camera_index 选择摄像头（默认0=前置），format（jpeg/png默认jpeg），"
        "max_size（最长边像素默认1280，0=不缩放）。"
        "注意：拍摄会访问摄像头，需要用户确认。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "camera_index": {
                "type": "integer",
                "description": "摄像头索引（默认0=前置摄像头，1=后置/外接摄像头）",
                "default": 0,
            },
            "format": {
                "type": "string",
                "enum": ["jpeg", "png"],
                "description": "回传图片格式。jpeg 体积小省 token（默认），png 无损。",
                "default": "jpeg",
            },
            "max_size": {
                "type": "integer",
                "description": "回传图片最长边像素，超出等比缩放（默认1280，0=不缩放）",
                "default": 1280,
            },
        },
    }
    max_result_chars = 1_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        # 拍摄本身不改系统状态，但访问摄像头涉及隐私，不算纯只读
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        # 多个摄像头可并行，但同一摄像头不建议并行（设备占用冲突）
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        # 拍用户需要用户确认（隐私敏感）
        # yolo 模式下放行（用户已信任），其他模式 ASK
        mode = ctx.permission_mode
        if mode == "yolo":
            return PermissionResult.allow("yolo 模式自动放行摄像头拍摄")
        return PermissionResult.ask("摄像头拍摄（访问摄像头）")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        cam_idx = args.get("camera_index", 0)
        if not isinstance(cam_idx, int) or cam_idx < 0:
            return ValidationResult.fail("camera_index 必须是非负整数")
        fmt = args.get("format", "jpeg")
        if fmt not in ("jpeg", "png"):
            return ValidationResult.fail("format 必须是 jpeg 或 png")
        max_size = args.get("max_size", 1280)
        if not isinstance(max_size, int) or max_size < 0:
            return ValidationResult.fail("max_size 必须是非负整数")
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        # 导入依赖
        try:
            cv2 = _import_cv2()
            numpy = _import_numpy()
            Image = _import_pil()
        except ImportError as e:
            return ToolResult.error(
                f"摄像头依赖未安装: {e}（pip install opencv-python 启用）"
            )

        cam_idx = args.get("camera_index", 0)
        fmt = args.get("format", "jpeg")
        max_size = args.get("max_size", 1280)

        # 打开摄像头拍一帧
        cap = None
        try:
            cap = cv2.VideoCapture(cam_idx)
            if not cap.isOpened():
                return ToolResult.error(
                    f"无法打开摄像头 {cam_idx}（可能被占用或不存在，用 ListCameras 查看可用摄像头）"
                )

            # 读一帧（warmup：第一帧可能是黑屏，读两帧确保稳定）
            for _ in range(2):
                ret, frame = cap.read()
            if not ret or frame is None:
                return ToolResult.error(f"摄像头 {cam_idx} 读取画面失败")

            # OpenCV 读出来是 BGR，转 RGB
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # 转 PIL Image 用于缩放和编码（复用 ScreenShot 的编码逻辑）
            pil_img = Image.fromarray(frame_rgb)

        except Exception as e:
            return ToolResult.error(f"摄像头拍摄失败: {type(e).__name__}: {e}")
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

        # 保存到 ~/.jarvis/cam/（便于用户事后查看）
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = _cam_shots_dir() / f"cam_{ts}.png"
        try:
            pil_img.save(str(save_path), format="PNG")
        except Exception:
            pass  # 保存失败不影响回传

        w, h = pil_img.size

        # 编码为 base64 图片块回传 LLM
        images: list[ImageContent] = []
        encode_note = ""
        try:
            images = [self._encode_image(pil_img, fmt, max_size)]
        except Exception as e:
            encode_note = f"\n（图片回传失败: {type(e).__name__}: {e}）"

        summary = (
            f"已拍摄摄像头照片\n"
            f"摄像头: {cam_idx}\n"
            f"路径: {save_path}\n"
            f"尺寸: {w} x {h}\n"
            f"时间: {ts}\n"
            f"图片已回传: {'是' if images else '否'}"
            f"（{fmt}, 最长边<={max_size or '原图'}）{encode_note}"
        )
        return ToolResult.ok(summary, images=images)

    @staticmethod
    def _encode_image(img: Any, fmt: str, max_size: int) -> ImageContent:
        """把 PIL Image 缩放并编码为 base64 图片块。

        与 ScreenShotTool._encode_image 逻辑一致:
        - 缩放到最长边 max_size（保持比例），省 token
        - JPEG quality 85，RGBA/LA/P 转 RGB
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
        cam_idx = args.get("camera_index", 0) if args else 0
        return f"拍摄摄像头 {cam_idx}"


class ListCamerasTool(Tool):
    """列出可用摄像头索引。

    多摄像头机器（如笔记本前置+外接USB摄像头）可用此工具查看有哪些摄像头。
    """

    name = "ListCameras"
    description = (
        "列出可用的摄像头索引。用于多摄像头场景选择 front/back。"
        "返回可用摄像头索引列表。只读，自动放行。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "max_check": {
                "type": "integer",
                "description": "最多检查多少个索引（默认3，即检查0/1/2）",
                "default": 3,
            }
        },
    }
    max_result_chars = 500

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读查询")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            cv2 = _import_cv2()
        except ImportError as e:
            return ToolResult.error(
                f"摄像头依赖未安装: {e}（pip install opencv-python 启用）"
            )

        max_check = args.get("max_check", 3)
        available: list[int] = []

        for i in range(max_check):
            cap = None
            try:
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    # 进一步验证能读到画面
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        available.append(i)
            except Exception:
                pass
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass

        if not available:
            return ToolResult.ok("未检测到可用摄像头（检查摄像头是否连接、驱动是否正常）")

        lines = [f"检测到 {len(available)} 个可用摄像头:"]
        for idx in available:
            label = "前置" if idx == 0 else f"索引{idx}"
            lines.append(f"  - {idx}: {label}摄像头")

        return ToolResult.ok("\n".join(lines))

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        return "查询可用摄像头"


# ---------------------------------------------------------------------------
# 注册函数
# ---------------------------------------------------------------------------


def register_camera_tools(registry) -> int:
    """注册摄像头工具。返回注册数。

    依赖 opencv-python，未安装时跳过（不影响其他工具）。
    """
    try:
        import cv2  # noqa: F401  验证依赖可用
    except ImportError:
        return 0

    count = 0
    for tool_cls in [CameraShotTool, ListCamerasTool]:
        if tool_cls.name in registry:
            continue
        try:
            registry.register(tool_cls())
            count += 1
        except Exception:
            pass
    return count
