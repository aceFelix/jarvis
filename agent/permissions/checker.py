"""权限校验主流程。

把"模式 / 规则 / 工具特判 / 路径守护 / Shell 分类"五者串成一个统一的判定管线。

判定顺序（fail-closed，安全侧优先）:
1. 工具特判（tool.check_permissions）—— 工具自己最懂自己
2. 路径守护（path_guard）—— 永远生效，返回 DENY
3. 模式覆写（mode）—— plan/yolo/accept_edits 的策略
4. 规则匹配（rules）—— allow/deny/ask 三态；带 spec 的 allow 规则可放宽
   工具默认 ASK/特判 ASK（对齐 yolo 的放宽口径，DENY 永不可放宽）；
   跨 workdir 边界的软提醒 ASK，仅当 allow 规则的 spec 明确覆盖
   目标路径时才可放宽（显式白名单语义）
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
from agent.permissions.path_match import path_matches
from agent.permissions.rules import PermissionRule, RuleSet, RuleValue
from agent.permissions.shell_classifier import classify as classify_shell

# 安全合并: 多个判定取更严格那个
_SAFETY_RANK = {PermissionBehavior.ALLOW: 0, PermissionBehavior.ASK: 1, PermissionBehavior.DENY: 2}

# 管线默认 ASK（fail-closed 兜底）的固定 reason：工具没有特判意见时才会出现，
# 裸名 allow 规则据此判断能否兜底放宽。
_DEFAULT_ASK_REASON = "no tool-specific permission rule"


def _stricter(a: PermissionResult, b: PermissionResult) -> PermissionResult:
    return a if _SAFETY_RANK[a.behavior] >= _SAFETY_RANK[b.behavior] else b


def _split_rule_pattern(pattern: str) -> tuple[str, str]:
    """拆解规则模式 "ToolName(spec)" → (工具名, spec 或空串)。

    无法解析时返回 (原串, "")。
    @author aceFelix
    """
    import re

    m = re.match(r"^(\w+)(?:\((.*)\))?$", pattern.strip())
    if not m:
        return pattern.strip(), ""
    return m.group(1), m.group(2) or ""


def _relax_by_rule(
    current: PermissionResult,
    rule_result: PermissionResult,
    *,
    rule_has_spec: bool,
    from_path_guard: bool,
) -> PermissionResult:
    """allow 规则命中时的合并：可放宽 ASK，不可放宽 DENY。

    旧行为用 _stricter，导致 allow 规则永远压不过工具默认 ASK（ASK 比
    ALLOW "更严"），用户必须在 yolo / 逐次按 a 之间二选一——permissions.yaml
    形同虚设。现在带 spec 的 allow 规则（如 Bash(git *)、FileWrite(~/reports/**)）
    命中即可放宽工具特判 ASK，口径对齐 yolo（DENY 仍永不可放宽）。

    限制（保守侧）：
    - 无 spec 的裸工具名 allow（如裸 "Bash"）只能放宽管线默认 ASK（工具
      无特判意见），不能放宽工具特判 ASK——后者相当于隐式 yolo；
    - 路径守护软 ASK（写 workdir 外）默认不放宽——意外逃逸应保持人工
      确认；但带 spec 的 allow 规则命中同一目标时放宽（与守护自身
      "除非用户显式加白名单"的设计一致；授权租约仍不放宽租约侧另判）。
      识别守护 ASK 不能用 reason 前缀：等秩 ASK 合并走 _stricter 保留
      先者，守护 reason 会被工具默认 reason 覆盖丢失，必须用 check()
      里维护的显式标记 + spec 覆盖判断。
    @author aceFelix
    """
    if current.behavior == PermissionBehavior.DENY:
        return current  # DENY 不可放宽
    if current.behavior == PermissionBehavior.ASK:
        if from_path_guard:
            # 跨边界软提醒：只有 spec 明确覆盖目标（白名单）才放宽
            return rule_result if rule_has_spec else current
        if rule_has_spec:
            return rule_result
        # 裸名 allow：仅当工具本身无特判意见（管线默认 ASK）时兜底放行
        if current.reason == _DEFAULT_ASK_REASON:
            return rule_result
        return current
    return _stricter(current, rule_result)


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
        path_result, guard_raw_path = self._check_path_guard(tool, args, ctx)
        # 显式标记"当前 ASK 来自路径守护"：等秩 ASK 合并保留先者会把守护
        # reason 覆盖丢失，后续放宽判断不能依赖 reason 字符串。
        guard_soft_ask = path_result is not None and path_result.behavior == PermissionBehavior.ASK
        if path_result is not None:
            result = _stricter(result, path_result)

        # 3. 模式覆写（用 _relax_by_mode：yolo 可放宽 ASK，不可放宽 DENY）
        mode_result = self._apply_mode(tool, args, ctx)
        if mode_result is not None:
            result = _relax_by_mode(result, mode_result)

        # 4. 规则匹配：deny/ask 取更严侧；带 spec 的 allow 可放宽工具 ASK
        rule_hit = self._match_rules(tool, args, ctx.workdir)
        if rule_hit is not None:
            if rule_hit.value == RuleValue.ALLOW:
                # spec 有无与"是否明确覆盖目标"取自命中规则本身：
                # 分别决定裸名 allow 的放宽范围、跨边界软提醒能否放宽
                _, _spec = _split_rule_pattern(rule_hit.pattern)
                _spec_covers_target = bool(_spec) and (
                    not guard_soft_ask
                    or path_matches([_spec], guard_raw_path or "", ctx.workdir)
                    or path_matches([_spec], args.get("file_path") or args.get("path") or "", ctx.workdir)
                )
                result = _relax_by_rule(
                    result,
                    PermissionResult.allow(f"规则命中: {rule_hit.pattern}"),
                    rule_has_spec=_spec_covers_target,
                    from_path_guard=guard_soft_ask,
                )
            elif rule_hit.value == RuleValue.ASK:
                result = _stricter(result, PermissionResult.ask(f"规则强制询问: {rule_hit.pattern}"))
            else:
                result = _stricter(result, PermissionResult.deny(f"规则命中: {rule_hit.pattern}"))

        # 5. 默认 ASK（fail-closed）
        if result.behavior == PermissionBehavior.ASK and result.reason == _DEFAULT_ASK_REASON:
            result = PermissionResult.ask(
                f"无 allow 规则命中: 工具 {tool.name}（模式={self._mode.value}）"
            )

        return result

    # ---- 内部: 各子检查 ----

    def _check_path_guard(
        self, tool: Tool, args: dict[str, Any], ctx: ToolContext
    ) -> tuple[PermissionResult | None, str]:
        """路径守护: 对写类工具检查目标路径。

        返回 (判定结果或 None, 守护实际使用的原始路径)：后者供规则放宽
        时判断 allow spec 是否明确覆盖了跨边界目标（白名单语义）。
        @author aceFelix
        """
        if tool.is_read_only(args):
            return None, ""
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
            return None, ""

        dangerous, reason = is_dangerous_write_path(ctx.workdir, raw_path)
        if dangerous == "deny":
            return PermissionResult.deny(f"路径守护拦截: {reason}"), raw_path
        if dangerous == "ask":
            return PermissionResult.ask(f"路径提醒: {reason}"), raw_path

        esc, esc_reason = is_symlink_escape(ctx.workdir, raw_path)
        if esc:
            return PermissionResult.deny(f"路径守护拦截: {esc_reason}"), raw_path
        return None, raw_path

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
        self, tool: Tool, args: dict[str, Any], workdir: str = ""
    ) -> PermissionRule | None:
        """规则匹配。按优先级 + deny 优先的顺序找第一条命中，返回规则本身。

        返回 PermissionRule 而非 PermissionResult：调用方需要 rule.pattern
        判断有无 spec（裸名 allow 与带 spec allow 的放宽范围不同）。
        @author aceFelix
        """
        matcher = tool.prepare_permission_matcher(args)
        for rule in self._rules.sorted_for_match():
            if self._rule_matches(tool, rule.pattern, args, matcher, workdir):
                return rule
        return None

    def _rule_matches(
        self,
        tool: Tool,
        pattern: str,
        args: dict[str, Any],
        matcher: PermissionMatcher | None,
        workdir: str = "",
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
        # 无 matcher 的退化匹配：保留旧的"spec 直接 fnmatch 原始入参"
        # 语义（兼容 Glob(**/x)、KeyTap(alt+f4) 等非路径 spec），同时
        # 增加路径归一候选：spec 与目标都经 ~ 展开/workdir 相对化/平台
        # 大小写归一后再比——裸 fnmatch 对 "FileWrite(~/reports/**)" +
        # 相对入参永远失配，这正是原策略引擎 path_within 独有的能力。
        target = args.get("file_path") or args.get("path") or ""
        if not target:
            return False
        if fnmatch.fnmatchcase(str(target), rule_spec):
            return True
        from agent.permissions.path_match import norm_case, norm_path, expand

        return path_matches([rule_spec], str(target), workdir) or fnmatch.fnmatchcase(
            norm_case(rule_spec), norm_path(str(target), workdir)
        )


def classify_tool_for_shell(tool: Tool, args: dict[str, Any]) -> str:
    """便捷工具函数: 对 BashTool 入参做命令分类。"""
    if tool.name in ("Bash", "PowerShell"):
        cmd = args.get("command", "")
        return classify_shell(cmd)
    return "unknown"
