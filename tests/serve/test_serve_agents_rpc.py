"""``agents.exec`` 与代理团队快照单测。

覆盖（aceFelix，2026-11）：
1. 快照形状：台账条目 + 团队文件补行（offline）+ 计数 + 任务台账摘要；
2. 未装配团队时 enabled=false 且不抛异常；
3. stop 的三种分支：未知代理 / 已结束的子代理 / 有任务可取消的子代理。

不碰真实 ``~/.jarvis``：团队与任务台账都用轻量假对象。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.collaboration.activity import get_activity_registry
from agent.collaboration.team import TeamFile, TeamMember, format_agent_id
from agent.serve.rpc_agents import AgentsRpcMixin, build_agents_payload


class _FakeMgr:
    """只实现 payload 用到的 load()。"""

    def __init__(self, team: TeamFile | None = None) -> None:
        self._team = team

    def load(self, name: str) -> TeamFile | None:
        return self._team


class _FakeTask:
    def __init__(self, tid: str, subject: str, status: str, owner: str = "") -> None:
        self.id = tid
        self.subject = subject
        self.status = status
        self.owner = owner
        self.active_form = ""


class _FakeTaskList:
    def __init__(self, tasks: list[_FakeTask]) -> None:
        self._tasks = tasks

    def list_all(self) -> list[_FakeTask]:
        return list(self._tasks)


class _FakeLoop:
    def __init__(self) -> None:
        self.cancelled: list[Any] = []

    def is_closed(self) -> bool:
        return False

    def call_soon_threadsafe(self, fn: Any, *args: Any) -> None:
        self.cancelled.append((fn, args))


class _FakeEngine:
    def __init__(
        self,
        *,
        mgr: Any = None,
        team_name: str = "",
        tasks: Any = None,
        loop: Any = None,
    ) -> None:
        self._team_mgr = mgr
        self._team_name = team_name
        self._task_list = tasks
        self._loop = loop


class _FakeApi:
    def __init__(self, engine: _FakeEngine) -> None:
        self._engine = engine


class _Host(AgentsRpcMixin):
    def __init__(self, engine: _FakeEngine) -> None:
        self._api = _FakeApi(engine)


def _make_team() -> TeamFile:
    lead = TeamMember(agent_id=format_agent_id("team-lead", "demo"), name="team-lead")
    member = TeamMember(
        agent_id=format_agent_id("coder-1", "demo"),
        name="coder-1",
        agent_type="coder",
        prompt="改造登录模块",
    )
    return TeamFile(
        name="demo",
        lead_agent_id=lead.agent_id,
        description="桌面工作区协作团队",
        members=[lead, member],
    )


def test_payload_merges_activity_and_team_members() -> None:
    """台账里的活代理 + 团队文件里的残留成员（offline）都要出现在快照里。"""
    reg = get_activity_registry()
    reg.clear()
    reg.start_agent("sub-x", "explorer-x", role="explorer", task="找入口")
    reg.update_agent("sub-x", status="running", activity="搜索中")

    engine = _FakeEngine(
        mgr=_FakeMgr(_make_team()),
        team_name="demo",
        tasks=_FakeTaskList([
            _FakeTask("1", "探索", "completed"),
            _FakeTask("2", "改造", "in_progress", "coder-1"),
            _FakeTask("3", "验收", "pending"),
        ]),
    )
    payload = build_agents_payload(engine)

    assert payload["enabled"] is True
    assert payload["team"]["name"] == "demo"
    names = {a["agent_id"] for a in payload["agents"]}
    assert "sub-x" in names
    # 团队文件里 coder-1 不在台账 → 补一行 offline
    assert format_agent_id("coder-1", "demo") in names
    offline = next(a for a in payload["agents"] if a["agent_id"].endswith("coder-1@demo"))
    assert offline["status"] == "offline"
    assert payload["counts"]["running"] == 1
    assert payload["counts"]["offline"] == 1
    assert payload["task_list"]["total"] == 3
    assert payload["task_list"]["completed"] == 1
    assert payload["task_list"]["in_progress"] == 1


def test_payload_without_team_is_safe() -> None:
    """团队未装配：enabled=false、空团队、空任务摘要，不抛异常。"""
    reg = get_activity_registry()
    reg.clear()
    engine = _FakeEngine()
    payload = build_agents_payload(engine)
    assert payload["enabled"] is False
    assert payload["agents"] == []
    assert payload["counts"]["total"] == 0
    assert payload["task_list"]["items"] == []


def test_stop_unknown_agent_reports_false() -> None:
    """未知 agent_id：ok=false + 说明，不取消任何任务。"""
    get_activity_registry().clear()
    engine = _FakeEngine(loop=_FakeLoop())
    host = _Host(engine)
    result = host._rpc_agents_exec({"action": "stop", "agent_id": "nope"})
    assert result["ok"] is False
    assert "没有在运行" in result["message"]


def test_stop_finished_subagent_marks_stopped() -> None:
    """台账有记录但任务已结束：标 stopped 并回 ok=true（用户看到它停了）。"""
    reg = get_activity_registry()
    reg.clear()
    engine = _FakeEngine(loop=_FakeLoop())
    reg.start_agent("sub-y", "coder-y", role="coder")
    result = host_call(engine, {"action": "stop", "agent_id": "sub-y"})
    assert result["ok"] is True
    assert reg.snapshot()[0]["status"] == "stopped"


def test_stop_running_subagent_schedules_cancel() -> None:
    """有任务时通过引擎 loop 的 call_soon_threadsafe 取消，不在调用线程直接 cancel。"""
    reg = get_activity_registry()
    reg.clear()
    loop = _FakeLoop()
    engine = _FakeEngine(loop=loop)
    reg.start_agent("sub-z", "researcher-z", role="researcher")

    class _Task:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            pass

    task = _Task()
    reg.bind_task("sub-z", task)
    result = host_call(engine, {"action": "stop", "agent_id": "sub-z"})
    assert result["ok"] is True
    assert loop.cancelled  # 投递到引擎 loop
    fn, args = loop.cancelled[0]
    assert args == ()


def host_call(engine: _FakeEngine, payload: dict[str, Any]) -> dict[str, Any]:
    """构造宿主并执行 agents.exec（strip 传输层信封键由被测函数负责）。"""
    return _Host(engine)._rpc_agents_exec(payload)
