"""W3 兑现层的桌面指令（``tasks.exec`` / ``actions.exec``）—— RPC 混入。

两条指令（aceFelix，2026-10 阶段 10 收尾切片）。刻意各用一条通用指令承载多个动作，
和 ``extensions.exec`` 同一个理由：一动作一指令会把协议常量撑爆，而"任务中心"面板
本来就是一次拼装、一次回传：

- ``tasks.exec``：``status`` / ``create`` / ``show`` / ``pause`` / ``resume`` /
  ``cancel`` / ``advance`` / ``delete``——跨会话目标台账的查看与管控，业务语义全部
  复用 :mod:`agent.core.tasks.spine_ops`（与终端 ``/tasks`` 子命令、``TrackGoal``
  工具同一份实现）；
- ``actions.exec``：``status`` / ``list`` / ``undo`` / ``cancel_hold`` / ``undo_task``
  ——副作用账本的查看、撤回窗口内动作、撤销已执行副作用与按目标批量撤销。

安全口径（比"面板能用"更重要）：

1. **``undo`` 必须 ``confirm=true`` 才真回滚**。文件类撤销的粒度是**整轮**，会把本轮
   其他文件改动一并退掉，所以未带 confirm 时只回影响面预览（``preview``），面板据此
   做"先看清单再点确认"两拍。这与终端 ``/undo`` 读 y/N 是同一约束的两种实现，
   不是"桌面比终端宽松"。
2. **``cancel_hold`` 不需要 confirm**：它撤的是"还没执行"的预约，做的是"不做"，
   没有任何已发生的东西要回滚。
3. **``delete`` 需要 confirm=true**：删目标会连同它挂着的等待与预约一起抹掉。
   未带 confirm 时把目标明细（``goal``）一并回传，面板才列得出「要删的到底是哪个」。
4. 读侧永不抛异常：账本没启用、台账读不到都照常返回空快照与说明字段，面板显示
   "未启用"而不是报错（与 ``percept.status.get`` 同一条底线）。

依赖宿主类（``DesktopBridgeServer``）提供：``_settings`` / ``_hub``。

@author aceFelix
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agent.serve.rpc_percept import TRANSPORT_ENVELOPE_KEYS

if TYPE_CHECKING:  # 仅为 IDE/类型检查声明宿主属性，运行期不引入循环导入
    from agent.config.settings import Settings

logger = logging.getLogger("jarvis.serve.rpc_tasks")

#: tasks.exec 支持的动词（面板与测试都以此为准，写错直接报错而不是静默忽略）
TASK_ACTIONS = ("status", "create", "show", "pause", "resume", "cancel", "advance", "delete")
ACTION_ACTIONS = ("status", "list", "undo", "cancel_hold", "undo_task")


def _payload(data: dict[str, Any]) -> dict[str, Any]:
    """剥掉 WS 传输层信封键（``type`` / ``id``），只留业务参数。

    与 ``percept.config.set`` 同一个坑：请求帧是平铺结构，信封键混在参数里，
    不剥掉就会把 ``type`` 当成业务字段校验（2026-10 人工走查抓到过一次）。
    @author aceFelix
    """
    return {k: v for k, v in (data or {}).items() if k not in TRANSPORT_ENVELOPE_KEYS}


class TasksRpcMixin:
    """``tasks.exec`` / ``actions.exec`` 两个指令的处理器。

    @author aceFelix
    """

    if TYPE_CHECKING:
        _settings: "Settings"
        _hub: Any

    # ---- tasks.exec ----

    def _rpc_tasks_exec(self, data: dict) -> dict:
        """tasks.exec：目标台账的查看与管控（一条指令承载八个动词）。

        返回值一律带 ``driver_running``：面板要能区分"目标建好了但没人推"与
        "驱动器正在盯"，这两件事对用户是不同结论（前者要开 ``[tasks] enabled``）。

        @author aceFelix
        """
        from agent.core.tasks.spine_ops import snapshot

        payload = _payload(data)
        action = str(payload.get("action") or "status").strip().lower()
        if action not in TASK_ACTIONS:
            raise ValueError(f"tasks.exec 未知动作：{action}（可用 {'/'.join(TASK_ACTIONS)}）")

        base = snapshot(limit=_as_int(payload.get("limit"), 20))
        base["tasks_config"] = self._tasks_config()

        if action == "status":
            return {"ok": True, "action": action, **base}
        if action == "create":
            return self._tasks_create(payload, base)
        if action == "show":
            return self._tasks_show(payload, base)
        return self._tasks_control(payload, action, base)

    def _tasks_create(self, payload: dict[str, Any], base: dict[str, Any]) -> dict:
        """tasks.exec create：建目标（参数与 ``TrackGoal`` 完全同形）。"""
        from agent.core.tasks.spine_ops import create_goal

        goal_text = str(payload.get("goal") or "").strip()
        if not goal_text:
            raise ValueError("tasks.exec create 必须给 goal")
        wait, err = _resolve_wait(payload.get("wait"), deadline_hours=_deadline_hours(payload, self._settings))
        if err:
            raise ValueError(err)
        steps = payload.get("steps")
        if steps is not None and not isinstance(steps, list):
            raise ValueError("steps 必须是字符串数组")
        res = create_goal(
            goal_text,
            steps=[str(s) for s in steps] if steps else None,
            wait=wait,
            workdir=str(getattr(self._settings, "workdir", "") or ""),
            session_id="serve",
        )
        # 目标明细随回执一起回：面板拿结构化 goal，而不是从 message 里截 id
        return {"action": "create", **res.to_dict(), **base}

    def _tasks_show(self, payload: dict[str, Any], base: dict[str, Any]) -> dict:
        """tasks.exec show：单个目标明细（定局的目标也能查，不只列活跃那一屏）。"""
        from agent.core.tasks.spine import get_spine_store
        from agent.core.tasks.spine_ops import find_goal, goal_detail

        goal_id = str(payload.get("goal_id") or "").strip()
        if not goal_id:
            raise ValueError("tasks.exec show 必须给 goal_id")
        goal = find_goal(get_spine_store(), goal_id)
        if goal is None:
            return {"ok": False, "action": "show", "message": f"没有目标：{goal_id}", **base}
        return {"ok": True, "action": "show", "goal": goal_detail(goal), **base}

    def _tasks_control(self, payload: dict[str, Any], action: str, base: dict[str, Any]) -> dict:
        """tasks.exec 的管控动作（pause/resume/cancel/advance/delete）。"""
        from agent.core.tasks.spine import get_spine_store
        from agent.core.tasks.spine_ops import control_goal, find_goal, goal_detail

        goal_id = str(payload.get("goal_id") or "").strip()
        if not goal_id:
            raise ValueError(f"tasks.exec {action} 必须给 goal_id")
        # 删除是唯一"抹掉历史"的动作，与终端不同之处仅在于确认由面板按钮承担
        if action == "delete" and payload.get("confirm") is not True:
            goal = find_goal(get_spine_store(), goal_id)
            preview = {"action": action, "ok": False, "need_confirm": True,
                       "message": "删除目标会连同它挂着的等待与预约一起抹掉，请带 confirm=true 重试",
                       **base}
            # 带上前面的明细，面板才能做"看清楚要删的是哪个"的两拍（查不到就照实不带）
            if goal is not None:
                preview["goal"] = goal_detail(goal)
            return preview
        res = control_goal(goal_id, action)
        return {"action": action, **res.to_dict(), **base}

    def _tasks_config(self) -> dict[str, Any]:
        """`[tasks]` 里面板要显示的开关（只回开关与阈值，不回敏感项）。"""
        s = self._settings
        return {
            "enabled": bool(getattr(s, "tasks_enabled", False)),
            "wait_deadline_hours": getattr(s, "tasks_wait_deadline_hours", 72),
            "step_max_attempts": getattr(s, "tasks_step_max_attempts", 3),
            "retry_backoff_s": getattr(s, "tasks_retry_backoff_s", 60),
            "blocked_ask_days": getattr(s, "tasks_blocked_ask_days", 3),
        }

    # ---- actions.exec ----

    def _rpc_actions_exec(self, data: dict) -> dict:
        """actions.exec：副作用账本的查看、撤回预约与撤销已执行副作用。

        @author aceFelix
        """
        from agent.core.actions import get_journal

        payload = _payload(data)
        action = str(payload.get("action") or "status").strip().lower()
        if action not in ACTION_ACTIONS:
            raise ValueError(f"actions.exec 未知动作：{action}（可用 {'/'.join(ACTION_ACTIONS)}）")

        journal = get_journal()
        base = self._actions_snapshot(journal, limit=_as_int(payload.get("limit"), 20))
        if not journal.enabled:
            # 账本关着就到此为止：面板显示"未启用"，不给一个永远为空的撤销按钮
            return {"ok": True, "action": action, "message": "副作用账本未启用（[actions].journal_enabled=false）", **base}

        if action in ("status", "list"):
            return {"ok": True, "action": action, **base}
        if action == "cancel_hold":
            return self._actions_cancel_hold(payload, journal, base)
        if action == "undo_task":
            return self._actions_undo_task(payload, journal, base)
        return self._actions_undo(payload, journal, base)

    def _actions_snapshot(self, journal: Any, *, limit: int) -> dict[str, Any]:
        """账本快照：计数 + 在窗预约 + 最近记录 + `[actions]` 配置。

        在窗条目额外带 ``labels``：那是用户唯一能看懂"什么被扣住了"的线索，
        而窗口登记表本身就存了展示文本，不必为取一个名字去重读整本账。
        @author aceFelix
        """
        from agent.core.actions import STATE_HELD, get_window

        window = get_window()
        held = [r.to_dict() for r in journal.held_records()][:limit]
        return {
            "enabled": bool(journal.enabled),
            "counts": journal.counts(),
            "held": held,
            "held_labels": window.pending_labels(),
            "window_size": window.size,
            "recent": [r.to_dict() for r in journal.recent(limit)],
            "actions_config": {
                "undo_window_s": getattr(self._settings, "actions_undo_window_s", 30),
                "track_local_writes": bool(getattr(self._settings, "actions_track_local_writes", True)),
                "hold_blocks_equivalent": bool(getattr(self._settings, "actions_hold_blocks_equivalent", True)),
                "irreversible_tools": list(getattr(self._settings, "actions_irreversible_tools", []) or []),
            },
        }

    def _actions_undo(self, payload: dict[str, Any], journal: Any, base: dict[str, Any]) -> dict:
        """undo：先给影响面，``confirm=true`` 才真回滚（整轮粒度，必须两拍）。"""
        from agent.core.actions import STATE_APPLIED, describe_undo, manager_for, undo_record

        rec = _pick_record(journal, str(payload.get("id") or ""), (STATE_APPLIED,))
        if rec is None:
            return {"ok": False, "action": "undo", "message": "没有可撤销的已执行副作用（待发项请用 cancel_hold）", **base}
        if not rec.undoable:
            return {"ok": False, "action": "undo", "message": f"{rec.label} 无法撤销 — {describe_undo(rec)}", **base}

        session_name = "serve"
        preview: dict[str, Any] = {}
        mgr = manager_for(rec.workdir, self._settings, session_name)
        cid = str(rec.undo_spec.get("cid", ""))
        desc = mgr.describe(cid) if mgr.available() else None
        if desc is None:
            return {"ok": False, "action": "undo", "message": f"{rec.label} 的本轮检查点已不可用（缺 git 或已被配额修剪）", **base}
        preview = {
            "files": (desc.get("files") or [])[:30],
            "untracked_count": len(desc.get("untracked") or []),
            "workdir": rec.workdir,
            "hint": "整轮回滚：该轮的其他文件改动会一并退掉",
        }
        if payload.get("confirm") is not True:
            return {"ok": True, "action": "undo", "need_confirm": True, "preview": preview,
                    "message": "未执行：这是整轮回滚，请确认后带 confirm=true 重试", **base}

        ok, note = undo_record(rec, settings=self._settings, session_name=session_name)
        return {"ok": ok, "action": "undo", "message": note, "preview": preview, **base}

    def _actions_cancel_hold(self, payload: dict[str, Any], journal: Any, base: dict[str, Any]) -> dict:
        """cancel_hold：撤回还在窗口里的预约（"不做"不需要二次确认）。"""
        from agent.core.actions import STATE_CANCELLED, STATE_HELD, get_window

        rec = _pick_record(journal, str(payload.get("id") or ""), (STATE_HELD,))
        if rec is None:
            return {"ok": False, "action": "cancel_hold", "message": "当前没有还在延时窗口里待发的动作", **base}
        get_window().cancel(rec.id)
        journal.set_state(rec.id, STATE_CANCELLED, error="桌面面板撤回（actions.exec cancel_hold）")
        return {"ok": True, "action": "cancel_hold", "message": f"已撤回，未执行：{rec.label}", **base}

    def _actions_undo_task(self, payload: dict[str, Any], journal: Any, base: dict[str, Any]) -> dict:
        """undo_task：按目标批量补偿，逐项报"撤了 / 撤不动 + 原因"。"""
        from agent.core.actions import undo_task

        task_id = str(payload.get("goal_id") or payload.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("actions.exec undo_task 必须给 goal_id")
        if payload.get("confirm") is not True:
            records = journal.task_records(task_id)
            return {
                "ok": True, "action": "undo_task", "need_confirm": True,
                "message": f"目标 {task_id} 名下有 {len(records)} 项已执行副作用，确认后带 confirm=true 再批量撤销",
                "preview": [r.to_dict() for r in records][:30], **base,
            }
        results = undo_task(task_id, settings=self._settings, session_name="serve")
        lines = [f"{'已撤销' if o else '撤不动'}：{rec.label} — {note}" for rec, o, note in results]
        ok = sum(1 for _r, o, _n in results if o)
        return {
            "ok": bool(results), "action": "undo_task",
            "message": f"目标 {task_id} 的副作用 {len(results)} 项，撤销成功 {ok} 项",
            "items": lines, **base,
        }


def _pick_record(journal: Any, arg: str, states: tuple[str, ...]) -> Any:
    """给了 id 就精确取（支持前缀），没给就取最近一条符合状态的。"""
    if arg:
        rec = journal.get(arg)
        if rec is not None:
            return rec
        # 面板上的 id 常被截断显示，前缀匹配让"点一条撤一条"不必复制全串
        prefix = [r for r in journal.recent(50) if r.id.startswith(arg)]
        return prefix[0] if len(prefix) == 1 else None
    return journal.last(*states)


def _as_int(raw: Any, default: int) -> int:
    """参数取整：非数字/负数一律回落默认，不把面板的脏值变成异常。"""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _resolve_wait(raw: Any, *, deadline_hours: float) -> tuple[Any, str]:
    """``wait`` 支持 dict 与字符串两种写法（与 ``TrackGoal`` 保持一致）。"""
    from agent.core.tasks.spine_ops import parse_wait, wait_from_spec

    if raw is None or raw == "":
        return None, ""
    if isinstance(raw, str):
        return parse_wait(raw, deadline_hours=deadline_hours)
    if isinstance(raw, dict):
        return wait_from_spec(raw, deadline_hours=deadline_hours)
    return None, "wait 必须是对象或字符串"


def _deadline_hours(payload: dict[str, Any], settings: Any) -> float:
    """等待兜底时限：参数优先，否则读配置（与工具层同一口径，不各写一份）。"""
    raw = payload.get("deadline_hours")
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    return float(getattr(settings, "tasks_wait_deadline_hours", 72) or 72)
