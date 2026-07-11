"""权限规则模型。

对应原项目 utils/permissions/PermissionRule.ts + permissionRuleParser.ts。

规则格式: "ToolName(spec)"
- spec 支持 fnmatch 通配符: Bash(git *), Read(src/**), Write(~/.ssh/*)
- 无 spec 的规则: Bash -> 匹配该工具任意调用

三态:
- allow: 命中则放行
- deny:  命中则拒绝（最高优先级，无法被模式放宽）
- ask:   命中则强制询问用户

来源层级（优先级从高到低）:
- enterprise: 企业托管（最高，不可被覆盖）
- user:       用户全局配置
- project:    项目本地配置
- session:    当前会话临时记忆（最低，用户在会话中"总是允许"时写入）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class RuleValue(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class RuleSource(str, Enum):
    ENTERPRISE = "enterprise"
    USER = "user"
    PROJECT = "project"
    SESSION = "session"


# 来源优先级（数值越大优先级越高）
_SOURCE_PRIORITY = {
    RuleSource.ENTERPRISE: 100,
    RuleSource.USER: 50,
    RuleSource.PROJECT: 30,
    RuleSource.SESSION: 10,
}


@dataclass
class PermissionRule:
    """单条权限规则。

    Attributes:
        pattern: 规则模式，如 "Bash(git *)" 或 "Read"
        value:   allow / deny / ask
        source:  来源层级
    """

    pattern: str
    value: RuleValue
    source: RuleSource = RuleSource.SESSION

    @property
    def priority(self) -> int:
        return _SOURCE_PRIORITY[self.source]


@dataclass
class RuleSet:
    """规则集合。提供按优先级排序后的查找。

    设计: 同一个工具调用可能命中多条规则，取优先级最高那条；
    优先级相同时 deny > ask > allow（更安全的胜出）。
    """

    rules: list[PermissionRule] = field(default_factory=list)

    def add(self, rule: PermissionRule) -> None:
        self.rules.append(rule)

    def sorted_for_match(self) -> list[PermissionRule]:
        """返回按优先级降序、同优先级 deny 优先的规则列表。"""
        tie_break = {RuleValue.DENY: 0, RuleValue.ASK: 1, RuleValue.ALLOW: 2}
        return sorted(
            self.rules,
            key=lambda r: (-r.priority, tie_break[r.value]),
        )

    def merge(self, other: RuleSet) -> RuleSet:
        """合并另一规则集（用于多源加载）。"""
        return RuleSet(rules=self.rules + other.rules)


def _parse_value(v: str) -> RuleValue:
    v = v.lower().strip()
    if v not in ("allow", "deny", "ask"):
        raise ValueError(f"无效的规则值: {v}（应为 allow/deny/ask）")
    return RuleValue(v)


def _parse_source(s: str | None) -> RuleSource:
    if s is None:
        return RuleSource.PROJECT
    try:
        return RuleSource(s.lower())
    except ValueError:
        return RuleSource.PROJECT


def load_rules(path: str | Path) -> RuleSet:
    """从 YAML 文件加载规则。

    YAML 格式示例::

        rules:
          - pattern: "Bash(git *)"
            value: allow
            source: user
          - pattern: "Read(~/.ssh/**)"
            value: deny
            source: enterprise
          - pattern: "Bash"
            value: ask
            source: project
    """
    p = Path(path)
    if not p.exists():
        return RuleSet()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw_rules = data.get("rules", [])
    rs = RuleSet()
    for item in raw_rules:
        pattern = item.get("pattern")
        if not pattern:
            continue
        rs.add(
            PermissionRule(
                pattern=pattern,
                value=_parse_value(item["value"]),
                source=_parse_source(item.get("source")),
            )
        )
    return rs


def load_default_rules() -> RuleSet:
    """加载默认规则: 危险目录/文件一律拒绝。"""
    rs = RuleSet()
    # 这些是硬编码的"永远拒绝"规则，保护用户系统
    deny_patterns = [
        "Write(~/.ssh/**)",
        "Write(~/.aws/**)",
        "Write(~/.gnupg/**)",
        "Bash(rm -rf /*)",
        "Bash(rm -rf ~)",
        "Bash(sudo rm *)",
        "Bash(:(){ :|:& };:)",  # fork bomb
        "Bash(mkfs*)",
    ]
    for pat in deny_patterns:
        rs.add(PermissionRule(pattern=pat, value=RuleValue.DENY, source=RuleSource.ENTERPRISE))
    return rs
