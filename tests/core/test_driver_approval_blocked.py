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

from agent.core.tasks.approval_ops import APPROVAL_HOLD_S, APPROVAL_LEASE_TTL_MIN
from agent.core.tasks.driver import StepOutcome, TaskDriver
from agent.core.tasks.spine import (
    BLOCK_APPROVAL,
    BLOCK_TIMEOUT,
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


def _parse_iso(text: str):
    """解析租约的 ISO 时刻（只用于断言 TTL，不参与业务判定）。"""
    from datetime import datetime

    return datetime.fromisoformat(text)


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


class TestApproveStepGrantsLease:
    """批准重试：租约必须按“被拒工具 + 本目标目录 + 短 TTL”三重收窄。

    这条通路存在的全部理由：只把步骤退回 pending 是个空转按钮——重试走同一条
    无 UI 的默认判定，同一个 ASK 还会落成 DENY（2026-10-22 实机之后补的设计）。
    租约单例由 tests/conftest.py 指向 tmp_path，不会写用户真实 ~/.jarvis。
    @author aceFelix
    """

    @staticmethod
    def _blocked(tmp_path, *, tools=None, workdir=None, state=None, reason=None):
        """跑出一个受阻步骤，并可按测试需要改写步骤的被拒清单/状态。"""
        goal = TaskGoal(
            goal="把周报存成文件", steps=[TaskStep(desc="写入 weekly.txt")],
            workdir=str(workdir if workdir is not None else tmp_path / "proj"),
        )
        stub = Stub(_blocked_outcome())
        driver, store = _driver(tmp_path, stub, goal=goal, notes=[])
        driver._tick()
        if tools is not None or state is not None or reason is not None:
            def _edit(g):
                s = g.steps[0]
                if tools is not None:
                    s.denied_tools = list(tools)
                if state is not None:
                    s.state = state
                if reason is not None:
                    s.block_reason = reason
            store.update(goal.id, _edit)
        return driver, store, goal.id, goal.steps[0].id

    def test_blocked_step_persists_denied_tools(self, tmp_path):
        """被拒工具名单必须跟着台账落盘：丢了它就只好盲放行。"""
        _driver_, store, gid, sid = self._blocked(tmp_path)
        step = store.load(gid).steps[0]
        assert step.denied_tools == ["FileWrite"]
        # 落盘形状里真带这个键（旧台账读成空表，但不能新台账丢字段）
        assert store.load(gid).to_dict()["steps"][0]["denied_tools"] == ["FileWrite"]
        assert sid == step.id

    def test_approve_grants_narrowly_scoped_lease(self, tmp_path):
        from agent.core.policy.lease import get_lease_store

        driver, _store, gid, sid = self._blocked(tmp_path)
        workdir = str(tmp_path / "proj")

        ok, msg = driver.approve_step(gid, sid)

        assert ok is True
        assert "已签发租约" in msg and "FileWrite" in msg and workdir in msg
        leases = get_lease_store().list_active()
        assert len(leases) == 1
        lease = leases[0]
        # 三重收窄：只放刚批过的工具、只放本目标目录、只给半个钟头
        assert lease.tools == ["FileWrite"]
        assert lease.path_within == [f"{workdir}/**"]
        assert lease.note and "周报" in lease.note
        granted = _parse_iso(lease.granted_at)
        assert int((_parse_iso(lease.expires_at) - granted).total_seconds() // 60) == APPROVAL_LEASE_TTL_MIN

    def test_lease_covers_only_that_tool_and_directory(self, tmp_path):
        """放行面的边界：换个工具、出了目录、不带路径参数都不能命中。"""
        from agent.core.policy.lease import get_lease_store

        driver, _store, gid, sid = self._blocked(tmp_path)
        driver.approve_step(gid, sid)
        store = get_lease_store()
        workdir = str(tmp_path / "proj")

        assert store.match("FileWrite", str(tmp_path / "proj" / "weekly.txt"), workdir) is not None
        assert store.match("FileWrite", str(tmp_path / "other" / "x.txt"), workdir) is None
        assert store.match("FileEdit", str(tmp_path / "proj" / "x.txt"), workdir) is None
        # Bash 这类没有路径参数的调用：限路径的租约不算命中（宁可多问一次）
        assert store.match("FileWrite", "", workdir) is None

    def test_approve_resets_step_for_retry(self, tmp_path):
        driver, store, gid, sid = self._blocked(tmp_path)
        driver._wake.clear()

        ok, _msg = driver.approve_step(gid, sid)

        assert ok is True
        after = store.load(gid).steps[0]
        assert after.state == STEP_PENDING
        assert after.next_run_at == 0.0 and after.asked is False
        assert after.denied_tools == [], "重试前不该留着上一轮的旧清单误导下一次"
        assert driver._wake.is_set()

    def test_missing_tool_list_refuses_to_grant_anything(self, tmp_path):
        """没有清单就不签：签“全部工具不限路径”比用户同意的更大。"""
        from agent.core.policy.lease import get_lease_store

        driver, store, gid, sid = self._blocked(tmp_path, tools=[])

        ok, msg = driver.approve_step(gid, sid)

        assert ok is False
        assert "/lease" in msg
        assert get_lease_store().list_active() == []
        assert store.load(gid).steps[0].state == STEP_BLOCKED, "没签成就不能把步骤放回重试"

    def test_only_approval_blocks_can_be_approved(self, tmp_path):
        """等条件超时与已定局的步骤不是“待批准”，错状态必须拒绝。"""
        driver, store, gid, sid = self._blocked(tmp_path, reason=BLOCK_TIMEOUT)
        ok, msg = driver.approve_step(gid, sid)
        assert ok is False and "不是等授权状态" in msg

        driver2, store2, gid2, sid2 = self._blocked(tmp_path, state=STEP_DONE, reason="")
        assert driver2.approve_step(gid2, sid2)[0] is False
        assert store2.load(gid2).steps[0].state == STEP_DONE

    def test_unknown_goal_or_step_is_honest(self, tmp_path):
        from agent.core.policy.lease import get_lease_store

        driver, _store, gid, _sid = self._blocked(tmp_path)

        ok1, msg1 = driver.approve_step("nope", "nope")
        assert ok1 is False and msg1
        ok2, msg2 = driver.approve_step(gid, "no-such-step")
        assert ok2 is False and "没有这个步骤" in msg2
        assert get_lease_store().list_active() == []


class TestHoldConstantSane:
    """退避取值的护栏：太长会盖住用户答复的意义，太短就回到烧钱循环。"""

    def test_hold_is_one_hour(self):
        assert APPROVAL_HOLD_S == 3600.0
