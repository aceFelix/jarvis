"""多任务浮窗规划器测试（纯逻辑，不创建 Tk）。

覆盖：auto 阈值、always、off、完成保留、上限与槽位腾挪、换轮清理、
并行顺序与入参摘要。

@author aceFelix
"""

from __future__ import annotations

from agent.ui.cockpit import VisualOptions
from agent.ui.cockpit_cards import (
    CardPlanner,
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    summarize_input,
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
    """构造一组测试用可视化选项。"""
    values = dict(task_cockpit="window", tool_cards="auto", card_min_ms=300, card_max=4,
                  card_hold_ms=600, effects="subtle")
    values.update(kwargs)
    return VisualOptions.from_values(**values)


def _start(tool_use_id: str, tool: str = "Bash", **extra):
    """构造 tool_start 事件。"""
    event = {"type": "tool_start", "tool_use_id": tool_use_id, "tool": tool}
    event.update(extra)
    return event


def _end(tool_use_id: str, ok: bool = True, ms: int = 500, **extra):
    """构造 tool_end 事件。"""
    event = {"type": "tool_end", "tool_use_id": tool_use_id, "ok": ok, "duration_ms": ms}
    event.update(extra)
    return event


class TestAutoThreshold:
    def test_short_tool_never_shows(self) -> None:
        """auto：未到 300ms 就结束的短工具不产生卡。"""
        clock = _Clock()
        planner = CardPlanner(_options(), clock)
        planner.update(_start("t1"))
        assert planner.visible() == []
        clock.advance(150)
        planner.tick()
        assert planner.visible() == []
        planner.update(_end("t1"))
        assert planner.visible() == []
        assert planner.pending_count() == 0

    def test_long_tool_shows_after_threshold(self) -> None:
        """auto：到 300ms 仍在运行才转为 running。"""
        clock = _Clock()
        planner = CardPlanner(_options(), clock)
        planner.update(_start("t1"))
        clock.advance(200)
        planner.tick()
        assert planner.visible() == []
        clock.advance(150)
        planner.tick()
        cards = planner.visible()
        assert len(cards) == 1
        assert cards[0].state == RUNNING

    def test_always_shows_immediately(self) -> None:
        """always：tool_start 立即显示。"""
        planner = CardPlanner(_options(tool_cards="always"), _Clock())
        planner.update(_start("t1"))
        assert [c.state for c in planner.visible()] == [RUNNING]

    def test_off_produces_nothing(self) -> None:
        """off：规划器不产生任何卡。"""
        planner = CardPlanner(_options(tool_cards="off"), _Clock())
        assert planner.update(_start("t1")) is False
        assert planner.visible() == []


class TestCompletionHold:
    def test_done_card_removed_after_hold(self) -> None:
        """完成后保留 hold_ms，到点收起。"""
        clock = _Clock()
        planner = CardPlanner(_options(tool_cards="always", card_hold_ms=600), clock)
        planner.update(_start("t1"))
        planner.update(_end("t1", ok=True, ms=120))
        cards = planner.visible()
        assert len(cards) == 1 and cards[0].state == DONE and cards[0].ms == 120
        clock.advance(400)
        planner.tick()
        assert len(planner.visible()) == 1
        clock.advance(250)
        planner.tick()
        assert planner.visible() == []

    def test_failed_card_marks_failed(self) -> None:
        """失败工具：状态为 failed 并记录错误摘要。"""
        planner = CardPlanner(_options(tool_cards="always"), _Clock())
        planner.update(_start("t1"))
        planner.update(_end("t1", ok=False, ms=300, error="exit 1"))
        card = planner.visible()[0]
        assert card.state == FAILED
        assert card.ok is False
        assert card.error == "exit 1"


class TestMaxSlots:
    def test_extra_tools_do_not_exceed_max(self) -> None:
        """always + max=2：第三张保持 pending，不显示。"""
        clock = _Clock()
        planner = CardPlanner(_options(tool_cards="always", card_max=2), clock)
        for index in range(3):
            planner.update(_start(f"t{index}"))
        planner.tick()
        assert len(planner.visible()) == 2
        assert planner.pending_count() == 1

    def test_pending_promotes_after_slot_frees(self) -> None:
        """一张卡收掉后，等待中的长工具会自动补位。"""
        clock = _Clock()
        planner = CardPlanner(
            _options(tool_cards="always", card_max=2, card_hold_ms=300), clock
        )
        planner.update(_start("t0"))
        planner.update(_start("t1"))
        planner.update(_start("t2"))
        assert planner.pending_count() == 1

        planner.update(_end("t0", ok=True, ms=100))
        clock.advance(350)
        planner.tick()  # 收掉 t0，触发 t2 补位
        visible = planner.visible()
        assert len(visible) == 2
        assert "t2" in {card.tool_use_id for card in visible}

    def test_run_start_resets_all_cards(self) -> None:
        """换轮清空所有卡（窗口由上层统一销毁）。"""
        planner = CardPlanner(_options(tool_cards="always"), _Clock())
        planner.update(_start("t1"))
        assert planner.visible()
        assert planner.update({"type": "run_start", "run_id": "r2"}) is True
        assert planner.visible() == []
        assert planner.pending_count() == 0


class TestHelpers:
    def test_summarize_input_prefers_command(self) -> None:
        """入参摘要优先 command / 路径类字段。"""
        assert summarize_input("Bash", {"command": "date +%Y"}) == "date +%Y"
        assert summarize_input("FileWrite", {"file_path": "E:/a.txt"}) == "E:/a.txt"

    def test_parallel_cards_keep_start_order(self) -> None:
        """并行工具按启动顺序返回，槽位稳定。"""
        planner = CardPlanner(_options(tool_cards="always"), _Clock())
        for name in ("A", "B", "C"):
            planner.update(_start(name.lower(), tool=name))
        assert [c.tool for c in planner.visible()] == ["A", "B", "C"]

    def test_pending_state_constant(self) -> None:
        """pending 是合法内部状态（断言避免常量被误改）。"""
        assert PENDING == "pending"

    def test_card_keeps_input_detail_for_expand(self) -> None:
        """点击展开需要完整入参字典，不能只留一行摘要。"""
        planner = CardPlanner(_options(tool_cards="always"), _Clock())
        planner.update(
            _start(
                "t1",
                tool="Bash",
                input={"command": "git status", "file_path": "a.py"},
            )
        )
        card = planner.visible()[0]
        assert card.input_detail == {"command": "git status", "file_path": "a.py"}
        assert card.input_summary == "git status"


class TestAgentFilter:
    def test_agent_tool_events_are_ignored(self) -> None:
        """子代理工具不再弹工具卡（由代理卡展示，避免同一动作两份卡）。"""
        planner = CardPlanner(_options(tool_cards="always"), _Clock())
        assert (
            planner.update(
                _start("t1", agent_id="sub-1", agent_name="explorer-1")
            )
            is False
        )
        assert planner.visible() == []
        assert planner.pending_count() == 0
