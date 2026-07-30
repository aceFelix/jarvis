"""Plugin 系统命令处理器。

包含 /plugin, /plugins 及其 install/uninstall/search/info/update/enable/disable/create/validate 子命令。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


def _show_plugins(ui: Any, settings: Any) -> None:
    """/plugin — 列出已安装插件。"""
    from agent.core.extensions.plugins import PluginManager

    pm = PluginManager(settings.plugin_marketplace)
    installed = pm.list_installed()
    plugins = installed.get("plugins", {})
    if not plugins:
        ui.info("（暂无已安装插件。使用 /plugin search 搜索可安装的插件。）")
        return
    if ui._console:
        from rich.table import Table
        table = Table(title="已安装插件", show_lines=False)
        table.add_column("名称", style="cyan", no_wrap=True)
        table.add_column("版本", style="dim")
        table.add_column("来源", style="dim")
        table.add_column("安装时间", style="dim")
        for p in plugins.values():
            table.add_row(
                p.get("name", "?"),
                p.get("version", "?"),
                p.get("source", "?"),
                p.get("installed_at", "?")[:19],
            )
        ui._console.print(table)
    else:
        for p in plugins.values():
            ui.info(f"  {p['name']} v{p['version']} 来自 {p['source']}")


def _plugin_install(ui: Any, settings: Any, stripped: str) -> None:
    """/plugin install <name> — 安装 Plugin 系统的插件。"""
    parts = stripped.split(None, 2)
    if len(parts) < 3:
        ui.warn("用法: /plugin install <插件名>")
        return
    name = parts[2].strip()
    from agent.core.extensions.plugins import PluginManager

    pm = PluginManager(
        marketplace_url=settings.plugin_marketplace,
        marketplace_local=settings.plugin_market_local,
    )
    ui.info(f"正在安装插件: {name} ...")
    ok, msg = pm.install(name)
    if ok:
        ui.info(f"插件 '{name}' 安装成功！" + (f" ({msg})" if msg else ""))
        ui.info("技能已安装到 ~/.jarvis/skills/，请 /reset 或重启后生效。")
    else:
        ui.error(f"安装失败: {msg}")


def _plugin_uninstall(ui: Any, settings: Any, stripped: str) -> None:
    """/plugin uninstall <name> — 卸载 Plugin 系统的插件。"""
    parts = stripped.split(None, 2)
    if len(parts) < 3:
        ui.warn("用法: /plugin uninstall <插件名>")
        return
    name = parts[2].strip()
    from agent.core.extensions.plugins import PluginManager

    pm = PluginManager(
        marketplace_url=settings.plugin_marketplace,
        marketplace_local=settings.plugin_market_local,
    )
    ok, msg = pm.uninstall(name)
    if ok:
        ui.info(f"插件 '{name}' 已卸载。")
    else:
        ui.error(f"卸载失败: {msg}")


def _plugin_search(ui: Any, settings: Any, stripped: str) -> None:
    """/plugin search [keyword] — 搜索 Plugin 系统的市场（远程 + 本地）。"""
    parts = stripped.split(None, 2)
    keyword = parts[2].strip() if len(parts) > 2 else ""
    from agent.core.extensions.plugins import PluginManager

    pm = PluginManager(
        marketplace_url=settings.plugin_marketplace,
        marketplace_local=settings.plugin_market_local,
    )
    label = keyword if keyword else "全部"
    ui.info(f"搜索插件: {label} ...")
    try:
        results = pm.search(keyword)
    except Exception as e:
        ui.error(f"搜索失败: {e}")
        return
    if not results:
        ui.info("无匹配插件。")
        return
    if ui._console:
        from rich.table import Table
        table = Table(title=f"插件市场搜索结果: {label}", show_lines=True)
        table.add_column("名称", style="cyan", no_wrap=True)
        table.add_column("版本", style="dim")
        table.add_column("描述")
        table.add_column("来源")
        for p in results:
            source_label = "本地" if p.get("_is_local") else "远程"
            table.add_row(
                p.get("name", "?"),
                p.get("version", "?"),
                p.get("description", ""),
                source_label,
            )
        ui._console.print(table)
    else:
        for p in results:
            source_label = "本地" if p.get("_is_local") else "远程"
            ui.info(f"  {p['name']} v{p['version']} [{source_label}] - {p.get('description', '')}")
    ui.info("使用 /plugin install <名称> 安装，/plugin info <名称> 查看详情")


def _plugin_info(ui: Any, settings: Any, stripped: str) -> None:
    """/plugin info <name> — 查看 Plugin 系统的插件详情。"""
    parts = stripped.split(None, 2)
    if len(parts) < 3:
        ui.warn("用法: /plugin info <插件名>")
        return
    name = parts[2].strip()
    from agent.core.extensions.plugins import PluginManager

    pm = PluginManager(
        marketplace_url=settings.plugin_marketplace,
        marketplace_local=settings.plugin_market_local,
    )
    try:
        results = pm.search(name)
    except Exception as e:
        ui.error(f"搜索失败: {e}")
        return
    plugin = None
    for p in results:
        if p.get("name") == name:
            plugin = p
            break
    if plugin is None and results:
        plugin = results[0]
    if plugin is None:
        ui.warn(f"未找到插件: {name}")
        return

    installed = pm.list_installed().get("plugins", {})
    is_installed = name in installed
    installed_ver = installed.get(name, {}).get("version", "") if is_installed else ""

    lines = [
        f"[bold]插件详情[/bold]",
        f"  名称: {plugin.get('name', '?')}",
        f"  描述: {plugin.get('description', '')}",
        f"  版本: {plugin.get('version', '?')}",
        f"  状态: {'已安装' + (' v' + installed_ver if installed_ver else '') if is_installed else '未安装'}",
    ]
    if plugin.get("author"):
        lines.append(f"  作者: {plugin['author']}")
    if plugin.get("category"):
        lines.append(f"  分类: {plugin['category']}")
    if plugin.get("tags"):
        lines.append(f"  标签: {', '.join(plugin['tags'])}")
    source = plugin.get("source", {})
    if plugin.get("_is_local"):
        lines.append(f"  来源: 本地 ({source.get('path', '')})")
    elif source.get("repo"):
        lines.append(f"  仓库: {source['repo']}")
    lines.append(f"  安装: /plugin install {plugin.get('name', '')}")
    if ui._console:
        ui._console.print("\n".join(lines))
    else:
        ui.info("\n".join(lines))


def _compare_versions(v1: str, v2: str) -> int:
    """比较两个语义化版本号。

    Returns:
        1 if v1 > v2, -1 if v1 < v2, 0 if equal.

    @author aceFelix
    """
    def parse(v: str) -> tuple[int, ...]:
        parts = v.strip().split(".")
        nums = []
        for part in parts[:3]:
            try:
                nums.append(int(part))
            except ValueError:
                nums.append(0)
        while len(nums) < 3:
            nums.append(0)
        return tuple(nums)

    a, b = parse(v1), parse(v2)
    if a > b:
        return 1
    if a < b:
        return -1
    return 0


def _plugin_check_updates(ui: Any, settings: Any) -> None:
    """/plugin update — 检查 Plugin 系统已安装插件的更新。"""
    from agent.core.extensions.plugins import PluginManager

    pm = PluginManager(
        marketplace_url=settings.plugin_marketplace,
        marketplace_local=settings.plugin_market_local,
    )
    ui.info("正在检查插件更新 ...")
    try:
        installed = pm.list_installed().get("plugins", {})
        market_plugins = {p.get("name", ""): p for p in pm.fetch_marketplace()}
    except Exception as e:
        ui.error(f"检查更新失败: {e}")
        return

    updates = []
    for pname, entry in installed.items():
        market_entry = market_plugins.get(pname)
        if market_entry and market_entry.get("version"):
            installed_ver = entry.get("version", "0.0.0")
            market_ver = market_entry.get("version", "0.0.0")
            if _compare_versions(market_ver, installed_ver) > 0:
                updates.append((pname, installed_ver, market_ver))

    if not updates:
        ui.info("所有插件均为最新版本。")
        return
    if ui._console:
        from rich.table import Table
        table = Table(title="可更新插件", show_lines=False)
        table.add_column("名称", style="cyan")
        table.add_column("当前版本", style="dim")
        table.add_column("最新版本", style="green")
        for pname, cur, new in updates:
            table.add_row(pname, cur, new)
        ui._console.print(table)
    else:
        for pname, cur, new in updates:
            ui.info(f"  {pname}: {cur} → {new}")
    ui.info("使用 /plugin install <名称> 重新安装以更新")


def _plugin_enable(ui: Any, settings: Any, stripped: str) -> None:
    """/plugin enable <name> — 启用被禁用的 Plugin 插件。"""
    parts = stripped.split(None, 2)
    if len(parts) < 3:
        ui.warn("用法: /plugin enable <插件名>")
        return
    name = parts[2].strip()
    from agent.core.extensions.plugins import PluginManager

    pm = PluginManager(
        marketplace_url=settings.plugin_marketplace,
        marketplace_local=settings.plugin_market_local,
    )
    ok, msg = pm.enable(name)
    if ok:
        ui.info(msg)
    else:
        ui.error(msg)


def _plugin_disable(ui: Any, settings: Any, stripped: str) -> None:
    """/plugin disable <name> — 禁用 Plugin 插件（不卸载）。"""
    parts = stripped.split(None, 2)
    if len(parts) < 3:
        ui.warn("用法: /plugin disable <插件名>")
        return
    name = parts[2].strip()
    from agent.core.extensions.plugins import PluginManager

    pm = PluginManager(
        marketplace_url=settings.plugin_marketplace,
        marketplace_local=settings.plugin_market_local,
    )
    ok, msg = pm.disable(name)
    if ok:
        ui.info(msg)
    else:
        ui.error(msg)


def _plugin_create(ui: Any, settings: Any, stripped: str) -> None:
    """/plugin create <name> [--desc "描述"] [--dir <目录>] — 创建 Plugin 插件脚手架。"""
    parts = stripped.split(None, 2)
    if len(parts) < 3:
        ui.warn("用法: /plugin create <插件名> [--desc \"描述\"] [--dir <目录>]")
        return
    rest = parts[2].strip()

    description = ""
    output_dir = ""

    tokens = rest.split(None)
    name = tokens[0] if tokens else ""
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--type" and i + 1 < len(tokens):
            t = tokens[i + 1]
            if t == "harness":
                ui.warn("harness 脚手架请用 /cli_anything create <id>")
                return
            i += 2
        elif tok == "--desc" and i + 1 < len(tokens):
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

    if not name:
        ui.warn("插件名不能为空")
        return

    from agent.core.extensions.plugins import PluginManager

    pm = PluginManager(
        marketplace_url=settings.plugin_marketplace,
        marketplace_local=settings.plugin_market_local,
    )
    ok, msg = pm.create_plugin(
        name,
        description=description,
        output_dir=output_dir or None,
    )
    if ok:
        ui.info(f"Plugin 插件脚手架已创建: {msg}")
        ui.info("编辑生成的文件后，重启 Jarvis 或 /reset 即可加载。")
    else:
        ui.error(f"创建失败: {msg}")


def _plugin_validate(ui: Any, settings: Any, stripped: str) -> None:
    """/plugin validate <path> — 校验 plugin.json。"""
    parts = stripped.split(None, 2)
    if len(parts) < 3:
        ui.warn("用法: /plugin validate <插件目录或 plugin.json 路径>")
        return
    path = parts[2].strip()
    from agent.core.extensions.plugins import PluginManager

    pm = PluginManager(
        marketplace_url=settings.plugin_marketplace,
        marketplace_local=settings.plugin_market_local,
    )
    ok, errors = pm.validate_plugin(path)
    if ok:
        ui.info(f"校验通过: {path}")
    else:
        ui.error("校验失败:")
        for err in errors:
            ui.error(f"  - {err}")


async def handle_plugins(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /plugin /plugins：列出已安装插件。"""
    _show_plugins(ctx.ui, ctx.settings)
    return True


async def handle_plugin(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /plugin 各子命令。"""
    cmd = stripped.lower()
    ui = ctx.ui
    settings = ctx.settings

    if cmd.startswith("/plugin install "):
        _plugin_install(ui, settings, stripped)
    elif cmd.startswith("/plugin uninstall "):
        _plugin_uninstall(ui, settings, stripped)
    elif cmd.startswith("/plugin search"):
        _plugin_search(ui, settings, stripped)
    elif cmd.startswith("/plugin info "):
        _plugin_info(ui, settings, stripped)
    elif cmd in ("/plugin update", "/plugin updates"):
        _plugin_check_updates(ui, settings)
    elif cmd.startswith("/plugin enable "):
        _plugin_enable(ui, settings, stripped)
    elif cmd.startswith("/plugin disable "):
        _plugin_disable(ui, settings, stripped)
    elif cmd.startswith("/plugin create "):
        _plugin_create(ui, settings, stripped)
    elif cmd.startswith("/plugin validate "):
        _plugin_validate(ui, settings, stripped)
    else:
        _show_plugins(ui, settings)
    return True
