"""权限校验主流程。

把"模式 / 规则 / 工具特判 / 路径守护 / Shell 分类"五者串成一个统一的判定管线。

判定顺序（fail-closed，安全侧优先）:
1. 工具特判（tool.check_permissions）—— 工具自己最懂自己
2. 路径守护（path_guard）—— 永远生效，返回 DENY
3. 模式覆写（mode）—— plan/yolo/accept_edits 的策略
4. 规则匹配（rules）—— allow/deny/ask 三态
5. 默认 ASK —— 没有任何 allow 命中时，询问用户

判定合并规则（同时命中多种结论时取"更安全"的那个）:
    DENY > ASK > ALLOW
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior, PermissionResult
from agent.core.tool import PermissionMatcher, Tool
from agent.permissions.modes import (
    AUTO_ALLOW_EDIT_TOOLS,
    PLAN_ALLOWED_TOOLS,
    PermissionMode,
)
from agent.permissions.path_guard import is_dangerous_write_path, is_symlink_escape
from agent.permissions.rules import RuleSet, RuleValue
from agent.permissions.shell_classifier import classify as classify_shell

# 安全合并: 多个判定取更严格那个
_SAFETY_RANK = {PermissionBehavior.ALLOW: 0, PermissionBehavior.ASK: 1, PermissionBehavior.DENY: 2}


def _stricter(a: PermissionResult, b: PermissionResult) -> PermissionResult:
    return a if _SAFETY_RANK[a.behavior] >= _SAFETY_RANK[b.behavior] else b


def _relax_by_mode(current: PermissionResult, mode_result: PermissionResult | None) -> PermissionResult:
    """模式覆写合并：模式可以放宽 ASK 为 ALLOW，但不能放宽 DENY。

    普通的 _stricter 会把 ASK 视为比 ALLOW 更严格，导致工具特判的 ASK
    永远压过 yolo 模式的 ALLOW——这与 yolo "自动放行非 deny"的设计意图相悖。
    此函数专门用于模式覆写步骤：若模式返回 ALLOW 且当前不是 DENY，则放行。
    DENY（路径守护硬拦截、危险命令、deny 规则）永远不可放宽。
    """
    if mode_result is None:
        return current
    if current.behavior == PermissionBehavior.DENY:
        return current  # DENY 不可放宽
    if mode_result.behavior == PermissionBehavior.ALLOW:
        return mode_result  # 模式放行，覆盖 ASK
    return _stricter(current, mode_result)


class PermissionChecker:
    """权限校验器。

    用法::

        checker = PermissionChecker(rules=load_rules("permissions.yaml"))
        result = checker.check(tool, args, ctx)
        if result.behavior == PermissionBehavior.ASK:
            # 弹 UI 询问用户
            ...
    """

    def __init__(
        self,
        rules: RuleSet | None = None,
        mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> None:
        from agent.permissions.rules import load_default_rules

        # 默认规则（危险目录拒绝）始终生效
        self._rules = load_default_rules().merge(rules) if rules else load_default_rules()
        self._mode = mode

    @property
    def mode(self) -> PermissionMode:
        return self._mode

    def with_mode(self, mode: PermissionMode) -> "PermissionChecker":
        """返回一个换了模式的 checker（用于子代理等场景）。"""
        return PermissionChecker(rules=self._rules, mode=mode)

    def check(self, tool: Tool, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """主校验入口。返回 ALLOW / DENY / ASK 之一。"""
        # 0. 先校验输入（不通过直接 DENY）
        v = tool.validate_input(args, ctx)
        if not v.ok:
            return PermissionResult.deny(f"输入校验失败: {v.message}")

        # 1. 工具特判（工具最懂自己）
        result = tool.check_permissions(args, ctx)

        # 2. 路径守护（对有 getPath 的工具做额外检查）
        path_result = self._check_path_guard(tool, args, ctx)
        if path_result is not None:
            result = _stricter(result, path_result)

        # 3. 模式覆写（用 _relax_by_mode：yolo 可放宽 ASK，不可放宽 DENY）
        mode_result = self._apply_mode(tool, args, ctx)
        if mode_result is not None:
            result = _relax_by_mode(result, mode_result)

        # 4. 规则匹配
        rule_result = self._match_rules(tool, args)
        if rule_result is not None:
            result = _stricter(result, rule_result)

        # 5. 默认 ASK（fail-closed）
        if result.behavior == PermissionBehavior.ASK and result.reason == "no tool-specific permission rule":
            result = PermissionResult.ask(
                f"无 allow 规则命中: 工具 {tool.name}（模式={self._mode.value}）"
            )

        return result

    # ---- 内部: 各子检查 ----

    def _check_path_guard(
        self, tool: Tool, args: dict[str, Any], ctx: ToolContext
    ) -> PermissionResult | None:
        """路径守护: 对写类工具检查目标路径。"""
        if tool.is_read_only(args):
            return None
        # 工具声明了 getPath 用它，否则尝试常见字段名
        raw_path = ""
        get_path = getattr(tool, "get_path", None)
        if callable(get_path):
            try:
                raw_path = get_path(args) or ""
            except Exception:
                raw_path = ""
        if not raw_path:
            raw_path = args.get("file_path") or args.get("path") or ""

        if not raw_path:
            return None

        dangerous, reason = is_dangerous_write_path(ctx.workdir, raw_path)
        if dangerous == "deny":
            return PermissionResult.deny(f"路径守护拦截: {reason}")
        if dangerous == "ask":
            return PermissionResult.ask(f"路径提醒: {reason}")

        esc, esc_reason = is_symlink_escape(ctx.workdir, raw_path)
        if esc:
            return PermissionResult.deny(f"路径守护拦截: {esc_reason}")
        return None

    def _apply_mode(
        self, tool: Tool, args: dict[str, Any], ctx: ToolContext
    ) -> PermissionResult | None:
        """模式策略覆写。返回 None 表示模式对此无意见。"""
        # plan 模式: 只放行只读白名单
        if self._mode == PermissionMode.PLAN:
            if tool.name in PLAN_ALLOWED_TOOLS or tool.is_read_only(args):
                return PermissionResult.allow("plan 模式放行只读工具")
            return PermissionResult.deny(f"plan 模式禁止写操作: {tool.name}")

        # accept_edits: 文件编辑工具自动放行
        if self._mode == PermissionMode.ACCEPT_EDITS:
            if tool.name in AUTO_ALLOW_EDIT_TOOLS:
                return PermissionResult.allow("accept_edits 模式放行文件编辑")
            return None  # 其他工具交给后续判定

        # yolo: 非 deny 的全部放行（deny 由规则/守护处理）
        if self._mode == PermissionMode.YOLO:
            return PermissionResult.allow("yolo 模式自动放行")

        return None

    def _match_rules(
        self, tool: Tool, args: dict[str, Any]
    ) -> PermissionResult | None:
        """规则匹配。按优先级 + deny 优先的顺序找第一条命中。"""
        matcher = tool.prepare_permission_matcher(args)
        for rule in self._rules.sorted_for_match():
            if self._rule_matches(tool, rule.pattern, args, matcher):
                if rule.value == RuleValue.ALLOW:
                    return PermissionResult.allow(f"规则命中: {rule.pattern}")
                if rule.value == RuleValue.DENY:
                    return PermissionResult.deny(f"规则命中: {rule.pattern}")
                return PermissionResult.ask(f"规则强制询问: {rule.pattern}")
        return None

    def _rule_matches(
        self,
        tool: Tool,
        pattern: str,
        args: dict[str, Any],
        matcher: PermissionMatcher | None,
    ) -> bool:
        """单条规则是否匹配当前工具调用。"""
        # 工具提供了自定义 matcher（如 BashTool 解析命令前缀）
        if matcher is not None:
            return matcher.matches(pattern)
        # 退化为工具名匹配
        import fnmatch
        import re

        m = re.match(r"^(\w+)(?:\((.*)\))?$", pattern.strip())
        if not m:
            return False
        rule_tool, rule_spec = m.group(1), m.group(2)
        if rule_tool != tool.name:
            return False
        if not rule_spec:
            return True
        # 对文件类工具，用 file_path 做通配匹配
        target = args.get("file_path") or args.get("path") or ""
        return bool(target) and fnmatch.fnmatchcase(target, rule_spec)


def classify_tool_for_shell(tool: Tool, args: dict[str, Any]) -> str:
    """便捷工具函数: 对 BashTool 入参做命令分类。"""
    if tool.name in ("Bash", "PowerShell"):
        cmd = args.get("command", "")
        return classify_shell(cmd)
    return "unknown"
