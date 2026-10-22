"""检查点配额分族：任务级快照不许把消息级回溯（/rewind）挤掉。

盯的是 W3 的一个真实取舍：后台值守每推一步都要打一个 shadow git 检查点，
如果和消息级回溯共用一份 20 条配额，跑几个目标就会把用户的 /rewind 历史挤没
（副作用账本顶部早就为这个警告过）。所以 shadow git 里分两族各守各的上限。

覆盖：两族互不占用、族内修剪掉的是最旧那几条、manifest 记 kind、
旧版无 kind 的 manifest 归入消息级（向后兼容）、任务级检查点照样能回滚。

git 不在 PATH 时整组 skip（CI 各平台均自带 git）——这是被验证能力自身的
前置条件，不是测试环境的额外依赖。

@author aceFelix
"""
from __future__ import annotations

import json
import shutil

import pytest

from agent.core.checkpoint import CheckpointManager

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="环境缺少 git，检查点测试跳过"
)


@pytest.fixture()
def workdir(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "a.txt").write_text("original", encoding="utf-8")
    return d


def _mgr(tmp_path, workdir, **over) -> CheckpointManager:
    return CheckpointManager(
        str(workdir), "s1", base_dir=str(tmp_path / "store"), **over,
    )


def _manifest(mgr: CheckpointManager) -> list[dict]:
    return json.loads(mgr._manifest_path.read_text(encoding="utf-8"))


def _kinds(mgr: CheckpointManager) -> list[str]:
    return [str(e.get("kind") or "turn") for e in _manifest(mgr)]


class TestQuotasAreIndependent:
    """两族各守各的上限，互不侵占。"""

    def test_task_overflow_keeps_turn_entries(self, tmp_path, workdir):
        mgr = _mgr(tmp_path, workdir, max_checkpoints=5, task_max_checkpoints=2)
        turns = [mgr.create(f"轮次{i}") for i in range(4)]
        for i in range(4):
            assert mgr.create(f"任务{i}", kind="task")
        kinds = _kinds(mgr)
        # 消息级 4 条一条没掉，任务级只留最新 2 条
        assert kinds.count("turn") == 4
        assert kinds.count("task") == 2
        for cid in turns:
            assert mgr.has_checkpoint(cid), "消息级回溯不该被任务级配额挤掉"

    def test_turn_overflow_keeps_task_entries(self, tmp_path, workdir):
        mgr = _mgr(tmp_path, workdir, max_checkpoints=2, task_max_checkpoints=3)
        tasks = [mgr.create(f"任务{i}", kind="task") for i in range(3)]
        for i in range(4):
            assert mgr.create(f"轮次{i}")
        kinds = _kinds(mgr)
        assert kinds.count("turn") == 2
        assert kinds.count("task") == 3
        for cid in tasks:
            assert mgr.has_checkpoint(cid)

    def test_prune_drops_oldest_in_that_family(self, tmp_path, workdir):
        """超配额时丢的是本族最旧的几条，不是全局最旧的。"""
        mgr = _mgr(tmp_path, workdir, max_checkpoints=9, task_max_checkpoints=2)
        first = mgr.create("任务0", kind="task")
        mgr.create("轮次0")
        last_two = [mgr.create(f"任务{i}", kind="task") for i in (1, 2)]
        assert not mgr.has_checkpoint(first), "该族最旧的一条应被修剪"
        for cid in last_two:
            assert mgr.has_checkpoint(cid)

    def test_refs_do_not_collide_across_families(self, tmp_path, workdir):
        """ref 序号全局递增：两族共用一个 shadow 仓库也不会撞号。"""
        mgr = _mgr(tmp_path, workdir, max_checkpoints=3, task_max_checkpoints=3)
        mgr.create("轮次0")
        mgr.create("任务0", kind="task")
        refs = [e["ref"] for e in _manifest(mgr)]
        assert len(set(refs)) == len(refs) == 2


class TestManifestKindField:
    """kind 落盘与向后兼容。"""

    def test_kind_recorded(self, tmp_path, workdir):
        mgr = _mgr(tmp_path, workdir)
        mgr.create("任务", kind="task")
        assert _kinds(mgr) == ["task"]

    def test_default_kind_is_turn(self, tmp_path, workdir):
        mgr = _mgr(tmp_path, workdir)
        mgr.create("轮次")
        assert _kinds(mgr) == ["turn"]

    def test_legacy_manifest_without_kind_counts_as_turn(self, tmp_path, workdir):
        """老 manifest（没有 kind 字段）一律归消息级：升级后不能把历史当成任务级
        而按更小的配额修剪掉。"""
        mgr = _mgr(tmp_path, workdir, max_checkpoints=20, task_max_checkpoints=1)
        mgr.create("初始")
        rows = _manifest(mgr)
        for r in rows:
            r.pop("kind", None)
        mgr._save_manifest(rows)

        # 触发一次修剪扫描：消息级配额 20，不该有东西被删
        mgr.create("任务", kind="task")
        assert mgr.has_checkpoint(rows[0]["id"])
        assert len(_manifest(mgr)) == 2


class TestTaskCheckpointRestorable:
    """任务级检查点必须真的能回滚，不然入账就是假的可撤销。"""

    def test_restore_reverts_files(self, tmp_path, workdir):
        mgr = _mgr(tmp_path, workdir)
        cid = mgr.create("步骤开始前", kind="task")
        (workdir / "a.txt").write_text("modified", encoding="utf-8")
        (workdir / "new.txt").write_text("x", encoding="utf-8")

        ok, _note = mgr.restore(cid)
        assert ok is True
        assert (workdir / "a.txt").read_text(encoding="utf-8") == "original"
        assert not (workdir / "new.txt").exists()
