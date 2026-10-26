"""tests 全局夹具 —— 默认隔离进程级单例，防测试读写真实用户目录。

背景（2026-10-14）：阶段 8 policy 包（租约/状态/待批清单）是
进程内单例 + ~/.jarvis 落盘。test_orchestrator 等旧套件在用户真实
/away 状态下跑全量时，ASK 被误转 DENY、fake_tool 条目写进用户真实
待批清单——测试必须与用户机器状态无关。

tests/policy/conftest.py 的 policy_paths 夹具仍供该目录做细粒度定制；
本 autouse 夹具兜底全部套件。阶段 9（W2 感知层）的总线单例、阶段 10（W3
兑现层）的副作用账本/目标台账/延时窗口/驱动器句柄同样在本夹具里复位，
避免跨用例共享订阅方、去重键、轮询线程与待发动作。

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
    percept = importlib.import_module("agent.core.percept")

    lease_mod._store_singleton = None
    state_mod._state_singleton = None
    state_mod._pending_singleton = None
    gate._settings = None
    gate._budget_singleton = None

    # 空转检测器同为进程级单例：不复位会让旧套件的重复签名跨用例累积，
    # 第三条同型调用被误升级 ASK（2026-10-14 全量跑 2 例回归的根因）
    from agent.core.policy.loop_detect import get_loop_detector
    get_loop_detector().reset()

    # 预建到 tmp：get_x(None) 复用现有单例，不预建会回退真实 ~/.jarvis 路径
    lease_mod.get_lease_store(tmp_path / "leases.json")
    state_mod.get_policy_state(tmp_path / "policy_state.json")
    state_mod.get_pending_approvals(tmp_path / "pending_approvals.json")

    # W2 感知层同为进程级单例：不复位则跨用例共享总线（订阅方、去重键、
    # 轮询线程相互污染），且不注入 settings 会把事件写进用户真实 ~/.jarvis
    percept._settings = None
    percept._bus_singleton = None

    # W3 兑现层也是进程级单例：副作用账本 / 目标台账 / 延时窗口 / 驱动器句柄。
    # 不复位会跨用例共享待发动作与台账目录，更会把流水写进用户真实 ~/.jarvis
    actions = importlib.import_module("agent.core.actions.journal")
    spine_mod = importlib.import_module("agent.core.tasks.spine")
    driver_mod = importlib.import_module("agent.core.tasks.driver")
    actions.configure_journal(
        enabled=True, path=tmp_path / "actions.jsonl", clear_window=True,
    )
    spine_mod.configure_spine_store(tmp_path / "goals")
    driver_mod.set_driver(None)

    yield

    lease_mod._store_singleton = None
    state_mod._state_singleton = None
    state_mod._pending_singleton = None
    gate._settings = None
    gate._budget_singleton = None
    get_loop_detector().reset()
    bus = percept._bus_singleton
    percept._settings = None
    percept._bus_singleton = None
    if bus is not None:  # 用例遗留的轮询线程必须停干净，否则跨用例串味
        bus.stop()
        bus.shutdown_sources()
    # 延时窗口里遗留的定时器（threading.Timer / call_later）必须摘干净，
    # 不然上一个用例会在下一个用例中途真执行一次回调
    actions.get_window().clear()
    actions._journal = None
    spine_mod._store = None
    driver_mod._driver = None
