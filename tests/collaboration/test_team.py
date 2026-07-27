"""TeamManager 团队管理测试。

覆盖团队创建、加载、成员管理、leader 判断、删除。
"""

import pytest

from agent.collaboration.team import (
    TEAM_LEAD_NAME,
    TeamManager,
    TeamMember,
    format_agent_id,
    get_team_manager,
    sanitize_name,
)


class TestTeamManager:
    """TeamManager 测试。"""

    @pytest.fixture(autouse=True)
    def _reset_manager(self, jarvis_home):
        """每个测试前重置全局管理器和团队目录。"""
        mgr = get_team_manager()
        mgr._active_team = None
        self.mgr = mgr

    def test_create_team(self):
        """创建团队后应包含 leader 成员。"""
        team = self.mgr.create("my-project", description="测试团队")
        assert team.name == "my-project"
        assert team.lead_agent_id == format_agent_id(TEAM_LEAD_NAME, "my-project")
        assert len(team.members) == 1
        assert team.members[0].name == TEAM_LEAD_NAME
        assert self.mgr.active_team == "my-project"

    def test_create_duplicate_raises(self):
        """重复创建同名团队应抛异常。"""
        self.mgr.create("dup")
        with pytest.raises(RuntimeError):
            self.mgr.create("dup")

    def test_load_and_save(self):
        """团队应能持久化加载。"""
        self.mgr.create("persist", description="d1")
        loaded = self.mgr.load("persist")
        assert loaded is not None
        assert loaded.description == "d1"

    def test_add_and_remove_member(self):
        """添加/移除成员应生效。"""
        self.mgr.create("members")
        member = TeamMember(
            agent_id=format_agent_id("coder", "members"),
            name="coder",
            agent_type="coder",
        )
        self.mgr.add_member("members", member)
        team = self.mgr.load("members")
        assert team.get_member("coder") is not None

        self.mgr.remove_member("members", "coder")
        team = self.mgr.load("members")
        assert team.get_member("coder") is None

    def test_cannot_remove_leader(self):
        """不能移除 leader。"""
        self.mgr.create("no-remove-leader")
        with pytest.raises(RuntimeError):
            self.mgr.remove_member("no-remove-leader", TEAM_LEAD_NAME)

    def test_mark_member_active(self):
        """标记成员活跃状态应持久化。"""
        self.mgr.create("active")
        member = TeamMember(
            agent_id=format_agent_id("coder", "active"),
            name="coder",
            agent_type="coder",
        )
        self.mgr.add_member("active", member)

        self.mgr.mark_member_active("active", "coder", False)
        team = self.mgr.load("active")
        assert team.get_member("coder").is_active is False

        self.mgr.mark_member_active("active", "coder", True)
        team = self.mgr.load("active")
        assert team.get_member("coder").is_active is None

    def test_delete_with_active_members_raises(self):
        """有活跃非 leader 成员时删除应抛异常。"""
        self.mgr.create("cannot-delete")
        member = TeamMember(
            agent_id=format_agent_id("coder", "cannot-delete"),
            name="coder",
            agent_type="coder",
            is_active=True,
        )
        self.mgr.add_member("cannot-delete", member)
        with pytest.raises(RuntimeError):
            self.mgr.delete("cannot-delete")

    def test_delete_empty_team(self):
        """空团队应能删除。"""
        self.mgr.create("empty")
        assert self.mgr.delete("empty") is True
        assert self.mgr.load("empty") is None
        assert self.mgr.active_team is None


class TestNameUtils:
    """团队名工具函数测试。"""

    def test_sanitize_name(self):
        """sanitize_name 应只保留合法字符。"""
        assert sanitize_name("my project") == "my-project"
        assert sanitize_name("a@b#c") == "a-b-c"
        assert sanitize_name("---") == "team"

    def test_format_agent_id(self):
        """agent_id 格式应为 name@team。"""
        assert format_agent_id("coder", "my-project") == "coder@my-project"
