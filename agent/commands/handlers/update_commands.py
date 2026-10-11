"""版本更新命令处理器（/update）。

提供 jarvis 本体的一键升级：检查 PyPI 最新版 → 有新版本则执行对应渠道的
升级命令（pip / uv tool）→ 提示重启生效。开发模式（editable）与未安装场景
不执行升级，仅给出指引。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


def handle_update(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /update：检查新版本并一键升级。

    @param ctx: REPL 共享状态（用到 ui）
    @param stripped: 原始输入（本命令无参数）
    @return True 表示命令已处理
    """
    from agent import update as update_mod

    ui = ctx.ui
    cur = update_mod.current_version()

    # 强制联网查询（用户显式发起，忽略 24h 节流）
    latest = update_mod.latest_version()
    if not latest:
        ui.info(f"当前版本 {cur}；无法连接 PyPI 检查更新（离线或网络受限）。")
        return True

    if not update_mod.is_newer(latest, cur):
        ui.info(f"当前已是最新版本 {cur}。")
        return True

    ui.info(f"发现新版本 {latest}（当前 {cur}），开始升级…")
    ok, output = update_mod.run_upgrade()
    if ok:
        ui.info("升级完成，请重启 jarvis 生效。")
        return True

    # 失败：优先展示可手动执行的命令，便于用户自行排查（如文件被占用）
    ui.error(output.splitlines()[-1] if output else "升级失败")
    cmd = update_mod.upgrade_command()
    if cmd:
        ui.info("可手动执行：" + " ".join(cmd))
    return True
