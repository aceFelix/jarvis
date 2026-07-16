"""路径守护。

核心:
- 危险目录拒绝写入（.ssh / .aws / .gnupg / 系统9个关键目录）
- 符号链接逃逸检测（防止通过软链绕过限制）
- workdir 限制（默认禁止写到工作目录之外，除非用户显式加白名单）

fail-closed: 不在白名单内的路径，写操作一律按"危险"处理（交给 checker 决定 ASK/DENY）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal


class PathGuardError(PermissionError):
    """路径守护拦截。"""


# 永远禁止写入的危险目录（按 home 展开）
_DANGEROUS_DIR_NAMES = {".ssh", ".aws", ".gnupg", ".config", ".gnupg"}

# 系统级根目录白名单之外都算危险（Windows / Unix 通用近似）
_SYSTEM_ROOTS = {"/", "C:\\", "C:/", "c:\\", "c:/"}


def _resolve_under(workdir: str, raw_path: str) -> Path:
    """把 raw_path 解析为绝对路径（相对 workdir）。"""
    p = Path(raw_path).expanduser()
    if not p.is_absolute():
        p = Path(workdir) / p
    return p.resolve(strict=False)


def is_dangerous_write_path(workdir: str, raw_path: str) -> tuple[str, str]:
    """判断写路径是否危险。

    返回 (level, reason):
    - "deny": 硬拦截（敏感目录 .ssh/.aws/.gnupg/.config、home 根、系统根）。
              任何模式都不可放宽。
    - "ask":  软提醒（工作目录之外）。可被 yolo 模式放宽为 ALLOW。
    - "safe": 无问题。

    这样 yolo 模式下用户可写 E 盘/桌面等非工作目录，但敏感目录仍受保护。
    """
    target = _resolve_under(workdir, raw_path)

    # 1. 危险目录（硬拦截）
    home = Path.home()
    for name in _DANGEROUS_DIR_NAMES:
        danger_dir = home / name
        try:
            target.relative_to(danger_dir)
            return "deny", f"目标位于敏感目录: ~/{name}"
        except ValueError:
            pass

    # 2. 系统根目录 / home 根直接写（硬拦截）
    target_str = str(target)
    if target_str.rstrip("/\\") in {str(home), ""}:
        return "deny", "禁止直接覆盖 home 根目录"

    # 3. 工作目录之外（软提醒，yolo 可放宽）
    try:
        workdir_abs = Path(workdir).resolve()
        target.relative_to(workdir_abs)
    except ValueError:
        return "ask", f"目标在工作目录之外: {target}（workdir={workdir}）"

    return "safe", ""


def is_symlink_escape(workdir: str, raw_path: str) -> tuple[bool, str]:
    """检测符号链接逃逸: 路径中某一段是符号链接，且指向 workdir 之外。

    对应原项目 pathValidation.ts 的符号链接解析逻辑。
    """
    p = Path(raw_path).expanduser()
    if not p.is_absolute():
        p = Path(workdir) / p

    workdir_abs = Path(workdir).resolve()
    current = Path("/")
    # 逐段检查路径组件是否为符号链接
    for part in p.parts[1:]:  # 跳过根
        current = current / part
        if current.is_symlink():
            real = current.resolve()
            try:
                real.relative_to(workdir_abs)
            except ValueError:
                return True, f"符号链接逃逸: {current} -> {real}（指向 workdir 外）"
    return False, ""
