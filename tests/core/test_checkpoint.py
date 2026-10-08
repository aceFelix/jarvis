"""消息级回溯检查点 CheckpointManager 测试（shadow git）。

覆盖：create/restore/describe 主链路、未跟踪文件清理、exclude 生效、
配额修剪、禁用与无 git 降级、跨 workdir 隔离。git 不在 PATH 时整组
skip（CI 各平台均自带 git）。

@author aceFelix
"""

from __future__ import annotations

import shutil

import pytest

from agent.core.checkpoint import CheckpointManager, _workdir_key

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="环境缺少 git，检查点测试跳过"
)


@pytest.fixture()
def workdir(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "a.txt").write_text("original", encoding="utf-8")
    return d


@pytest.fixture()
def mgr(tmp_path, workdir):
    return CheckpointManager(
        str(workdir), "s1", base_dir=str(tmp_path / "store"),
    )


class TestCreateRestore:
    def test_create_returns_id_and_restore_reverts(self, mgr, workdir):
        """打点 → 改文件 → 回滚：内容恢复原样。"""
        cid = mgr.create(reason="初始")
        assert cid
        (workdir / "a.txt").write_text("modified", encoding="utf-8")
        ok, _note = mgr.restore(cid)
        assert ok is True
        assert (workdir / "a.txt").read_text(encoding="utf-8") == "original"

    def test_restore_deletes_new_files(self, mgr, workdir):
        """检查点后新建的文件在回滚时被删除。"""
        cid = mgr.create()
        (workdir / "new.py").write_text("x = 1", encoding="utf-8")
        ok, _ = mgr.restore(cid)
        assert ok is True
        assert not (workdir / "new.py").exists()

    def test_restore_keeps_ignored_dirs(self, mgr, workdir):
        """node_modules（exclude 名单）既不入快照也不被 clean 删除。"""
        nm = workdir / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("1", encoding="utf-8")
        cid = mgr.create()
        (workdir / "b.txt").write_text("new file", encoding="utf-8")
        ok, _ = mgr.restore(cid)
        assert ok is True
        assert (nm / "index.js").exists()  # 忽略目录不受回滚影响
        assert not (workdir / "b.txt").exists()  # 普通新文件被清理

    def test_describe_lists_changes(self, mgr, workdir):
        """describe 报告工作区相对检查点的改动与新增文件。"""
        cid = mgr.create()
        (workdir / "a.txt").write_text("changed", encoding="utf-8")
        (workdir / "extra.txt").write_text("e", encoding="utf-8")
        desc = mgr.describe(cid)
        assert desc is not None
        paths = {f["path"] for f in desc["files"]}
        assert "a.txt" in paths
        assert "extra.txt" in desc["untracked"]

    def test_restore_unknown_id(self, mgr):
        """不存在的检查点 id → 拒绝回滚并给出原因。"""
        ok, note = mgr.restore("0" * 40)
        assert ok is False
        assert "检查点" in note

    def test_has_checkpoint(self, mgr):
        cid = mgr.create()
        assert cid and mgr.has_checkpoint(cid)
        assert mgr.has_checkpoint("deadbeef" * 5) is False
        assert mgr.has_checkpoint("") is False


class TestQuota:
    def test_oldest_pruned(self, tmp_path, workdir):
        """超过配额后最旧检查点失效，新检查点仍可用。"""
        m = CheckpointManager(
            str(workdir), "s1", max_checkpoints=2, base_dir=str(tmp_path / "store"),
        )
        old = m.create(reason="第1个")
        (workdir / "f1.txt").write_text("1", encoding="utf-8")
        m.create(reason="第2个")
        (workdir / "f2.txt").write_text("2", encoding="utf-8")
        newest = m.create(reason="第3个")
        assert m.has_checkpoint(old) is False
        assert m.has_checkpoint(newest) is True


class TestDegradation:
    def test_disabled_returns_none(self, tmp_path, workdir):
        """enabled=False：create 返回 None、restore 拒绝（仅对话回退）。"""
        m = CheckpointManager(
            str(workdir), "s1", enabled=False, base_dir=str(tmp_path / "store"),
        )
        assert m.available() is False
        assert m.create() is None
        ok, note = m.restore("x")
        assert ok is False and "不可用" in note

    def test_cross_workdir_isolated(self, tmp_path, workdir):
        """另一工作目录的检查点 id 在当前目录查不到（跨目录回滚防护）。"""
        other = tmp_path / "other"
        other.mkdir()
        store = str(tmp_path / "store")
        m1 = CheckpointManager(str(workdir), "s1", base_dir=store)
        m2 = CheckpointManager(str(other), "s1", base_dir=store)
        cid = m1.create()
        assert cid
        assert m2.has_checkpoint(cid) is False
        ok, _ = m2.restore(cid)
        assert ok is False


def test_workdir_key_stable_and_distinct(tmp_path):
    """存储键：同目录稳定；不同目录不同；大小写归一（Windows 语义）。"""
    d1 = tmp_path / "Proj"
    d1.mkdir()
    k1 = _workdir_key(str(d1))
    assert k1 == _workdir_key(str(d1))
    assert k1 == _workdir_key(str(d1).upper())
    d2 = tmp_path / "Other"
    d2.mkdir()
    assert k1 != _workdir_key(str(d2))
    # 目录名保留为可读前缀
    assert k1.startswith("Proj") or k1.startswith("proj")
