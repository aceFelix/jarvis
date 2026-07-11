"""权限系统。

对应原项目 utils/permissions/（8000+ 行）。这里大幅精简，保留核心:
- modes.py:    权限模式（default/plan/accept_edits/yolo）
- rules.py:    三态规则模型（allow/deny/ask）+ 来源层级
- checker.py:  权限校验主流程（规则匹配 + 模式覆写 + 工具特判）
- path_guard.py: 路径守护（危险目录、符号链接逃逸）
- shell_classifier.py: Shell 命令分类（只读白名单 + 危险模式）

设计原则（fail-closed）:
- 没有 allow 规则命中时，默认 ASK
- deny 规则永远生效，任何模式都无法绕过
- yolo 模式才会跳过 ASK（但仍受 deny 约束）
- 路径守护与模式无关，永远生效
"""

from agent.permissions.checker import PermissionChecker
from agent.permissions.modes import PermissionMode, parse_mode
from agent.permissions.rules import RuleSet, load_rules

__all__ = [
    "PermissionChecker",
    "PermissionMode",
    "parse_mode",
    "RuleSet",
    "load_rules",
]
