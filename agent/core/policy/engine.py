"""确定性策略引擎 —— policy-as-code 的判定中枢（W1·阶段 8）。

回答五层权限管线回答不了的两个问题："这次调用值不值得打断用户"、
"无人值守时该不该做"。规则源 `~/.jarvis/policy.yaml`：

    rules:
      - name: 只读查询免打扰
        match: {tool: ["FileRead", "Glob", "Grep"]}
        effect: allow
      - name: 外发类先看预览
        match: {tool: ["SendMail"]}
        effect: dry_run
      - name: 无人值守禁破坏性命令
        match: {tool: ["Bash"], destructive: true, away: true}
        effect: deny

合并语义（与既有管线一致，fail-closed）：
- 命中的多条规则按 DENY > ASK > DRY_RUN > ALLOW 取"更安全者"生效；
- allow 只能放宽 ASK（对齐模式覆写 _relax_by_mode），**永不放宽 DENY**；
- ask 可把管线 ALLOW 升级为 ASK（策略是显式收紧，属用户意志）；
- 未命中任何规则 → 原样回落管线结论（漏配不会误放行）。

dry_run 是新增效果语义：行为仍映射为 ASK（不破坏既有消费方），但 reason
带 [dry_run] 标记——要求先出执行预览、用户确认后才真正执行。本期在询问
文案中体现预览要求，工具层拦截随 W3 账本落地（蓝图待决策 5 的口径）。

@author aceFelix
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from agent.core.policy.patterns import SHELL_TOOLS, path_matches
from agent.core.result import PermissionBehavior, PermissionResult

_DEFAULT_PATH = Path.home() / ".jarvis" / "policy.yaml"


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DRY_RUN = "dry_run"
    DENY = "deny"


# 多规则命中时的合并序（数值大者胜，"更安全"优先）
_EFFECT_RANK = {
    PolicyEffect.ALLOW: 0,
    PolicyEffect.DRY_RUN: 2,
    PolicyEffect.ASK: 2,
    PolicyEffect.DENY: 3,
}


@dataclass
class PolicyRule:
    """单条策略规则。

    match 字段（全部为可选与条件）：
    - tool: 工具名列表或 fnmatch 模式串
    - path_within: 目标路径 glob 列表（~ 展开、随平台归一大小写）
    - destructive: True 时仅命中 shell 分类为 dangerous 的 Bash/PowerShell
    - away: True 时仅在无人值守状态下命中
    """

    name: str
    match: dict[str, Any]
    effect: PolicyEffect


@dataclass
class PolicyDecision:
    """一次策略评估的结果，携带可解释性（命中了哪些规则、依据什么）。"""

    effect: PolicyEffect
    rule_name: str
    matched_rules: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return f"策略[{self.effect.value}] 命中规则「{self.rule_name}」"


class PolicyEngine:
    """策略引擎：加载规则、评估调用、把结论合并进管线结果。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        self._rules: list[PolicyRule] = []
        self._mtime: float = -1.0

    # ---- 加载（mtime 变更自动重载）----

    def _ensure_loaded(self) -> None:
        try:
            m = self._path.stat().st_mtime
        except OSError:
            self._rules, self._mtime = [], -1.0
            return
        if m != self._mtime:
            self._rules = self._load_file(self._path)
            self._mtime = m

    @staticmethod
    def _load_file(p: Path) -> list[PolicyRule]:
        """解析 policy.yaml。非法规则跳过并警告（配置错误不拖垮主流程）。"""
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError) as e:
            import logging

            logging.getLogger("jarvis.policy").warning("policy.yaml 加载失败: %s", e)
            return []
        rules: list[PolicyRule] = []
        for item in data.get("rules", []) or []:
            try:
                effect = PolicyEffect(str(item["effect"]).lower())
                match = item.get("match") or {}
                if not match:
                    continue
                rules.append(PolicyRule(name=item.get("name", f"rule#{len(rules)}"),
                                        match=match, effect=effect))
            except (KeyError, ValueError):
                import logging

                logging.getLogger("jarvis.policy").warning("跳过非法规则: %s", item)
        return rules

    def reload(self) -> None:
        """强制重载（配置热更新入口）。"""
        self._mtime = -1.0
        self._ensure_loaded()

    @property
    def rules(self) -> list[PolicyRule]:
        self._ensure_loaded()
        return list(self._rules)

    # ---- 评估 ----

    def evaluate(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        workdir: str = "",
        away: bool = False,
    ) -> PolicyDecision | None:
        """评估一次工具调用。返回 None 表示未命中任何规则（回落管线）。"""
        self._ensure_loaded()
        hits: list[PolicyRule] = [r for r in self._rules if self._rule_matches(r, tool_name, args, workdir, away)]
        if not hits:
            return None
        # 多条命中取"更安全者"；同级取声明顺序在前者（确定性）
        winner = max(hits, key=lambda r: (_EFFECT_RANK[r.effect], -hits.index(r)))
        return PolicyDecision(
            effect=winner.effect,
            rule_name=winner.name,
            matched_rules=[r.name for r in hits],
        )

    @staticmethod
    def _rule_matches(
        rule: PolicyRule, tool_name: str, args: dict[str, Any],
        workdir: str, away: bool,
    ) -> bool:
        """单规则匹配：match 内所有已声明条件全部满足才算命中。"""
        m = rule.match
        # 工具名：列表精确名或 fnmatch 模式
        tools = m.get("tool")
        if tools is not None:
            names = [tools] if isinstance(tools, str) else list(tools)
            if not any(tool_name == n or fnmatch.fnmatchcase(tool_name, n) for n in names):
                return False
        # 破坏性：仅 shell 工具且分类为 dangerous
        if m.get("destructive") is True:
            if tool_name not in SHELL_TOOLS:
                return False
            from agent.permissions.shell_classifier import classify

            if classify(str(args.get("command", ""))) != "dangerous":
                return False
        # 无人值守条件
        if m.get("away") is True and not away:
            return False
        # 路径限定：工具声明路径或常见字段，命中任一 glob
        paths = m.get("path_within")
        if paths:
            target = args.get("file_path") or args.get("path") or ""
            if not target or not path_matches(list(paths), str(target), workdir):
                return False
        return True

    # ---- 合并进管线结论 ----

    @staticmethod
    def apply_to(base: PermissionResult, decision: PolicyDecision) -> PermissionResult:
        """把策略结论合并到管线结果上。

        DENY 永远压过一切（含 allow 策略与租约）；allow 只放宽 ASK；
        ask/dry_run 可收紧 ALLOW。可解释性写进 reason（命中规则名）。
        """
        if base.behavior == PermissionBehavior.DENY:
            return base  # 管线硬拒绝，策略不放宽
        eff = decision.effect
        if eff == PolicyEffect.DENY:
            return PermissionResult.deny(f"{decision.describe()}")
        if eff == PolicyEffect.ALLOW:
            if base.behavior == PermissionBehavior.ASK:
                return PermissionResult.allow(f"{decision.describe()}")
            return base
        if eff == PolicyEffect.DRY_RUN:
            return PermissionResult.ask(
                f"[dry_run 预览] {decision.describe()}：先展示执行预览，确认后再执行"
            )
        # ASK：收紧（即使管线已 ALLOW）
        return PermissionResult.ask(f"{decision.describe()}")


# ---- 进程内单例 ----

_engine_singleton: PolicyEngine | None = None


def get_engine(path: Path | str | None = None) -> PolicyEngine:
    global _engine_singleton
    if _engine_singleton is None or path is not None:
        _engine_singleton = PolicyEngine(path)
    return _engine_singleton


def brief_args(args: dict[str, Any], limit: int = 120) -> str:
    """工具入参摘要（待批清单/日志展示用），超长截断。"""
    try:
        s = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(args)
    return s[:limit] + ("…" if len(s) > limit else "")
