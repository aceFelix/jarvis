"""活动台账（代理团队数据源）单测。

覆盖三件事（aceFelix，2026-11）：
1. 生命周期写入与快照形状（字段齐全、按开始时间排序、快照是拷贝）；
2. 变更通知：状态跃迁立即通知、密集更新被节流；
3. ``AgentActivityUI`` 只把工具/文本事件收进台账，``ask_user`` 等仍转发宿主。

@author aceFelix
"""

from __future__ import annotations

import time
from typing import Any

from agent.collaboration.activity import (
    AgentActivityRegistry,
    AgentActivityUI,
    get_activity_registry,
)


def test_registry_lifecycle_and_snapshot() -> None:
    """开始 → 运行 → 收尾：字段落位，快照是普通 dict 列表。"""
    reg = AgentActivityRegistry()
    reg.start_agent("sub-1", "explorer-1", role="explorer", task="找所有 TODO")
    reg.update_agent("sub-1", status="running", current_tool="Grep TODO", activity="搜索 TODO")
    reg.update_agent("sub-1", iterations=3, tool_calls=5)
    reg.finish_agent("sub-1", status="done", output="找到 12 处")

    snap = reg.snapshot()
    assert len(snap) == 1
    row = snap[0]
    assert row["agent_id"] == "sub-1"
    assert row["name"] == "explorer-1"
    assert row["role"] == "explorer"
    assert row["status"] == "done"
    assert row["tool_calls"] == 5
    assert row["output"] == "找到 12 处"
    # 快照必须是拷贝：改返回值不能影响台账内部状态
    row["status"] = "tampered"
    assert reg.snapshot()[0]["status"] == "done"


def test_registry_ignores_unknown_fields_and_unknown_agent() -> None:
    """未知字段/未知代理不抛异常（埋点写错不能反噬执行链）。"""
    reg = AgentActivityRegistry()
    reg.update_agent("missing", status="running")
    reg.start_agent("sub-2", "coder-1", role="coder")
    reg.update_agent("sub-2", not_a_field=1)
    assert reg.snapshot()[0]["status"] == "starting"


def test_registry_notify_force_and_throttle() -> None:
    """起止立即通知；同窗口内的密集更新合并成一次拖尾通知。"""
    reg = AgentActivityRegistry()
    calls: list[list[dict[str, Any]]] = []
    reg.set_listener(lambda rows: calls.append(rows))

    reg.start_agent("sub-3", "researcher-1", role="researcher")
    assert len(calls) == 1  # 开始立即通知

    for i in range(30):
        reg.update_agent("sub-3", current_tool=f"tool-{i}")
    # 节流窗口内不应逐条通知（最多留一次拖尾）
    assert len(calls) <= 2

    reg.finish_agent("sub-3", status="done")
    assert calls[-1][0]["status"] == "done"  # 收尾立即通知


def test_activity_ui_routes_tool_events_into_registry() -> None:
    """工具/文本事件写进台账；info/ask_user 仍转发宿主 UI。"""

    class BaseUI:
        def __init__(self) -> None:
            self.asked: list[str] = []
            self.infos: list[str] = []

        def ask_user(self, prompt: str) -> str:
            self.asked.append(prompt)
            return "ok"

        def info(self, text: str) -> None:
            self.infos.append(text)

    reg = get_activity_registry()
    reg.clear()
    reg.start_agent("sub-ui", "coder-1", role="coder")

    base = BaseUI()
    ui = AgentActivityUI(base, "sub-ui")
    ui.assistant_thinking("想一想")
    ui.tool_use("FileRead", {"path": "agent/core/tool.py"}, "t1")
    ui.tool_result("FileRead", "1: first line", False, "t1")
    ui.assistant_text("结论：入口在 main.py")

    row = reg.snapshot()[0]
    assert row["current_tool"] == ""  # 返回后清空
    assert "结论" in row["output"]
    assert row["activity"]

    # 转发路径：ask_user 不能被台账吞掉（子代理需要用户输入时不能静默）
    assert ui.ask_user("确认吗") == "ok"
    assert base.asked == ["确认吗"]
    ui.info("进度")
    assert base.infos == ["进度"]


def test_registry_task_binding() -> None:
    """任务绑定可查可摘（agents.exec stop 依此取消同步子代理）。"""
    reg = AgentActivityRegistry()
    marker = object()
    reg.bind_task("sub-4", marker)
    assert reg.task_for("sub-4") is marker
    reg.forget_task("sub-4")
    assert reg.task_for("sub-4") is None


def test_has_listener_gates_terminal_isolation() -> None:
    """没有宿主监听器（终端）时子代理仍走原始 UI；桌面绑定后才隔离。"""
    reg = AgentActivityRegistry()
    assert reg.has_listener() is False
    reg.set_listener(lambda rows: None)
    assert reg.has_listener() is True
    reg.clear(keep_listener=False)
    assert reg.has_listener() is False


def test_registry_clear_resets_everything() -> None:
    """清空后快照为空，监听器仍在（会话重置不拆事件通道）。"""
    reg = AgentActivityRegistry()
    reg.start_agent("sub-5", "general-1")
    reg.clear()
    assert reg.snapshot() == []
    # 清空会发一次空快照通知；此处只验证不抛异常
    time.sleep(0)
