"""微信连接命令处理器。

包含 /connect-wechat, /disconnect-wechat 命令。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


async def _connect_wechat(
    ui,
    settings,
    loop,
    ctx,
) -> None:
    """/connect-wechat 命令 —— 微信扫码连接 JARVIS（通过 ClawBot）。

    通过腾讯 iLink Bot API 接入微信 ClawBot：
    1. 获取二维码 → 终端渲染 ASCII QR
    2. 微信扫码确认 → 拿到 bot_token
    3. 后台长轮询接收微信消息 → QueryLoop 处理 → 回复到微信

    依赖：aiohttp（HTTP 客户端）、qrcode（终端二维码渲染）。

    @author aceFelix
    """
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        ui.error("缺少 aiohttp 库，请运行: pip install aiohttp")
        ui.info("安装后即可用 /connect-wechat 扫码连接微信")
        return

    try:
        from agent.wechat import start_wechat_in_thread, start_wechat_loop, stop_wechat
    except ImportError as e:
        ui.error(f"微信模块不可用: {e}")
        return

    stop_wechat()

    bridge = start_wechat_in_thread(
        query_loop=loop,
        ctx=ctx,
        ui=ui,
        workdir=settings.workdir,
        main_loop=asyncio.get_running_loop(),
    )

    ui.info("📱 微信 ClawBot 连接中...")

    def _render_qr(qrcode_url: str) -> None:
        ui.info(f"扫码地址: {qrcode_url}")
        try:
            import qrcode as _qr

            qr = _qr.QRCode(
                version=1,
                error_correction=_qr.constants.ERROR_CORRECT_M,
                box_size=1,
                border=1,
            )
            qr.add_data(qrcode_url)
            qr.make(fit=True)
            matrix = qr.modules
            rows = len(matrix)
            cols = len(matrix[0]) if rows else 0
            lines = []
            for r in range(0, rows, 2):
                top = matrix[r]
                bottom = matrix[r + 1] if r + 1 < rows else [False] * cols
                line = ""
                for c in range(cols):
                    t = top[c]
                    b = bottom[c]
                    if t and b:
                        line += "█"
                    elif t and not b:
                        line += "▀"
                    elif not t and b:
                        line += "▄"
                    else:
                        line += " "
                lines.append(line)
            ui.info("\n" + "\n".join(lines))
        except ImportError:
            ui.info("提示: 安装 qrcode 库可在终端显示二维码 (pip install qrcode)")
        except Exception as e:
            ui.warn(f"二维码渲染失败: {e}")

    def _verify_input(retry: bool) -> str:
        prompt = "配对码不匹配，请重新输入: " if retry else "请输入手机微信显示的数字配对码: "
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    success = await bridge.login(
        verify_callback=_verify_input,
        qrcode_callback=_render_qr,
    )

    if not success:
        ui.error("微信登录失败，请重试")
        stop_wechat()
        return

    start_wechat_loop()

    ui.info("✅ 微信已连接！")
    ui.info("   在微信中找到 ClawBot 发消息即可与贾维斯对话")
    ui.info("   输入 /disconnect-wechat 可断开连接")
    ui.info("   连接有效期 24 小时，到期前会提醒重新扫码")


def _disconnect_wechat(ui) -> None:
    """/disconnect-wechat 命令 —— 断开微信 ClawBot 连接。

    @author aceFelix
    """
    try:
        from agent.wechat import get_wechat_bridge, stop_wechat
    except ImportError:
        ui.error("微信模块不可用")
        return

    if get_wechat_bridge() is None:
        ui.info("当前没有微信连接")
        return

    stop_wechat()
    ui.info("微信 ClawBot 已断开")


async def handle_connect_wechat(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /connect-wechat /wechat。"""
    await _connect_wechat(ctx.ui, ctx.settings, ctx.loop, ctx.ctx)
    return True


async def handle_disconnect_wechat(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /disconnect-wechat。"""
    _disconnect_wechat(ctx.ui)
    return True
