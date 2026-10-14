"""tests 全局夹具 —— 默认隔离进程级单例，防测试读写真实用户目录。

背景（2026-10-14）：阶段 8 policy 包（租约/状态/待批清单）是
进程内单例 + ~/.jarvis 落盘。test_orchestrator 等旧套件在用户真实
/away 状态下跑全量时，ASK 被误转 DENY、fake_tool 条目写进用户真实
待批清单——测试必须与用户机器状态无关。

tests/policy/conftest.py 的 policy_paths 夹具仍供该目录做细粒度定制；
本 autouse 夹具兜底全部套件。

@author aceFelix
"""
from __future__ import annotations

import importlib


import pytest


@pytest.fixture(autouse=True)
def _isolate_policy_singletons(tmp_path):
    """所有测试默认把 policy 包单例指向 tmp_path，结束后复位。

    重置为 None 而非恢复旧值：单例应按需重建，避免跨会话残留路径。
    """
    from agent.core.policy import lease as lease_mod, state as state_mod
    gate = importlib.import_module("agent.core.policy")

    lease_mod._store_singleton = None
    state_mod._state_singleton = None
    state_mod._pending_singleton = None
    gate._settings = None
    gate._budget_singleton = None

    # 预建到 tmp：get_x(None) 复用现有单例，不预建会回退真实 ~/.jarvis 路径
    lease_mod.get_lease_store(tmp_path / "leases.json")
    state_mod.get_policy_state(tmp_path / "policy_state.json")
    state_mod.get_pending_approvals(tmp_path / "pending_approvals.json")

    yield

    lease_mod._store_singleton = None
    state_mod._state_singleton = None
    state_mod._pending_singleton = None
    gate._settings = None
    gate._budget_singleton = None
