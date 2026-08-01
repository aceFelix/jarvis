"""图片 / 剪贴板助手 —— 加载、编码、去重待发送图片。

从 main 拆出，供 main（REPL 空行贴图 / 自动附加）与 media_commands
（/paste /image 命令）共用。
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


def _auto_attach_clipboard_image(ctx: ToolContext, ui: RichCLI) -> list[ImageContent]:
    """如果剪贴板有新图片，自动加入待发送列表并返回。

    @author aceFelix
    """
    pending = ctx.extra.pop("pending_images", None) or []
    if pending:
        return pending
    img = _load_image_from_clipboard()
    if img is None:
        return []
    h = _hash_image(img)
    if h == ctx.extra.get("_last_clipboard_image_hash"):
        return []
    pending.append(img)
    ctx.extra["_last_clipboard_image_hash"] = h
    ui.info("✅ 检测到剪贴板图片，已自动附加到当前消息")
    return pending
