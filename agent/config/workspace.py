"""工作目录解析：默认工作区、安全判定与回退。

## 为什么需要它（2026-11-20 实机）

旧口径是「workdir 默认取进程 cwd」。对开发者顺手（``cd project && jarvis``），
但对刚装好的用户是个坑：

- 从 ``C:\\Windows\\System32`` / ``C:\\Program Files`` 启动 → 系统目录：无写权限
  会让项目级会话记忆直接抛 ``PermissionError`` 打崩启动，检查点还会去遍历数万
  文件把主循环卡死；
- 从用户主目录根启动 → 把整个 home 当项目，``git add -A`` 与 ``/rewind`` 的
  影响面远超预期。

## 新口径（三档）

1. **显式指定**（``--workdir`` / 配置里的 ``workdir`` / 桌面最近项目）→ 原样采用；
2. **当前目录是普通项目目录** → 继续用它（保留 ``cd project && jarvis`` 的习惯）；
3. **当前目录是系统目录 / 盘根 / 主目录根** → 回退到 ``~/.jarvis/workspace``
   （自动创建），并给用户一条可读提示（由宿主首屏打印一次）。

@author aceFelix
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: 默认工作区目录名（位于 ``~/.jarvis/`` 下，与数据同根，一定可写）。
DEFAULT_WORKSPACE_NAME = "workspace"

#: 不能当工作区的系统目录前缀（normcase + normpath 后比较）。
_UNSAFE_PREFIXES = (
    "c:\\windows",
    "c:\\program files",
    "c:\\program files (x86)",
    "c:\\programdata",
)

#: 最近一次回退的提示语（空串 = 没回退）。宿主用 consume_workdir_note 取走。
_WORKDIR_NOTE = ""


def default_workspace() -> Path:
    """默认工作区：``~/.jarvis/workspace``。"""
    return Path.home() / ".jarvis" / DEFAULT_WORKSPACE_NAME


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def unsafe_reason(path: Path | str) -> str:
    """返回该目录不适合当工作区的原因（人类可读）；安全则返回空串。

    只做字符串判定，不遍历目录、不做写探测——预检本身不能变成新的卡点。
    @author aceFelix
    """
    norm = _norm(path)
    if re.fullmatch(r"[a-z]:\\", norm):
        return "盘根目录会把整个磁盘当项目"
    home = Path.home()
    if norm == _norm(home):
        return "用户主目录根会把桌面/下载/文档一起当项目"
    if norm == _norm(home.parent):
        return "用户目录的父级不能当项目"
    for prefix in _UNSAFE_PREFIXES:
        if norm == prefix or norm.startswith(prefix + "\\"):
            return f"系统目录 {path}"
    return ""


def resolve_workdir(requested: str | None = None) -> Path:
    """解析本次运行的 workdir；需要回退时记下一条提示语。

    Args:
        requested: 显式指定的目录（CLI / 配置 / 桌面最近项目）；空或 None 表示
            没有显式指定，按「cwd → 默认工作区」两级判定。

    Returns:
        绝对路径的 Path。显式值原样采用；cwd 安全则用 cwd；否则用默认工作区。

    @author aceFelix
    """
    global _WORKDIR_NOTE
    _WORKDIR_NOTE = ""
    if requested:
        return Path(requested).expanduser().resolve()
    cwd = Path.cwd().resolve()
    reason = unsafe_reason(cwd)
    if not reason:
        return cwd
    target = default_workspace()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 极端环境（home 不可写）：保留旧行为，交给会话记忆/检查点的降级兜住
        return cwd
    _WORKDIR_NOTE = f"当前目录不适合当工作区（{reason}），已改用 {target}"
    return target


def consume_workdir_note() -> str:
    """取走并清空最近一次回退提示（宿主首屏打印一次，避免每轮刷屏）。"""
    global _WORKDIR_NOTE
    note, _WORKDIR_NOTE = _WORKDIR_NOTE, ""
    return note
