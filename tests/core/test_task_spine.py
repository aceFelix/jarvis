"""W3.1 任务脊柱 spine.py 单元测试：台账模型、落盘、条件等待判定。

覆盖：
- TaskGoal / TaskStep / TaskWait 的 JSON 往返与非法值降级（坏状态、坏等待类型）
- 三种等待各命中一次：time 到点、file_exists（exists/gone 两向）、
  percept_event（由驱动器写回 hit_at，脊柱自己不查总线）
- deadline 超时判定（等待必须有收尾，绝不静默挂起）
- 崩溃安全落盘：临时文件 + os.replace，读坏文件不崩、坏行跳过
- 跨进程读-改-写（update 在锁内），目标 ID 的路径穿越清洗
- 进度/状态回算（不覆盖用户显式 paused/cancelled）与台账摘要文案

判定一律用注入的 ``now``，不依赖挂钟；文件用 tmp_path，绝不动用户 ~/.jarvis。

@author aceFelix
"""

from __future__ import annotations

import json
import time
from datetime import datetime

import pytest

from agent.core.tasks.spine import (
    BLOCK_APPROVAL,
    BLOCK_TIMEOUT,
    BLOCK_WAIT,
    GOAL_ACTIVE,
    GOAL_CANCELLED,
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
    parse_iso,
)


def _goal(steps: list[TaskStep] | None = None, **over) -> TaskGoal:
    """造一个目标（默认两步，便于进度断言）。"""
    data = {
        "goal": "把周报整理好发给团队",
        "steps": steps if steps is not None else [
            TaskStep(desc="收集纪要"),
            TaskStep(desc="发送"),
        ],
    }
    data.update(over)
    return TaskGoal(**data)


class TestParseHelpers:
    """时间解析必须"尽力解释、失败即无"。"""

    def test_parse_iso_accepts_multiple_shapes(self):
        assert parse_iso(1_700_000_000) == 1_700_000_000.0
        assert parse_iso("1700000000") == 1_700_000_000.0
        assert parse_iso("2026-10-21") == datetime(2026, 10, 21).timestamp()
        assert parse_iso("2026-10-21T09:00") > 0
        assert parse_iso("2026-10-21 09:00") > 0
        assert parse_iso("2026-10-21T09:00:00") > 0

    def test_parse_iso_never_raises(self):
        """脏值一律 0.0：一个非法时间不该让整个目标读不出来。"""
        for bad in ("", None, "明天下午", "not-a-date", [], {}):
            assert parse_iso(bad) == 0.0

    def test_iso_of_roundtrip_and_zero(self):
        assert iso_of(0) == ""
        ts = 1_700_000_000.0
        assert parse_iso(iso_of(ts)) == pytest.approx(ts, abs=1.0)


class TestWaitJudgement:
    """三种等待：判定不花钱、必须能收尾。"""

    def test_time_wait_hits_at_deadline(self):
        at = time.time() + 3600
        wait = TaskWait(kind=WAIT_TIME, spec={"at": iso_of(at)})
        ok, note = wait.check(time.time())
        assert ok is False and "还需等" in note
        assert wait.check(at + 1)[0] is True

    def test_time_wait_with_unparsable_at_is_treated_as_expired(self):
        """解析不出时间就当已到期：不能因此永久卡住一个步骤。"""
        wait = TaskWait(kind=WAIT_TIME, spec={"at": "明天"})
        assert wait.check(0)[0] is True

    def test_file_exists_both_directions(self, tmp_path):
        target = tmp_path / "report.pdf"
        wait = TaskWait(kind=WAIT_FILE_EXISTS, spec={"path": str(target)})
        assert wait.check(None)[0] is False
        target.write_text("x", encoding="utf-8")
        ok, note = wait.check(None)
        assert ok is True and "已出现" in note

        gone = TaskWait(kind=WAIT_FILE_EXISTS, spec={"path": str(target), "mode": "gone"})
        assert gone.check(None)[0] is False
        target.unlink()
        assert gone.check(None)[0] is True

    def test_file_exists_without_path_is_no_condition(self):
        assert TaskWait(kind=WAIT_FILE_EXISTS, spec={}).check(None)[0] is True

    def test_percept_event_only_hit_by_mark(self):
        """脊柱自己不查总线（查就退化成轮询）：命中只能由驱动器登记。"""
        wait = TaskWait(kind=WAIT_PERCEPT_EVENT, spec={"source": "mail", "kind": "new_mail"})
        ok, note = wait.check(100.0)
        assert ok is False and "还在等" in note
        wait.mark_event_hit(123.0, summary="张三来信")
        assert wait.check(130.0)[0] is True
        assert wait.spec["hit_summary"] == "张三来信"

    def test_unknown_kind_falls_back_to_no_wait(self):
        assert TaskWait(kind="telepathy").check(0)[0] is True

    def test_expired_only_when_deadline_set(self):
        assert TaskWait(kind=WAIT_TIME, spec={"at": "1"}).expired(999) is False
        wait = TaskWait(kind=WAIT_PERCEPT_EVENT, spec={}, deadline_at=100.0)
        assert wait.expired(99.0) is False
        assert wait.expired(100.0) is True


class TestSerialization:
    """JSON 往返与降级。"""

    def test_goal_roundtrip_keeps_every_field(self, tmp_path):
        g = _goal()
        g.steps[0].wait = TaskWait(kind=WAIT_TIME, spec={"at": "2026-10-21T09:00"}, deadline_at=5.0)
        g.steps[0].journal_ids = ["abc123"]
        g.parent_task_id = "7"
        g.budget = {"tokens": 1200, "usd": 0.03}
        back = TaskGoal.from_dict(json.loads(json.dumps(g.to_dict())))
        assert back.id == g.id and back.goal == g.goal
        assert back.steps[0].wait.spec["at"] == "2026-10-21T09:00"
        assert back.steps[0].journal_ids == ["abc123"]
        assert back.parent_task_id == "7" and back.budget["tokens"] == 1200

    def test_illegal_status_degrades_not_crashes(self):
        g = TaskGoal.from_dict({"goal": "x", "status": "wat", "steps": [
            {"desc": "a", "state": "sunny"},
            {"desc": "b", "state": STEP_DONE, "block_reason": "unknown-reason"},
        ]})
        assert g.status == GOAL_ACTIVE
        assert g.steps[0].state == STEP_PENDING
        assert g.steps[1].block_reason == ""

    def test_unknown_wait_kind_degrades_to_time(self):
        w = TaskWait.from_dict({"kind": "smell", "spec": "不是字典"})
        assert w.kind == WAIT_TIME and w.spec == {}

    def test_non_dict_steps_are_dropped(self):
        g = TaskGoal.from_dict({"goal": "x", "steps": ["裸字符串", {"desc": "ok"}]})
        assert len(g.steps) == 1 and g.steps[0].desc == "ok"

    def test_long_result_and_error_truncated(self):
        s = TaskStep.from_dict({"desc": "a", "result": "R" * 900, "last_error": "E" * 900})
        assert len(s.result) <= 400 and len(s.last_error) <= 200


class TestProgressAndStatus:
    """推进视图：按序只看第一个未结束步骤。"""

    def test_current_step_is_first_open(self):
        g = _goal([
            TaskStep(desc="一", state=STEP_DONE),
            TaskStep(desc="二", state=STEP_BLOCKED, block_reason=BLOCK_WAIT),
            TaskStep(desc="三", state=STEP_PENDING),
        ])
        assert g.current_step().desc == "二"
        assert g.progress() == (1, 3)

    def test_failed_step_is_not_open(self):
        """failed 步骤不再被驱动器关心（等人来管，不自动重试）。"""
        g = _goal([TaskStep(desc="一", state=STEP_FAILED)])
        assert g.current_step() is None

    def test_refresh_status(self):
        g = _goal([TaskStep(desc="一", state=STEP_DONE), TaskStep(desc="二", state=STEP_DONE)])
        assert g.refresh_status() == GOAL_DONE
        g2 = _goal([TaskStep(desc="一", state=STEP_FAILED)])
        assert g2.refresh_status() == GOAL_FAILED
        g3 = _goal([TaskStep(desc="一", state=STEP_DONE), TaskStep(desc="二", state=STEP_PENDING)])
        assert g3.refresh_status() == GOAL_ACTIVE
        assert g3.terminal is False

    def test_refresh_status_respects_user_pause(self):
        g = _goal([TaskStep(desc="一", state=STEP_DONE), TaskStep(desc="二", state=STEP_DONE)])
        g.status = GOAL_PAUSED
        assert g.refresh_status() == GOAL_PAUSED
        g.status = GOAL_CANCELLED
        assert g.refresh_status() == GOAL_CANCELLED

    def test_summary_line_shows_block_and_wait(self):
        g = _goal([TaskStep(
            desc="等张三确认数据", state=STEP_BLOCKED, block_reason=BLOCK_TIMEOUT,
            wait=TaskWait(kind=WAIT_PERCEPT_EVENT, spec={"source": "mail"}),
        )])
        line = g.summary_line()
        # 进度的括号是全角（Rich 会把半角方括号当样式标签），这里按实际文案断言
        assert "阻塞：timeout" in line and "（0/1）" in line

        g2 = _goal([TaskStep(desc="发送", wait=TaskWait(kind=WAIT_TIME, spec={"at": "x"}))])
        assert "等条件：time" in g2.summary_line()

        g3 = _goal([TaskStep(desc="收集纪要")])
        assert "下一步：收集纪要" in g3.summary_line()


class TestSpineStore:
    """落盘台账：崩溃安全 + 坏文件不崩。"""

    @pytest.fixture()
    def store(self, tmp_path) -> SpineStore:
        return SpineStore(tmp_path / "goals")

    def test_save_and_load(self, store):
        g = _goal()
        assert store.save(g) is True
        got = store.load(g.id)
        assert got is not None and got.goal == g.goal and len(got.steps) == 2

    def test_save_leaves_no_temp_file(self, store):
        store.save(_goal())
        assert list(store.base_dir.glob("*.tmp")) == []
        assert len(list(store.base_dir.glob("*.json"))) == 1

    def test_load_broken_json_returns_none(self, store):
        store.base_dir.mkdir(parents=True, exist_ok=True)
        (store.base_dir / "junk.json").write_text("{半截", encoding="utf-8")
        assert store.load("junk") is None

    def test_list_goals_skips_broken_files(self, store):
        store.save(_goal())
        store.base_dir.mkdir(parents=True, exist_ok=True)
        (store.base_dir / "bad.json").write_text("null", encoding="utf-8")
        (store.base_dir / "worse.json").write_text("[1,2]", encoding="utf-8")
        assert len(store.list_goals(limit=10)) == 1

    def test_open_goals_excludes_terminal(self, store):
        live = _goal()
        paused = _goal(status=GOAL_PAUSED)
        done = _goal(status=GOAL_DONE)
        for g in (live, paused, done):
            store.save(g)
        ids = {g.id for g in store.open_goals()}
        assert ids == {live.id, paused.id}

    def test_update_runs_mutation_and_persists(self, store):
        g = _goal()
        store.save(g)
        out = store.update(g.id, lambda x: setattr(x.steps[0], "state", STEP_DONE))
        assert out is not None
        assert store.load(g.id).steps[0].state == STEP_DONE

    def test_update_missing_goal_returns_none(self, store):
        assert store.update("nope", lambda g: None) is None

    def test_goal_id_is_sanitized(self, store):
        """ID 清洗：路径穿越字符不能变成目录分隔。"""
        path = store._path_for("../../etc/passwd")
        assert path.parent == store.base_dir
        assert "/" not in path.name and "\\" not in path.name[:-5]
        assert store._path_for("").name == "invalid.json"

    def test_delete(self, store):
        g = _goal()
        store.save(g)
        assert store.delete(g.id) is True
        assert store.load(g.id) is None
        assert store.delete(g.id) is False

    def test_add_goal_accepts_strings_and_steps(self, store):
        g = store.add_goal("整理书房", ["分类", "上架"], workdir="/tmp/x", parent_task_id="3")
        assert g is not None and len(g.steps) == 2
        assert g.steps[0].state == STEP_PENDING
        assert store.load(g.id).steps[1].desc == "上架"

    def test_add_goal_empty_text_still_persists(self, store):
        """空目标照样落盘（读侧会显示成空标题，但不至于丢一次写入）。"""
        g = store.add_goal("", [])
        assert g is not None and g.steps == []

    def test_resume_after_restart_reads_same_ledger(self, store, tmp_path):
        """重启续跑：换一个 SpineStore 实例指向同一目录仍能读到未完成的目标。"""
        g = _goal([
            TaskStep(desc="一", state=STEP_DONE),
            TaskStep(desc="二", state=STEP_RUNNING),
        ])
        g.blocked_since = 0
        store.save(g)

        reopened = SpineStore(tmp_path / "goals")
        got = reopened.load(g.id)
        assert got is not None and got.status == GOAL_ACTIVE
        assert got.steps[1].state == STEP_RUNNING   # 崩溃时停在 running，由驱动器 resume 复位
        assert got.current_step().desc == "二"


class TestStepHelpers:
    """展示用图标与字段默认。"""

    def test_icons_are_distinct_and_bracket_free(self):
        marks = {TaskStep(desc="x", state=s).icon() for s in
                 (STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_BLOCKED, STEP_FAILED)}
        assert len(marks) == 5
        assert all("[" not in m for m in marks)

    def test_next_run_at_roundtrip(self):
        s = TaskStep(desc="a", next_run_at=1234.5)
        assert TaskStep.from_dict(s.to_dict()).next_run_at == 1234.5

    def test_block_reason_constants_are_known(self):
        assert {BLOCK_WAIT, BLOCK_TIMEOUT, BLOCK_APPROVAL} <= {
            "waiting", "timeout", "error", "approval",
        }
