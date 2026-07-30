"""CLI-Anything harness 命令处理器。

包含 /cli_anything, /harnesses 及其子命令。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


def _cli_anything_list(ui: Any, workdir: str = "") -> None:
    """/cli_anything list — 列出已安装 harness。"""
    from agent.cli_anything import list_installed

    installed = list_installed(workdir=workdir or None)
    if not installed:
        ui.info("暂无已安装的 CLI-Anything harness")
        return
    lines = [f"  - {h['id']}: {h['name']} — {h['description']}" for h in installed]
    text = "[bold]已安装 harness:[/bold]\n" + "\n".join(lines)
    if ui._console:
        ui._console.print(text)
    else:
        print(text)


def _cli_anything_market(ui: Any, settings: Any | None = None) -> None:
    """/cli_anything market — 列出市场可用 harness。"""
    from agent.cli_anything import list_market

    custom_url = getattr(settings, "harness_market_url", "") or ""
    custom_local = getattr(settings, "harness_market_local", "") or ""
    market = list_market(custom_market_url=custom_url, custom_market_local=custom_local)
    if not market:
        ui.warn("无法读取市场列表，请确保 CLI-Anything 仓库在同级目录或配置了自定义市场")
        return
    lines = [
        f"  - {h['id']}: {h['name']} [{h['installed']}] [{h.get('source', '官方')}] — {h['description']}"
        for h in market
    ]
    text = "[bold]市场可用 harness:[/bold]\n" + "\n".join(lines)
    if ui._console:
        ui._console.print(text)
    else:
        print(text)


def _cli_anything_install(ui: Any, settings: Any, registry: Any, stripped: str) -> None:
    """/cli_anything install <id> — 安装 harness。"""
    from agent.cli_anything import install_harness

    parts = stripped.split()
    if len(parts) < 3:
        ui.warn("用法: /cli_anything install <harness-id>")
        return
    harness_id = parts[2].strip()
    result = install_harness(
        harness_id, registry,
        workdir=settings.workdir or None,
        custom_market_url=getattr(settings, "harness_market_url", "") or "",
        custom_market_local=getattr(settings, "harness_market_local", "") or "",
    )
    if result["success"]:
        ui.info(result["message"])
    else:
        ui.warn(result["message"])


def _cli_anything_uninstall(ui: Any, settings: Any, registry: Any, stripped: str) -> None:
    """/cli_anything uninstall <id> — 卸载 harness。"""
    from agent.cli_anything import uninstall_harness

    parts = stripped.split()
    if len(parts) < 3:
        ui.warn("用法: /cli_anything uninstall <harness-id>")
        return
    harness_id = parts[2].strip()
    result = uninstall_harness(harness_id, registry, workdir=settings.workdir or None)
    if result["success"]:
        ui.info(result["message"])
    else:
        ui.warn(result["message"])


def _cli_anything_enable(ui: Any, stripped: str) -> None:
    """/cli_anything enable <id> — 启用被禁用的 harness。"""
    parts = stripped.split(None, 3)
    if len(parts) < 3:
        ui.warn("用法: /cli_anything enable <harness ID>")
        return
    harness_id = parts[2].strip()
    from agent.cli_anything.market import enable_harness

    result = enable_harness(harness_id)
    if result["success"]:
        ui.info(result["message"])
    else:
        ui.error(result["message"])


def _cli_anything_disable(ui: Any, stripped: str) -> None:
    """/cli_anything disable <id> — 禁用 harness（不卸载）。"""
    parts = stripped.split(None, 3)
    if len(parts) < 3:
        ui.warn("用法: /cli_anything disable <harness ID>")
        return
    harness_id = parts[2].strip()
    from agent.cli_anything.market import disable_harness

    result = disable_harness(harness_id)
    if result["success"]:
        ui.info(result["message"])
    else:
        ui.error(result["message"])


def _cli_anything_create(ui: Any, stripped: str) -> None:
    """/cli_anything create <id> [--desc "描述"] [--dir <目录>] — 创建 harness 脚手架。"""
    parts = stripped.split(None, 3)
    if len(parts) < 3:
        ui.warn("用法: /cli_anything create <harness ID> [--desc \"描述\"] [--dir <目录>]")
        return
    rest = parts[2].strip() if len(parts) > 2 else ""

    description = ""
    output_dir = ""

    tokens = rest.split(None)
    harness_id = tokens[0] if tokens else ""
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--desc" and i + 1 < len(tokens):
            desc_start = rest.find('"', rest.find("--desc"))
            desc_end = rest.find('"', desc_start + 1) if desc_start != -1 else -1
            if desc_start != -1 and desc_end != -1:
                description = rest[desc_start + 1:desc_end]
                i = len(tokens)
            else:
                description = tokens[i + 1] if i + 1 < len(tokens) else ""
                i += 2
        elif tok == "--dir" and i + 1 < len(tokens):
            output_dir = tokens[i + 1]
            i += 2
        else:
            i += 1

    if not harness_id:
        ui.warn("harness ID 不能为空")
        return

    from agent.cli_anything.market import create_harness

    result = create_harness(
        harness_id,
        description=description,
        output_dir=output_dir or None,
    )
    if result["success"]:
        ui.info(f"Harness 脚手架已创建: {result['message']}")
        ui.info("编辑生成的 SKILL.md 后，重启 Jarvis 或 /reset 即可加载。")
    else:
        ui.error(f"创建失败: {result['message']}")


def _cli_anything_validate(ui: Any, stripped: str) -> None:
    """/cli_anything validate <路径> — 校验 SKILL.md。"""
    parts = stripped.split(None, 3)
    if len(parts) < 3:
        ui.warn("用法: /cli_anything validate <harness 目录或 SKILL.md 路径>")
        return
    path = parts[2].strip()
    from agent.cli_anything.market import validate_harness

    ok, errors = validate_harness(path)
    if ok:
        ui.info(f"校验通过: {path}")
    else:
        ui.error("校验失败:")
        for err in errors:
            ui.error(f"  - {err}")


async def handle_cli_anything(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /cli_anything 及 /harnesses 各子命令。"""
    cmd = stripped.lower()
    ui = ctx.ui
    settings = ctx.settings
    registry = ctx.registry

    if cmd in ("/cli_anything", "/cli_anything list", "/harnesses"):
        _cli_anything_list(ui, workdir=settings.workdir)
    elif cmd == "/cli_anything market":
        _cli_anything_market(ui, settings)
    elif cmd.startswith("/cli_anything install "):
        _cli_anything_install(ui, settings, registry, stripped)
    elif cmd.startswith("/cli_anything uninstall "):
        _cli_anything_uninstall(ui, settings, registry, stripped)
    elif cmd.startswith("/cli_anything enable "):
        _cli_anything_enable(ui, stripped)
    elif cmd.startswith("/cli_anything disable "):
        _cli_anything_disable(ui, stripped)
    elif cmd.startswith("/cli_anything create "):
        _cli_anything_create(ui, stripped)
    elif cmd.startswith("/cli_anything validate "):
        _cli_anything_validate(ui, stripped)
    else:
        _cli_anything_list(ui, workdir=settings.workdir)
    return True
