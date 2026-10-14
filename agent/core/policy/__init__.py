"""W1 运行时层（阶段 8）—— 授权租约 / 打扰预算 / 无人值守。

统一出口 `policy_gate()`：挂在 PermissionChecker 之后，只处理管线
**表达不了的运行时语义**——限时预授权（租约）与人不在场分流（away）。
声明式规则（allow/deny/ask + 路径作用域）原属本层的策略引擎，已并入
五层管线本身（permissions/checker.py：带 spec 的 allow 规则可放宽工具
默认 ASK），不再另设一套平行规则系统。

合并次序（自上而下，先严者胜）：
1. 管线结论（含 DENY，任何层不可放宽）；
2. 租约只放宽 ASK，且放宽不了路径守护软提醒（workdir 外写入）；
3. away 时仍为 ASK 的调用 → 入待批清单 + 本次拒绝（回来呈批）。

配置注入：宿主启动时调 `init_policy(settings)`；未注入时用安全缺省
（away 仅认手动标志、预算用默认参数）。租约/away/budget 均为零配置
设计：不敲 /away、不签 /lease 时完全不介入，无需开关。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.policy.budget import BudgetConfig, NotifyAction, NotifyBudget
from agent.core.policy.lease import Lease, LeaseStore, get_lease_store
from agent.core.policy.state import (
    PendingApprovalStore,
    PolicyStateStore,
    brief_args,
    get_pending_approvals,
    get_policy_state,
)
from agent.core.result import PermissionBehavior, PermissionResult

__all__ = [
    "BudgetConfig", "Lease", "LeaseStore", "NotifyAction", "NotifyBudget",
    "PendingApprovalStore", "PolicyStateStore", "brief_args",
    "get_budget", "get_lease_store", "get_pending_approvals",
    "get_policy_state", "init_policy", "policy_gate",
]

# 模块级配置快照（init_policy 注入；None = 安全缺省）
_settings: Any = None
_budget_singleton: NotifyBudget | None = None


def init_policy(settings: Any = None) -> None:
    """宿主启动/热更新时注入 Settings 快照并重置预算单例。

    @param settings: Settings 实例（读取 away_idle_minutes / notify_* 预算
    字段）；None 恢复缺省。
    """
    global _settings, _budget_singleton
    _settings = settings
    _budget_singleton = None  # 下次 get_budget 按新配置重建


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
    """五层管线之后的 W1 运行时闸门。base 为 PermissionChecker.check 结论。"""
    workdir = str(getattr(ctx, "workdir", "") or "")
    state = get_policy_state()
    state.touch_activity()
    away, away_note = state.is_away(int(_cfg("away_idle_minutes", 0) or 0))

    result = base

    # ① 授权租约：只放宽 ASK，且放宽不了路径守护软提醒（与管线 allow
    #    规则同一保守口径：跨边界写入应保持人工确认）
    if (result.behavior == PermissionBehavior.ASK
            and not (result.reason or "").startswith("路径提醒")):
        target = str(args.get("file_path") or args.get("path") or "")
        lease = get_lease_store().match(tool.name, target, workdir)
        if lease is not None:
            note = f"（{lease.note}）" if lease.note else ""
            result = PermissionResult.allow(
                f"租约 {lease.id} 命中{note}，有效期至 {lease.expires_at}"
            )

    # ② away 分流：仍是 ASK 的调用入待批清单，本次拒绝
    if away and result.behavior == PermissionBehavior.ASK:
        reason = result.reason or "需要确认"
        get_pending_approvals().add(
            tool=tool.name, args_brief=brief_args(args),
            reason=reason, effect="ask/away",
        )
        result = PermissionResult.deny(
            f"无人值守（{away_note or 'away'}）：{reason} —— 已入待批清单，"
            "/back 回来呈批；请勿重试或改用其他工具绕过确认"
        )

    return result
