"""yolo 模式的目标域限制。

## 为什么要这一层

原来的口径是「yolo = 自动放行所有非 deny 的操作」，DENY 永不可放宽——那是一条
干净的不变量，但代价是：连删 D 盘一个临时目录都要先改权限规则。

本模块把 yolo 从「全放行」改成「按目标域放行」：

- **目标不在受保护根（默认只有 C 盘）→ 一律放行**，连原本会 DENY 的危险动作也放行；
- **目标在受保护根且动作不可逆 → 转成一次确认**（点头后照做，不再挂 W3 窗口）；
- **硬禁止清单（默认 ~/.ssh、~/.aws、~/.gnupg）→ 永远 DENY**，连点头都不收。

## 已知局限（必须说清，不能假装严密）

目标是**从参数与命令行文本里扫出来的**，不是解析出来的执行计划。所以：

- `pushd C:\\ && del x` 这类先切目录的写法，扫不到 `C:\\` 后面的真实落点；
- 变量拼接（`set d=C:\\x` 再 `del %d%`）、软链、UNC 长路径前缀都可能绕过；
- 反过来会**误报**：命令里出现 `C:\\` 字样（哪怕只是打印一条消息）也会要求确认。

误报的方向是「多问一句」，可以接受；漏报的方向是「该问没问」，这才是风险。所以
yolo 的这层限制应当被理解为**降低误操作概率的安全带，不是安全边界**。真要防死，
该用账号隔离或沙箱，而不是文件路径匹配。

@author aceFelix
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from agent.core.tool import Tool

#: 从参数里取路径的候选键（不同工具命名不同，宁可多取）
_PATH_ARG_KEYS = (
    "file_path", "filepath", "path", "paths", "target", "dest", "destination",
    "src", "source", "directory", "dir", "cwd", "workdir", "project_dir",
    "output", "output_path",
)

#: 命令行文本里的 Windows 盘符路径（含引号外的裸片段，尽量多抓）
_DRIVE_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"'|;&)>\]]*")
#: 命令行文本里的 Unix 绝对路径
_POSIX_RE = re.compile(r"(?<![\w.-])/[^\s\"'|;&)\]]+")


def _norm(p: str) -> str:
    """归一化：展开 ~、统一分隔符、折叠重复斜杠、盘符折叠大小写并保留根反斜杠。"""
    s = re.sub(r"\\+", r"\\", os.path.expanduser(p.strip()).replace("/", "\\")).lower()
    if re.match(r"^[a-z]:$", s):
        return s + "\\"          # "c:" -> "c:\"（盘符根，不能丢反斜杠）
    if re.match(r"^[a-z]:\\", s):
        return s.rstrip("\\") + "\\"
    return s.rstrip("\\")


def extract_targets(tool: Tool, args: dict[str, Any]) -> list[str]:
    """尽量多地取出这次调用可能触碰到的路径。

    取不到就返回空表——空表不应被当成"安全"，调用方按"非受保护"处理，
    也就是放行；这与「非受保护根即放行」的规则一致，方向上是宽松的，
    但比让整条命令因为解析不出路径而卡住更符合 yolo 的使用场景。
    """
    found: list[str] = []
    for key in _PATH_ARG_KEYS:
        v = args.get(key)
        if isinstance(v, str) and v:
            found.append(v)
        elif isinstance(v, (list, tuple)):
            found.extend(str(x) for x in v if isinstance(x, str) and x)
    # 命令行文本：扫盘符与 Unix 绝对路径
    cmd = args.get("command") or args.get("script") or ""
    if isinstance(cmd, str) and cmd:
        found.extend(m.group(0) for m in _DRIVE_RE.finditer(cmd))
        found.extend(m.group(0) for m in _POSIX_RE.finditer(cmd))

    # 去重但保序
    seen: set[str] = set()
    out: list[str] = []
    for p in found:
        n = _norm(p)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def under_any(targets: list[str], roots: list[str]) -> str | None:
    """任一目标落在受保护根下，返回命中的根；否则 None。"""
    norm_roots = [_norm(r) for r in roots if r]
    for t in targets:
        for r in norm_roots:
            if t == r or t.startswith(r):
                return r
    return None


def matches_hard_deny(targets: list[str], patterns: list[str]) -> str | None:
    """硬禁止清单匹配（glob，按 ~ 展开）。命中则连确认都不给。"""
    if not patterns:
        return None
    home = str(Path.home()).replace("/", "\\").lower()
    for pat in patterns:
        p = pat.replace("/", "\\").lower()
        if p.startswith("~\\") or p == "~":
            p = home + p[1:]
        rx = re.escape(p).replace(r"\*\*", ".*").replace(r"\*", "[^\\\\]*")
        for t in targets:
            if re.fullmatch(rx, t):
                return pat
    return None


def is_guarded(tool: Tool, args: dict[str, Any], roots: list[str]) -> str | None:
    """这次调用的目标是否落在受保护根内。"""
    if not roots:
        return None
    return under_any(extract_targets(tool, args), roots)
