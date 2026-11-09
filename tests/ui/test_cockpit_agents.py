"""子代理任务卡规划器测试（纯逻辑，不创建 Tk）。

覆盖：出现阈值、字段映射、完成保留与收起、失败/停止、空闲卡保留、
上限与溢出、快照消失清理、关闭模式。

@author aceFelix
"""

from __future__ import annotations

from agent.ui.cockpit import VisualOptions
from agent.ui.cockpit_agents import (
    ACTIVE,
    DONE,
    FAILED,
    IDLE,
    AgentCardPlanner,
)


class _Clock:
    """可推进的假单调时钟（秒）。"""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms / 1000.0


def _options(**kwargs) -> VisualOptions:
    """构造一组测试用可视化选项（默认代理卡开启）。"""
    values = {
        "task_cockpit": "window",
        "tool_cards": "auto",
        "card_min_ms": 300,
        "card_max": 4,
        "card_hold_ms": 600,
        "agent_cards": "auto",
        "agent_card_max": 3,
        "agent_card_hold_ms": 800,
        "agent_card_keep_idle": True,
        "agent_card_min_ms": 0,
    }
    values.update(kwargs)
    return VisualOptions.from_values(**values)


def _item(agent_id: str, status: str = "running", **fields):
    """构造一条台账快照记录。"""
    item = {
        "agent_id": agent_id,
        "name": agent_id,
        "role": "explorer",
        "mode": "subagent",
        "status": status,
        "started_at": 1000.0,
    }
    item.update(fields)
    return item


class TestAppearance:
    def test_active_agent_shows_immediately(self) -> None:
        """min_ms=0：子代理一开始就显示，字段正确映射。"""
        planner = AgentCardPlanner(_options(), _Clock())
        assert planner.update([_item("sub-1", task="找 TODO", current_tool="Grep src/", iterations=2, tool_calls=3)]) is True

        cards = planner.visible()
        assert len(cards) == 1
        card = cards[0]
        assert card.state == ACTIVE
        assert card.name == "sub-1"
        assert card.task == "找 TODO"
        assert card.current_tool == "Grep src/"
        assert card.iterations == 2 and card.tool_calls == 3

    def test_unchanged_snapshot_does_not_report_change(self) -> None:
        """台账节流会重复发同一快照：无变化时不能反复整卡重绘。"""
        planner = AgentCardPlanner(_options(), _Clock())
        items = [_item("sub-1")]
        assert planner.update(items) is True
        assert planner.update(items) is False

    def test_min_ms_delays_appearance(self) -> None:
        """出现阈值：未到点不显示，到点后 tick 报告变化让卡片补位。"""
        clock = _Clock()
        planner = AgentCardPlanner(_options(agent_card_min_ms=500), clock)
        planner.update([_item("sub-1")])
        assert planner.visible() == []

        clock.advance(300)
        assert planner.tick() is False
        assert planner.visible() == []

        clock.advance(250)
        assert planner.tick() is True
        assert len(planner.visible()) == 1

    def test_off_produces_nothing(self) -> None:
        """agent_cards=off：不产生任何卡，原有台账记录被清掉。"""
        planner = AgentCardPlanner(_options(agent_cards="off"), _Clock())
        assert planner.update([_item("sub-1")]) is False
        assert planner.visible() == []


class TestTerminalStates:
    def test_done_holds_then_hides(self) -> None:
        """完成后保留 hold_ms，到点收起。"""
        clock = _Clock()
        planner = AgentCardPlanner(_options(agent_card_hold_ms=800), clock)
        planner.update([_item("sub-1")])
        planner.update([_item("sub-1", status="done", output="找到 12 处")])

        card = planner.visible()[0]
        assert card.state == DONE
        assert card.output == "找到 12 处"

        clock.advance(700)
        assert planner.tick() is False
        assert len(planner.visible()) == 1

        clock.advance(150)
        assert planner.tick() is True
        assert planner.visible() == []

    def test_failed_marks_error(self) -> None:
        """失败：状态 failed 并保留错误摘要。"""
        planner = AgentCardPlanner(_options(), _Clock())
        planner.update([_item("sub-1")])
        planner.update([_item("sub-1", status="failed", error="模型超时")])

        card = planner.visible()[0]
        assert card.state == FAILED
        assert card.error == "模型超时"

    def test_stopped_removes_immediately(self) -> None:
        """stopped：没有保留价值，立即让出槽位。"""
        planner = AgentCardPlanner(_options(), _Clock())
        planner.update([_item("sub-1")])
        assert planner.update([_item("sub-1", status="stopped")]) is True
        assert planner.visible() == []

    def test_never_seen_done_is_ignored(self) -> None:
        """窗口启动前就结束的代理，不补弹幽灵卡。"""
        planner = AgentCardPlanner(_options(), _Clock())
        assert planner.update([_item("sub-old", status="done")]) is False
        assert planner.visible() == []

    def test_fast_agent_below_threshold_never_flashes(self) -> None:
        """未跨过出现阈值的快任务结束时直接丢弃，不闪一下。"""
        clock = _Clock()
        planner = AgentCardPlanner(_options(agent_card_min_ms=500), clock)
        planner.update([_item("sub-1")])
        clock.advance(200)
        assert planner.update([_item("sub-1", status="done")]) is True
        assert planner.visible() == []


class TestIdle:
    def test_idle_kept_as_dim_card(self) -> None:
        """队友空闲：保留暗色卡（state=idle）。"""
        planner = AgentCardPlanner(_options(agent_card_keep_idle=True), _Clock())
        planner.update([_item("mate-1", status="idle", mode="teammate", team="alpha")])
        card = planner.visible()[0]
        assert card.state == IDLE
        assert card.team == "alpha"

    def test_idle_removed_when_disabled(self) -> None:
        """keep_idle=false：空闲卡直接收起。"""
        planner = AgentCardPlanner(_options(agent_card_keep_idle=False), _Clock())
        assert planner.update([_item("mate-1", status="idle")]) is False
        assert planner.visible() == []

    def test_idle_transitions_back_to_active(self) -> None:
        """队友接到新任务：空闲卡恢复运行状态并保持可见。"""
        planner = AgentCardPlanner(_options(), _Clock())
        planner.update([_item("mate-1", status="idle")])
        planner.update([_item("mate-1", status="running", current_tool="Grep")])
        card = planner.visible()[0]
        assert card.state == ACTIVE
        assert card.current_tool == "Grep"


class TestLimits:
    def test_max_truncates_and_prioritizes_active_over_idle(self) -> None:
        """超上限时先收空闲卡：两个运行中 + 两个空闲、上限 2 → 只显示运行中。"""
        planner = AgentCardPlanner(_options(agent_card_max=2), _Clock())
        planner.update([
            _item("idle-a", status="idle", started_at=900.0),
            _item("run-a", started_at=1000.0),
            _item("idle-b", status="idle", started_at=900.0),
            _item("run-b", started_at=1001.0),
        ])

        cards = planner.visible()
        assert [card.agent_id for card in cards] == ["run-a", "run-b"]
        assert planner.overflow_count() == 2
        assert planner.summary() == "AGENTS 2 · +2"

    def test_summary_empty_without_cards(self) -> None:
        """没有卡时主 HUD 页脚不显示代理计数。"""
        planner = AgentCardPlanner(_options(), _Clock())
        assert planner.summary() == ""

    def test_missing_agent_removed(self) -> None:
        """快照里消失的代理（台账 clear）同步移除。"""
        planner = AgentCardPlanner(_options(), _Clock())
        planner.update([_item("sub-1"), _item("sub-2")])
        assert planner.update([_item("sub-1")]) is True
        assert [card.agent_id for card in planner.visible()] == ["sub-1"]
        assert planner.update([]) is True
        assert planner.visible() == []
