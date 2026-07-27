"""Mailbox 消息系统测试。

覆盖消息工厂、序列化/反序列化、邮箱读写、广播、未读检测、清空。
"""

import pytest

from agent.collaboration.mailbox import (
    TeammateMessage,
    broadcast_mailbox,
    clear_mailbox,
    has_unread,
    make_broadcast,
    make_heartbeat,
    make_idle_notification,
    make_permission_request,
    make_permission_response,
    make_plain_message,
    make_plan_approval_request,
    make_plan_approval_response,
    make_shutdown_request,
    make_shutdown_response,
    make_task_assignment,
    make_task_claimed,
    make_task_completed,
    read_mailbox,
    write_mailbox,
)
from agent.collaboration.team import TEAM_LEAD_NAME, TeamManager, format_agent_id, get_team_manager


class TestMessageFactories:
    """消息工厂函数测试。"""

    def test_make_plain_message(self):
        """普通文本消息应包含基本字段。"""
        msg = make_plain_message("team-lead", "hello", summary="hi")
        assert msg.type == "plain"
        assert msg.from_name == "team-lead"
        assert msg.text == "hello"
        assert msg.summary == "hi"
        assert msg.timestamp

    def test_make_idle_notification(self):
        """空闲通知应携带摘要和任务 ID。"""
        msg = make_idle_notification("coder", summary="done", completed_task_id="3")
        assert msg.type == "idle_notification"
        assert msg.summary == "done"
        assert msg.task_id == "3"

    def test_make_plan_approval_request(self):
        """计划审批请求应生成 request_id。"""
        msg = make_plan_approval_request("coder", "plan text")
        assert msg.type == "plan_approval_request"
        assert msg.text == "plan text"
        assert len(msg.request_id) == 12

    def test_make_permission_request(self):
        """权限请求应记录 action / tool / args。"""
        msg = make_permission_request("coder", "write file", "FileWrite", {"path": "a.txt"})
        assert msg.type == "permission_request"
        assert msg.action == "write file"
        assert msg.tool == "FileWrite"
        assert msg.args == {"path": "a.txt"}

    def test_make_task_claimed_and_completed(self):
        """任务领取/完成消息应携带任务信息。"""
        claimed = make_task_claimed("coder", "7", "fix login")
        assert claimed.type == "task_claimed"
        assert claimed.task_id == "7"
        assert claimed.task_subject == "fix login"

        completed = make_task_completed("coder", "7", status="completed", summary="done")
        assert completed.type == "task_completed"
        assert completed.status == "completed"
        assert completed.summary == "done"

    def test_make_heartbeat(self):
        """心跳消息应携带状态和当前任务。"""
        msg = make_heartbeat("coder", status="idle", task_id="7")
        assert msg.type == "heartbeat"
        assert msg.status == "idle"
        assert msg.task_id == "7"


class TestMessageSerialization:
    """消息序列化测试。"""

    def test_roundtrip(self):
        """to_dict / from_dict 应无损往返。"""
        original = make_permission_request(
            "coder", "write", "FileWrite", {"path": "a.txt"}, request_id="req123"
        )
        d = original.to_dict()
        restored = TeammateMessage.from_dict(d)

        assert restored.type == original.type
        assert restored.from_name == original.from_name
        assert restored.action == original.action
        assert restored.tool == original.tool
        assert restored.args == original.args
        assert restored.request_id == original.request_id

    def test_unknown_fields_go_to_data(self):
        """from_dict 对未知字段应放入 data 兜底。"""
        d = {
            "type": "plain",
            "from": "x",
            "timestamp": "2026-01-01T00:00:00",
            "text": "hi",
            "unknown_key": "unknown_value",
        }
        msg = TeammateMessage.from_dict(d)
        assert msg.data == {"unknown_key": "unknown_value"}


class TestMailboxIO:
    """邮箱读写测试。"""

    @pytest.fixture(autouse=True)
    def _setup_team(self, jarvis_home):
        """每个测试前创建一个临时团队。"""
        self.team_name = "test-mailbox"
        mgr = TeamManager()
        mgr._active_team = None
        mgr.create(self.team_name, lead_session_id="s1", description="test")

    def test_write_and_read(self):
        """写入消息后可读取。"""
        msg = make_plain_message("team-lead", "task for you")
        write_mailbox("coder", msg, self.team_name)

        messages = read_mailbox("coder", self.team_name, unread_only=False)
        assert len(messages) == 1
        assert messages[0].text == "task for you"
        assert messages[0].from_name == "team-lead"

    def test_unread_only(self):
        """unread_only=True 应过滤已读消息。"""
        msg = make_plain_message("team-lead", "first")
        write_mailbox("coder", msg, self.team_name)

        # 第一次读取会标记已读
        read_mailbox("coder", self.team_name, unread_only=True, mark_read=True)
        # 第二次应无未读
        unread = read_mailbox("coder", self.team_name, unread_only=True, mark_read=True)
        assert len(unread) == 0

    def test_has_unread(self):
        """has_unread 应反映未读状态。"""
        assert not has_unread("coder", self.team_name)
        write_mailbox("coder", make_plain_message("team-lead", "ping"), self.team_name)
        assert has_unread("coder", self.team_name)

    def test_clear_mailbox(self):
        """清空后读取为空。"""
        write_mailbox("coder", make_plain_message("team-lead", "a"), self.team_name)
        clear_mailbox("coder", self.team_name)
        assert read_mailbox("coder", self.team_name, unread_only=False) == []

    def test_broadcast(self, jarvis_home):
        """广播应向所有成员发送（排除发送者）。"""
        from agent.collaboration.team import TeamMember

        mgr = get_team_manager()
        mgr.add_member(
            self.team_name,
            TeamMember(
                agent_id=format_agent_id("researcher", self.team_name),
                name="researcher",
                agent_type="researcher",
            ),
        )
        mgr.add_member(
            self.team_name,
            TeamMember(
                agent_id=format_agent_id("tester", self.team_name),
                name="tester",
                agent_type="tester",
            ),
        )

        broadcast_mailbox(
            TEAM_LEAD_NAME,
            make_broadcast(TEAM_LEAD_NAME, "urgent", summary="urgent"),
            self.team_name,
        )

        assert has_unread("researcher", self.team_name)
        assert has_unread("tester", self.team_name)
        # leader 不应收到自己的广播
        assert not has_unread(TEAM_LEAD_NAME, self.team_name)
