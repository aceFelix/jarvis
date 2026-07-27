"""共享任务列表 TaskList 测试。

覆盖创建、更新、删除、依赖链、可领取任务、owner 变更回调。
"""

import pytest

from agent.collaboration.task_list import TaskList


class TestTaskCrud:
    """任务增删改查测试。"""

    @pytest.fixture(autouse=True)
    def _setup(self, jarvis_home):
        """每个测试前重置任务列表。"""
        self.tl = TaskList("test-crud")
        self.tl.reset()

    def test_create_and_read(self):
        """创建任务后应能读取。"""
        tid = self.tl.create("探索认证模块", "找所有 auth 相关代码")
        task = self.tl.read(tid)
        assert task is not None
        assert task.subject == "探索认证模块"
        assert task.status == "pending"
        assert task.owner is None

    def test_update_status_and_owner(self):
        """更新状态和 owner 应持久化。"""
        tid = self.tl.create("修改登录逻辑", "")
        updated = self.tl.update(tid, status="in_progress", owner="coder")
        assert updated is not None
        assert updated.status == "in_progress"
        assert updated.owner == "coder"

        task = self.tl.read(tid)
        assert task.status == "in_progress"
        assert task.owner == "coder"

    def test_delete(self):
        """删除任务后状态为 deleted，且不在活跃列表中。"""
        tid = self.tl.create("临时任务", "")
        self.tl.delete(tid)
        # 删除保留记录（status=deleted），但不出现在 list_all
        assert self.tl.read(tid).status == "deleted"
        assert self.tl.list_all() == []

    def test_id_increments(self):
        """任务 ID 应递增。"""
        t1 = self.tl.create("t1", "")
        t2 = self.tl.create("t2", "")
        assert int(t2) == int(t1) + 1


class TestTaskDependencies:
    """任务依赖链测试。"""

    @pytest.fixture(autouse=True)
    def _setup(self, jarvis_home):
        """每个测试前重置任务列表。"""
        self.tl = TaskList("test-deps")
        self.tl.reset()

    def test_available_tasks(self):
        """可领取任务应为 pending + 无阻塞（不检查 owner，只检查状态/阻塞）。"""
        t1 = self.tl.create("t1", "")
        self.tl.create("t2", "", owner="coder")  # pending + owner 仍属于可用
        t3 = self.tl.create("t3", "")
        self.tl.update(t3, add_blocked_by=[t1])

        available = self.tl.get_available_tasks()
        assert len(available) == 2
        assert t1 in {t.id for t in available}

    def test_dependency_chain(self):
        """完成任务应解除下游阻塞。"""
        t1 = self.tl.create("上游", "")
        t2 = self.tl.create("下游", "")
        self.tl.update(t2, add_blocked_by=[t1])

        assert self.tl.read(t2).blocked_by == [t1]

        self.tl.update(t1, status="completed")

        # t2 的 blocked_by 应被清空
        assert self.tl.read(t2).blocked_by == []
        assert len(self.tl.get_available_tasks()) == 1

    def test_owner_changed_callback(self):
        """owner 变更应触发回调。"""
        captured = {}

        def on_owner_changed(task, old_owner):
            captured["task_id"] = task.id
            captured["old_owner"] = old_owner
            captured["new_owner"] = task.owner

        self.tl.set_hooks(on_owner_changed=on_owner_changed)
        tid = self.tl.create("callback task", "")
        self.tl.update(tid, owner="coder")

        assert captured["task_id"] == tid
        assert captured["old_owner"] is None
        assert captured["new_owner"] == "coder"

    def test_completed_callback(self):
        """任务完成应触发回调。"""
        completed_ids = []

        def on_completed(task):
            completed_ids.append(task.id)

        self.tl.set_hooks(on_completed=on_completed)
        tid = self.tl.create("complete me", "")
        self.tl.update(tid, status="completed")

        assert completed_ids == [tid]
