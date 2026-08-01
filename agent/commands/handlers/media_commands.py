"""媒体与剪贴图片命令处理器。

包含 /paste, /image, /say 命令。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


async def handle_paste(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /paste /p /clipboard：将剪贴板图片加入待发送列表。"""
    # 延迟导入 core.images 中的图片辅助函数，避免循环引用
    from agent.core.images import _load_image_from_clipboard, _pending_images

    ui = ctx.ui
    img = _load_image_from_clipboard()
    if img is None:
        ui.warn("剪贴板中没有图片（或缺少 Pillow）")
    else:
        _pending_images(ctx.ctx).append(img)
        ui.info("✅ 已添加剪贴板图片，下一条消息会附带发送")
    return True


async def handle_image(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /image <path> /img <path>：加载本地图片到待发送列表。"""
    from agent.core.images import _load_image_from_path, _pending_images

    ui = ctx.ui
    parts = stripped.split(None, 1)
    if len(parts) < 2:
        ui.warn("用法: /image <图片路径>")
        return True

    img = _load_image_from_path(parts[1].strip())
    if img is None:
        ui.warn(f"无法加载图片: {parts[1].strip()}")
    else:
        _pending_images(ctx.ctx).append(img)
        ui.info(f"✅ 已添加图片，下一条消息会附带发送: {parts[1].strip()}")
    return True


async def handle_say(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /say <text>：语音朗读。"""
    from agent.commands.handlers.voice_commands import _say

    parts = stripped.split(" ", 1)
    text = parts[1] if len(parts) > 1 else ""
    _say(ctx.ui, ctx.settings, text)
    return True
