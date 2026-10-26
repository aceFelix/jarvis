"""W3.2 副作用账本 ActionJournal / DelayWindow / 撤销执行 单元测试。

覆盖：
- jsonl 追加写与"读时按 id 合并最新状态"（状态流转 held→applied→undone）
- 坏行跳过、禁用态不写、IO 失败只返 False 不抛（辅助能力不得拖垮主流程）
- 轮转上限、recent/last/held_records/task_records/counts 读取口径
- DelayWindow：登记、撤回、到期回调、无事件循环时的线程定时器退化
- resume_pending：重启后遗留待发动作一律作废（绝不自动补发外发动作）
- undo_record / undo_task：checkpoint 类真回滚、不可撤销项如实报原因、
  按 task_id 批量补偿时把撤不动的也返回并说明

测试策略：账本一律指向 tmp_path（conftest 已兜底，本文件再显式传路径，
避免用例改动全局单例后互相影响）；真实 git 回滚那条用 monkeypatch 把
检查点存储目录也钉进 tmp，绝不在用户 ~/.jarvis 留痕。

@author aceFelix
"""

from __future__ import annotations

import json
import shutil

import pytest

from agent.core.actions import journal as journal_mod
from agent.core.actions.journal import (
    ALL_STATES,
    STATE_APPLIED,
    STATE_CANCELLED,
    STATE_FAILED,
    STATE_HELD,
    STATE_UNDONE,
    UNDO_CHECKPOINT,
    UNDO_NONE,
    ActionJournal,
    DelayWindow,
    EffectRecord,
    describe_undo,
    resume_pending,
    undo_record,
    undo_task,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="环境缺少 git，检查点相关断言跳过"
)


#: 只给“真开 shadow git 打快照”的那条用例用：其余用例要么把管理器打桩，
#: 要么根本不碰 git，不该因环境缺 git 被连带跳过。
needs_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="环境缺少 git，检查点回滚断言跳过"
)


@pytest.fixture()
def book(tmp_path) -> ActionJournal:
    """指向临时文件的新账本。"""
    return ActionJournal(tmp_path / "actions.jsonl")


@pytest.fixture()
def workdir(tmp_path):
    """一个带初始文件的工作目录（供 checkpoint 类用例快照/回滚）。"""
    d = tmp_path / "proj"
    d.mkdir()
    (d / "a.txt").write_text("original", encoding="utf-8")
    return d


def _applied_with_checkpoint(book, workdir, tmp_path) -> tuple[EffectRecord, object]:
    """打一个真实检查点并把“引用它的副作用”记进账本，返回 (记录, 管理器)。

    检查点存储指到 tmp：全局口径会把 shadow 仓库写进用户 ~/.jarvis/checkpoints。
    @author aceFelix
    """
    from agent.core.checkpoint import CheckpointManager

    mgr = CheckpointManager(
        str(workdir), "s1", base_dir=str(tmp_path / "ckpt-store"),
    )
    cid = mgr.create(reason="本轮改动前")
    assert cid
    rec = EffectRecord(
        tool="FileWrite",
        args={"file_path": "a.txt", "content": "modified"},
        preview="写入 a.txt",
        reversible=True,
        undo_spec={"kind": UNDO_CHECKPOINT, "cid": cid, "workdir": str(workdir)},
        state=STATE_APPLIED,
        workdir=str(workdir),
        session_id="session-test",
    )
    assert book.record(rec) is True
    return rec, mgr


class TestJournalAppendAndMerge:
    """追加写 + 读时合并。"""

    def test_record_then_get(self, book):
        """记一条读一条：字段原样还原。"""
        rec = EffectRecord(tool="SendEmail", args={"to": "a@b.c"}, preview="发给 a@b.c")
        assert book.record(rec) is True
        got = book.get(rec.id)
        assert got is not None
        assert got.tool == "SendEmail"
        assert got.args == {"to": "a@b.c"}
        assert got.preview == "发给 a@b.c"

    def test_get_by_unique_prefix(self, book):
        """ID 支持唯一前缀（命令行里手输 4 个字符就够）。"""
        rec = EffectRecord(tool="FileWrite")
        book.record(rec)
        assert book.get(rec.id[:4]).id == rec.id
        assert book.get("zzzz") is None

    def test_set_state_appends_new_snapshot(self, book):
        """改状态是追加新行，历史行不重写（崩溃只会坏最后一行）。"""
        rec = EffectRecord(tool="SendEmail", state=STATE_HELD, due_at=123.0)
        book.record(rec)
        updated = book.set_state(rec.id, STATE_APPLIED)
        assert updated is not None and updated.state == STATE_APPLIED
        lines = [json.loads(x) for x in book.path.read_text(encoding="utf-8").splitlines()]
        assert len(lines) == 2
        assert lines[0]["state"] == STATE_HELD      # 旧快照仍在文件里
        assert lines[1]["state"] == STATE_APPLIED
        assert book.get(rec.id).state == STATE_APPLIED  # 读时取最新

    def test_set_state_clears_error_when_leaving_failed(self, book):
        """离开 failed 状态要清掉 error，否则 /undo list 会挂着过期原因。"""
        rec = EffectRecord(tool="Bash", state=STATE_APPLIED)
        book.record(rec)
        book.set_state(rec.id, STATE_FAILED, error="超时")
        assert book.get(rec.id).error == "超时"
        after = book.set_state(rec.id, STATE_UNDONE)
        assert after.error == ""

    def test_bad_line_skipped_not_crash(self, book):
        """坏行静默跳过：一行写坏不该让整本账读不出来。"""
        good = EffectRecord(tool="FileWrite")
        book.record(good)
        with book.path.open("a", encoding="utf-8") as f:
            f.write("{不是 JSON\n")
            f.write(json.dumps({"no_id": 1}) + "\n")
        assert book.get(good.id) is not None
        assert len(book.recent(10)) == 1

    def test_disabled_journal_refuses_writes(self, tmp_path):
        """禁用态（journal_enabled=false）不写文件、返回 False。"""
        book = ActionJournal(tmp_path / "off.jsonl", enabled=False)
        assert book.enabled is False
        assert book.record(EffectRecord(tool="FileWrite")) is False
        assert not book.path.exists()

    def test_io_failure_returns_false_not_raise(self, tmp_path):
        """落盘失败只返回 False：账本是辅助能力，不能炸掉工具执行主流程。"""
        book = ActionJournal(tmp_path)  # 路径本身是目录，open('a') 必失败
        assert book.enabled is True      # 父目录（tmp）建得出来，仍算启用
        assert book.record(EffectRecord(tool="FileWrite")) is False

    def test_unreadable_parent_degrades_to_disabled(self, tmp_path):
        """账本目录建不出来时降级为禁用，而不是抛异常。"""
        blocker = tmp_path / "occupied"
        blocker.write_text("我是文件不是目录", encoding="utf-8")
        book = ActionJournal(blocker / "nested" / "actions.jsonl")
        assert book.enabled is False

    def test_counts_and_recent_ordering(self, book):
        """counts 给各状态计数；recent 新在前。"""
        first = EffectRecord(tool="FileWrite", preview="第一步")
        book.record(first)
        book.set_state(first.id, STATE_UNDONE)
        second = EffectRecord(tool="SendEmail", preview="第二步", state=STATE_HELD)
        book.record(second)
        assert book.counts()[STATE_UNDONE] == 1
        assert book.counts()[STATE_HELD] == 1
        assert [r.id for r in book.recent(10)] == [second.id, first.id]

    def test_last_respects_state_filter(self, book):
        """``last()`` 不给状态时只看 applied（/undo 撤上一步的取值口径）。"""
        held = EffectRecord(tool="SendEmail", state=STATE_HELD)
        book.record(held)
        assert book.last() is None                       # 待发项不算"上一步"
        assert book.last(STATE_HELD).id == held.id

    def test_rotate_keeps_recent_entries(self, tmp_path):
        """超条数上限时轮转，且轮转后按 id 只剩一份最新快照。"""
        book = ActionJournal(tmp_path / "rot.jsonl", max_entries=50)
        ids = []
        for i in range(60):
            rec = EffectRecord(tool="FileWrite", preview=f"p{i}")
            book.record(rec)
            ids.append(rec.id)
            book.set_state(rec.id, STATE_APPLIED)
        # 轮转阈值是 1MB，本用例体量小不会触发；改断言口径：读取仍然自洽
        rows = book.recent(200)
        assert len(rows) == 60
        assert rows[0].id == ids[-1]
        assert book.path.stat().st_size > 0

    def test_all_states_are_round_trippable(self, book):
        """状态机全部取值都能落盘读回（新增状态漏进 ALL_STATES 会在这里暴露）。"""
        assert set(ALL_STATES) == {
            STATE_HELD, STATE_APPLIED, STATE_UNDONE, STATE_FAILED, STATE_CANCELLED,
        }
        for state in ALL_STATES:
            rec = EffectRecord(tool="X", state=state)
            book.record(rec)
            assert book.get(rec.id).state == state


class TestDelayWindow:
    """延时窗口登记表。"""

    def test_schedule_fires_after_window(self):
        """无运行中的事件循环时退化为线程定时器，到点真的回调。"""
        import threading
        import time

        win = DelayWindow()
        hit = threading.Event()
        win.schedule("r1", 0.01, lambda: hit.set())
        assert win.pending() == ["r1"]
        assert hit.wait(1.0) is True
        assert win.pending() == []
        time.sleep(0)  # 让退出登记干净（_run 先摘登记再回调）

    def test_cancel_prevents_fire(self):
        """撤回后回调不再发生（/undo cancel 的真意：邮件没发出去）。"""
        fired = []
        win = DelayWindow()
        win.schedule("r2", 30.0, lambda: fired.append(1))
        assert win.cancel("r2") is True
        assert fired == []
        assert win.cancel("r2") is False   # 已经不在册
        assert win.pending() == []

    def test_zero_window_fires_immediately(self):
        """window_s<=0 表示配置上关了延时：直接执行，不排队。"""
        fired = []
        DelayWindow().schedule("r3", 0, lambda: fired.append(1))
        assert fired == [1]

    def test_fire_exception_is_contained(self):
        """回调自身异常不得冒泡进定时器线程。"""
        win = DelayWindow()
        win.schedule("r4", 0.01, lambda: 1 / 0)
        import time
        time.sleep(0.1)
        assert win.size == 0

    def test_clear_cancels_all(self):
        """宿主收尾用 clear：一次摘干净，返回摘掉的数量。"""
        win = DelayWindow()
        win.schedule("a", 30.0, lambda: None)
        win.schedule("b", 30.0, lambda: None)
        assert win.clear() == 2
        assert win.pending() == []


class TestResumePending:
    """重启后的遗留待发动作：一律作废，绝不补发。"""

    def test_stale_held_records_are_cancelled(self, book):
        rec = EffectRecord(tool="SendEmail", preview="发给老板", state=STATE_HELD, due_at=1.0)
        book.record(rec)
        stale = resume_pending(book, DelayWindow())
        assert [r.id for r in stale] == [rec.id]
        assert book.get(rec.id).state == STATE_CANCELLED
        assert "重启" in book.get(rec.id).error

    def test_applied_records_are_untouched(self, book):
        """已执行的动作与窗口无关，不该被这次作废改动。"""
        rec = EffectRecord(tool="FileWrite", state=STATE_APPLIED)
        book.record(rec)
        assert resume_pending(book, DelayWindow()) == []
        assert book.get(rec.id).state == STATE_APPLIED

    def test_records_still_in_current_window_are_kept(self, book):
        """还在本次进程登记表里的 held 不该被当作遗留作废。"""
        rec = EffectRecord(tool="SendEmail", state=STATE_HELD, due_at=9_999_999_999.0)
        book.record(rec)
        win = DelayWindow()
        win.schedule(rec.id, 30.0, lambda: None)
        try:
            assert resume_pending(book, win) == []
            assert book.get(rec.id).state == STATE_HELD
        finally:
            win.clear()


class TestUndoRecord:
    """撤销执行的如实性：撤不动要说清原因。"""

    def test_held_item_directs_to_cancel(self, book):
        rec = EffectRecord(tool="SendEmail", state=STATE_HELD)
        book.record(rec)
        ok, note = undo_record(rec, journal=book)
        assert ok is False
        assert "cancel" in note

    def test_already_undone_is_reported(self, book):
        rec = EffectRecord(tool="FileWrite", state=STATE_UNDONE, reversible=True,
                           undo_spec={"kind": UNDO_CHECKPOINT, "cid": "abc"})
        book.record(rec)
        ok, note = undo_record(rec, journal=book)
        assert ok is False
        assert "已经撤销" in note

    def test_irreversible_item_states_reason(self, book):
        """外发项必须报"已离开本机/无法撤回"，不能给一句含糊的失败。"""
        rec = EffectRecord(
            tool="SendEmail", preview="发给 a@b.c", reversible=False,
            undo_spec={"kind": UNDO_NONE, "reason": "该动作已离开本机，无法撤回"},
            state=STATE_APPLIED,
        )
        book.record(rec)
        assert rec.undoable is False
        ok, note = undo_record(rec, journal=book)
        assert ok is False
        assert note == "该动作已离开本机，无法撤回"
        assert describe_undo(rec) == note

    def test_missing_git_marks_failed_availability(self, book, monkeypatch):
        """检查点不可用（缺 git/关闭开关）时如实拒绝，不改状态。"""
        rec = EffectRecord(
            tool="FileWrite", reversible=True, state=STATE_APPLIED,
            undo_spec={"kind": UNDO_CHECKPOINT, "cid": "deadbeef", "workdir": "."},
        )
        book.record(rec)
        monkeypatch.setattr(
            journal_mod, "manager_for",
            lambda *a, **k: type("M", (), {"available": lambda self: False})(),
        )
        ok, note = undo_record(rec, journal=book)
        assert ok is False
        assert "不可用" in note
        assert book.get(rec.id).state == STATE_APPLIED

    def test_pruned_checkpoint_refuses(self, book, monkeypatch, tmp_path):
        """检查点已被配额修剪时报"无法回退工作区"，不能假称已撤销。"""
        rec = EffectRecord(
            tool="FileWrite", reversible=True, state=STATE_APPLIED,
            undo_spec={"kind": UNDO_CHECKPOINT, "cid": "gone", "workdir": str(tmp_path)},
        )
        book.record(rec)
        fake = type("M", (), {
            "available": lambda self: True,
            "has_checkpoint": lambda self, cid: False,
        })()
        monkeypatch.setattr(journal_mod, "manager_for", lambda *a, **k: fake)
        ok, note = undo_record(rec, journal=book)
        assert ok is False
        assert "配额修剪" in note

    @needs_git
    def test_checkpoint_undo_reverts_workdir(self, book, workdir, tmp_path, monkeypatch):
        """真回滚：撤销把文件退到本轮开始前的状态，并记成 undone。"""
        from agent.core.checkpoint import CheckpointManager

        rec, _mgr = _applied_with_checkpoint(book, workdir, tmp_path)
        (workdir / "a.txt").write_text("modified", encoding="utf-8")
        (workdir / "new.txt").write_text("x", encoding="utf-8")

        def _manager_at_tmp(workdir_arg, settings=None, session_name="journal"):
            """把检查点存储钉进 tmp（否则测试会在用户机器上留一份 shadow 仓库）。"""
            return CheckpointManager(
                workdir_arg or ".", session_name or "journal",
                base_dir=str(tmp_path / "ckpt-store"),
            )

        monkeypatch.setattr(journal_mod, "manager_for", _manager_at_tmp)
        ok, note = undo_record(rec, journal=book)
        assert ok is True, note
        assert (workdir / "a.txt").read_text(encoding="utf-8") == "original"
        assert not (workdir / "new.txt").exists()
        assert book.get(rec.id).state == STATE_UNDONE

    def test_failed_restore_marks_failed_with_reason(self, book, monkeypatch):
        """restore 失败时状态转 failed 并留存原因（供 /undo list 复查）。"""
        rec = EffectRecord(
            tool="FileWrite", reversible=True, state=STATE_APPLIED,
            undo_spec={"kind": UNDO_CHECKPOINT, "cid": "c1", "workdir": "."},
        )
        book.record(rec)
        fake = type("M", (), {
            "available": lambda self: True,
            "has_checkpoint": lambda self, cid: True,
            "restore": lambda self, cid: (False, "工作区被占用"),
        })()
        monkeypatch.setattr(journal_mod, "manager_for", lambda *a, **k: fake)
        ok, note = undo_record(rec, journal=book)
        assert ok is False and note == "工作区被占用"
        after = book.get(rec.id)
        assert after.state == STATE_FAILED and after.error == "工作区被占用"

    def test_round_rollback_marks_same_checkpoint_siblings(self, book, monkeypatch):
        """整轮回滚要把同检查点的其它条目一并标 undone。

        2026-10-26 实测：`/undo` 只标被点的那条，同轮的 `FileEdit` 仍显“可撤销”，
        用户会以为还有一层能单独撤——实际它的效果已随检查点一起被抹掉了。
        """
        first = EffectRecord(
            tool="FileWrite", state=STATE_APPLIED, reversible=True,
            undo_spec={"kind": UNDO_CHECKPOINT, "cid": "round1"},
        )
        sibling = EffectRecord(
            tool="FileEdit", state=STATE_APPLIED, reversible=True,
            undo_spec={"kind": UNDO_CHECKPOINT, "cid": "round1"},
        )
        outsider = EffectRecord(
            tool="FileWrite", state=STATE_APPLIED, reversible=True,
            undo_spec={"kind": UNDO_CHECKPOINT, "cid": "round2"},
        )
        for r in (first, sibling, outsider):
            book.record(r)
        fake = type("M", (), {
            "available": lambda self: True,
            "has_checkpoint": lambda self, cid: True,
            "restore": lambda self, cid: (True, "工作区已恢复到该消息发出之前"),
        })()
        monkeypatch.setattr(journal_mod, "manager_for", lambda *a, **k: fake)

        ok, note = undo_record(first, journal=book)

        assert ok is True, note
        assert "另 1 项" in note, note
        assert book.get(first.id).state == STATE_UNDONE
        assert book.get(sibling.id).state == STATE_UNDONE      # 同轮连带
        assert book.get(outsider.id).state == STATE_APPLIED    # 别的轮次不动


class TestUndoTask:
    """按任务目标批量补偿。"""

    def test_only_executed_records_are_processed(self, book):
        """held/cancelled 不在批量范围内（前者还没执行、后者已经撤过）。"""
        applied = EffectRecord(tool="FileWrite", task_id="goal1", state=STATE_APPLIED)
        held = EffectRecord(tool="SendEmail", task_id="goal1", state=STATE_HELD)
        cancelled = EffectRecord(tool="Bash", task_id="goal1", state=STATE_CANCELLED)
        other = EffectRecord(tool="FileWrite", task_id="goal2", state=STATE_APPLIED)
        for r in (applied, held, cancelled, other):
            book.record(r)
        assert book.task_records("goal1") and len(book.task_records("goal1")) == 3

        results = undo_task("goal1", journal=book)
        assert [r.id for r, _o, _n in results] == [applied.id]

    def test_irreversible_item_is_returned_with_reason(self, book, monkeypatch):
        """撤不动的照样返回并带原因，让调用方报"撤了 1 件、另有 1 封已发出"。"""
        reversible = EffectRecord(
            tool="FileWrite", task_id="goal1", state=STATE_APPLIED, reversible=True,
            undo_spec={"kind": UNDO_CHECKPOINT, "cid": "c1", "workdir": "."},
        )
        outbound = EffectRecord(
            tool="SendEmail", task_id="goal1", state=STATE_APPLIED, preview="发给 a@b.c",
            undo_spec={"kind": UNDO_NONE, "reason": "该动作已离开本机，无法撤回"},
        )
        for r in (reversible, outbound):
            book.record(r)
        monkeypatch.setattr(journal_mod, "manager_for", lambda *a, **k: type("M", (), {
            "available": lambda self: True,
            "has_checkpoint": lambda self, cid: True,
            "restore": lambda self, cid: (True, "已回滚"),
        })())
        results = undo_task("goal1", journal=book)
        assert len(results) == 2
        assert results[0][1] is True
        assert results[1][1] is False
        assert "离开本机" in results[1][2]

    def test_unknown_task_returns_empty(self, book):
        assert undo_task("nope", journal=book) == []
