"""权限模式。

对应原项目 utils/permissions/PermissionMode.ts。模式是"全局策略开关"，
规则是"细粒度配置"。模式可以放宽 ASK（比如 accept_edits 自动放行写文件），
但不能放宽 DENY。

模式语义:
- default:       严格模式，所有非 allow 规则命中的操作都 ASK
- plan:          计划模式，只允许只读操作；写操作一律 DENY（用于先看后做）
- accept_edits:  自动放行文件编辑类工具（FileEdit/FileWrite），其他仍 ASK
- yolo:          自动放行所有非 deny 的操作（危险，仅可信环境用）

注意: 即使在 yolo 模式，DENY 规则和路径守护仍生效。
"""

from __future__ import annotations

from enum import Enum


class PermissionMode(str, Enum):
    DEFAULT = "default"
    PLAN = "plan"
    ACCEPT_EDITS = "accept_edits"
    YOLO = "yolo"


def parse_mode(s: str | None) -> PermissionMode:
    """从字符串解析模式，无效值回退到 DEFAULT（fail-safe）。"""
    if not s:
        return PermissionMode.DEFAULT
    try:
        return PermissionMode(s.lower())
    except ValueError:
        return PermissionMode.DEFAULT


# accept_edits 模式自动放行的"写类"工具
AUTO_ALLOW_EDIT_TOOLS = frozenset({"FileEdit", "FileWrite", "NotebookEdit"})

# plan 模式下视为"只读"、允许放行的工具白名单（其余一律拒绝）
PLAN_ALLOWED_TOOLS = frozenset(
    {"FileRead", "Glob", "Grep", "WebFetch", "WebSearch", "ListMcpResources", "ReadMcpResource"}
)
