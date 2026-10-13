"""授权租约单测 —— 签发/命中/过期/撤销（阶段 8 验收③）。

覆盖：工具名与路径范围匹配、租约限定路径而调用无路径不命中（宁可多问）、
到期自动失效（行为立刻回 ASK）、撤销即失效、跨进程 JSON 共享、
list_active 顺带 GC 过期项。

@author aceFelix
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from agent.core.policy.lease import Lease, LeaseStore


def _store(policy_paths) -> LeaseStore:
    return LeaseStore(policy_paths["leases"])


def test_grant_and_match_tool(policy_paths):
    """签发后同工具命中；其他工具不命中。"""
    store = _store(policy_paths)
    lease = store.grant(["FileWrite"], None, ttl_minutes=60, note="测试")
    hit = store.match("FileWrite", "", "")
    assert hit is not None and hit.id == lease.id
    assert store.match("Bash", "", "") is None


def test_match_fnmatch_pattern(policy_paths):
    """tools 支持 fnmatch 模式串。"""
    store = _store(policy_paths)
    store.grant(["File*"], None, ttl_minutes=60)
    assert store.match("FileWrite", "", "") is not None
    assert store.match("Bash", "", "") is None


def test_path_scope(policy_paths):
    """path_within 限定：目录内命中，目录外与无路径参数不命中。"""
    import os
    store = _store(policy_paths)
    scope = "~/work/reports/**"
    store.grant(["FileWrite"], [scope], ttl_minutes=60)
    inside = os.path.join(os.path.expanduser("~"), "work", "reports", "a.md")
    outside = os.path.join(os.path.expanduser("~"), "work", "else", "a.md")
    assert store.match("FileWrite", inside, "") is not None
    assert store.match("FileWrite", outside, "") is None
    # 租约限定了路径而调用没带路径 → 不算命中（宁可多问一次）
    assert store.match("FileWrite", "", "") is None


def test_expiry_automatic(policy_paths):
    """过期租约不命中，且 list_active 顺带把它从文件里清掉（lazy GC）。"""
    store = _store(policy_paths)
    lease = store.grant(["FileWrite"], None, ttl_minutes=1)
    # 直接改盘把到期时间拨到过去，模拟时间流逝
    items = json.loads(policy_paths["leases"].read_text(encoding="utf-8"))
    items[0]["expires_at"] = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    policy_paths["leases"].write_text(json.dumps(items), encoding="utf-8")
    assert store.match("FileWrite", "", "") is None
    assert store.list_active() == []
    assert json.loads(policy_paths["leases"].read_text(encoding="utf-8")) == []


def test_revoke(policy_paths):
    """撤销后立刻不命中；撤销不存在的 id 返回 False。"""
    store = _store(policy_paths)
    lease = store.grant(["FileWrite"], None, ttl_minutes=60)
    assert store.revoke(lease.id) is True
    assert store.match("FileWrite", "", "") is None
    assert store.revoke("nope") is False


def test_cross_process_shared_view(policy_paths):
    """两个 LeaseStore 实例（模拟 CLI 与 serve 两进程）共享同一文件。"""
    a = _store(policy_paths)
    b = _store(policy_paths)
    lease = a.grant(["Bash"], None, ttl_minutes=30, note="跨进程")
    hit = b.match("Bash", "", "")
    assert hit is not None and hit.id == lease.id


def test_min_ttl_one_minute(policy_paths):
    """ttl 传 0/负数也至少 1 分钟有效（防误签发即失效）。"""
    store = _store(policy_paths)
    lease = store.grant(["FileWrite"], None, ttl_minutes=0)
    assert lease.is_active()
