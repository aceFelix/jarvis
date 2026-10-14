"""路径 glob 匹配公共工具 —— 权限规则与授权租约共用。

提供家目录展开、平台对齐的大小写归一、glob 匹配。大小写语义随平台
文件系统对齐（Windows 不敏感→归一；POSIX 敏感→保留原样），与
core/checkpoint.py::_workdir_key 同一口径，避免跨平台 CI 翻车。

原为 core/policy/patterns.py（策略引擎与租约共用）；策略引擎并入
五层管线后迁到 permissions/ 供管线规则与租约共同使用。

@author aceFelix
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path


def norm_case(path: str) -> str:
    """按平台文件系统语义归一路径大小写。

    Windows 大小写不敏感 → 统一小写；POSIX 保留原样。
    @author aceFelix
    """
    return path.lower() if os.name == "nt" else path


def expand(pattern: str) -> str:
    """展开 ~ 与环境变量并归一分隔符，返回可比较形态（不 resolve，容忍不存在路径）。

    normpath 统一混用分隔符（~/x/** 在 Windows 展开后为 C:\\Users\\x/y/**，
    不归一会让 fnmatch/前缀比较全部失配），* 通配符段不受影响。
    @author aceFelix
    """
    p = os.path.expanduser(os.path.expandvars(pattern))
    try:
        p = os.path.normpath(p)
    except (OSError, ValueError):
        pass
    return p


def norm_path(raw: str, workdir: str = "") -> str:
    """把工具入参路径归一为可比较形态：展开 ~ → 绝对化 → 平台大小写归一。

    绝对化基于 workdir（相对路径属于当前工作目录）；失败时退回原值。
    @author aceFelix
    """
    if not raw:
        return ""
    p = Path(expand(raw))
    try:
        if not p.is_absolute():
            base = Path(workdir) if workdir else Path.cwd()
            p = base / p
        p = Path(os.path.normpath(str(p)))
    except OSError:
        return norm_case(raw)
    return norm_case(str(p))


def path_matches(patterns: list[str], target: str, workdir: str = "") -> bool:
    """目标路径是否命中任一 glob 模式。

    模式语义：`~/work/**` 匹配该目录内任意深度；`~/work/*` 只匹配一层。
    另外约定：目录模式（以 / 或 \\ 结尾或 `dir/**`）命中其自身与子孙。
    @author aceFelix
    """
    t = norm_path(target, workdir)
    if not t:
        return False
    for pat in patterns or []:
        pn = norm_case(expand(pat))
        # "dir/**" 同时匹配 dir 本身（用户心智：目录内=含目录）
        trimmed = pn.rstrip("/\\")
        if trimmed and fnmatch.fnmatchcase(t, trimmed):
            return True
        if fnmatch.fnmatchcase(t, pn):
            return True
        # dir/** 语义：前缀命中目录内部任意深度
        if pn.endswith("/**") and (t + os.sep).startswith(norm_case(trimmed) + os.sep):
            return True
    return False
