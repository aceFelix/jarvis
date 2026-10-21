"""W3.1 任务脊柱 —— 让"替我盯着"成立的跨会话目标台账。

三个"任务"概念的关系（刻意不互相侵蚀存储）：
- ``core/daemon/scheduler.py`` 的 ``ScheduleTask`` 是**闹钟**：只有 ``trigger_at +
  content``，到点说一句话，活不过一次对话；
- ``collaboration/task_list.py`` 的 ``TaskList`` 是**一次查询内**的并行子任务；
- 本模块是**跨会话、跨天**的目标台账：一个目标拆成有序步骤，步骤可以"等条件"，
  驱动器（``driver.py``）在条件满足后推进一步。三者用 ``parent_task_id`` 关联。

存储：``~/.jarvis/goals/<id>.json``，一个目标一个文件。
为什么不是升级计划里写的 ``~/.jarvis/tasks/goals/``：``~/.jarvis/tasks/<team>/`` 已被
协作 TaskList 用作团队目录，插一个 ``goals`` 子目录会在团队恰好叫 "goals" 时串目录、
双方互相读坏。挪到顶层独立目录，两套存储天然隔离（代价：路径与计划文档不一致，
已在 docs/roadmap/PLAN-butler-autonomy-upgrade.md §4 的进度块里记明）。

写入一律"临时文件 + os.replace"（对齐 ``profile_store`` / ``memory/recovery`` 的崩溃
安全范式），并用 ``agent/utils/filelock`` 的跨进程锁做读-改-写：CLI 与 serve 是两个
进程，可能同时动同一份台账。

条件等待的三条硬约束：
1. **判定不花钱**：本模块只回答"条件满足了吗"，用时间比较与本地 stat，绝不调 LLM、
   不起子进程；唤醒时机由驱动器的定时器与总线订阅负责（验收项"条件等待不烧轮询"）。
2. **一律带 deadline**：感知层逐源默认关，``percept_event`` 可能永不到来；超时必须转
   ``timeout`` 并主动问用户，绝不静默挂起（否则"替我盯着"变成"替我失踪"）。
3. **坏文件不崩**：任何一行读不出来就跳过该目标，不抛异常（台账是可恢复观测数据）。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from agent.utils.filelock import file_lock

# ---- 目标状态 ----
GOAL_ACTIVE = "active"        # 正在推进（或正在等条件）
GOAL_PAUSED = "paused"        # 用户按停，驱动器不动
GOAL_DONE = "done"            # 全部步骤 done
GOAL_FAILED = "failed"        # 某步重试到上限仍失败
GOAL_CANCELLED = "cancelled"  # 用户放弃

GOAL_STATUSES = (GOAL_ACTIVE, GOAL_PAUSED, GOAL_DONE, GOAL_FAILED, GOAL_CANCELLED)

# ---- 步骤状态 ----
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_BLOCKED = "blocked"      # 等条件或等人，附带 block_reason
STEP_FAILED = "failed"

STEP_STATES = (STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_BLOCKED, STEP_FAILED)

# ---- 阻塞原因（决定驱动器是"等着"还是"去问人"）----
BLOCK_WAIT = "waiting"        # 条件还没满足，继续等
BLOCK_TIMEOUT = "timeout"     # 等待超过 deadline_at，该来问用户
BLOCK_ERROR = "error"         # 执行失败且已达重试上限
BLOCK_APPROVAL = "approval"   # 需要用户授权才能继续（W1 待批）

BLOCK_REASONS = (BLOCK_WAIT, BLOCK_TIMEOUT, BLOCK_ERROR, BLOCK_APPROVAL)

# ---- 条件等待类型 ----
WAIT_TIME = "time"                # {"at": "2026-10-21T09:00:00"}
WAIT_FILE_EXISTS = "file_exists"  # {"path": "~/Downloads/x.pdf", "mode": "exists|gone"}
WAIT_PERCEPT_EVENT = "percept_event"  # {"source","kind","contains",内部记 "hit_at"}

WAIT_KINDS = (WAIT_TIME, WAIT_FILE_EXISTS, WAIT_PERCEPT_EVENT)

# 步骤结果与错误在磁盘上的截断长度：台账要能一眼读完，不该变成日志仓库
_RESULT_LIMIT = 400
_ERROR_LIMIT = 200


def _new_id() -> str:
    """目标/步骤 ID（12 位 hex，可在命令行里手输）。"""
    return uuid.uuid4().hex[:12]


def parse_iso(value: Any) -> float:
    """ISO 8601 字符串 → unix 秒；解析不了返回 0.0（不抛）。

    容忍三种写法：完整 ISO、仅日期（补 00:00）、以及纯数字串（当作 unix 秒）。
    配置与模型产出的时间串形态不可控，这里必须"尽力解释、失败即无"，
    不能因为一个非法时间把整个目标读不出来。
    @author aceFelix
    """
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "").strip()
    if not s:
        return 0.0
    try:
        return float(s)  # 纯数字串按 unix 秒处理
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def iso_of(ts: float) -> str:
    """unix 秒 → 本地时区 ISO 字符串（台账里存 ISO，便于人读与手改）。"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except (ValueError, OSError):
        return ""


@dataclass
class TaskWait:
    """一个步骤的等待条件。

    Attributes:
        kind: WAIT_TIME / WAIT_FILE_EXISTS / WAIT_PERCEPT_EVENT。
        spec: 条件参数（见 WAIT_KINDS 注释里的字段形态）。
        deadline_at: 最长等待时刻（unix 秒）；0 表示不设限（只有 WAIT_TIME 允许，
            因为它自身就是确定时刻）。

    @author aceFelix
    """

    kind: str = WAIT_TIME
    spec: dict[str, Any] = field(default_factory=dict)
    deadline_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "spec": dict(self.spec), "deadline_at": self.deadline_at}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskWait":
        kind = str(data.get("kind") or WAIT_TIME)
        if kind not in WAIT_KINDS:
            kind = WAIT_TIME  # 未知类型退化为"按时间等"，至少驱动器还能收尾
        spec = data.get("spec")
        return cls(
            kind=kind,
            spec=dict(spec) if isinstance(spec, dict) else {},
            deadline_at=float(data.get("deadline_at") or 0.0),
        )

    # ---- 判定 ----

    def expired(self, now: float | None = None) -> bool:
        """是否已超过最长等待时限（无 deadline 永不超时）。"""
        if not self.deadline_at:
            return False
        return (now if now is not None else time.time()) >= self.deadline_at

    def check(self, now: float | None = None) -> tuple[bool, str]:
        """条件是否满足。返回 ``(满足?, 说明)``，判定本身不花钱、不起进程。"""
        current = now if now is not None else time.time()

        if self.kind == WAIT_TIME:
            at = parse_iso(self.spec.get("at"))
            if not at:
                return True, "等待时间无法解析，按已到期处理"
            if current >= at:
                return True, f"已到 {iso_of(at)}"
            return False, f"还需等到 {iso_of(at)}"

        if self.kind == WAIT_FILE_EXISTS:
            raw = str(self.spec.get("path") or "")
            if not raw:
                return True, "未指定路径，视为无等待条件"
            target = Path(os.path.expanduser(raw))
            want_gone = str(self.spec.get("mode") or "exists") == "gone"
            exists = target.exists()
            ok = (not exists) if want_gone else exists
            verb = "不再存在" if want_gone else "已出现"
            return ok, (f"{target.name}{verb}" if ok else f"仍在等 {target}")

        if self.kind == WAIT_PERCEPT_EVENT:
            # 命中与否由驱动器订阅总线时写回 spec["hit_at"]（订阅回调是唯一
            # 判定入口）：本模块绝不去"主动查总线"，否则就退化成轮询。
            hit_at = float(self.spec.get("hit_at") or 0.0)
            if hit_at:
                return True, f"已收到 {self.spec.get('source', '')} 事件（{iso_of(hit_at)}）"
            want = self.spec.get("kind") or "任意"
            return False, f"还在等 {self.spec.get('source', '')}/{want} 事件"

        return True, f"未知等待类型 {self.kind}，按无等待处理"

    def mark_event_hit(self, ts: float | None = None, *, summary: str = "") -> None:
        """由驱动器（总线订阅回调）登记一次事件命中。

        Args:
            ts: 命中时刻；None 取当前时间。
            summary: 事件摘要（只存一行，供台账与播报引用）。

        @author aceFelix
        """
        self.spec["hit_at"] = float(ts if ts is not None else time.time())
        if summary:
            self.spec["hit_summary"] = summary[:_RESULT_LIMIT]


@dataclass
class TaskStep:
    """目标里的一个步骤。

    Attributes:
        desc: 步骤描述（同时是交给子代理的任务文本）。
        state: STEP_STATES 之一。
        wait: 该步骤开始执行前需要满足的条件（None=可直接执行）。
        attempts: 已尝试次数（驱动器按它做退避与重试上限判定）。
        last_error: 最近一次失败原因（截断存储）。
        result: 完成后的汇报摘要（截断存储）。
        block_reason: 阻塞原因（BLOCK_REASONS 之一），state=blocked 时有意义。
        started_at/finished_at: 执行起止时刻。
        next_run_at: 最早可再次执行的时刻（失败退避用）；0=可立即执行。
            退避时刻必须落盘而不是只记在内存里：进程重启若丢掉它，未达上限的失败
            步骤会被立刻重跑，退避形同虚设（烧钱方式最隐蔽的一种）。
        asked: 是否已就本步骤询问过用户（防止反复打扰，同一原因只问一次）。
        journal_ids: 本步骤产生的副作用账本记录 ID（/undo --task 的关联线索）。

    @author aceFelix
    """

    desc: str
    id: str = field(default_factory=_new_id)
    state: str = STEP_PENDING
    wait: TaskWait | None = None
    attempts: int = 0
    last_error: str = ""
    result: str = ""
    block_reason: str = ""
    asked: bool = False
    started_at: float = 0.0
    finished_at: float = 0.0
    next_run_at: float = 0.0
    journal_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "desc": self.desc, "state": self.state,
            "wait": self.wait.to_dict() if self.wait else None,
            "attempts": self.attempts, "last_error": self.last_error,
            "result": self.result, "block_reason": self.block_reason,
            "asked": self.asked, "started_at": self.started_at,
            "finished_at": self.finished_at, "next_run_at": self.next_run_at,
            "journal_ids": list(self.journal_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskStep":
        wait_data = data.get("wait")
        state = str(data.get("state") or STEP_PENDING)
        if state not in STEP_STATES:
            state = STEP_PENDING
        reason = str(data.get("block_reason") or "")
        if reason and reason not in BLOCK_REASONS:
            reason = ""
        return cls(
            desc=str(data.get("desc") or ""),
            id=str(data.get("id") or _new_id()),
            state=state,
            wait=TaskWait.from_dict(wait_data) if isinstance(wait_data, dict) else None,
            attempts=int(data.get("attempts") or 0),
            last_error=str(data.get("last_error") or "")[:_ERROR_LIMIT],
            result=str(data.get("result") or "")[:_RESULT_LIMIT],
            block_reason=reason,
            asked=bool(data.get("asked")),
            started_at=float(data.get("started_at") or 0.0),
            finished_at=float(data.get("finished_at") or 0.0),
            next_run_at=float(data.get("next_run_at") or 0.0),
            journal_ids=[str(x) for x in (data.get("journal_ids") or []) if x],
        )

    @property
    def open(self) -> bool:
        """还没结束（仍需驱动器关心）的步骤。"""
        return self.state in (STEP_PENDING, STEP_RUNNING, STEP_BLOCKED)

    def icon(self) -> str:
        """台账展示用图标（不放方括号，避免 Rich 把它当样式标签吃掉）。"""
        return {
            STEP_PENDING: "○", STEP_RUNNING: "●", STEP_DONE: "✓",
            STEP_BLOCKED: "◇", STEP_FAILED: "✗",
        }.get(self.state, "?")


@dataclass
class TaskGoal:
    """一个跨会话目标（任务脊柱的一条台账）。

    Attributes:
        goal: 目标描述（用户原话或整理后的一句话）。
        steps: 有序步骤；驱动器**按序推进**，做完一步才看下一步。
        status: GOAL_STATUSES 之一。
        session_id/workdir: 产生目标的会话与工作目录（重跑时用来还原现场）。
        parent_task_id: 关联协作 TaskList 里的任务 ID（同一次查询内的子任务），
            空=独立目标。两套存储互不写入，只留这根线索。
        budget: 累计开销快照 ``{"tokens": int, "usd": float}``（由驱动器记账）。
        blocked_since: 进入阻塞的时刻（驱动器据此判断"阻塞超 N 天该问一次"）。

    @author aceFelix
    """

    goal: str
    steps: list[TaskStep] = field(default_factory=list)
    id: str = field(default_factory=_new_id)
    status: str = GOAL_ACTIVE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    session_id: str = ""
    workdir: str = ""
    parent_task_id: str = ""
    budget: dict[str, Any] = field(default_factory=lambda: {"tokens": 0, "usd": 0.0})
    blocked_since: float = 0.0

    # ---- 序列化 ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "goal": self.goal, "status": self.status,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "session_id": self.session_id, "workdir": self.workdir,
            "parent_task_id": self.parent_task_id, "budget": dict(self.budget),
            "blocked_since": self.blocked_since,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskGoal":
        status = str(data.get("status") or GOAL_ACTIVE)
        if status not in GOAL_STATUSES:
            status = GOAL_ACTIVE
        steps_raw = data.get("steps")
        steps = [
            TaskStep.from_dict(s) for s in steps_raw
            if isinstance(s, dict)
        ] if isinstance(steps_raw, list) else []
        budget = data.get("budget")
        return cls(
            goal=str(data.get("goal") or ""),
            steps=steps,
            id=str(data.get("id") or _new_id()),
            status=status,
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            session_id=str(data.get("session_id") or ""),
            workdir=str(data.get("workdir") or ""),
            parent_task_id=str(data.get("parent_task_id") or ""),
            budget=dict(budget) if isinstance(budget, dict) else {"tokens": 0, "usd": 0.0},
            blocked_since=float(data.get("blocked_since") or 0.0),
        )

    # ---- 推进视图 ----

    def current_step(self) -> TaskStep | None:
        """第一个还没结束的步骤（驱动器只看它，保证按序推进）。"""
        for s in self.steps:
            if s.open:
                return s
        return None

    def progress(self) -> tuple[int, int]:
        """(已完成步数, 总步数)。"""
        return sum(1 for s in self.steps if s.state == STEP_DONE), len(self.steps)

    @property
    def terminal(self) -> bool:
        """目标是否已定局（驱动器不再动它）。"""
        return self.status in (GOAL_DONE, GOAL_FAILED, GOAL_CANCELLED)

    def refresh_status(self) -> str:
        """按步骤实况回算目标状态（不覆盖用户显式 paused/cancelled）。"""
        if self.status in (GOAL_PAUSED, GOAL_CANCELLED):
            return self.status
        if not self.steps:
            self.status = GOAL_ACTIVE
            return self.status
        if any(s.state == STEP_FAILED for s in self.steps):
            self.status = GOAL_FAILED
        elif all(s.state == STEP_DONE for s in self.steps):
            self.status = GOAL_DONE
        else:
            self.status = GOAL_ACTIVE
        return self.status

    def summary_line(self) -> str:
        """一行台账摘要（/tasks 与播报都用它）。"""
        done, total = self.progress()
        step = self.current_step()
        tail = ""
        if step is not None:
            if step.state == STEP_BLOCKED:
                # 阻塞原因直接写进摘要：一眼能区分"在等条件"与"在等人"
                tail = f"｜阻塞：{step.block_reason or BLOCK_WAIT}"
            elif step.wait is not None:
                tail = f"｜等条件：{step.wait.kind}"
            else:
                tail = f"｜下一步：{step.desc[:24]}"
        return f"{self.goal[:40]}（{done}/{total}）{tail}"


class SpineStore:
    """任务脊柱的落盘台账（一个目标一个 JSON 文件，跨进程锁保护读-改-写）。

    @author aceFelix
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base = Path(base_dir) if base_dir else Path.home() / ".jarvis" / "goals"

    @property
    def base_dir(self) -> Path:
        return self._base

    def _path_for(self, goal_id: str) -> Path:
        """目标 ID → 文件路径（ID 先做白名单清洗，防路径穿越）。"""
        safe = "".join(ch for ch in str(goal_id) if ch.isalnum() or ch in "-_")[:40]
        return self._base / f"{safe or 'invalid'}.json"

    @property
    def _lock_path(self) -> Path:
        return self._base / ".goals.lock"

    # ---- 读 ----

    def load(self, goal_id: str) -> TaskGoal | None:
        """读一个目标；文件缺失/损坏返回 None（不抛）。"""
        path = self._path_for(goal_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return TaskGoal.from_dict(data)
        except Exception:
            return None

    def list_goals(
        self,
        statuses: Iterable[str] | None = None,
        *,
        limit: int = 50,
    ) -> list[TaskGoal]:
        """列目标（默认新的在前）。坏文件直接跳过。"""
        wanted = set(statuses) if statuses else None
        out: list[TaskGoal] = []
        try:
            files = sorted(self._base.glob("*.json"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return []
        for path in reversed(files[-max(limit, 1):]):
            goal = self.load(path.stem)
            if goal is None:
                continue
            if wanted is not None and goal.status not in wanted:
                continue
            out.append(goal)
        return out

    def open_goals(self, limit: int = 50) -> list[TaskGoal]:
        """仍活跃/暂停中的目标（驱动器与 /tasks 都只看这些）。"""
        return self.list_goals((GOAL_ACTIVE, GOAL_PAUSED), limit=limit)

    # ---- 写 ----

    def save(self, goal: TaskGoal) -> bool:
        """整量写一个目标：临时文件 + os.replace，中途崩不会留下半份 JSON。"""
        goal.updated_at = time.time()
        path = self._path_for(goal.id)
        try:
            self._base.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(goal.to_dict(), ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            os.replace(tmp, path)
            return True
        except OSError:
            return False

    def update(self, goal_id: str, mutate: Callable[[TaskGoal], Any]) -> TaskGoal | None:
        """跨进程锁内做"读-改-写"，返回改后的目标（目标不存在返回 None）。

        为什么需要它而不是 load→改→save：CLI 与 serve 是两个进程，可能在同一时刻
        改同一个目标（用户在 REPL 里按停、后台驱动器刚跑完一步）。不加锁的读-改-写
        会丢掉其中一方的改动。

        Args:
            goal_id: 目标 ID。
            mutate: 就地修改回调；返回值忽略（要取结果请改完再读对象）。

        @author aceFelix
        """
        try:
            with file_lock(self._lock_path):
                goal = self.load(goal_id)
                if goal is None:
                    return None
                mutate(goal)
                self.save(goal)
                return goal
        except (TimeoutError, OSError):
            return None

    def delete(self, goal_id: str) -> bool:
        """删除台账文件（归档即删除，本期不做软删）。"""
        try:
            self._path_for(goal_id).unlink()
            return True
        except OSError:
            return False

    # ---- 便捷工厂 ----

    def add_goal(
        self,
        goal_text: str,
        steps: list[str] | list[TaskStep] | None = None,
        *,
        workdir: str = "",
        session_id: str = "",
        parent_task_id: str = "",
    ) -> TaskGoal | None:
        """新建目标并落盘。``steps`` 接字符串或 TaskStep 两种写法。

        返回 None 表示落盘失败（磁盘只读等）——调用方据此如实报错，
        不能假装"目标已创建"。
        @author aceFelix
        """
        built: list[TaskStep] = []
        for item in steps or []:
            built.append(item if isinstance(item, TaskStep) else TaskStep(desc=str(item)))
        goal = TaskGoal(
            goal=str(goal_text or "").strip(),
            steps=built,
            workdir=workdir,
            session_id=session_id,
            parent_task_id=parent_task_id,
        )
        return goal if self.save(goal) else None


# 进程内单例（宿主与命令都用同一份目录口径；测试用 configure_spine_store 换目录）
_store: SpineStore | None = None


def get_spine_store() -> SpineStore:
    """全局台账单例。"""
    global _store
    if _store is None:
        _store = SpineStore()
    return _store


def configure_spine_store(base_dir: str | Path | None = None) -> SpineStore:
    """重建台账单例（测试注入临时目录用；生产路径一般不需要调）。"""
    global _store
    _store = SpineStore(base_dir)
    return _store
