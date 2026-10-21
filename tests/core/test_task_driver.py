"""W3.1 任务脊柱驱动器测试：不烧轮询、一次一步、播报只走闸门。

覆盖验收硬要求与几条安全口径：
- **条件未到零 LLM 调用**：time 等待没到点时反复 ``_tick()``，spawn 一次都不进；
  失败退避未到点同样不进（退避时刻落在磁盘上，重启也不丢）
- 一次只跑一步（``_busy`` 闸门：步骤执行期间重入 ``_tick`` 不再派工）
- 推进用的是台账里目标自带的 workdir，不是驱动器的缺省
- 播报一律经 notifier（源名固定 ``task``），且 notifier 必须先过 W1 闸门；
  同一个步骤的询问只问一次
- 禁用态 / 没有可用 provider 时不起线程（读侧仍可列台账）
- ``budget`` 权重表里必须登记 ``task`` 源，否则乘上紧迫度就落进门槛以下，
  整个脊柱"永远不吱声"（同 notify-budget-percept-relay-fix.md 的坑）

执行体全部注入 spawn 桩，绝不调真实 LLM；台账一律 tmp_path。

@author aceFelix
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from agent.core.tasks.driver import (
    FILE_CHECK_INTERVAL_S,
    GATE_URGENCY,
    NOTIFY_SOURCE,
    StepOutcome,
    TaskDriver,
    make_gate_notifier,
)
from agent.core.tasks.spawn import default_spawn
from agent.core.tasks.spine import (
    BLOCK_APPROVAL,
    BLOCK_ERROR,
    BLOCK_TIMEOUT,
    BLOCK_WAIT,
    GOAL_DONE,
    GOAL_FAILED,
    GOAL_PAUSED,
    STEP_BLOCKED,
    STEP_DONE,
    STEP_FAILED,
    STEP_PENDING,
    STEP_RUNNING,
    WAIT_FILE_EXISTS,
    WAIT_PERCEPT_EVENT,
    WAIT_TIME,
    SpineStore,
    TaskGoal,
    TaskStep,
    TaskWait,
    iso_of,
)


def _settings(**over) -> SimpleNamespace:
    """``[tasks]`` 字段（缺省与 settings.py 一致）。"""
    base = {
        "tasks_enabled": True,
        "tasks_step_max_attempts": 3,
        "tasks_retry_backoff_s": 60,
        "tasks_blocked_ask_days": 3,
        "tasks_wait_deadline_hours": 72,
        "workdir": r"D:\default",
        # provider 可用性判定用得到：默认"没配 LLM"，靠注入 spawn/spawn 计数跑测试
        "api_format": "",
        "api_key": "",
    }
    base.update(over)
    return SimpleNamespace(**base)


class Recorder:
    """notifier 与 spawn 的公共记录器。"""

    def __init__(self, outcomes=None) -> None:
        self.notifications: list[tuple[str, str, str]] = []
        self.prompts: list[str] = []
        self._outcomes = outcomes or []
        self.spawn_count = 0

    def notify(self, source: str, text: str, dedup_key: str = "") -> None:
        self.notifications.append((source, text, dedup_key))

    def spawn(self, definition, prompt: str) -> StepOutcome:
        self.spawn_count += 1
        self.prompts.append(prompt)
        if self._outcomes:
            return self._outcomes.pop(0)
        return StepOutcome(success=True, report="办完了")


def _driver(tmp_path, rec: Recorder, *, settings=None, goal: TaskGoal | None = None,
            **over) -> tuple[TaskDriver, SpineStore]:
    """装配一个不起线程的驱动器 + 台账（store 指到 tmp）。"""
    store = SpineStore(tmp_path / "goals")
    driver = TaskDriver(
        settings=settings if settings is not None else _settings(),
        store=store,
        notifier=rec.notify,
        spawn=rec.spawn,
        **over,
    )
    if goal is not None:
        store.save(goal)
    return driver, store


def _goal(steps, **over) -> TaskGoal:
    data = {"goal": "把周报整理好发给团队", "steps": steps}
    data.update(over)
    return TaskGoal(**data)


class TestAvailability:
    """不起线程的三种情形：开关关、没 provider、没注入执行体。"""

    def test_disabled_switch(self, tmp_path):
        rec = Recorder()
        driver, _ = _driver(tmp_path, rec, settings=_settings(tasks_enabled=False))
        assert driver.enabled is False
        assert driver.start() is False
        assert driver.running is False
        assert rec.spawn_count == 0

    def test_no_provider_and_no_spawn(self, tmp_path):
        """开关开着但没有可用 LLM 配置：判"不可用"，不白起线程。"""
        rec = Recorder()
        store = SpineStore(tmp_path / "goals")
        driver = TaskDriver(settings=_settings(), store=store, notifier=rec.notify)
        assert driver.enabled is False
        assert driver.start() is False

    def test_configured_llm_counts_as_available(self, tmp_path):
        """settings 里 api_format/api_key 齐备即可推进（provider 到用时才现建）。"""
        rec = Recorder()
        store = SpineStore(tmp_path / "goals")
        driver = TaskDriver(
            settings=_settings(api_format="openai", api_key="sk-test"),
            store=store, notifier=rec.notify,
        )
        assert driver.enabled is True

    def test_stop_is_idempotent(self, tmp_path):
        rec = Recorder()
        driver, _ = _driver(tmp_path, rec)
        driver.stop()
        driver.stop()
        assert driver.running is False


class TestNoPollingBurn:
    """验收硬要求：条件等待命中前不重复调 LLM。"""

    def test_time_wait_not_due_zero_llm_calls(self, tmp_path):
        rec = Recorder()
        goal = _goal([TaskStep(
            desc="发送",
            wait=TaskWait(kind=WAIT_TIME, spec={"at": iso_of(time.time() + 3600)}),
        )])
        driver, store = _driver(tmp_path, rec, goal=goal)

        for _ in range(5):
            driver._tick()
        assert rec.spawn_count == 0
        # 等待中只登记"在等条件"，不烧执行
        assert store.load(goal.id).steps[0].state == STEP_BLOCKED
        assert store.load(goal.id).steps[0].block_reason == BLOCK_WAIT

    def test_wake_seconds_points_at_the_deadline(self, tmp_path):
        """唤醒时刻由条件本身算出来（这就是"不轮询"的实现口径）。"""
        rec = Recorder()
        at = time.time() + 600
        goal = _goal([TaskStep(desc="发送", wait=TaskWait(kind=WAIT_TIME, spec={"at": iso_of(at)}))])
        driver, _ = _driver(tmp_path, rec, goal=goal)
        waited = driver._tick()
        assert 300 < waited <= 600

    def test_file_wait_uses_cheap_interval(self, tmp_path):
        rec = Recorder()
        goal = _goal([TaskStep(
            desc="归档", wait=TaskWait(kind=WAIT_FILE_EXISTS, spec={"path": str(tmp_path / "x.pdf")}),
        )])
        driver, _ = _driver(tmp_path, rec, goal=goal)
        assert driver._tick() == pytest.approx(FILE_CHECK_INTERVAL_S, abs=1.0)
        assert rec.spawn_count == 0

    def test_no_goals_waits_long(self, tmp_path):
        rec = Recorder()
        driver, _ = _driver(tmp_path, rec)
        assert driver._tick() == 3600.0
        assert rec.spawn_count == 0

    def test_retry_backoff_not_due_zero_llm_calls(self, tmp_path):
        """失败退避期间同样一次都不调（重启也照算：next_run_at 在磁盘上）。"""
        rec = Recorder(outcomes=[StepOutcome(success=False, error="网络断了")])
        goal = _goal([TaskStep(desc="收集纪要")])
        driver, store = _driver(tmp_path, rec, goal=goal)

        driver._tick()                      # 第一次失败 → 排退避
        assert rec.spawn_count == 1
        stored = store.load(goal.id).steps[0]
        assert stored.state == STEP_PENDING and stored.next_run_at > time.time()

        for _ in range(3):
            driver._tick()
        assert rec.spawn_count == 1, "退避未到点就重试就是在烧轮询"

        # 到点后重试
        stored.next_run_at = time.time() - 1
        store.save(_reactivate(goal, stored))
        driver._tick()
        assert rec.spawn_count == 2

    def test_paused_goal_is_not_advanced(self, tmp_path):
        rec = Recorder()
        goal = _goal([TaskStep(desc="收集纪要")], status=GOAL_PAUSED)
        driver, _ = _driver(tmp_path, rec, goal=goal)
        driver._tick()
        assert rec.spawn_count == 0


def _reactivate(goal: TaskGoal, step: TaskStep) -> TaskGoal:
    """把改过的步骤并回目标对象（驱动器按整目标覆盖写盘）。"""
    for i, s in enumerate(goal.steps):
        if s.id == step.id:
            goal.steps[i] = step
    return goal


class TestStepAdvance:
    """按序推进与状态收尾。"""

    def test_wait_satisfied_then_step_runs(self, tmp_path):
        rec = Recorder()
        goal = _goal([
            TaskStep(desc="收集纪要"),
            TaskStep(desc="发送", wait=TaskWait(kind=WAIT_TIME, spec={"at": iso_of(time.time() - 1)})),
        ])
        driver, store = _driver(tmp_path, rec, goal=goal)

        driver._tick()                       # 第一步跑完
        assert store.load(goal.id).steps[0].state == STEP_DONE
        assert store.load(goal.id).steps[1].wait is not None

        driver._tick()                       # 到点：清掉等待，本轮不执行（0 秒后再醒）
        assert store.load(goal.id).steps[1].wait is None
        driver._tick()                       # 真正跑第二步
        assert store.load(goal.id).steps[1].state == STEP_DONE
        assert rec.spawn_count == 2
        assert store.load(goal.id).status == GOAL_DONE

    def test_goal_completion_is_announced_once(self, tmp_path):
        rec = Recorder()
        goal = _goal([TaskStep(desc="唯一一步")])
        driver, store = _driver(tmp_path, rec, goal=goal)
        driver._tick()
        driver._tick()                        # 已结束：不再推进也不再报

        done_notes = [n for n in rec.notifications if "已" in n[1] and "停在失败" not in n[1]]
        assert len(done_notes) == 1
        assert done_notes[0][0] == NOTIFY_SOURCE
        assert done_notes[0][2] == f"goal:{goal.id}:done"
        assert store.load(goal.id).status == GOAL_DONE

    def test_attempts_exhausted_marks_failed_and_asks(self, tmp_path):
        rec = Recorder(outcomes=[StepOutcome(success=False, error="权限被拒")] * 3)
        goal = _goal([TaskStep(desc="发送")])
        driver, store = _driver(tmp_path, rec, goal=goal, settings=_settings(
            tasks_step_max_attempts=3, tasks_retry_backoff_s=60,
        ))
        for _ in range(3):
            driver._tick()
            g = store.load(goal.id)
            step = g.steps[0]
            if step.next_run_at:      # 把退避推到过去，模拟"到点重试"
                step.next_run_at = time.time() - 1
                store.save(_reactivate(g, step))

        after = store.load(goal.id)
        assert after.steps[0].state == STEP_FAILED
        assert after.status == GOAL_FAILED
        assert rec.spawn_count == 3
        asks = [n for n in rec.notifications if "卡在" in n[1]]
        assert len(asks) == 1 and "权限被拒" in asks[0][1]

    def test_same_block_is_not_asked_twice(self, tmp_path):
        rec = Recorder()
        goal = _goal([TaskStep(
            desc="等回信", state=STEP_BLOCKED, block_reason=BLOCK_TIMEOUT, asked=True,
            wait=TaskWait(kind=WAIT_PERCEPT_EVENT, spec={"source": "mail"}, deadline_at=time.time() - 1),
        )])
        driver, store = _driver(tmp_path, rec, goal=goal)
        driver._tick()
        driver._tick()
        assert [n for n in rec.notifications if "卡在" in n[1]] == []
        assert store.load(goal.id).steps[0].state == STEP_BLOCKED

    def test_timeout_wait_blocks_and_asks(self, tmp_path):
        rec = Recorder()
        goal = _goal([TaskStep(
            desc="等回信",
            wait=TaskWait(
                kind=WAIT_PERCEPT_EVENT, spec={"source": "mail"},
                deadline_at=time.time() - 1,
            ),
        )])
        driver, store = _driver(tmp_path, rec, goal=goal)
        driver._tick()

        after = store.load(goal.id).steps[0]
        assert after.state == STEP_BLOCKED and after.block_reason == BLOCK_TIMEOUT
        assert rec.spawn_count == 0
        asks = [n for n in rec.notifications if "卡在" in n[1]]
        assert len(asks) == 1 and "超时" in asks[0][1]
        assert "等" in asks[0][1] or "搁下" in asks[0][1]

    def test_one_step_at_a_time(self, tmp_path):
        """``_busy`` 闸门：步骤执行期间重入 ``_tick`` 不再派工。"""
        reentered = []

        driver_box = {}

        def spawn(definition, prompt):
            # 执行途中模拟驱动线程被事件唤醒重入一轮
            reentered.append(driver_box["d"]._tick())
            return StepOutcome(success=True, report="办完了")

        store = SpineStore(tmp_path / "goals")
        calls = {"n": 0}

        def counting_spawn(definition, prompt):
            calls["n"] += 1
            return spawn(definition, prompt)

        driver = TaskDriver(
            settings=_settings(), store=store, notifier=lambda *a: None,
            spawn=counting_spawn,
        )
        driver_box["d"] = driver
        store.save(_goal([TaskStep(desc="一"), TaskStep(desc="二")]))
        driver._tick()
        assert calls["n"] == 1
        assert reentered and reentered[0] <= 1.0   # 重入只拿到"稍后再看"的极短节拍


class TestPromptComposition:
    """交给子代理的任务文本。"""

    def test_goal_workdir_wins_over_driver_default(self, tmp_path):
        rec = Recorder()
        goal = _goal([TaskStep(desc="收集纪要")], workdir=r"D:\other\project")
        driver, _ = _driver(tmp_path, rec, goal=goal, workdir=r"D:\driver\dir")
        driver._tick()
        assert r"D:\other\project" in rec.prompts[0]
        assert r"D:\driver\dir" not in rec.prompts[0]

    def test_previous_results_marked_as_hint_only(self, tmp_path):
        rec = Recorder(outcomes=[StepOutcome(success=True, report="找到 3 份纪要"),
                                 StepOutcome(success=True, report="已发送")])
        goal = _goal([TaskStep(desc="收集纪要"), TaskStep(desc="发送")])
        driver, _ = _driver(tmp_path, rec, goal=goal)
        driver._tick()
        driver._tick()
        driver._tick()
        assert "仅作线索" in rec.prompts[-1]
        assert "找到 3 份纪要" in rec.prompts[-1]
        assert "总目标：把周报整理好发给团队" in rec.prompts[-1]


class TestResumeAndApproval:
    """崩溃复位与授权答复。"""

    def test_resume_resets_stale_running(self, tmp_path):
        rec = Recorder()
        goal = _goal([
            TaskStep(desc="一", state=STEP_DONE),
            TaskStep(desc="二", state=STEP_RUNNING),
        ])
        driver, store = _driver(tmp_path, rec, goal=goal)
        assert driver.resume() == 1
        step = store.load(goal.id).steps[1]
        assert step.state == STEP_PENDING
        assert step.attempts == 1
        assert "未跑完" in step.last_error

    def test_resume_counts_toward_failure_cap(self, tmp_path):
        """复位算一次尝试：已到上限的步骤重启后不会再白跑一轮。"""
        rec = Recorder(outcomes=[StepOutcome(success=False, error="还是不行")])
        goal = _goal([TaskStep(desc="二", state=STEP_RUNNING, attempts=2)])
        driver, store = _driver(tmp_path, rec, goal=goal, settings=_settings(
            tasks_step_max_attempts=3, tasks_retry_backoff_s=1,
        ))
        assert driver.resume() == 1
        after_resume = store.load(goal.id).steps[0]
        assert after_resume.attempts == 3 and after_resume.state == STEP_PENDING

        driver._tick()                       # 再试一次即越线 → 定局成 failed
        step = store.load(goal.id).steps[0]
        assert step.state == STEP_FAILED
        assert step.block_reason == BLOCK_ERROR
        assert rec.spawn_count == 1
        assert store.load(goal.id).status == GOAL_FAILED

    def test_request_approval_approve_resets_attempts(self, tmp_path):
        rec = Recorder()
        goal = _goal([TaskStep(
            desc="发送", state=STEP_BLOCKED, block_reason=BLOCK_APPROVAL,
            asked=True, attempts=2,
        )])
        driver, store = _driver(tmp_path, rec, goal=goal)
        assert driver.request_approval(goal.id, goal.steps[0].id, approve=True) is True
        step = store.load(goal.id).steps[0]
        assert step.state == STEP_PENDING and step.attempts == 0 and step.asked is False

    def test_request_approval_reject_pauses_goal(self, tmp_path):
        rec = Recorder()
        goal = _goal([TaskStep(desc="发送", state=STEP_BLOCKED, block_reason=BLOCK_APPROVAL)])
        driver, store = _driver(tmp_path, rec, goal=goal)
        assert driver.request_approval(goal.id, goal.steps[0].id, approve=False) is True
        after = store.load(goal.id)
        assert after.status == GOAL_PAUSED

    def test_request_approval_unknown_goal(self, tmp_path):
        rec = Recorder()
        driver, _ = _driver(tmp_path, rec)
        assert driver.request_approval("nope", "s1", approve=True) is False


class TestPerceptWiring:
    """percept_event 靠总线订阅唤醒，不靠轮询查总线。"""

    def test_matching_event_marks_hit_and_wakes(self, tmp_path):
        rec = Recorder()
        goal = _goal([TaskStep(
            desc="归档附件",
            wait=TaskWait(kind=WAIT_PERCEPT_EVENT, spec={"source": "mail", "kind": "new_mail"}),
        )])
        driver, store = _driver(tmp_path, rec, goal=goal)

        event = SimpleNamespace(source="mail", kind="new_mail", ts=123.0,
                                summary=lambda: "张三来信：附件已附")
        driver.on_percept_event(event)

        step = store.load(goal.id).steps[0]
        assert step.wait.spec["hit_at"] == 123.0
        assert driver._wake.is_set() is True
        assert rec.spawn_count == 0                     # 命中只登记，执行仍由 _tick 决定

    def test_non_matching_event_is_ignored(self, tmp_path):
        rec = Recorder()
        goal = _goal([TaskStep(
            desc="归档附件",
            wait=TaskWait(kind=WAIT_PERCEPT_EVENT, spec={"source": "mail", "contains": "张三"}),
        )])
        driver, store = _driver(tmp_path, rec, goal=goal)
        driver.on_percept_event(SimpleNamespace(
            source="mail", kind="new_mail", ts=1.0, summary=lambda: "李四来信",
        ))
        assert "hit_at" not in store.load(goal.id).steps[0].wait.spec
        assert driver._wake.is_set() is False

    def test_contains_matches_kind_and_summary(self, tmp_path):
        rec = Recorder()
        wait = TaskWait(kind=WAIT_PERCEPT_EVENT, spec={"source": "vcs", "contains": "未提交"})
        event = SimpleNamespace(source="vcs", kind="dirty", ts=1.0,
                               summary=lambda: "有 3 个文件未提交")
        assert TaskDriver._event_matches(event, wait, "vcs", "dirty") is True
        assert TaskDriver._event_matches(
            SimpleNamespace(source="vcs", kind="dirty", ts=1.0, summary=lambda: "clean"),
            wait, "vcs", "dirty",
        ) is False

    def test_bus_absent_still_honours_deadline(self, tmp_path):
        """感知层没开（取不到总线）时不订阅，但等待仍按 deadline 收尾。"""
        rec = Recorder()
        goal = _goal([TaskStep(
            desc="等回信",
            wait=TaskWait(kind=WAIT_PERCEPT_EVENT, spec={"source": "mail"},
                          deadline_at=time.time() - 1),
        )])
        driver, store = _driver(tmp_path, rec, goal=goal, bus_getter=lambda: None)
        driver._subscribe_bus()
        assert driver._unsub_bus is None
        driver._tick()
        assert store.load(goal.id).steps[0].block_reason == BLOCK_TIMEOUT


class TestNotifyGate:
    """播报出口：只经闸门，源名固定 task。"""

    def test_source_name_is_task(self, tmp_path):
        rec = Recorder()
        driver, _ = _driver(tmp_path, rec)
        driver._notify("一条测试播报", dedup_key="k1")
        assert rec.notifications == [(NOTIFY_SOURCE, "一条测试播报", "k1")]

    def test_notifier_failure_does_not_break_advance(self, tmp_path):
        def boom(*args):
            raise RuntimeError("播报通道挂了")

        goal = _goal([TaskStep(desc="唯一一步")])
        store = SpineStore(tmp_path / "goals")
        store.save(goal)
        driver = TaskDriver(
            settings=_settings(), store=store, notifier=boom,
            spawn=lambda d, p: StepOutcome(success=True, report="办完了"),
        )
        driver._tick()                       # 完成播报会抛，但必须被吃掉
        assert store.load(goal.id).status == GOAL_DONE

    def test_gate_can_suppress_the_message(self, monkeypatch):
        """闸门判"不播"时真的不投递（静默时段/日额度都在那道闸门里）。"""
        seen: list[tuple] = []

        def fake_gate(source, text, *, dedup_key="", urgency=0.6, budget=None):
            seen.append((source, text, dedup_key, urgency, budget))
            return None                      # 闸门拦下

        import agent.core.daemon.proactive as proactive_mod
        import agent.core.policy as policy_mod

        monkeypatch.setattr(proactive_mod, "apply_notify_gate", fake_gate)
        monkeypatch.setattr(policy_mod, "get_budget", lambda: "BUDGET")
        emitted: list[str] = []
        notify = make_gate_notifier(emitted.append)
        notify("task", "目标卡住了", "ask:g:s")

        assert emitted == []
        assert seen and seen[0][0] == "task"
        assert seen[0][3] == GATE_URGENCY and seen[0][4] == "BUDGET"

    def test_gate_passes_text_through(self, monkeypatch):
        def fake_gate(source, text, *, dedup_key="", urgency=0.6, budget=None):
            return f"[{source}] {text}"

        import agent.core.daemon.proactive as proactive_mod
        import agent.core.policy as policy_mod

        monkeypatch.setattr(proactive_mod, "apply_notify_gate", fake_gate)
        monkeypatch.setattr(policy_mod, "get_budget", lambda: None)
        emitted: list[str] = []
        make_gate_notifier(emitted.append)("task", "已办完", "k")
        assert emitted == ["[task] 已办完"]

    def test_gate_exception_degrades_to_direct_delivery(self, monkeypatch):
        """闸门自身挂掉不能把播报一起吞掉（否则目标永远不吱声）。"""
        def boom(*args, **kwargs):
            raise RuntimeError("闸门挂了")

        import agent.core.daemon.proactive as proactive_mod

        monkeypatch.setattr(proactive_mod, "apply_notify_gate", boom)
        emitted: list[str] = []
        make_gate_notifier(emitted.append)("task", "已办完", "k")
        assert emitted == ["已办完"]


class TestBudgetWeightRegistered:
    """验收防线：``task`` 源必须在预算权重表里显式登记。"""

    def test_task_weight_exists_and_clears_threshold(self):
        from agent.core.policy.budget import _DEFAULT_SOURCE_WEIGHTS, BudgetConfig

        assert "task" in _DEFAULT_SOURCE_WEIGHTS, "漏登记就落 default 被误裁"
        weight = _DEFAULT_SOURCE_WEIGHTS["task"]
        score = weight * GATE_URGENCY
        assert score >= BudgetConfig().min_salience, (
            f"task 权重 {weight} × 紧迫度 {GATE_URGENCY} = {score:.3f}，低于门槛"
            f" {BudgetConfig().min_salience}：脊柱的询问会被永久抑制"
        )

    def test_notify_source_constant_matches_registry_key(self):
        from agent.core.policy.budget import _DEFAULT_SOURCE_WEIGHTS

        assert NOTIFY_SOURCE in _DEFAULT_SOURCE_WEIGHTS


class TestThreadedLoop:
    """起停线程本身要干净（不靠 sleep 轮询）。"""

    def test_start_then_stop(self, tmp_path):
        rec = Recorder()
        goal = _goal([TaskStep(
            desc="发送", wait=TaskWait(kind=WAIT_TIME, spec={"at": iso_of(time.time() + 3600)}),
        )])
        driver, _ = _driver(tmp_path, rec, goal=goal)
        assert driver.start() is True
        try:
            assert driver.running is True
            driver.wake("测试")
            time.sleep(0.05)
        finally:
            driver.stop()
        assert driver.running is False
        assert rec.spawn_count == 0          # 等待期间一次都没调


class TestSideEffectBinding:
    """副作用归属接线：task_id 传给执行体、账本 id 回填 TaskStep.journal_ids。

    实机走查暴露的链路断点（docs/fixlogs/w3-delay-window-bypass-fix.md 3）：编排器
    只从 ctx.extra["task_id"] 取归属，驱动器全程没写入，journal_ids 也没回填，
    于是 `/undo --task <goal>` 端到端列不到任何条目。旧用例的 spawn 都是两参函数，
    刚好把这条断链绕过去了本类把"谁写、谁收"补成显式契约。
    @author aceFelix
    """

    def test_binding_spawn_receives_task_identity(self, tmp_path):
        seen = {}

        def spawn(definition, prompt, *, task_id, session_name, effect_ids):
            seen["task_id"] = task_id
            seen["session_name"] = session_name
            seen["effect_ids"] = effect_ids
            effect_ids.append("rec-1")
            effect_ids.append("rec-2")
            return StepOutcome(success=True, report="办完了")

        goal = _goal([TaskStep(desc="发周报")])
        store = SpineStore(tmp_path / "goals")
        store.save(goal)
        driver = TaskDriver(
            settings=_settings(), store=store, notifier=lambda *a: None,
            spawn=spawn, session_name="sess-1",
        )
        driver._tick()

        assert seen["task_id"] == goal.id
        assert seen["session_name"] == "sess-1"
        after = store.load(goal.id)
        # 必须落盘：/undo --task 是另一个进程（或下一次会话）读的
        assert after.steps[0].journal_ids == ["rec-1", "rec-2"]

    def test_retry_keeps_previous_journal_ids(self, tmp_path):
        """重试同一步：上一次的记录不能被新一次覆盖（两处都可能要撤）。"""
        round_no = {"n": 0}

        def spawn(definition, prompt, *, task_id, session_name, effect_ids):
            round_no["n"] += 1
            effect_ids.append(f"rec-{round_no['n']}")
            if round_no["n"] == 1:
                return StepOutcome(success=False, error="先失败一次")
            return StepOutcome(success=True, report="办完了")

        goal = _goal([TaskStep(desc="发周报")])
        store = SpineStore(tmp_path / "goals")
        store.save(goal)
        driver = TaskDriver(
            settings=_settings(tasks_step_max_attempts=3, tasks_retry_backoff_s=1),
            store=store, notifier=lambda *a: None, spawn=spawn,
        )
        driver._tick()
        step = store.load(goal.id).steps[0]
        step.next_run_at = time.time() - 1
        store.save(_reactivate(store.load(goal.id), step))
        driver._tick()

        assert store.load(goal.id).steps[0].journal_ids == ["rec-1", "rec-2"]

    def test_legacy_two_arg_spawn_still_runs(self, tmp_path):
        """旧式注入通路（只收 definition/prompt）不该被打挂，只是记不上归属。"""
        rec = Recorder()
        goal = _goal([TaskStep(desc="发周报")])
        driver, store = _driver(tmp_path, rec, goal=goal)
        driver._tick()

        assert rec.spawn_count == 1
        assert store.load(goal.id).steps[0].state == STEP_DONE
        assert store.load(goal.id).steps[0].journal_ids == []

    def test_default_spawn_passes_extra_to_subagent(self, monkeypatch):
        """真通路：task_id/session_name/effect_ids 必须进子 ctx.extra，且列表是同一个对象。

        断言"同一个列表对象"是关键：浅拷贝就会把回扣链断在 run_subagent 里，
        驱动器永远读不到子代理追加的 id。
        """
        from agent.collaboration import subagent as sa

        captured = {}

        async def fake_run_subagent(definition, prompt, **kw):
            captured.update(kw)
            return SimpleNamespace(
                success=True, report="办完了", error="", iterations=1, tool_calls=0,
            )

        monkeypatch.setattr(sa, "run_subagent", fake_run_subagent)
        ids: list[str] = []
        outcome = default_spawn(
            object(), "做这一步", provider=object(), workdir="W",
            task_id="goal-y", session_name="sess-y", effect_ids=ids,
        )

        assert outcome.success is True
        extra = captured["extra"]
        assert extra["task_id"] == "goal-y" and extra["session_name"] == "sess-y"
        assert extra["effect_ids"] is ids
        assert str(captured["permission_mode"]) != "yolo", "后台步骤不能默认放行"
