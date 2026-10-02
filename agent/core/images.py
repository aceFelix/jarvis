"""图片 / 剪贴板助手 —— 加载、编码、去重待发送图片。

从 main 拆出，供 main（REPL Ctrl+V 贴图）与 media_commands
（/paste /image 命令）共用。

历史：曾在"空回车"与"每次发消息"时自动检测剪贴板图片，但剪贴板内容
长期残留，导致普通提问被反复误贴图，已改为仅 Ctrl+V / /paste 显式添加。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from agent.core.context import ToolContext
from agent.core.message import ImageContent
from agent.ui.cli import RichCLI


def _load_image_from_path(path: str) -> ImageContent | None:
    """从文件路径加载图片，缩放并编码为 ImageContent。

    @author aceFelix
    """
    try:
        from PIL import Image
        from agent.tools.system.screen import ScreenShotTool
    except ImportError:
        return None

    p = Path(path).expanduser().resolve()
    if not p.exists():
        return None
    try:
        img = Image.open(p)
        img.load()
        return ScreenShotTool._encode_image(img, "jpeg", 1280)
    except Exception:
        return None


def _load_image_from_clipboard() -> ImageContent | None:
    """从系统剪贴板读取图片（Windows/macOS 支持），编码为 ImageContent。

    @author aceFelix
    """
    try:
        from PIL import Image, ImageGrab
        from agent.tools.system.screen import ScreenShotTool
    except ImportError:
        return None

    data = ImageGrab.grabclipboard()
    if data is None:
        return None
    if isinstance(data, Image.Image):
        return ScreenShotTool._encode_image(data, "jpeg", 1280)
    # Windows 剪贴板有时是文件路径列表
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                ext = Path(item).suffix.lower()
                if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                    content = _load_image_from_path(item)
                    if content is not None:
                        return content
    return None


def _pending_images(ctx: ToolContext) -> list[ImageContent]:
    """获取当前待发送的图片列表。

    @author aceFelix
    """
    return ctx.extra.setdefault("pending_images", [])


def _hash_image(img: ImageContent) -> str:
    """为 ImageContent 生成稳定哈希，用于去重。

    @author aceFelix
    """
    return hashlib.md5(f"{img.media_type}:{img.data}".encode()).hexdigest()


def paste_clipboard_image(ctx: ToolContext, ui: RichCLI) -> int:
    """Ctrl+V / /paste 显式添加剪贴板图片到待发送列表，返回新增张数。

    与 _pending_images 同一列表（ctx.extra["pending_images"]），随下一条
    用户消息发出；同一张图连按去重（按内容哈希比对已在列表中的图）。
    @author aceFelix
    """
    img = _load_image_from_clipboard()
    if img is None:
        ui.warn("剪贴板中没有图片（或缺少 Pillow）")
        return 0
    pending = _pending_images(ctx)
    h = _hash_image(img)
    if any(_hash_image(p) == h for p in pending):
        ui.info("剪贴板图片已在待发送列表中，未重复添加")
        return 0
    pending.append(img)
    ui.info(f"✅ 已添加剪贴板图片（待发送 {len(pending)} 张），随下一条消息发出")
    return 1
