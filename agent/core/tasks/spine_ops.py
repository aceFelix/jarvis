"""W3.1 任务脊柱的**写入与管控**通路（spine_ops）。

为什么要有这一层（阶段 10 收尾切片，2026-10-21）：脊柱的后端在上一切片就交付了，
但**没有任何入口能把目标写进去**——`/tasks` 第二节只能读、驱动器只能推已有的台账，
实机上永远是空的，用户说"替我盯着这件事"没有承载体。这一层把「建目标 / 挂等待条件 /
暂停恢复取消 / 立刻推进」收在一处，让三个入口共用同一套语义：

- `/tasks <子命令>`（终端，用户不经模型就能测）——`commands/handlers/collab_commands.py`
- `TrackGoal` 工具（模型侧，"替我盯着…"一句话就落台账）——`tools/track_goal.py`
- `tasks.exec` / `actions.exec` RPC（桌面「任务中心」）——`serve/rpc_tasks.py`

三处各自实现一遍必然漂移（尤其是等待条件的解析与"取消该不该清 wait"这类取舍），
所以业务规则只写在本文件里，入口层只做参数搬运与呈现。

分层口径：本模块只依赖 `spine.py`（数据与落盘）与 `driver.wake_driver`（唤醒），
**不 import UI / 命令 / serve**，也不自己起线程、不调 LLM。

@author aceFelix
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from agent.core.tasks.spine import (
    BLOCK_WAIT,
    GOAL_ACTIVE,
    GOAL_CANCELLED,
    GOAL_PAUSED,
    STEP_BLOCKED,
    STEP_PENDING,
    TaskGoal,
    TaskWait,
    WAIT_FILE_EXISTS,
    WAIT_PERCEPT_EVENT,
    WAIT_TIME,
    get_spine_store,
    iso_of,
    parse_iso,
)

#: 相对时间写法：``+90s`` / ``+10m`` / ``+2h`` / ``+1d``（实机走查最常用的形态）
_RELATIVE_RE = re.compile(r"^\+(\d+)([smhd])$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

#: 步骤分隔符：中英文分号都认（命令行里一次写多步比多次敲命令省事）
_STEP_SPLIT_RE = re.compile(r"[;；]")

#: 目标管控的合法动作。定局（done/failed/cancelled）的目标只允许 delete，
#: 其余动作一律拒绝——把已做完的目标改回活跃会让驱动器重跑已经发出去的事。
CONTROL_ACTIONS = ("pause", "resume", "cancel", "advance", "delete")


@dataclass
class OpResult:
    """一次脊柱操作的归一结果。

    三个入口要的东西不一样：命令层要 ``message``（直接打印）、工具层要
    ``message + goal``（回给模型看 id），RPC 层要 ``to_dict()``（整个 JSON 回传）。
    归成一个形状，避免出现"命令里成功、RPC 里报失败"的双口径。

    @author aceFelix
    """

    ok: bool
    message: str = ""
    goal: TaskGoal | None = None
    goals: list[TaskGoal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转成可 JSON 序列化的回执（RPC 与工具返回都用它）。"""
        data: dict[str, Any] = {"ok": self.ok, "message": self.message}
        if self.goal is not None:
            data["goal"] = goal_detail(self.goal)
        if self.goals:
            data["goals"] = [goal_detail(g) for g in self.goals]
        return data


# ---- 文本解析：命令行/模型参数 → 结构化等待条件 ----

def split_steps(text: str) -> list[str]:
    """把「目标 + 分号分隔的步骤」拆成步骤数组。

    没写分号就当成**一个步骤**（步骤文本等于目标本身）：单步目标是实机测试的
    主要形态，"只写目标、步骤让模型自己拆"会让台账里出现零步骤的目标，
    驱动器看 ``current_step() is None`` 就直接跳过——用户会以为功能坏了。
    @author aceFelix
    """
    parts = [p.strip() for p in _STEP_SPLIT_RE.split(str(text or ""))]
    return [p for p in parts if p]


def parse_wait(raw: str, *, deadline_hours: float = 72.0, now: float | None = None) -> tuple[TaskWait | None, str]:
    """解析等待条件串。返回 ``(TaskWait 或 None, 错误说明)``。

    支持的形态（前缀决定 kind，冒号后是参数）：

    - ``time:+90s`` / ``time:+2h`` / ``time:2026-10-21T18:30`` → 等到点
    - ``file:<路径>`` 等文件出现；``gone:<路径>`` 等文件消失
    - ``event:<source>/<kind>`` 等一个感知事件（kind 可省，写成 ``event:mail`` 也行）

    ``time`` 类不补 deadline：它本身就是确定时刻，到点必走；其余两类必须有兜底时限，
    否则"等一个永远不来的文件"会挂着目标无限期唤醒（超时转 blocked_timeout 来问人
    才是对的）。``deadline_hours<=0`` 表示调用方要求不设限，照实尊重。

    @author aceFelix
    """
    current = now if now is not None else time.time()
    text = str(raw or "").strip()
    if not text:
        return None, ""
    if ":" not in text:
        return None, f"等待条件要写成 time:+90s / file:<路径> / event:<source>/<kind>，收到：{text}"

    head, _, rest = text.partition(":")
    head = head.strip().lower()
    rest = rest.strip()
    if not rest:
        return None, f"等待条件 {head} 缺参数：{text}"

    if head == "time":
        at = _resolve_time(rest, current)
        if not at:
            return None, f"时间解析不出来：{rest}（可用 +90s/+2h 或 2026-10-21T18:30）"
        if at <= current:
            return None, f"等待时间已过期（{iso_of(at)}），请给将来的时刻"
        return TaskWait(kind=WAIT_TIME, spec={"at": iso_of(at)}, deadline_at=0.0), ""

    if head in ("file", "gone"):
        deadline = current + deadline_hours * 3600.0 if deadline_hours > 0 else 0.0
        return (
            TaskWait(
                kind=WAIT_FILE_EXISTS,
                spec={"path": rest, "mode": "gone" if head == "gone" else "exists"},
                deadline_at=deadline,
            ),
            "",
        )

    if head == "event":
        source, _, ev_kind = rest.partition("/")
        deadline = current + deadline_hours * 3600.0 if deadline_hours > 0 else 0.0
        return (
            TaskWait(
                kind=WAIT_PERCEPT_EVENT,
                spec={"source": source.strip(), "kind": ev_kind.strip()},
                deadline_at=deadline,
            ),
            "",
        )

    return None, f"未知等待类型：{head}（只支持 time / file / gone / event）"


def wait_from_spec(spec: dict[str, Any] | None, *, deadline_hours: float = 72.0, now: float | None = None) -> tuple[TaskWait | None, str]:
    """从结构化 dict 构造等待条件（模型工具与 RPC 走这条，不逼调用方拼字符串）。

    认字段不认位置：``{"kind":"time","at":"+90s"|"2026-10-21T18:30"}``、
    ``{"kind":"file_exists","path":"...","mode":"exists|gone"}``、
    ``{"kind":"percept_event","source":"mail","event":"mail_received"}``。
    与 :func:`parse_wait` 共用同一套落库形状，避免两种入口写出两种台账。

    @author aceFelix
    """
    current = now if now is not None else time.time()
    if not spec:
        return None, ""
    kind = str(spec.get("kind") or "").strip().lower()
    if kind in ("", "none"):
        return None, ""

    if kind in (WAIT_TIME, "time"):
        raw = str(spec.get("at") or spec.get("time") or "").strip()
        if not raw:
            return None, "time 类等待必须给 at（+90s 或 ISO 时刻）"
        at = _resolve_time(raw, current)
        if not at:
            return None, f"时间解析不出来：{raw}"
        if at <= current:
            return None, f"等待时间已过期（{iso_of(at)}），请给将来的时刻"
        return TaskWait(kind=WAIT_TIME, spec={"at": iso_of(at)}, deadline_at=0.0), ""

    if kind in (WAIT_FILE_EXISTS, "file", "file_exists"):
        path = str(spec.get("path") or "").strip()
        if not path:
            return None, "file_exists 类等待必须给 path"
        mode = str(spec.get("mode") or "exists").strip().lower()
        if mode not in ("exists", "gone"):
            return None, f"mode 只能是 exists 或 gone，收到：{mode}"
        deadline = current + deadline_hours * 3600.0 if deadline_hours > 0 else 0.0
        return TaskWait(kind=WAIT_FILE_EXISTS, spec={"path": path, "mode": mode}, deadline_at=deadline), ""

    if kind in (WAIT_PERCEPT_EVENT, "event", "percept_event"):
        source = str(spec.get("source") or "").strip()
        if not source:
            return None, "percept_event 类等待必须给 source（如 mail/window/vcs）"
        ev = str(spec.get("event") or spec.get("event_kind") or "").strip()
        deadline = current + deadline_hours * 3600.0 if deadline_hours > 0 else 0.0
        return TaskWait(kind=WAIT_PERCEPT_EVENT, spec={"source": source, "kind": ev}, deadline_at=deadline), ""

    return None, f"未知等待类型：{kind}（支持 time / file_exists / percept_event）"


def _resolve_time(raw: str, current: float) -> float:
    """把 ``+90s`` 这类相对写法与各种 ISO 写法统一成 unix 秒；解不出返回 0。"""
    m = _RELATIVE_RE.match(raw)
    if m:
        return current + int(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]
    return parse_iso(raw)


# ---- 写入：建目标 ----

def create_goal(
    goal_text: str,
    *,
    steps: list[str] | None = None,
    wait: TaskWait | None = None,
    workdir: str = "",
    session_id: str = "",
    store: Any | None = None,
) -> OpResult:
    """新建一个目标台账并唤醒驱动器。

    步骤来源优先级：显式 ``steps`` > ``goal_text`` 里的分号分段 > 拿目标本身当唯一
    步骤（见 :func:`split_steps` 的理由）。等待条件挂在**第一个未完成步骤**上：
    "先等、再动手"是唯一有物理意义的顺序，挂到后面的步骤上会看起来没生效。

    @author aceFelix
    """
    text = str(goal_text or "").strip()
    if not text:
        return OpResult(ok=False, message="目标文本不能为空")

    store = store or get_spine_store()
    segs = [str(s).strip() for s in (steps or []) if str(s).strip()] or split_steps(text) or [text]
    goal = store.add_goal(text.split(";")[0].split("；")[0].strip() or text, segs, workdir=workdir, session_id=session_id)
    if goal is None:
        # 落盘失败必须照实说：假装建好了，用户会一直等一个不存在的目标
        return OpResult(ok=False, message="台账落盘失败（检查 ~/.jarvis/ 是否可写）")

    if wait is not None:
        head = goal.steps[0] if goal.steps else None
        if head is not None:
            head.wait = wait
            head.block_reason = BLOCK_WAIT
            store.save(goal)

    _wake(f"新建目标 {goal.id}")
    tail = f"，先等 {wait.kind}" if wait is not None else ""
    return OpResult(
        ok=True,
        message=f"已建立目标〔{goal.id}〕：{goal.goal[:40]}（{len(goal.steps)} 步{tail}）",
        goal=goal,
    )


# ---- 管控：暂停 / 恢复 / 取消 / 立刻推进 / 删除 ----

def control_goal(goal_id: str, action: str, *, store: Any | None = None) -> OpResult:
    """按 ``action`` 管控一个目标；id 支持前缀匹配（台账里 id 很长，手敲不便）。

    四条取舍写清楚，免得下一个人改：

    1. **终态目标一律拒绝**：done/failed/cancelled 之后再"恢复"会让驱动器重跑
       已经做完的事（重复外发是最坏结果）；
    2. **cancel 不清 wait**：取消只是不再推进，保留现场便于 `/tasks show` 复盘
       "当初在等什么"；
    3. **advance 清 wait 并把该步改回 pending**：这是给人测的绕行口子（不必真等
       90 秒），只跳过等待条件，不改步骤内容，也不谎称条件已满足；
    4. **delete 是硬删**：本期不做软删归档（见 18 号文档 §十），删掉就没了。

    @author aceFelix
    """
    if action not in CONTROL_ACTIONS:
        return OpResult(ok=False, message=f"未知操作：{action}（可用 {'/'.join(CONTROL_ACTIONS)}）")
    store = store or get_spine_store()
    goal = find_goal(store, goal_id)
    if goal is None:
        return OpResult(ok=False, message=f"没有 id 以 {goal_id} 开头的目标（/tasks 可列出现有目标）")
    if goal.terminal and action in ("pause", "resume", "advance"):
        return OpResult(ok=False, message=f"目标〔{goal.id}〕已定局（{goal.status}），不再改动")

    if action == "delete":
        removed = store.delete(goal.id)
        return OpResult(
            ok=removed,
            message=f"已删除目标〔{goal.id}〕" if removed else f"删除失败：〔{goal.id}〕",
            goal=None,
        )

    def _mutate(g: TaskGoal) -> None:
        if action == "pause":
            g.status = GOAL_PAUSED
        elif action == "resume":
            g.status = GOAL_ACTIVE
        elif action == "cancel":
            g.status = GOAL_CANCELLED
        elif action == "advance":
            # 只跳等待，不改步骤文本：跳过条件不等于条件成立
            g.status = GOAL_ACTIVE
            step = g.current_step()
            if step is not None:
                step.wait = None
                step.next_run_at = 0.0
                step.state = STEP_PENDING
                step.block_reason = ""
                step.result = (step.result + "\n（用户以 advance 跳过等待条件）").strip()[:400]
        g.updated_at = time.time()

    # 走 store.update：它在文件锁内重新加载一份目标再改，避免覆盖驱动器的并发写入
    saved = store.update(goal.id, _mutate)
    if saved is None:
        return OpResult(ok=False, message=f"操作未落盘：〔{goal.id}〕（台账读写失败）")
    woke = _wake(f"目标 {goal.id} {action}")
    note = {
        "pause": "已暂停（驱动器不再推进）",
        "resume": "已恢复",
        "cancel": "已取消（记录保留，可 /tasks show 复盘）",
        "advance": "已跳过等待，下一次驱动复查就推进",
    }[action]
    # 台账改了但没人推：必须跟着说一句，否则用户敲完 /tasks advance 会盯看屏幕等一个
    # 永远不会来的结果（驱动器未装配时只能写入台账，不能谎称“已在推”）。
    if action in ("advance", "resume") and not woke:
        note += "（当前没有驱动线程，需先开 [tasks] enabled 才会真的执行）"
    return OpResult(ok=True, message=f"目标〔{goal.id}〕{note}", goal=saved)


# ---- 只读视图：给 /tasks show、工具与 RPC 共用 ----

def find_goal(store: Any, goal_id: str) -> TaskGoal | None:
    """按 id 精确取；取不到再按前缀取唯一匹配（多个就当作没找到，不猜）。

    前缀匹配是台账 id 长（12 位随机串）留下的唯一人性化口子：手敲完整 id
    不现实。但“歧义时宁可不猜”：多个目标共享同一前缀时报“没找到”比挑一个
    然后把错的目标暂停/删掉便宜得多。
    @author aceFelix
    """
    arg = str(goal_id or "").strip()
    if not arg:
        return None
    goal = store.load(arg)
    if goal is not None:
        return goal
    hits = [g for g in store.list_goals(None, limit=200) if g.id.startswith(arg)]
    return hits[0] if len(hits) == 1 else None


def wait_readable(wait: TaskWait | None) -> str:
    """等待条件的一行可读描述（`/tasks show` 与工具返回都用它）。"""
    if wait is None:
        return ""
    if wait.kind == WAIT_TIME:
        return f"time（等到 {wait.spec.get('at', '?')}）"
    if wait.kind == WAIT_FILE_EXISTS:
        verb = "消失" if str(wait.spec.get("mode")) == "gone" else "出现"
        return f"file_exists（等 {wait.spec.get('path', '?')} {verb}）"
    if wait.kind == WAIT_PERCEPT_EVENT:
        return f"percept_event（等 {wait.spec.get('source', '?')}/{wait.spec.get('kind') or '任意'} 事件）"
    return wait.kind


def goal_detail(goal: TaskGoal) -> dict[str, Any]:
    """目标的结构化明细（RPC 直接回传，命令层拼文本也用它）。"""
    done, total = goal.progress()
    step = goal.current_step()
    return {
        "id": goal.id,
        "goal": goal.goal,
        "status": goal.status,
        "progress": f"{done}/{total}",
        "summary": goal.summary_line(),
        "workdir": goal.workdir,
        "session_id": goal.session_id,
        "created_at": iso_of(goal.created_at),
        "updated_at": iso_of(goal.updated_at),
        "current_step": (
            {
                "id": step.id,
                "desc": step.desc,
                "state": step.state,
                "block_reason": step.block_reason,
                "attempts": step.attempts,
                "wait": wait_readable(step.wait),
                "next_run_at": iso_of(step.next_run_at) if step.next_run_at else "",
                "journal_ids": list(step.journal_ids),
                # 面板的「批准」要照这份清单签租约，所以必须随明细一起回传
                "denied_tools": list(step.denied_tools),
            }
            if step is not None
            else None
        ),
        "steps": [
            {
                "id": s.id,
                "desc": s.desc,
                "state": s.state,
                "wait": wait_readable(s.wait),
                "result": s.result,
                "last_error": s.last_error,
                "journal_ids": list(s.journal_ids),
                "denied_tools": list(s.denied_tools),
            }
            for s in goal.steps
        ],
    }


def snapshot(*, limit: int = 20, store: Any | None = None) -> dict[str, Any]:
    """脊柱整体快照：活跃目标 + 驱动线程状态 + 台账目录。

    ``driver_running`` 与 ``driver_enabled`` 要分开报：前者是"线程真的在跑"，
    后者是配置意图。只报一个字段就会出现"面板显示已开、实际没线程"的假可用
    （与 W2 感知层同一个坑）。

    @author aceFelix
    """
    store = store or get_spine_store()
    goals = store.open_goals(limit=limit)
    driver_running = False
    try:
        from agent.core.tasks.driver import get_driver

        driver = get_driver()
        driver_running = bool(driver is not None and driver.running)
    except Exception:  # noqa: BLE001 - 读状态永不抛异常，取不到就当没起
        driver_running = False
    return {
        "goals": [goal_detail(g) for g in goals],
        "count": len(goals),
        "driver_running": driver_running,
        "base_dir": str(getattr(store, "base_dir", "")),
    }


def _wake(reason: str) -> bool:
    """唤醒驱动器复查一次；没装配驱动器时返回 False（读侧不因此报错）。

    为什么不能因为"没驱动器"就报错：台账单例可以被宿主单独使用（比如脚本里建目标、
    或 `[tasks] enabled=false` 时只留台账），唤醒失败只是"不会自动往下走"。
    返回值给调用方用作"要不要补一句没人推"的依据。
    @author aceFelix
    """
    try:
        from agent.core.tasks.driver import wake_driver

        return bool(wake_driver(reason))
    except Exception:  # noqa: BLE001 - 唤醒失败不影响台账已写入的事实
        return False


def describe_steps(goal: TaskGoal) -> list[str]:
    """把目标逐步展开成可打印行（``/tasks show`` 用；Rich 转义由调用方负责）。"""
    lines: list[str] = []
    for i, s in enumerate(goal.steps, start=1):
        mark = s.icon()
        extra = f"｜{wait_readable(s.wait)}" if s.wait is not None else ""
        err = f"｜上次失败：{s.last_error[:40]}" if s.last_error and s.state != STEP_BLOCKED else ""
        lines.append(f"  {i}. {mark} {s.desc[:60]}（{s.state}）{extra}{err}")
        if s.result:
            lines.append(f"       ↳ {s.result[:80]}")
    return lines
