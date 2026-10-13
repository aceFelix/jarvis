"""无人值守 + 判断层闸门单测 —— away 状态 / 待批清单 / policy_gate 合并（阶段 8 验收①④）。

覆盖：/away /back 手动切换、空闲推断、away 时 ASK 入待批清单转 DENY、
ALLOW/DENY 不受 away 影响、闸门对管线的零回归（policy_enabled=False 或
未命中规则时结论原样回落）、租约只放宽管线原生 ASK。

@author aceFelix
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from agent.core.policy import PolicyEffect, policy_gate
from agent.core.policy.state import PendingApprovalStore, PolicyStateStore
from agent.core.result import PermissionBehavior, PermissionResult


class _Tool:
    """工具桩：闸门只用 name。"""

    def __init__(self, name: str) -> None:
        self.name = name


class _Ctx:
    """上下文桩：闸门只用 workdir。"""

    def __init__(self, workdir: str = "") -> None:
        self.workdir = workdir


class _Settings:
    """settings 桩：init_policy 注入用，字段与 Settings 的 W1 域同名。"""

    def __init__(self, policy_paths, **over) -> None:
        self.policy_enabled = over.get("policy_enabled", True)
        self.policy_path = str(policy_paths["policy_yaml"])
        self.away_idle_minutes = over.get("away_idle_minutes", 0)
        self.notify_budget_enabled = over.get("notify_budget_enabled", True)
        self.notify_daily_max = 10
        self.notify_quiet_hours = "23:00-07:00"
        self.notify_aggregate_window_s = 300
        self.notify_min_salience = 0.3


@pytest.fixture()
def gate_ok(policy_paths):
    """初始化闸门依赖：注入 settings 桩 + 建好空的 policy.yaml。"""
    from agent.core.policy import init_policy
    policy_paths["policy_yaml"].write_text("rules: []\n", encoding="utf-8")
    init_policy(_Settings(policy_paths))
    return policy_paths


# ---- PolicyStateStore（away 状态）----

def test_manual_away_toggle(policy_paths):
    """set_away(True, manual) 即 away；set_away(False) 解除。"""
    s = PolicyStateStore(policy_paths["state"])
    assert s.is_away(0) == (False, "")
    s.set_away(True, source="manual")
    away, note = s.is_away(0)
    assert away and "手动" in note
    s.set_away(False, source="manual")
    assert s.is_away(0)[0] is False


def test_idle_inference(policy_paths):
    """idle_minutes>0：最后活动超阈值视为 away；=0 时仅认手动。"""
    s = PolicyStateStore(policy_paths["state"])
    past = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
    s._path.write_text(json.dumps({"last_activity": past}), encoding="utf-8")
    assert s.is_away(0)[0] is False      # 阈值 0 = 关闭空闲推断
    away, note = s.is_away(10)
    assert away and "空闲" in note


def test_back_refreshes_activity(policy_paths):
    """/back（set_away False）刷新活动时间，避免立刻又被推断成 away。"""
    s = PolicyStateStore(policy_paths["state"])
    past = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    s._path.write_text(json.dumps({"last_activity": past}), encoding="utf-8")
    s.set_away(False, source="manual")
    assert s.is_away(10)[0] is False


# ---- PendingApprovalStore（待批清单）----

def test_pending_add_drain(policy_paths):
    """入库后可 list；drain 取走并清空。"""
    p = PendingApprovalStore(policy_paths["pending"])
    p.add(tool="FileWrite", args_brief='{"file_path":"a"}', reason="默认询问", effect="ask/away")
    assert len(p.list()) == 1
    items = p.drain()
    assert items[0]["tool"] == "FileWrite"
    assert p.list() == []


def test_pending_cap_50(policy_paths):
    """清单上限 50，超出丢最旧。"""
    p = PendingApprovalStore(policy_paths["pending"], max_items=50)
    for i in range(55):
        p.add(tool="Bash", args_brief=str(i), reason="r", effect="ask/away")
    items = p.list()
    assert len(items) == 50 and items[0]["args"] == "5"


# ---- policy_gate（统一闸门）----

def test_gate_zero_regression_when_disabled(gate_ok):
    """policy_enabled=False：结论原样回落管线（对象同一，零影响）。"""
    from agent.core.policy import init_policy
    init_policy(_Settings(gate_ok, policy_enabled=False))
    base = PermissionResult.ask("管线默认")
    assert policy_gate(_Tool("FileWrite"), {}, _Ctx(), base) is base


def test_gate_no_rules_falls_back(gate_ok):
    """无规则命中：原样回落管线结论。"""
    base = PermissionResult.ask("管线默认")
    out = policy_gate(_Tool("FileWrite"), {"file_path": "x.md"}, _Ctx(), base)
    assert out is base


def test_gate_allow_relaxes_native_ask(gate_ok):
    """策略 allow 放宽管线原生 ASK。"""
    gate_ok["policy_yaml"].write_text(
        "rules:\n  - name: 只读免打扰\n    match: {tool: [FileRead]}\n    effect: allow\n",
        encoding="utf-8")
    out = policy_gate(_Tool("FileRead"), {}, _Ctx(), PermissionResult.ask("默认询问"))
    assert out.behavior == PermissionBehavior.ALLOW
    assert "只读免打扰" in (out.reason or "")


def test_gate_never_relaxes_pipeline_deny(gate_ok):
    """管线 DENY 压过一切：即使策略配了 allow 也不放宽。"""
    gate_ok["policy_yaml"].write_text(
        "rules:\n  - name: 全放行\n    match: {tool: [Bash]}\n    effect: allow\n",
        encoding="utf-8")
    base = PermissionResult.deny("危险命令")
    out = policy_gate(_Tool("Bash"), {"command": "rm -rf /"}, _Ctx(), base)
    assert out.behavior == PermissionBehavior.DENY


def test_gate_lease_relaxes_native_ask(gate_ok):
    """命中租约放宽原生 ASK；到期即回 ASK（无需重启）。"""
    from agent.core.policy import get_lease_store
    store = get_lease_store()
    lease = store.grant(["FileWrite"], None, ttl_minutes=60, note="测试")
    out = policy_gate(_Tool("FileWrite"), {"file_path": "a.md"}, _Ctx(), PermissionResult.ask())
    assert out.behavior == PermissionBehavior.ALLOW and lease.id in (out.reason or "")
    # 把到期时间拨到过去 → 立刻回到 ASK
    items = json.loads(gate_ok["leases"].read_text(encoding="utf-8"))
    items[0]["expires_at"] = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    gate_ok["leases"].write_text(json.dumps(items), encoding="utf-8")
    out2 = policy_gate(_Tool("FileWrite"), {"file_path": "a.md"}, _Ctx(), PermissionResult.ask())
    assert out2.behavior == PermissionBehavior.ASK


def test_gate_lease_cannot_beat_policy_ask(gate_ok):
    """策略显式 ask 收紧时，租约不放宽（policy_escalated 挡住租约通道）。"""
    from agent.core.policy import get_lease_store
    gate_ok["policy_yaml"].write_text(
        "rules:\n  - name: 外发必确认\n    match: {tool: [SendEmail]}\n    effect: ask\n",
        encoding="utf-8")
    get_lease_store().grant(["SendEmail"], None, ttl_minutes=60)
    out = policy_gate(_Tool("SendEmail"), {"to": "x@y.z"}, _Ctx(), PermissionResult.allow("模式放行"))
    assert out.behavior == PermissionBehavior.ASK


def test_gate_away_denies_ask_and_records_pending(gate_ok):
    """away 时 ASK → 入待批清单并本次拒绝；ALLOW 不受影响。"""
    from agent.core.policy import get_pending_approvals, get_policy_state
    get_policy_state().set_away(True, source="manual")
    out = policy_gate(_Tool("FileWrite"), {"file_path": "a.md"}, _Ctx(), PermissionResult.ask("默认询问"))
    assert out.behavior == PermissionBehavior.DENY
    assert "待批清单" in (out.reason or "")
    pending = get_pending_approvals().list()
    assert len(pending) == 1 and pending[0]["tool"] == "FileWrite"
    # 放行类不拦
    out2 = policy_gate(_Tool("FileRead"), {}, _Ctx(), PermissionResult.allow("管线放行"))
    assert out2.behavior == PermissionBehavior.ALLOW


def test_gate_away_policy_deny_not_double_recorded(gate_ok):
    """策略直接 deny 的调用不进待批清单（不是被 away 拦的确认请求）。"""
    from agent.core.policy import get_pending_approvals, get_policy_state
    gate_ok["policy_yaml"].write_text(
        "rules:\n  - name: 无人值守禁破坏\n    match: {tool: [Bash], destructive: true, away: true}\n    effect: deny\n",
        encoding="utf-8")
    get_policy_state().set_away(True, source="manual")
    out = policy_gate(_Tool("Bash"), {"command": "rm -rf x"}, _Ctx(), PermissionResult.ask())
    assert out.behavior == PermissionBehavior.DENY
    assert "无人值守禁破坏" in (out.reason or "")
    assert get_pending_approvals().list() == []
