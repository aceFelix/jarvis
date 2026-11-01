"""桌面「扩展服务」面板数据源 —— 五类扩展能力的结构化清单与增删改启停。

背景（2026-10）：桌面标题栏新增「扩展服务」按钮，右栏打开两级面板集中管理
MCP 服务 / 内置工具 / 技能 / 插件 / harness。终端侧虽已有 ``/mcp`` ``/tools``
``/skills`` ``/plugin`` ``/cli_anything`` 五个命令，但它们的处理器把结果打印到
Rich Console（见 agent/commands/handlers/*），**不是结构化返回值**，桌面无法
复用。本模块提供结构化 RPC 数据源，供 serve 的 ``extensions.exec`` 指令调用。

设计要点：

- **单入口分发**：``run_extension(settings, engine, payload)`` 按
  ``payload["category"]`` 分发到五类处理器；非法分类/动作抛 ``ValueError``，
  由 serve 侧转成 ``ok=false`` 回执。
- **统一返回体**：每个动作返回 ``{"ok": bool, "data": <结构化结果>,
  "message": str}``。列表类 ``ok=True`` + data；写操作 ``ok`` 表示业务成败
  （不抛异常，错误走 message 内联展示，与桌面表单不回灌聊天流的口径一致）。
- **MCP 启停不动 mcp.json**：禁用名单存独立文件 ``~/.jarvis/mcp_disabled.json``
  （见 mcp_client.load_mcp_disabled/save_mcp_disabled），连接时过滤，规避触碰含
  明文/占位 token 的配置文件。
- **harness 写操作用临时 registry**：install/uninstall 会调
  ``discover_and_register`` 刷新工具表；桌面列表数据源是磁盘扫描，不依赖运行中
  registry，故传一次性 ``build_default_registry()`` 避免跨线程改引擎活动 registry。

@author aceFelix
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Callable

# 支持的扩展分类（与桌面 protocol.CMD_EXTENSIONS 的 category 取值一致）
_CATEGORIES: tuple[str, ...] = ("mcp", "tools", "skills", "plugins", "harness")


def run_extension(settings: Any, engine: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """扩展服务统一入口：按 category/action 分发到对应处理器。

    Args:
        settings: 全局 Settings（取 workdir / 市场配置等）。
        engine: ChatEngine（取活动 registry / MCP 连接快照）。
        payload: ``{"category": str, "action": str, ...其余动作参数}``。

    Returns:
        ``{"ok": bool, "data": ..., "message": str}``。

    Raises:
        ValueError: category 非法、action 缺失或该分类不支持该 action。

    @author aceFelix
    """
    category = str(payload.get("category") or "").strip().lower()
    action = str(payload.get("action") or "").strip().lower()
    if category not in _CATEGORIES:
        raise ValueError(f"未知扩展分类: {category or '(空)'}")
    if not action:
        raise ValueError("缺少 action 参数")
    return _HANDLERS[category](settings, engine, action, payload)


# ---- 参数归一化辅助 ----


def _normalize_args(value: Any) -> list[str]:
    """把 args 的多种输入形态归一为字符串列表。

    支持前端传列表（``["-y", "pkg"]``）或整段字符串（按 shell 规则分词），
    空值返回空列表。@author aceFelix
    """
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _normalize_env(value: Any) -> dict[str, str]:
    """把 env 的多种输入形态归一为 ``{K: V}``。

    支持 dict 或整段字符串（每行 ``K=V``，忽略空行与 ``#`` 注释）。
    @author aceFelix
    """
    if not value:
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    env: dict[str, str] = {}
    for line in str(value).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key:
            env[key] = val.strip()
    return env


# ---- MCP 服务 ----


def _mcp_list(settings: Any, engine: Any) -> dict[str, Any]:
    """列出配置的 MCP server：配置项 + 禁用态 + 连接态 + 已加载工具名。"""
    from agent.core.extensions.mcp_client import (
        load_mcp_config,
        load_mcp_disabled,
        mcp_config_path,
    )

    config = load_mcp_config()
    disabled = load_mcp_disabled()
    status = getattr(engine, "mcp_status", None) or {}
    connected_names = set(status.get("connected") or [])

    # 运行中 MCP 客户端（可能未装配/已断开）：用于取每 server 的工具名清单
    client = getattr(engine, "_mcp_client", None)
    conns: dict[str, Any] = {}
    if client is not None and getattr(client, "available", False):
        try:
            conns = {c.name: c for c in client.list_connections()}
        except Exception:
            conns = {}

    servers: list[dict[str, Any]] = []
    for name, cfg in config.items():
        conn = conns.get(name)
        connected = name in connected_names or (conn is not None and conn.connected)
        tools: list[str] = []
        if conn is not None and conn.connected:
            try:
                tools = [t.name for t in conn.tools]
            except Exception:
                tools = []
        servers.append(
            {
                "name": name,
                "command": str(cfg.get("command") or ""),
                "args": [str(a) for a in (cfg.get("args") or [])],
                "env": {str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
                "disabled": name in disabled,
                "connected": bool(connected),
                "tools": tools,
            }
        )
    servers.sort(key=lambda x: x["name"])
    return {
        "ok": True,
        "data": {
            "config_path": str(mcp_config_path()),
            "enabled": bool(getattr(settings, "enable_mcp", True)),
            "servers": servers,
        },
    }


def _mcp_upsert(payload: dict[str, Any], *, overwrite: bool) -> dict[str, Any]:
    """新增/更新一个 MCP server 配置（写入 ~/.jarvis/mcp.json）。

    overwrite=False（add）时同名拒绝；overwrite=True（update）时覆盖。
    @author aceFelix
    """
    from agent.core.extensions.mcp_client import load_mcp_config, save_mcp_config

    name = str(payload.get("name") or "").strip()
    command = str(payload.get("command") or "").strip()
    if not name:
        return {"ok": False, "message": "server 名不能为空"}
    if not command:
        return {"ok": False, "message": "command 不能为空"}

    current = load_mcp_config()
    exists = name in current
    if exists and not overwrite:
        return {"ok": False, "message": f"MCP server '{name}' 已存在（可用编辑覆盖）"}

    cfg: dict[str, Any] = {"command": command}
    args = _normalize_args(payload.get("args"))
    if args:
        cfg["args"] = args
    env = _normalize_env(payload.get("env"))
    if env:
        cfg["env"] = env
    current[name] = cfg
    save_mcp_config({"mcpServers": current})
    verb = "已更新" if exists else "已添加"
    return {"ok": True, "message": f"{verb} MCP server: {name}（重启后生效）"}


def _mcp_remove(payload: dict[str, Any]) -> dict[str, Any]:
    """删除一个 MCP server（并从禁用名单清理残留）。@author aceFelix"""
    from agent.core.extensions.mcp_client import (
        load_mcp_disabled,
        remove_mcp_config,
        save_mcp_disabled,
    )

    name = str(payload.get("name") or "").strip()
    if not name:
        return {"ok": False, "message": "缺少 server 名"}
    removed = remove_mcp_config([name])
    disabled = load_mcp_disabled()
    if name in disabled:
        disabled.discard(name)
        save_mcp_disabled(disabled)
    if removed:
        return {"ok": True, "message": f"已移除 MCP server: {name}（重启后生效）"}
    return {"ok": False, "message": f"未找到 MCP server: {name}"}


def _mcp_set_disabled(payload: dict[str, Any], *, disabled: bool) -> dict[str, Any]:
    """启用/禁用一个 MCP server（写独立禁用名单，不改 mcp.json）。@author aceFelix"""
    from agent.core.extensions.mcp_client import (
        load_mcp_config,
        load_mcp_disabled,
        save_mcp_disabled,
    )

    name = str(payload.get("name") or "").strip()
    if not name:
        return {"ok": False, "message": "缺少 server 名"}
    if name not in load_mcp_config():
        return {"ok": False, "message": f"未找到 MCP server: {name}"}
    names = load_mcp_disabled()
    if disabled:
        names.add(name)
    else:
        names.discard(name)
    save_mcp_disabled(names)
    verb = "禁用" if disabled else "启用"
    return {"ok": True, "message": f"已{verb} MCP server: {name}（重启后生效）"}


def _handle_mcp(
    settings: Any, engine: Any, action: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """MCP 分类分发：list / add / update / remove / enable / disable。@author aceFelix"""
    if action == "list":
        return _mcp_list(settings, engine)
    if action in ("add", "update", "set"):
        return _mcp_upsert(payload, overwrite=(action != "add"))
    if action == "remove":
        return _mcp_remove(payload)
    if action == "enable":
        return _mcp_set_disabled(payload, disabled=False)
    if action == "disable":
        return _mcp_set_disabled(payload, disabled=True)
    raise ValueError(f"mcp 不支持的动作: {action}")


# ---- 内置工具（只读） ----


def _handle_tools(
    settings: Any, engine: Any, action: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """内置工具分类：仅 list（registry 里的工具由代码注册，不可增删）。@author aceFelix"""
    if action != "list":
        raise ValueError(f"tools 不支持的动作: {action}")
    registry = getattr(engine, "_active_registry", None)
    items: list[dict[str, Any]] = []
    if registry is not None:
        try:
            for tool in registry.all():
                desc = tool.description or ""
                first_line = desc.splitlines()[0] if desc else ""
                items.append({"name": tool.name, "description": first_line})
        except Exception:
            items = []
    items.sort(key=lambda x: str(x["name"]))
    return {"ok": True, "data": items}


# ---- 技能（只读） ----


def _handle_skills(
    settings: Any, engine: Any, action: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """技能分类：仅 list（技能目录清单 + 已加载技能）。@author aceFelix"""
    if action != "list":
        raise ValueError(f"skills 不支持的动作: {action}")
    from agent.core.extensions.skills import list_skill_files, load_skills

    workdir = settings.workdir or ""
    dirs: list[dict[str, Any]] = []
    for label, path in (list_skill_files(workdir) or {}).items():
        p = Path(path) if path else None
        dirs.append(
            {
                "label": str(label),
                "path": str(p) if p else "",
                "exists": bool(p and p.exists()),
            }
        )
    skills: list[dict[str, Any]] = []
    for s in load_skills(workdir):
        skills.append(
            {
                "name": s.name,
                "source": s.source,
                "description": s.description or "",
                "when_to_use": s.when_to_use or "",
            }
        )
    skills.sort(key=lambda x: str(x["name"]).lower())
    return {"ok": True, "data": {"dirs": dirs, "skills": skills}}


# ---- 插件 ----


def _plugin_manager(settings: Any) -> Any:
    """按 settings 的市场配置构造 PluginManager。@author aceFelix"""
    from agent.core.extensions.plugins import PluginManager

    return PluginManager(
        marketplace_url=settings.plugin_marketplace,
        marketplace_local=settings.plugin_market_local,
    )


def _handle_plugins(
    settings: Any, engine: Any, action: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """插件分类分发：list / search / install / uninstall / enable / disable。

    写操作含网络/git 克隆，仍在 serve 侧 ``asyncio.to_thread`` 的工作线程执行。
    @author aceFelix
    """
    pm = _plugin_manager(settings)

    if action == "list":
        installed = pm.list_installed().get("plugins", {}) or {}
        disabled = set(pm.list_disabled())
        items: list[dict[str, Any]] = []
        for name, p in installed.items():
            items.append(
                {
                    "name": p.get("name", name),
                    "version": p.get("version", ""),
                    "source": p.get("source", ""),
                    "installed_at": str(p.get("installed_at", ""))[:19],
                    "disabled": name in disabled,
                }
            )
        items.sort(key=lambda x: str(x["name"]).lower())
        return {"ok": True, "data": items}

    if action == "search":
        keyword = str(payload.get("keyword") or "").strip()
        results = pm.search(keyword)
        installed = set((pm.list_installed().get("plugins", {}) or {}).keys())
        data = [
            {
                "name": p.get("name", ""),
                "version": p.get("version", ""),
                "description": p.get("description", ""),
                "source": "本地" if p.get("_is_local") else "远程",
                "installed": p.get("name", "") in installed,
            }
            for p in results
        ]
        return {"ok": True, "data": data}

    name = str(payload.get("name") or "").strip()
    if not name:
        return {"ok": False, "message": "缺少插件名"}
    if action == "install":
        ok, msg = pm.install(name)
    elif action == "uninstall":
        ok, msg = pm.uninstall(name)
    elif action == "enable":
        ok, msg = pm.enable(name)
    elif action == "disable":
        ok, msg = pm.disable(name)
    else:
        raise ValueError(f"plugins 不支持的动作: {action}")
    return {"ok": bool(ok), "message": msg or ("操作成功" if ok else "操作失败")}


# ---- harness ----


def _handle_harness(
    settings: Any, engine: Any, action: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """harness 分类分发：list / market / install / uninstall / enable / disable。

    install/uninstall 会刷新工具表，故传入一次性 registry（不触碰引擎活动
    registry，避免跨线程竞态）；列表数据源始终为磁盘扫描。@author aceFelix
    """
    from agent.cli_anything import (
        install_harness,
        list_installed,
        list_market,
        uninstall_harness,
    )
    from agent.cli_anything.market import (
        disable_harness,
        enable_harness,
        is_harness_disabled,
    )

    workdir = settings.workdir or ""
    market_url = getattr(settings, "harness_market_url", "") or ""
    market_local = getattr(settings, "harness_market_local", "") or ""

    if action == "list":
        data = [
            {
                "id": h.get("id", ""),
                "name": h.get("name", ""),
                "description": h.get("description", ""),
                "disabled": is_harness_disabled(h.get("id", "")),
            }
            for h in list_installed(workdir=workdir or None)
        ]
        return {"ok": True, "data": data}

    if action == "market":
        data = [
            {
                "id": h.get("id", ""),
                "name": h.get("name", ""),
                "description": h.get("description", ""),
                "requires": h.get("requires", ""),
                "installed": h.get("installed", "否") == "是",
                "source": h.get("source", ""),
            }
            for h in list_market(
                custom_market_url=market_url, custom_market_local=market_local
            )
        ]
        return {"ok": True, "data": data}

    harness_id = str(payload.get("id") or "").strip()
    if not harness_id:
        return {"ok": False, "message": "缺少 harness ID"}

    if action == "install":
        registry = _temp_registry()
        result = install_harness(
            harness_id,
            registry,
            workdir=workdir or None,
            custom_market_url=market_url,
            custom_market_local=market_local,
        )
    elif action == "uninstall":
        registry = _temp_registry()
        result = uninstall_harness(harness_id, registry, workdir=workdir or None)
    elif action == "enable":
        result = enable_harness(harness_id)
    elif action == "disable":
        result = disable_harness(harness_id)
    else:
        raise ValueError(f"harness 不支持的动作: {action}")
    return {
        "ok": bool(result.get("success")),
        "message": str(result.get("message", "")),
    }


def _temp_registry() -> Any:
    """构造一次性工具注册表（供 harness 安装/卸载后刷新，不触碰引擎活动 registry）。

    @author aceFelix
    """
    from agent.core.tool import build_default_registry

    return build_default_registry()


# 分类 → 处理器
_HANDLERS: dict[str, Callable[[Any, Any, str, dict[str, Any]], dict[str, Any]]] = {
    "mcp": _handle_mcp,
    "tools": _handle_tools,
    "skills": _handle_skills,
    "plugins": _handle_plugins,
    "harness": _handle_harness,
}