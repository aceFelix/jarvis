"""W3 受阻判定回归：被权限拦下的步骤绝不能报成"已完成"。

盯的是 2026-10-22 实机走查抓到的谎报缺陷（docs/fixlogs/w3-driver-approval-blocked-report.md）：
无人值守的后台步骤里 ``FileWrite`` 无条件 ASK、无 UI 时落成 DENY，子代理如实汇报
"需要授权"，而旧代码凭 ``success=True``（它的含义其实只是"子代理跑完没抛异常"）把
步骤定成 done、目标定成 done，还向用户播报"已完成：1/1 步"。

覆盖四条口径：
- 受阻判定排在 success 前面：有 denial 就阻塞并问人，不落成 done
- 受阻必须挂安全退避：受阻步骤没有 ``wait`` 护身，不退避就是每轮重跑一次 LLM 的死循环
- 同一受阻只问一次（沿用 ``asked`` 抑制）
- 用户批准后 ``next_run_at`` 清零并唤醒驱动器（否则"批了要等一小时才动"看着像没生效），
  且批一个不存在的步骤必须返回 False
- 正常放行后重试能真把步骤做完（批准不是只改个标记）

执行体全部注入 spawn 桩，绝不调真实 LLM；台账一律 tmp_path。

@author aceFelix
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from agent.core.tasks.approval_ops import APPROVAL_HOLD_S
from agent.core.tasks.driver import StepOutcome, TaskDriver
from agent.core.tasks.spine import (
    BLOCK_APPROVAL,
    GOAL_PAUSED,
    STEP_BLOCKED,
    STEP_DONE,
    STEP_PENDING,
    SpineStore,
    TaskGoal,
    TaskStep,
)

#: 子代理被拦下时的真实汇报形状（照实取自 2026-10-22 的实机台账）
_DENIAL = {"tool": "FileWrite", "reason": "需要用户确认但当前环境无 UI"}
_REPORT = "已尝试执行本步骤，但被权限拦截，因此未产生任何改动。需要授权：请授予写入权限。"


def _settings(**over) -> SimpleNamespace:
    base = {
        "tasks_enabled": True,
        "tasks_step_max_attempts": 3,
        "tasks_retry_backoff_s": 60,
        "tasks_blocked_ask_days": 3,
        "tasks_wait_deadline_hours": 72,
        "workdir": r"D:\default",
        "api_format": "",
        "api_key": "",
    }
    base.update(over)
    return SimpleNamespace(**base)


class Stub:
    """按脚本回报的注入通路，可顺带伪造“本步确实产生了副作用”。"""

    def __init__(self, outcome: StepOutcome, *, journal_ids: list[str] | None = None) -> None:
        self.outcome = outcome
        self.calls = 0
        self._journal_ids = journal_ids or []

    def spawn(self, definition, prompt: str, *, task_id: str = "",
              session_name: str = "", effect_ids=None) -> StepOutcome:
        self.calls += 1
        if effect_ids is not None and self._journal_ids:
            # 模拟编排器记完账后把账本 id 回扣给宿主
            effect_ids.extend(self._journal_ids)
        return self.outcome


def _driver(tmp_path, stub: Stub, *, settings=None, goal: TaskGoal | None = None,
            notes=None) -> tuple[TaskDriver, SpineStore]:
    """装配不起线程的驱动器 + 台账（notifier 收成列表便于断言播报内容）。"""
    store = SpineStore(tmp_path / "goals")
    sink = notes if notes is not None else []
    # notifier 的实际调用形状是 (source, text, dedup_key) 三个位置参数
    driver = TaskDriver(
        settings=settings or _settings(),
        store=store,
        notifier=lambda source, text, dedup_key="": sink.append((source, text, dedup_key)),
        spawn=stub.spawn,
    )
    if goal is not None:
        store.save(goal)
    return driver, store


def _goal() -> TaskGoal:
    return TaskGoal(goal="把周报存成文件", steps=[TaskStep(desc="写入 weekly.txt")])


def _blocked_outcome() -> StepOutcome:
    return StepOutcome(success=True, report=_REPORT, denials=[dict(_DENIAL)])


class TestBlockedWinsOverSuccess:
    """受阻优先：这是整条谎报缺陷的核心。"""

    def test_step_blocks_instead_of_done(self, tmp_path):
        notes: list[tuple[str, str, str]] = []
        goal = _goal()
        stub = Stub(_blocked_outcome())
        driver, store = _driver(tmp_path, stub, goal=goal, notes=notes)

        driver._tick()

        step = store.load(goal.id).steps[0]
        assert step.state == STEP_BLOCKED
        assert step.block_reason == BLOCK_APPROVAL
        assert step.state != STEP_DONE, "被拦下的步骤绝不能是 done"

    def test_goal_not_reported_as_completed(self, tmp_path):
        notes: list[tuple[str, str, str]] = []
        goal = _goal()
        stub = Stub(_blocked_outcome())
        driver, _ = _driver(tmp_path, stub, goal=goal, notes=notes)

        driver._tick()

        texts = " ".join(n[1] for n in notes)
        assert "已完成" not in texts, f"受阻却播报已完成：{texts}"
        assert "需要授权" in texts
        assert "FileWrite" in texts, "播报要带上是哪个工具被拒，用户才知道批什么"

    def test_subagent_report_kept_as_result(self, tmp_path):
        goal = _goal()
        stub = Stub(_blocked_outcome())
        driver, store = _driver(tmp_path, stub, goal=goal, notes=[])

        driver._tick()

        step = store.load(goal.id).steps[0]
        # 子代理的如实汇报必须留在台账里，供 /tasks show 与面板回看
        assert "未产生任何改动" in step.result
        assert "FileWrite" in step.last_error

    def test_clean_success_still_done(self, tmp_path):
        """守住正常路径：没被拒且 success 时照旧落 done（别把修好的路改坏）。"""
        goal = _goal()
        stub = Stub(StepOutcome(success=True, report="已写入 weekly.txt"))
        driver, store = _driver(tmp_path, stub, goal=goal, notes=[])

        driver._tick()

        assert store.load(goal.id).steps[0].state == STEP_DONE


class TestPartialProgressWithDenials:
    """有产出却夹着被拒：照实落 done，但不能瞒，也不能喊“需要授权”。

    2026-10-22 第二轮实机踩的就是这一头：``FileWrite`` 已按规则写成文件并入账，
    子代理随后调 ``Bash`` 确认时被拒。“有 DENY 即受阻"会把一个已经完成的目标
    报成卡住，让用户去批一个根本不需要批的东西——与谎报同样错，只是方向相反。
    """

    def test_done_with_denials_reported_honestly(self, tmp_path):
        notes: list[tuple[str, str, str]] = []
        goal = _goal()
        stub = Stub(_blocked_outcome(), journal_ids=["rec-0001"])
        driver, store = _driver(tmp_path, stub, goal=goal, notes=notes)

        driver._tick()

        step = store.load(goal.id).steps[0]
        assert step.state == STEP_DONE, "已经写成文件了，不能报成卡住"
        assert "被权限拦下" in step.result and "FileWrite" in step.result, \
            "被拒项必须附在结论里，不能隐去"
        assert step.journal_ids == ["rec-0001"]
        texts = " ".join(n[1] for n in notes)
        assert "需要授权" not in texts, "对已完成的目标不该要用户批一个不存在的阻塞"
        assert step.next_run_at == 0.0

    def test_no_output_still_blocks(self, tmp_path):
        """对照组：同一个 denial，只要没任何产出就必须去问人。"""
        goal = _goal()
        stub = Stub(_blocked_outcome())  # 不注入 journal_ids
        driver, store = _driver(tmp_path, stub, goal=goal, notes=[])

        driver._tick()

        assert store.load(goal.id).steps[0].state == STEP_BLOCKED


class TestApprovalHold:
    """受阻必须挂退避：否则每轮重跑一次 LLM，与"不烧轮询"这条验收相冲。"""

    def test_next_run_deferred(self, tmp_path):
        goal = _goal()
        stub = Stub(_blocked_outcome())
        driver, store = _driver(tmp_path, stub, goal=goal, notes=[])

        driver._tick()

        step = store.load(goal.id).steps[0]
        assert step.next_run_at > time.time(), "受阻步骤必须挂上安全退避"

    def test_no_retry_while_holding(self, tmp_path):
        goal = _goal()
        stub = Stub(_blocked_outcome())
        driver, store = _driver(tmp_path, stub, goal=goal, notes=[])

        driver._tick()
        assert stub.calls == 1
        driver._tick()
        driver._tick()
        assert stub.calls == 1, "退避期内不得再次派工（一次受阻 = 一次 LLM，绝不重复）"
        assert store.load(goal.id).steps[0].state == STEP_BLOCKED

    def test_ask_once(self, tmp_path):
        notes: list[tuple[str, str, str]] = []
        stub = Stub(_blocked_outcome())
        driver, _ = _driver(tmp_path, stub, goal=_goal(), notes=notes)

        driver._tick()
        for _ in range(3):
            driver._tick()
        asks = [n for n in notes if "需要授权" in n[1]]
        assert len(asks) == 1, "同一受阻反复打扰用户，等于把闸门当噪音"


class TestRequestApproval:
    """用户答复后的收尾：清零退避 + 立刻唤醒。"""

    def test_approve_clears_hold_and_wakes(self, tmp_path):
        goal = _goal()
        stub = Stub(_blocked_outcome())
        driver, store = _driver(tmp_path, stub, goal=goal, notes=[])
        driver._tick()
        step_id = store.load(goal.id).steps[0].id
        driver._wake.clear()

        assert driver.request_approval(goal.id, step_id, approve=True) is True

        after = store.load(goal.id).steps[0]
        assert after.state == STEP_PENDING
        assert after.next_run_at == 0.0, "批准后还等退避到期，看起来就像批准没生效"
        assert after.asked is False
        assert driver._wake.is_set(), "用户刚表过态，驱动器要立刻复查而不是等下一轮"

    def test_approve_then_retry_succeeds(self, tmp_path):
        goal = _goal()
        stub = Stub(_blocked_outcome())
        driver, store = _driver(tmp_path, stub, goal=goal, notes=[])
        driver._tick()
        step_id = store.load(goal.id).steps[0].id
        # 用户批准后重试：这回通路放行（桩改报成功、无 denial）
        stub.outcome = StepOutcome(success=True, report="已写入 weekly.txt")

        driver.request_approval(goal.id, step_id, approve=True)
        driver._tick()

        assert store.load(goal.id).steps[0].state == STEP_DONE

    def test_deny_pauses_whole_goal(self, tmp_path):
        goal = _goal()
        stub = Stub(_blocked_outcome())
        driver, store = _driver(tmp_path, stub, goal=goal, notes=[])
        driver._tick()
        step_id = store.load(goal.id).steps[0].id

        driver.request_approval(goal.id, step_id, approve=False)

        assert store.load(goal.id).status == GOAL_PAUSED
        assert store.load(goal.id).steps[0].block_reason == BLOCK_APPROVAL

    def test_unknown_step_returns_false(self, tmp_path):
        goal = _goal()
        stub = Stub(_blocked_outcome())
        driver, store = _driver(tmp_path, stub, goal=goal, notes=[])
        driver._tick()
        before = store.load(goal.id).steps[0]

        assert driver.request_approval(goal.id, "no-such-step", approve=True) is False
        after = store.load(goal.id).steps[0]
        # 批了个不存在的步骤：既不能回成功，也不能顺手把真那个步骤改了
        assert (after.state, after.block_reason, after.asked) == (
            before.state, before.block_reason, before.asked)
        assert driver._wake.is_set() is False


class TestHoldConstantSane:
    """退避取值的护栏：太长会盖住用户答复的意义，太短就回到烧钱循环。"""

    def test_hold_is_one_hour(self):
        assert APPROVAL_HOLD_S == 3600.0
