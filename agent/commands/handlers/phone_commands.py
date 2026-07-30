"""手机连接命令处理器。

包含 /connect-phone 命令。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


async def _connect_phone(
    ui,
    settings,
    loop,
    ctx,
) -> None:
    """/connect-phone 命令 —— 生成二维码，让手机扫码连接 JARVIS。

    手机通过 Web PWA 与当前 REPL 会话建立 WebSocket 连接。
    手机和电脑共享同一会话历史（messages 共享），手机端默认 PLAN 权限模式。

    依赖：qrcode 库用于生成终端 ASCII 二维码，未安装时提示手动安装。

    @author aceFelix
    """
    try:
        from agent.bridge import start_bridge_in_thread, stop_bridge
    except ImportError as e:
        ui.error(f"跨设备协同模块不可用: {e}")
        return

    try:
        import qrcode  # type: ignore[import-not-found]
    except ImportError:
        ui.error("缺少 qrcode 库，请运行: pip install qrcode")
        ui.info("安装后即可用 /connect-phone 生成二维码扫码连接")
        return

    stop_bridge()

    server = start_bridge_in_thread(
        query_loop=loop,
        ctx=ctx,
        http_port=getattr(settings, "bridge_http_port", 8765),
        ws_port=getattr(settings, "bridge_ws_port", 8766),
        token=getattr(settings, "bridge_token", ""),
        workdir=settings.workdir,
    )

    url = server.url

    ui.info("🌐 跨设备协同已启动")
    ui.info(f"   手机访问: {url}")
    ui.info("   手机和电脑需在同一局域网（Wi-Fi）")

    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=1,
        )
        qr.add_data(url)
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
    except Exception as e:
        ui.warn(f"二维码生成失败: {e}")

    ui.info("提示: 手机扫码或手动访问上方 URL 即可开始对话")
    ui.info("      输入 /connect-phone 可重新生成二维码")


async def handle_connect_phone(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /connect-phone /phone。"""
    await _connect_phone(ctx.ui, ctx.settings, ctx.loop, ctx.ctx)
    return True
