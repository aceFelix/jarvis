"""TrackGoal 工具 —— 让模型能把"替我盯着这件事"落成跨会话目标（W3.1 入口）。

为什么必须有它（阶段 10 收尾切片，2026-10-21）：脊柱后端上一切片就交付了，但**没有任何
模型侧入口**，用户说"等张三的邮件到了再把附件归档"，模型只能回一句"好的"然后什么都不发生。
这个工具把 :mod:`agent.core.tasks.spine_ops` 暴露给模型，一句话就能写出台账，
剩下的等待与推进由后台驱动器负责（等待期间一次 LLM 都不调）。

与 ``TodoWrite`` 的分工要说清，否则模型会拿它当清单用：

- ``TodoWrite`` = **本轮**的工作清单，会话结束就没了，纯展示；
- ``TrackGoal`` = **跨会话**的目标，条件满足才推进一步，会真的动手做事、会花钱。

安全口径：``delete`` 需要用户确认（ASK）——后台子代理不该悄悄抹掉用户的台账记录；
其余动作只写 ``~/.jarvis/goals/``，不外发、不改工作区，与 ``TodoWrite`` 同级放行。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool

#: 只有这一类动作是只读的（决定并发安全与权限放行口径，别靠字符串散落判断）
_READ_ONLY_ACTIONS = ("list", "show")

_ALL_ACTIONS = ("create", "list", "show", "pause", "resume", "cancel", "advance", "delete")


class TrackGoalTool(Tool):
    name = "TrackGoal"
    description = (
        "管理跨会话目标（任务脊柱台账）：创建、查看、暂停、恢复、取消、跳过等待、删除。"
        "只在用户要「替我盯着…条件到了再往下做」「等 X 之后再 Y」这类**需要等待**的诉求时使用；"
        "本轮就能做完的事直接用对应工具，不要建目标。"
        "目标在等待期间不消耗模型调用，条件满足后由后台驱动器一步步执行并经打扰闸门播报。"
        f"action 可选: {'/'.join(_ALL_ACTIONS)}。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(_ALL_ACTIONS), "description": "要做的操作"},
            "goal": {
                "type": "string",
                "description": "create 必填：目标描述（用户原话整理成一句话）。可用分号追加步骤。",
            },
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "create 可选：有序步骤文本；省略时按 goal 里的分号拆分，再省略就单步。",
            },
            "wait": {
                "type": "object",
                "description": (
                    "create 可选：第一步开始前要满足的条件。"
                    "kind=time 给 at（+90s/+2h 或 2026-10-21T18:30）；"
                    "kind=file_exists 给 path 与 mode(exists|gone)；"
                    "kind=percept_event 给 source（mail/window/vcs）与可选 event。"
                ),
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["time", "file_exists", "percept_event"],
                    },
                    "at": {"type": "string"},
                    "path": {"type": "string"},
                    "mode": {"type": "string", "enum": ["exists", "gone"]},
                    "source": {"type": "string"},
                    "event": {"type": "string"},
                },
            },
            "deadline_hours": {
                "type": "number",
                "description": "wait 的最长等待小时数；0 或省略=按配置 [tasks].wait_deadline_hours。",
            },
            "goal_id": {
                "type": "string",
                "description": "非 create 动作必填：目标 id（支持前缀匹配）。",
            },
        },
        "required": ["action"],
    }
    max_result_chars = 4_000

    # ---- 权限与并发 ----

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return str(args.get("action") or "") in _READ_ONLY_ACTIONS

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        # 台账是文件锁内的读-改-写，且"建目标"后紧跟唤醒驱动器：并行会互相看见半成品
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """写本机台账不需要确认，**删除**需要。

        理由：目标里可能挂着"到点自动发邮件"这种已经排好的动作，悄悄删掉会让用户
        以为还在盯着；而 create/pause/cancel 都只是往台账里加东西或停下，不丢历史。
        @author aceFelix
        """
        action = str(args.get("action") or "")
        if action == "delete":
            return PermissionResult.ask("删除目标会连同它挂着的等待与预约一起抹掉，请确认")
        return PermissionResult.allow(f"任务脊柱台账操作：{action}")

    # ---- 校验 ----

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        action = str(args.get("action") or "").strip().lower()
        if action not in _ALL_ACTIONS:
            return ValidationResult.fail(
                f"action 无效：{action or '（空）'}（可用 {'/'.join(_ALL_ACTIONS)}）"
            )
        if action == "create" and not str(args.get("goal") or "").strip():
            return ValidationResult.fail("create 必须给 goal（一句话说清要盯什么）")
        if action not in ("create", "list") and not str(args.get("goal_id") or "").strip():
            return ValidationResult.fail(f"{action} 必须给 goal_id")
        return ValidationResult.pass_()

    # ---- 执行 ----

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """按 action 分发到 spine_ops，并把结果写成模型能直接接话的一行 + 明细。

        返回文本刻意带上"驱动器现在在不在跑"：模型据此如实告诉用户"会自己往下走"
        还是"要你先开 [tasks] enabled"，而不是建完目标就假装已经在盯了。
        @author aceFelix
        """
        from agent.core.tasks.spine_ops import (
            control_goal,
            create_goal,
            goal_detail,
            snapshot,
        )

        action = str(args.get("action") or "").strip().lower()
        store_note = _driver_note(snapshot())

        if action == "list":
            data = snapshot(limit=20)
            if not data["goals"]:
                return ToolResult.ok("跨会话目标为空。" + store_note)
            lines = [f"跨会话目标 {data['count']} 个："]
            lines += [f"  {g['summary']}  〔{g['id']}〕" for g in data["goals"]]
            lines.append(store_note.strip("； "))
            return ToolResult.ok("\n".join(lines))

        if action == "show":
            from agent.core.tasks.spine import get_spine_store

            from agent.core.tasks.spine_ops import find_goal

            goal = find_goal(get_spine_store(), str(args.get("goal_id") or ""))
            if goal is None:
                return ToolResult.error(f"没有目标：{args.get('goal_id')}（定局的目标也已归档，用 /tasks delete 才能抹掉）")
            return ToolResult.ok(_render_goal(goal_detail(goal)) + store_note)

        if action == "create":
            hours = _deadline_hours(args, ctx)
            # wait 接受两种写法：结构化 dict（主流）或一个字符串（与 /tasks add 同形）
            wait, err = _resolve_wait(args.get("wait"), deadline_hours=hours)
            if err:
                return ToolResult.error(f"等待条件无效：{err}")
            steps = args.get("steps")
            if steps is not None and not isinstance(steps, list):
                return ToolResult.error("steps 必须是字符串数组")
            res = create_goal(
                str(args.get("goal") or ""),
                steps=[str(s) for s in steps] if steps else None,
                wait=wait,
                workdir=str(getattr(ctx, "workdir", "") or ""),
                session_id=str(getattr(ctx, "session_name", "") or ""),
            )
            if not res.ok:
                return ToolResult.error(res.message)
            return ToolResult.ok(res.message + store_note + "（把目标 id 原样告诉用户，方便他喊停）")

        res = control_goal(str(args.get("goal_id") or ""), action)
        if not res.ok:
            return ToolResult.error(res.message)
        return ToolResult.ok(res.message + store_note)

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if not args:
            return None
        action = str(args.get("action") or "")
        if action == "create":
            return f"建立跨会话目标：{str(args.get('goal') or '')[:24]}"
        return f"任务脊柱操作：{action} {str(args.get('goal_id') or '')}"


def _resolve_wait(raw: Any, *, deadline_hours: float) -> tuple[Any, str]:
    """``wait`` 参数的两种写法都收：dict 走 :func:`wait_from_spec`，字符串走 :func:`parse_wait`。

    为什么两种都接：模型偶尔会把结构化字段写成 ``"time:+2h"`` 这种串（跟它看到的
    工具描述里那个形态很像），报参数类型错不如直接解析成同一种 TaskWait。
    @author aceFelix
    """
    from agent.core.tasks.spine_ops import parse_wait, wait_from_spec

    if raw is None or raw == "":
        return None, ""
    if isinstance(raw, str):
        return parse_wait(raw, deadline_hours=deadline_hours)
    if isinstance(raw, dict):
        return wait_from_spec(raw, deadline_hours=deadline_hours)
    return None, "wait 必须是对象或字符串（如 {\"kind\":\"time\",\"at\":\"+2h\"} 或 \"time:+2h\"）"


def _deadline_hours(args: dict[str, Any], ctx: ToolContext) -> float:
    """本次等待的兜底时限：参数给了用参数，否则读配置。"""
    raw = args.get("deadline_hours")
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    settings = getattr(ctx, "settings", None)
    return float(getattr(settings, "tasks_wait_deadline_hours", 72) or 72)


def _driver_note(data: dict[str, Any]) -> str:
    """驱动器状态说明（三个入口共用一句，避免各处口径不同）。"""
    if data.get("driver_running"):
        return "；后台驱动线程在跑，条件满足会自动往下推"
    return "；注意：后台驱动线程未在跑（[tasks] enabled=false 或未装配），目标不会自动推进，请如实告知用户"


def _render_goal(detail: dict[str, Any]) -> str:
    """把一个目标明细渲染成几行文本（工具返回给模型看）。"""
    lines = [f"目标〔{detail['id']}〕：{detail['goal']}（{detail['status']}，{detail['progress']}）"]
    for i, s in enumerate(detail.get("steps") or [], start=1):
        wait = f"｜等 {s['wait']}" if s.get("wait") else ""
        err = f"｜上次失败：{s['last_error'][:40]}" if s.get("last_error") else ""
        lines.append(f"  {i}. {s['state']} {s['desc'][:60]}{wait}{err}")
    ids = [j for s in (detail.get("steps") or []) for j in (s.get("journal_ids") or [])]
    if ids:
        lines.append(f"  已产生副作用 {len(ids)} 项，可用 /undo --task {detail['id']} 回收")
    return "\n".join(lines)
