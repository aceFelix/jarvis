"""W1 运行时层测试夹具 —— 全部单例隔离到 tmp 目录，互不串盘。

policy 包的租约/状态/待批/预算都是进程内单例 + ~/.jarvis 落盘，
测试必须逐例重置，否则会读到上一例或真实用户目录的状态。

@author aceFelix
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def policy_paths(tmp_path):
    """把 policy 各单例指向 tmp_path 并清理模块级注入状态。

    返回 {name: Path}：leases / state / pending / budget，
    供各测试构造 gate 的 settings 桩读取。
    """
    from agent.core.policy import lease as lease_mod, state as state_mod
    gate = importlib.import_module("agent.core.policy")

    paths = {
        "leases": tmp_path / "leases.json",
        "state": tmp_path / "policy_state.json",
        "pending": tmp_path / "pending_approvals.json",
        "budget": tmp_path / "notify_state.json",
    }

    # 各单例重置为 tmp 路径（get_x(path) 传参即强制重建）
    lease_mod._store_singleton = None
    state_mod._state_singleton = None
    state_mod._pending_singleton = None
    gate._settings = None
    gate._budget_singleton = None

    # 必须预建到 tmp：gate 内 get_x(None) 复用现有单例，不预建会回退
    # 默认路径读到真实 ~/.jarvis 状态，测试被用户机器环境污染。
    lease_mod.get_lease_store(paths["leases"])
    state_mod.get_policy_state(paths["state"])
    state_mod.get_pending_approvals(paths["pending"])

    yield paths

    lease_mod._store_singleton = None
    state_mod._state_singleton = None
    state_mod._pending_singleton = None
    gate._settings = None
    gate._budget_singleton = None
