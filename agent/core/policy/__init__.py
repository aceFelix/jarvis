"""W1 判断层（阶段 8）—— 策略引擎 / 授权租约 / 打扰预算 / 无人值守。

统一出口 `policy_gate()`：挂在 PermissionChecker 之后，把
"管线结论 → 策略规则 → 授权租约 → away 分流"合成最终权限结果。

合并次序与边界（自上而下，先严者胜）：
1. 管线 DENY（路径守护/危险规则）不可被任何层放宽；
2. 策略引擎四态（allow/ask/deny/dry_run）按声明合并进管线结论；
3. 租约只放宽"管线原生 ASK"，覆盖不了策略显式 ask/dry_run 的收紧；
4. away 时仍为 ASK 的调用 → 入待批清单 + 本次拒绝（回来呈批）。

配置注入：宿主启动时调 `init_policy(settings)`；未注入时用安全缺省
（启用引擎、无 policy.yaml 即全回落管线、away 仅认手动标志）。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.policy.budget import BudgetConfig, NotifyAction, NotifyBudget
from agent.core.policy.engine import (
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    brief_args,
    get_engine,
)
from agent.core.policy.lease import Lease, LeaseStore, get_lease_store
from agent.core.policy.state import (
    PendingApprovalStore,
    PolicyStateStore,
    get_pending_approvals,
    get_policy_state,
)
from agent.core.result import PermissionBehavior, PermissionResult

__all__ = [
    "BudgetConfig", "Lease", "LeaseStore", "NotifyAction", "NotifyBudget",
    "PendingApprovalStore", "PolicyDecision", "PolicyEffect", "PolicyEngine",
    "PolicyStateStore", "get_budget", "get_engine", "get_lease_store",
    "get_pending_approvals", "get_policy_state", "init_policy", "policy_gate",
]

# 模块级配置快照（init_policy 注入；None = 安全缺省）
_settings: Any = None
_budget_singleton: NotifyBudget | None = None


def init_policy(settings: Any = None) -> None:
    """宿主启动/热更新时注入 Settings 快照并重置预算单例。

    @param settings: Settings 实例（读取 policy_enabled / policy_path /
    away_idle_minutes / notify_* 预算字段）；None 恢复缺省。
    """
    global _settings, _budget_singleton
    _settings = settings
    _budget_singleton = None  # 下次 get_budget 按新配置重建
    if settings is not None and getattr(settings, "policy_enabled", True):
        get_engine(getattr(settings, "policy_path", "") or None).reload()


def _cfg(field: str, default: Any) -> Any:
    """从注入的 settings 读字段（未注入/缺字段回默认）。"""
    if _settings is None:
        return default
    value = getattr(_settings, field, default)
    return default if value is None else value


def get_budget() -> NotifyBudget | None:
    """打扰预算单例（notify_budget_enabled=False 时返回 None=不设闸）。"""
    global _budget_singleton
    if not _cfg("notify_budget_enabled", True):
        return None
    if _budget_singleton is None:
        config = BudgetConfig(
            daily_max=int(_cfg("notify_daily_max", 10)),
            quiet_hours=str(_cfg("notify_quiet_hours", "23:00-07:00")),
            aggregate_window_s=int(_cfg("notify_aggregate_window_s", 300)),
            min_salience=float(_cfg("notify_min_salience", 0.3)),
        )
        _budget_singleton = NotifyBudget(config)
    return _budget_singleton


def policy_gate(tool: Any, args: dict[str, Any], ctx: Any, base: PermissionResult) -> PermissionResult:
    """五层管线之后的 W1 统一闸门。base 为 PermissionChecker.check 结论。"""
    if not _cfg("policy_enabled", True):
        return base

    workdir = str(getattr(ctx, "workdir", "") or "")
    state = get_policy_state()
    state.touch_activity()
    away, away_note = state.is_away(int(_cfg("away_idle_minutes", 0) or 0))

    # ① 策略引擎
    result = base
    decision = get_engine(_cfg("policy_path", "") or None).evaluate(
        tool.name, args, workdir=workdir, away=away,
    )
    policy_escalated = False
    if decision is not None:
        result = PolicyEngine.apply_to(base, decision)
        policy_escalated = decision.effect in (PolicyEffect.ASK, PolicyEffect.DRY_RUN)

    # ② 授权租约：只放宽管线原生 ASK（策略显式收紧与 DENY 覆盖不了）
    if result.behavior == PermissionBehavior.ASK and not policy_escalated:
        target = str(args.get("file_path") or args.get("path") or "")
        lease = get_lease_store().match(tool.name, target, workdir)
        if lease is not None:
            note = f"（{lease.note}）" if lease.note else ""
            result = PermissionResult.allow(
                f"租约 {lease.id} 命中{note}，有效期至 {lease.expires_at}"
            )

    # ③ away 分流：仍是 ASK 的调用入待批清单，本次拒绝
    if away and result.behavior == PermissionBehavior.ASK:
        reason = result.reason or "需要确认"
        get_pending_approvals().add(
            tool=tool.name, args_brief=brief_args(args),
            reason=reason, effect="ask/away",
        )
        result = PermissionResult.deny(
            f"无人值守（{away_note or 'away'}）：{reason} —— 已入待批清单，/back 回来呈批"
        )

    return result
