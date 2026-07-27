"""TeammateRunner 消息处理与任务领取测试。

覆盖 _handle_message 各分支、自主任务领取、计划审批超时、注册表。
不需要真实 LLM provider，使用 mock 对象。
"""

import asyncio

import pytest

from agent.collaboration.mailbox import (
    TeammateMessage,
    make_permission_response,
    make_plan_approval_response,
    make_shutdown_request,
    make_task_assignment,
    read_mailbox,
)
from agent.collaboration.task_list import TaskList
from agent.collaboration.team import TEAM_LEAD_NAME, TeamManager, TeamMember, format_agent_id
from agent.collaboration.teammate import TeammateIdentity, TeammateRunner
from agent.collaboration.teammate_registry import TeammateRegistry


class _MockProvider:
    """模拟 LLM provider。"""

    default_model = "mock-model"


class _MockRegistry:
    """模拟工具注册表。"""

    def all(self):
        return []


@pytest.fixture
def teammate_runner(jarvis_home):
    """构造一个用于测试的 TeammateRunner（不启动后台循环）。"""
    mgr = TeamManager()
    mgr._active_team = None
    mgr.create("test-runner", lead_session_id="s1")
    mgr.add_member(
        "test-runner",
        TeamMember(
            agent_id=format_agent_id("coder", "test-runner"),
            name="coder",
            agent_type="coder",
        ),
    )

    identity = TeammateIdentity(
        agent_id=format_agent_id("coder", "test-runner"),
        agent_name="coder",
        team_name="test-runner",
    )

    runner = TeammateRunner(
        identity=identity,
        team_manager=mgr,
        task_list=None,
        provider=_MockProvider(),
        sub_registry=_MockRegistry(),
        workdir="/tmp",
        permission_mode="yolo",
    )
    return runner


class TestHandleMessage:
    """_handle_message 分支测试。"""

    def test_shutdown_request_returns_shutdown(self, teammate_runner):
        """shutdown_request 应返回 shutdown 并回复响应。"""
        msg = make_shutdown_request(TEAM_LEAD_NAME, request_id="r1")
        result = asyncio.run(
            teammate_runner._handle_message(msg, None, None, None)
        )
        assert result == "shutdown"
        # leader 应收到 shutdown_response
        responses = read_mailbox(TEAM_LEAD_NAME, "test-runner", unread_only=False)
        assert any(m.type == "shutdown_response" and m.approve is True for m in responses)

    def test_plain_message_from_leader_queues_text(self, teammate_runner):
        """leader 的 plain 消息应加入待处理队列。"""
        msg = TeammateMessage(type="plain", from_name=TEAM_LEAD_NAME, text="do this")
        result = asyncio.run(
            teammate_runner._handle_message(msg, None, None, None)
        )
        assert result == "resume"
        assert teammate_runner.state.pending_user_messages == ["do this"]

    def test_task_assignment_sets_current_task(self, teammate_runner):
        """task_assignment 应设置当前任务 ID 并加入队列。"""
        msg = make_task_assignment(TEAM_LEAD_NAME, "9", "fix bug", "details")
        result = asyncio.run(
            teammate_runner._handle_message(msg, None, None, None)
        )
        assert result == "resume"
        assert teammate_runner.state.current_task_id == "9"
        assert "fix bug" in teammate_runner.state.pending_user_messages[0]

    def test_plan_approval_response_approves(self, teammate_runner):
        """plan_approval_response approve=true 应设置通过事件。"""
        teammate_runner.state.pending_request_id = "req1"
        msg = make_plan_approval_response(TEAM_LEAD_NAME, "req1", approve=True)
        asyncio.run(teammate_runner._handle_message(msg, None, None, None))
        assert teammate_runner.state.plan_approved_event.is_set()
        assert not teammate_runner.state.plan_rejected_event.is_set()

    def test_plan_approval_response_mismatched_request_id_ignored(self, teammate_runner):
        """request_id 不匹配应忽略。"""
        teammate_runner.state.pending_request_id = "req1"
        msg = make_plan_approval_response(TEAM_LEAD_NAME, "req2", approve=True)
        asyncio.run(teammate_runner._handle_message(msg, None, None, None))
        assert not teammate_runner.state.plan_approved_event.is_set()

    def test_permission_response_rejects(self, teammate_runner):
        """permission_response approve=false 应设置驳回事件。"""
        teammate_runner.state.pending_request_id = "req1"
        msg = make_permission_response(TEAM_LEAD_NAME, "req1", approve=False, reason="no")
        asyncio.run(teammate_runner._handle_message(msg, None, None, None))
        assert teammate_runner.state.permission_rejected_event.is_set()
        assert not teammate_runner.state.permission_approved_event.is_set()


class TestClaimTask:
    """自主任务领取测试。"""

    def test_claim_available_task(self, jarvis_home):
        """teammate 应能领取 pending 且无阻塞的任务。"""
        mgr = TeamManager()
        mgr._active_team = None
        mgr.create("test-claim", lead_session_id="s1")
        mgr.add_member(
            "test-claim",
            TeamMember(
                agent_id=format_agent_id("coder", "test-claim"),
                name="coder",
                agent_type="coder",
            ),
        )

        tl = TaskList("test-claim")
        tl.reset()
        tid = tl.create("待领取任务", "描述")

        identity = TeammateIdentity(
            agent_id=format_agent_id("coder", "test-claim"),
            agent_name="coder",
            team_name="test-claim",
        )
        runner = TeammateRunner(
            identity=identity,
            team_manager=mgr,
            task_list=tl,
            provider=_MockProvider(),
            sub_registry=_MockRegistry(),
            workdir="/tmp",
        )

        claimed = runner._claim_available_task(None)
        assert claimed is True

        task = tl.read(tid)
        assert task.status == "in_progress"
        assert task.owner == "coder"
        assert runner.state.current_task_id == tid
        # leader 邮箱应收到 task_claimed
        messages = read_mailbox(TEAM_LEAD_NAME, "test-claim", unread_only=False)
        assert any(m.type == "task_claimed" and m.task_id == tid for m in messages)

    def test_claim_no_available_task(self, jarvis_home):
        """无可用任务时应返回 False。"""
        mgr = TeamManager()
        mgr._active_team = None
        mgr.create("test-empty", lead_session_id="s1")
        mgr.add_member(
            "test-empty",
            TeamMember(
                agent_id=format_agent_id("coder", "test-empty"),
                name="coder",
                agent_type="coder",
            ),
        )

        tl = TaskList("test-empty")
        tl.reset()
        # 创建一个已被占用任务
        tl.create("owned", "", owner="other")

        identity = TeammateIdentity(
            agent_id=format_agent_id("coder", "test-empty"),
            agent_name="coder",
            team_name="test-empty",
        )
        runner = TeammateRunner(
            identity=identity,
            team_manager=mgr,
            task_list=tl,
            provider=_MockProvider(),
            sub_registry=_MockRegistry(),
            workdir="/tmp",
        )

        assert runner._claim_available_task(None) is False


class TestPlanApproval:
    """计划审批等待测试。"""

    @pytest.mark.asyncio
    async def test_request_plan_approval_timeout(self, teammate_runner):
        """无响应时 request_plan_approval 应超时返回驳回。"""
        approved, reason = await teammate_runner.request_plan_approval(
            "plan", timeout=0.01
        )
        assert approved is False
        assert "超时" in reason


class TestTeammateRegistry:
    """TeammateRegistry 注册表测试。"""

    def test_register_and_get(self):
        """注册后应能按名字查找。"""
        registry = TeammateRegistry()
        fake_runner = object()
        registry.register("team-a", "coder", fake_runner)
        assert registry.get("team-a", "coder") is fake_runner
        assert registry.get("team-a", "other") is None

    def test_unregister(self):
        """注销后应无法查找。"""
        registry = TeammateRegistry()
        registry.register("team-a", "coder", object())
        registry.unregister("team-a", "coder")
        assert registry.get("team-a", "coder") is None

    def test_list_for_team(self):
        """应能列出团队下所有注册队友。"""
        registry = TeammateRegistry()
        registry.register("team-a", "coder", object())
        registry.register("team-a", "tester", object())
        registry.register("team-b", "coder", object())
        assert set(registry.list_for_team("team-a")) == {"coder", "tester"}
