"""配置文件写入护栏：拦住"绕开 ConfigSet 直接改文件"。

## 为什么必须有（2026-11 实测）

禁改清单原本只挂在 `ConfigSet` 上。模型完全可以不调用它，改用 `FileEdit` 直接改
`~/.jarvis/models.toml` —— 那条路上**没有任何一层**会看它改的是哪个键。这次它自觉
没绕（提示词说了不许），但那是规矩，不是护栏。

判定位置很关键：能改/不能改的知识在配置层，真正写文件的通道在文件层，两层不通 →
任何"不走 ConfigSet 的写法"都自动绕过整套清单。本模块把这条缝补上。

## 判据

- 目标是配置文件（`settings.toml` / `models.toml` / `permissions_file` 指向的 YAML）；
- **并且**要写的键命中禁改清单（`api_key` / `*_password` / `token` / `permissions_*` /
  `sandbox_*` …），或者目标本身就是权限规则文件。

结论是终局 DENY：任何模式都不放，**包括 yolo**——这些是护栏本身，点头也不授权拆监督机制。

口径与 `config_forbidden.py` 同源（同一个 `forbidden_reason`），不另立清单。

@author aceFelix
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent.config.config_forbidden import forbidden_fix, forbidden_reason
from agent.core.result import PermissionResult

#: 认得的配置文件（按文件名）
CONFIG_FILE_NAMES = frozenset({"settings.toml", "models.toml"})

#: TOML 里"这一行在给某个键赋值"
_ASSIGN_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*=", re.MULTILINE)
#: 段头，如 ``[sandbox]`` / ``[llm.models]``
_SECTION_RE = re.compile(r"^\s*\[([^\]\n]+)\]\s*$", re.MULTILINE)

#: Bash 里对配置文件做了写动作的迹象（保守：命中就拦，提示改用 FileEdit 或手改）
_BASH_WRITE_MARKERS = (">", "sed -i", "tee ", "cp ", "mv ", ">>")


def _target_path(args: dict[str, Any]) -> str:
    for key in ("file_path", "filepath", "path"):
        v = args.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


def _permissions_file(ctx: Any) -> str:
    settings = getattr(ctx, "settings", None)
    v = getattr(settings, "permissions_file", "") if settings is not None else ""
    return str(v or "")


def _same_file(a: str, b: str) -> bool:
    if not (a and b):
        return False
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return Path(a).name.lower() == Path(b).name.lower()


def _keys_written(tool_name: str, args: dict[str, Any]) -> list[str]:
    """从写入内容里抽出"这次要改哪些键"，含段前缀的拼合形式。

    为什么要拼：禁改清单用的是**扁平属性名**（``sandbox_enabled``），而 TOML 里写的是
    ``[sandbox]`` + ``enabled``。只看裸键名会漏判——实测 ``[sandbox] enabled = false``
    就被放过去了。
    """
    text = ""
    if tool_name in ("FileWrite", "Write"):
        text = str(args.get("content") or "")
    elif tool_name in ("FileEdit", "Edit"):
        text = str(args.get("new_string") or args.get("new_str") or "")
    if not text:
        return []

    keys: list[str] = []
    section = ""
    for line in text.splitlines():
        sec = _SECTION_RE.match(line)
        if sec:
            section = sec.group(1).strip().strip('"').replace(".", "_")
            continue
        m = _ASSIGN_LINE_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        keys.append(key)
        if section:
            keys.append(f"{section}_{key}")
    return keys


def check(tool: Any, args: dict[str, Any], ctx: Any) -> PermissionResult | None:
    """返回终局判定（DENY）或 None（放行，交给后面的层）。"""
    path = _target_path(args)
    perm_file = _permissions_file(ctx)

    if path and perm_file and _same_file(path, perm_file):
        return PermissionResult.deny(
            "权限规则文件不能通过写文件改：它就是护栏本身。"
            f"正确改法：{forbidden_fix('permissions_file')}"
        )

    is_file_tool = tool.name in ("FileWrite", "Write", "FileEdit", "Edit")
    name = Path(path).name.lower() if path else ""

    # 非文件工具（Bash 等）：看不清改的是哪个键，命中配置文件就是保守拦下。
    if not is_file_tool:
        cmd = str(args.get("command") or "")
        low = cmd.lower()
        hit = any(n in low for n in CONFIG_FILE_NAMES) or (
            perm_file and Path(perm_file).name.lower() in low
        )
        if hit and any(m in cmd for m in _BASH_WRITE_MARKERS):
            return PermissionResult.deny(
                "配置文件不能用命令行改写（看不清改了哪个键）。改用 FileEdit 精确改非护栏项，"
                "或让用户手改；护栏项一律不许模型改。"
            )
        return None

    if name not in CONFIG_FILE_NAMES:
        return None

    for key in _keys_written(tool.name, args):
        why = forbidden_reason(key)
        if why:
            fix = forbidden_fix(key)
            tail = f"正确改法：{fix}" if fix else "请让用户自己改。"
            return PermissionResult.deny(
                f"{name} 里的 {key} 属于禁改项：{why}。{tail}"
            )
    return None
