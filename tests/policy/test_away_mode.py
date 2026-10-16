"""无人值守 + 运行时闸门单测 —— away 状态 / 待批清单 / policy_gate 合并（阶段 8 验收④）。

覆盖：/away /back 手动切换、空闲推断、**视觉在场推断（阶段 9 W2 补上）**、
away 时 ASK 入待批清单转 DENY、ALLOW/DENY 不受 away 影响、沙箱自动放行在
away 时被压制升级待批、闸门零介入（无租约无 away 时结论原样回落、对象同一）、
租约只放宽 ASK 且放宽不了路径守护软提醒。

声明式规则合并（原策略引擎）已并入五层管线，测于
tests/test_permissions_relax.py。

@author aceFelix
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from agent.core.policy import policy_gate
from agent.core.policy.state import (
    VISION_AWAY_TTL_MIN,
    PendingApprovalStore,
    PolicyStateStore,
)
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

    def __init__(self, **over) -> None:
        self.away_idle_minutes = over.get("away_idle_minutes", 0)
        self.notify_budget_enabled = over.get("notify_budget_enabled", True)
        self.notify_daily_max = 10
        self.notify_quiet_hours = "23:00-07:00"
        self.notify_aggregate_window_s = 300
        self.notify_min_salience = 0.3


@pytest.fixture()
def gate_ok(policy_paths):
    """初始化闸门依赖：注入 settings 桩（租约/状态单例已由 policy_paths 隔离）。"""
    from agent.core.policy import init_policy
    init_policy(_Settings())
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


# ---- 视觉在场推断（W2 第 4 步补上阶段 8 递延的那一项）----

def test_vision_presence_away_lifecycle(policy_paths):
    """人脸离开 → away（依据文本能分辨来源）；回来 → 解除。"""
    s = PolicyStateStore(policy_paths["state"])
    assert s.set_presence_away(True) is True
    away, note = s.is_away(0)
    assert away is True and note.startswith("视觉")
    assert s.set_presence_away(False) is True
    assert s.is_away(0) == (False, "")


def test_manual_away_not_overridden_by_camera(policy_paths):
    """用户明确 /away 后，摄像头不得替他取消（也不得改写过来源）。"""
    s = PolicyStateStore(policy_paths["state"])
    s.set_away(True, source="manual")
    assert s.set_presence_away(False) is False
    away, note = s.is_away(0)
    assert away is True and "手动" in note
    data = json.loads(s._path.read_text(encoding="utf-8"))
    assert data["away"]["source"] == "manual"


def test_presence_away_is_idempotent(policy_paths):
    """状态与来源都没变时不写盘（免摄像头在边界抖时反复落盘）。"""
    s = PolicyStateStore(policy_paths["state"])
    assert s.set_presence_away(True) is True
    before = s._path.read_text(encoding="utf-8")
    assert s.set_presence_away(True) is False
    assert s._path.read_text(encoding="utf-8") == before   # since 没被刷新


def test_vision_away_expires(policy_paths):
    """视觉 away 超 TTL 后不再压制播报（摄像头可能已关，检测不到回来）。"""
    s = PolicyStateStore(policy_paths["state"])
    s.set_presence_away(True)
    assert s.is_away(0)[0] is True
    data = json.loads(s._path.read_text(encoding="utf-8"))
    stale = (datetime.now() - timedelta(minutes=VISION_AWAY_TTL_MIN + 5)).isoformat(
        timespec="seconds")
    data["away"]["since"] = stale
    s._path.write_text(json.dumps(data), encoding="utf-8")
    s._mtime = -1.0                                  # 绕过 mtime 缓存重读
    assert s.is_away(0) == (False, "")
    # 手动 /away 不适用到期：用户说的“不在”不因时间流逝而失效
    s.set_away(True, source="manual")
    data = json.loads(s._path.read_text(encoding="utf-8"))
    data["away"]["since"] = stale
    s._path.write_text(json.dumps(data), encoding="utf-8")
    s._mtime = -1.0
    assert s.is_away(0)[0] is True


def test_unknown_away_source_is_not_trusted(policy_paths):
    """不在采信名单里的 active（手改文件/旧数据）不算 away，保持原有保守口径。"""
    s = PolicyStateStore(policy_paths["state"])
    s._path.write_text(json.dumps(
        {"away": {"active": True, "since": datetime.now().isoformat(timespec="seconds"),
                  "source": "某个新来源"}}), encoding="utf-8")
    s._mtime = -1.0
    assert s.is_away(0) == (False, "")


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


# ---- policy_gate（运行时闸门）----

def test_gate_zero_intervention(gate_ok):
    """无租约、非 away：结论原样回落管线（对象同一，零介入）。

    新闸门没有总开关概念：租约/away 均为零配置运行时状态，
    不存在生效租约且未 /away 时，任何 base 都透传。
    """
    base = PermissionResult.ask("管线默认")
    assert policy_gate(_Tool("FileWrite"), {}, _Ctx(), base) is base
    base2 = PermissionResult.allow("管线放行")
    assert policy_gate(_Tool("FileWrite"), {}, _Ctx(), base2) is base2


def test_gate_never_relaxes_pipeline_deny(gate_ok):
    """管线 DENY 压过一切：即使签了同工具租约也不放宽。"""
    from agent.core.policy import get_lease_store
    get_lease_store().grant(["Bash"], None, ttl_minutes=60)
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


def test_gate_lease_cannot_relax_path_guard_ask(gate_ok):
    """路径守护软提醒（写工作目录外）不被租约放宽——跨边界保持人工确认。"""
    from agent.core.policy import get_lease_store
    get_lease_store().grant(["FileWrite"], None, ttl_minutes=60)
    base = PermissionResult.ask("路径提醒: 目标在工作目录之外")
    out = policy_gate(_Tool("FileWrite"), {"file_path": "D:/elsewhere/a.md"}, _Ctx(), base)
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


def test_gate_away_pipeline_deny_not_recorded(gate_ok):
    """管线直接 DENY 的调用不进待批清单（不是被 away 拦的确认请求）。"""
    from agent.core.policy import get_pending_approvals, get_policy_state
    get_policy_state().set_away(True, source="manual")
    out = policy_gate(_Tool("Bash"), {"command": "rm -rf x"}, _Ctx(), PermissionResult.deny("危险命令"))
    assert out.behavior == PermissionBehavior.DENY
    assert "危险命令" in (out.reason or "")
    assert get_pending_approvals().list() == []


# ---- 沙箱自动放行压制（2026-10 实测发现的半完成问题）----

def _sandbox_allow() -> PermissionResult:
    """造一个 Bash 工具特判风格的沙箱自动放行 ALLOW（携协议前缀）。"""
    from agent.permissions.modes import SANDBOX_AUTO_ALLOW_PREFIX
    return PermissionResult.allow(f"{SANDBOX_AUTO_ALLOW_PREFIX} 沙箱已开启，中等风险命令在沙箱内执行")


def test_gate_away_suppresses_sandbox_auto_allow(gate_ok):
    """away 时沙箱自动放行 ALLOW 升级回 ASK → 入待批清单并本次拒绝。"""
    from agent.core.policy import get_pending_approvals, get_policy_state
    get_policy_state().set_away(True, source="manual")
    out = policy_gate(_Tool("Bash"), {"command": "mv a b"}, _Ctx(), _sandbox_allow())
    assert out.behavior == PermissionBehavior.DENY
    assert "升级待批" in (out.reason or "")
    pending = get_pending_approvals().list()
    assert len(pending) == 1 and pending[0]["tool"] == "Bash"


def test_gate_sandbox_auto_allow_ok_when_present(gate_ok):
    """在场（非 away）时沙箱自动放行不受影响，零介入透传。"""
    base = _sandbox_allow()
    assert policy_gate(_Tool("Bash"), {"command": "mv a b"}, _Ctx(), base) is base


def test_gate_lease_can_relax_suppressed_sandbox_ask(gate_ok):
    """升级后的 ASK 标普通管线 ASK，用户签了 Bash 租约仍可放宽
    （预先授权 = 主人同意无人时继续跑沙箱中风险命令）。"""
    from agent.core.policy import get_lease_store, get_policy_state
    get_policy_state().set_away(True, source="manual")
    get_lease_store().grant(["Bash"], None, ttl_minutes=60)
    out = policy_gate(_Tool("Bash"), {"command": "mv a b"}, _Ctx(), _sandbox_allow())
    assert out.behavior == PermissionBehavior.ALLOW


def test_gate_away_other_allow_untouched(gate_ok):
    """只认协议前缀：模式/规则/租约给的其他 ALLOW 在 away 时照常通过。"""
    from agent.core.policy import get_policy_state
    get_policy_state().set_away(True, source="manual")
    for reason in ("只读命令自动放行", "规则命中: Bash(git *)", "接受编辑模式自动放行"):
        base = PermissionResult.allow(reason)
        assert policy_gate(_Tool("Bash"), {"command": "ls"}, _Ctx(), base) is base
