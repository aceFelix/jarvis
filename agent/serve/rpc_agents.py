"""代理团队（多 Agent 协作）的桌面指令 —— ``agents.exec`` RPC 混入 + 快照拼装。

背景（aceFelix，2026-11）：
- 终端入口早就有 Agent / Team / Task 系列工具，但 ``serve`` 路径直到本次升级才
  注册它们；桌面右栏需要一份「谁在跑、在干什么」的实时视图，于是新增：

  - 指令 ``agents.exec``：``status`` / ``stop`` / ``message``，一条指令多动词
    （与 ``tasks.exec`` 同理由：避免一动作一指令把协议常量撑爆）；
  - 事件 ``agents_state``：活动台账变更时按快照整份推送（节流在台账侧做）。

数据来源（只读拼装，不引入第二份真相）：
- ``agent/collaboration/activity.py``：进程内实时状态（子代理 + 队友）；
- ``agent/collaboration/team.py``：磁盘团队文件（重启后成员仍在）；
- ``agent/collaboration/task_list.py``：共享任务台账（队友领取/完成情况）。

安全口径：
- ``status`` 读侧永不抛异常（团队未装配也回空快照 + enabled=false）；
- ``stop`` / ``message`` 只操作代理自身，不触碰主会话 ``_send_task``；
- 队友停机走 ``runner.shutdown()``（会发 shutdown_request 并取消自身 task），
  同步子代理走 ``asyncio.Task.cancel()``，两者都必须在引擎 loop 上执行。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agent.serve.rpc_percept import TRANSPORT_ENVELOPE_KEYS

if TYPE_CHECKING:  # 仅类型标注，运行期不引入循环导入
    from agent.serve.engine.engine import ChatEngine

#: agents.exec 支持的动词（面板与测试都以此为准）
AGENT_ACTIONS = ("status", "stop", "message")

#: 视为「正在跑」的状态（计数与徽标共用一份口径）
_ACTIVE_STATUSES = ("starting", "running", "thinking")


def _payload(data: dict[str, Any]) -> dict[str, Any]:
    """剥掉 WS 传输层信封键（``type`` / ``id``），只留业务参数。"""
    return {k: v for k, v in (data or {}).items() if k not in TRANSPORT_ENVELOPE_KEYS}


def build_agents_payload(
    engine: "ChatEngine", agents: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """拼装右栏「代理团队」快照（RPC 与事件共用同一形状）。

    Args:
        engine: 引擎实例（读 ``_team_mgr`` / ``_task_list`` / ``_team_name``）。
        agents: 活动台账快照；None = 现读台账。

    Returns:
        ``{"enabled", "team", "agents", "counts", "task_list"}``。
    """
    from agent.collaboration.activity import get_activity_registry

    items = list(agents) if agents is not None else get_activity_registry().snapshot()

    mgr = getattr(engine, "_team_mgr", None)
    team_name = str(getattr(engine, "_team_name", "") or "")
    team: dict[str, Any] = {
        "name": team_name,
        "description": "",
        "lead_agent_id": "",
        "member_count": 0,
    }
    if mgr is not None and team_name:
        team_file = mgr.load(team_name)
        if team_file is not None:
            team = {
                "name": team_file.name,
                "description": team_file.description,
                "lead_agent_id": team_file.lead_agent_id,
                "member_count": len(team_file.members),
            }
            # 磁盘上还有、台账里没有的成员 = 上一进程残留：补一行「未运行」，
            # 否则用户重启桌面后会以为队友凭空消失。@author aceFelix
            seen = {a.get("agent_id") for a in items}
            for member in team_file.non_lead_members:
                if member.agent_id in seen:
                    continue
                items.append(
                    {
                        "agent_id": member.agent_id,
                        "name": member.name,
                        "role": member.agent_type or "general",
                        "mode": "teammate",
                        "team": team_file.name,
                        "status": "offline",
                        "task": member.prompt or "",
                        "current_tool": "",
                        "activity": "",
                        "output": "",
                        "started_at": member.joined_at,
                        "updated_at": member.joined_at,
                        "iterations": 0,
                        "tool_calls": 0,
                        "error": "",
                    }
                )

    counts = {
        "total": len(items),
        "running": sum(1 for a in items if a.get("status") in _ACTIVE_STATUSES),
        "idle": sum(1 for a in items if a.get("status") == "idle"),
        "offline": sum(1 for a in items if a.get("status") == "offline"),
        "done": sum(1 for a in items if a.get("status") == "done"),
        "failed": sum(1 for a in items if a.get("status") == "failed"),
        "stopped": sum(1 for a in items if a.get("status") == "stopped"),
    }

    return {
        "enabled": bool(mgr is not None and team_name),
        "team": team,
        "agents": sorted(items, key=lambda x: x.get("started_at") or 0),
        "counts": counts,
        "task_list": _task_summary(getattr(engine, "_task_list", None)),
    }


def _task_summary(task_list: Any) -> dict[str, Any]:
    """共享任务台账摘要（读失败回零值，不让面板整块消失）。"""
    summary: dict[str, Any] = {
        "total": 0,
        "pending": 0,
        "in_progress": 0,
        "completed": 0,
        "items": [],
    }
    if task_list is None:
        return summary
    try:
        tasks = task_list.list_all()
    except Exception:
        return summary
    counts = {"pending": 0, "in_progress": 0, "completed": 0}
    for task in tasks:
        if task.status in counts:
            counts[task.status] += 1
    summary.update(counts)
    summary["total"] = len(tasks)
    summary["items"] = [
        {
            "id": task.id,
            "subject": task.subject,
            "status": task.status,
            "owner": task.owner or "",
            "active_form": task.active_form or "",
        }
        for task in tasks[:20]
    ]
    return summary


class AgentsRpcMixin:
    """``agents.exec`` 的处理器（与 Tasks/Percept 同一种混入方式）。

    @author aceFelix
    """

    if TYPE_CHECKING:
        _api: Any

    # ---- agents.exec ----

    def _rpc_agents_exec(self, data: dict) -> dict:
        """agents.exec：查看团队、停止一个代理、给队友发补充指令。"""
        payload = _payload(data)
        action = str(payload.get("action") or "status").strip().lower()
        if action not in AGENT_ACTIONS:
            raise ValueError(f"agents.exec 未知动作：{action}（可用 {'/'.join(AGENT_ACTIONS)}）")
        engine = self._api._engine

        if action == "status":
            return {"ok": True, "action": action, "message": "", **build_agents_payload(engine)}

        agent_id = str(payload.get("agent_id") or "").strip()
        if not agent_id:
            raise ValueError(f"agents.exec {action} 必须给 agent_id")

        if action == "message":
            text = str(payload.get("text") or "").strip()
            if not text:
                raise ValueError("agents.exec message 必须给 text")
            return self._agents_message(engine, agent_id, text)

        return self._agents_stop(engine, agent_id)

    def _agents_stop(self, engine: Any, agent_id: str) -> dict:
        """停止一个代理：队友走 shutdown，同步子代理取消它自己的 asyncio 任务。"""
        from agent.collaboration.activity import get_activity_registry
        from agent.collaboration.team import parse_agent_id
        from agent.collaboration.teammate_registry import get_teammate_registry

        loop = getattr(engine, "_loop", None)
        if loop is None or loop.is_closed():
            return {
                "ok": False,
                "action": "stop",
                "message": "引擎还没启动，无法执行停止",
                **build_agents_payload(engine),
            }

        record = next(
            (a for a in get_activity_registry().snapshot() if a.get("agent_id") == agent_id),
            None,
        )
        if record is None:
            return {
                "ok": False,
                "action": "stop",
                "message": f"没有在运行的代理：{agent_id}",
                **build_agents_payload(engine),
            }

        if str(record.get("mode")) == "teammate":
            name, team = parse_agent_id(agent_id)
            runner = get_teammate_registry().get(team, name)
            if runner is None:
                get_activity_registry().finish_agent(agent_id, status="stopped", output="已停止")
                return {
                    "ok": True,
                    "action": "stop",
                    "message": f"队友 {name} 已不在本进程内运行，已标记为停止",
                    **build_agents_payload(engine),
                }
            asyncio.run_coroutine_threadsafe(runner.shutdown(), loop)
            return {
                "ok": True,
                "action": "stop",
                "message": f"已请求队友 {name} 停止（shutdown_request）",
                **build_agents_payload(engine),
            }

        task = get_activity_registry().task_for(agent_id)
        if task is None or task.done():
            get_activity_registry().finish_agent(agent_id, status="stopped", output="已停止")
            return {
                "ok": True,
                "action": "stop",
                "message": f"子代理 {agent_id} 已结束，已标记为停止",
                **build_agents_payload(engine),
            }
        # Task.cancel 必须回到引擎 loop 上执行（台账写入线程 ≠ loop 线程）。
        loop.call_soon_threadsafe(task.cancel)
        return {
            "ok": True,
            "action": "stop",
            "message": f"已请求停止子代理 {agent_id}",
            **build_agents_payload(engine),
        }

    def _agents_message(self, engine: Any, agent_id: str, text: str) -> dict:
        """给后台队友发补充指令（同步子代理没有邮箱，直接拒绝并说明）。"""
        from agent.collaboration.team import parse_agent_id
        from agent.collaboration.teammate_registry import get_teammate_registry

        name, team = parse_agent_id(agent_id)
        runner = get_teammate_registry().get(team, name)
        if runner is None:
            return {
                "ok": False,
                "action": "message",
                "message": f"队友 {agent_id} 不在本进程内运行，无法发送指令",
                **build_agents_payload(engine),
            }
        loop = getattr(engine, "_loop", None)
        if loop is None or loop.is_closed():
            return {
                "ok": False,
                "action": "message",
                "message": "引擎还没启动，无法发送指令",
                **build_agents_payload(engine),
            }
        loop.call_soon_threadsafe(runner.send_message, text)
        return {
            "ok": True,
            "action": "message",
            "message": f"已把指令投递给队友 {name}",
            **build_agents_payload(engine),
        }
