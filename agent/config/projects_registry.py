"""桌面项目工作区的最近项目持久化（~/.jarvis/projects.toml）。

桌面 jarvis 支持「选择文件夹作为项目 → 在其中聊天开发」。为让重启后回到
上次项目、左栏展示最近项目列表，这里维护一个轻量登记表：

- ``[[project]]`` 数组：每项 ``{path, name, last_opened}``，按 last_opened 倒序；
- 顶层 ``last_active``：最后活跃项目的绝对路径（serve 启动据此恢复 workdir）。

与 models.toml / settings.toml 分域独立，不动 settings 语义。写入采用整份
重排（数据量小、结构简单），读取用 tomllib（3.11+，旧版回退 tomli）。

@author aceFelix
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

# 列表上限：过多最近项目无实际价值，截断保持文件轻量
_MAX_RECENT = 30


def _toml_path() -> Path:
    """projects.toml 路径（每次现取，便于测试 monkeypatch Path.home）。"""
    return Path.home() / ".jarvis" / "projects.toml"


def _escape(value: str) -> str:
    """TOML 基本字符串转义（反斜杠与双引号）；Windows 路径含反斜杠必需。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _load_raw() -> dict:
    """读取并解析 projects.toml；文件缺失/损坏返回空 dict（不抛，调用方降级）。"""
    path = _toml_path()
    if not path.is_file():
        return {}
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        return tomllib.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def list_projects() -> list[dict]:
    """最近项目列表 ``[{path, name, last_opened}]``，按 last_opened 倒序。

    磁盘上已不存在的目录仍返回（前端据此显示"失效"标记，用户可手动 forget）；
    是否失效由调用方用 Path(path).is_dir() 现判，本模块不越权探测。

    @author aceFelix
    """
    data = _load_raw()
    items = data.get("project") or []
    result: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        p = str(it.get("path") or "")
        if not p:
            continue
        result.append({
            "path": p,
            "name": str(it.get("name") or Path(p).name),
            "last_opened": str(it.get("last_opened") or ""),
        })
    result.sort(key=lambda x: x["last_opened"], reverse=True)
    return result


def get_last_active() -> str:
    """最后活跃项目的绝对路径；无记录返回空串（调用方据此决定是否覆盖 workdir）。

    @author aceFelix
    """
    return str(_load_raw().get("last_active") or "")


def get_last_active_existing() -> str:
    """last_active 且目录仍存在时返回其路径，否则空串（serve 启动恢复默认 workdir）。

    桌面 ``python -m agent.serve`` 无 --workdir 概念，启动直接据此"重开续用
    上次项目"；目录被删/移动则回退（返回空串，调用方保留进程默认 workdir）。

    @author aceFelix
    """
    last = get_last_active()
    return last if last and Path(last).is_dir() else ""


def touch_project(path: str) -> dict:
    """登记/置顶一个项目：更新其 last_opened、移到最近列表首位、设为 last_active。

    Args:
        path: 项目绝对路径（调用方已校验存在且为目录）。

    Returns:
        ``{path, name, last_opened}`` 落库后的项目记录。

    @author aceFelix
    """
    path = str(Path(path))
    now = datetime.now().isoformat(timespec="seconds")
    record = {"path": path, "name": Path(path).name, "last_opened": now}
    existing = [p for p in list_projects() if p["path"] != path]
    recent = [record] + existing
    recent = recent[:_MAX_RECENT]
    _write(recent, last_active=path)
    return record


def forget(path: str) -> bool:
    """从最近列表移除某项目（不删磁盘目录）；若它正是 last_active 则一并清除。

    Returns:
        True 表示确有该记录被移除，False 表示原本就不存在。

    @author aceFelix
    """
    path = str(Path(path))
    current = list_projects()
    remaining = [p for p in current if p["path"] != path]
    if len(remaining) == len(current):
        return False
    last_active = get_last_active()
    if last_active == path:
        last_active = ""
    _write(remaining, last_active=last_active)
    return True


def _write(recent: list[dict], *, last_active: str) -> None:
    """整份重写 projects.toml（结构小而稳定，重排比外科式 upsert 更可靠）。"""
    path = _toml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["# 桌面 jarvis 最近项目登记（自动生成，可手动清理失效项）", ""]
    if last_active:
        lines.append(f'last_active = "{_escape(last_active)}"')
        lines.append("")
    for it in recent:
        lines.append("[[project]]")
        lines.append(f'path = "{_escape(it["path"])}"')
        lines.append(f'name = "{_escape(it["name"])}"')
        lines.append(f'last_opened = "{_escape(it["last_opened"])}"')
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
