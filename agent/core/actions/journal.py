"""W3.2 副作用账本（兑现层）：把"做过什么"记成可执行的补偿计划。

与既有零件的分工（刻意不重复造轮子）：
- ``core/audit/tool_auditor.py``：**事后记录**，只读不回滚，本账本与它共用
  同一次工具调用的口径做对账；
- ``core/checkpoint.py``：**工作区整轮快照**。本账本只"引用"宿主本轮已经打过
  的检查点，绝不自己再打一个新的 —— shadow git 存储按 workdir 定位且有条数
  配额，账本每写一个文件就加一个检查点会把消息级回溯（/rewind）的检查点挤出
  配额，属于为了细粒度牺牲既有功能；
- ``core/sandbox/file_guard.py``：高风险 bash 执行前的整目录复制兜底，面向
  "这一次别把文件搞坏"，与本账本互不依赖。

为什么需要"延时窗口"：发邮件/发消息这类动作一旦离开本机就收不回来，权限层
的 ASK 确认只能保证"用户点过一次头"，挡不住"点完头才想起来发错人"。所以在
确认之后再加一段可撤回的静默期（默认 30 秒），到点才真执行。

安全红线：
- 延时窗口**只延后执行，不豁免权限**：入口在权限判定与 W1 闸门之后；
- 到点执行的是账本里**冻结的那一份参数**，模型后续再改口也影响不了已排队的动作；
- 全模块 IO 失败一律降级（不抛），沿用 ToolAuditor/diag 的"辅助能力不得拖垮
  主流程"惯例；
- 账本落在 ``~/.jarvis/actions.jsonl``（用户目录，含收件人等敏感字段，不外传）。

用法::

    journal = get_journal()
    journal.record(EffectRecord(tool="SendEmail", args={...}, reversible=False,
                                undo_spec={"kind": "none", "reason": "已离开本机"}))
    get_window().schedule(rec.id, 30, fire, loop=loop)   # 到点真执行
    journal.undo_record(rec.id)                          # /undo

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# 默认账本位置：用户目录，不随工作区走（换项目也要能查到同一份流水）
_DEFAULT_PATH = Path.home() / ".jarvis" / "actions.jsonl"

# 条数上限与轮转触发阈值（对齐 ToolAuditor：1MB 触发截断）
_MAX_ENTRIES = 500
_ROTATE_BYTES = 1024 * 1024

# ---- 状态机：held → applied / cancelled / failed，applied → undone ----
STATE_HELD = "held"            # 已确认，在延时窗口里等着，还没真执行
STATE_APPLIED = "applied"      # 真执行过且成功
STATE_UNDONE = "undone"        # 已按 undo_spec 撤销
STATE_FAILED = "failed"        # 执行或撤销失败
STATE_CANCELLED = "cancelled"  # 窗口内被 /undo cancel 撤回（或重启时作废）

ALL_STATES = (
    STATE_HELD, STATE_APPLIED, STATE_UNDONE, STATE_FAILED, STATE_CANCELLED,
)

# ---- undo_spec 的形态 ----
UNDO_CHECKPOINT = "checkpoint"  # {"kind","cid","workdir"}：回到本轮开始前的工作区
UNDO_NONE = "none"              # {"kind","reason"}：明确不可撤销，报告里要说清楚
# {"kind","before":{协议键: 原值}}：配置变更撤销（阶段 12）。配置是文本外科式写入，
# git 检查点那套工作区回滚术语用不了，故单列一种形态，回写走 config_specs。
UNDO_CONFIG = "config"


def _new_id() -> str:
    """副作用记录 ID（12 位 hex，够短可手输，够长不撞车）。"""
    return uuid.uuid4().hex[:12]


@dataclass
class EffectRecord:
    """一次已发生（或即将发生）的副作用 + 它的补偿计划。

    Attributes:
        id: 记录 ID。
        ts: 记录产生时刻（unix 秒）。
        tool: 工具名（如 SendEmail / FileWrite）。
        args: 冻结的执行参数（到点就按这份跑，不再接受改写）。
        preview: 给人看的一行话（同时充当"已发往 X"的撤销报告文案）。
        reversible: 是否可撤销。False 时 undo_spec 必为 UNDO_NONE 并带原因。
        undo_spec: 补偿计划，见 UNDO_CHECKPOINT / UNDO_NONE。
        state: 当前状态（ALL_STATES 之一）。
        due_at: 延时窗口的到期时刻（unix 秒）；0 表示不是待发动作。
        task_id: 归属的任务脊柱 ID（W3.1），空=会话内随手操作。
        session_id: 产生记录的会话名（只用于展示与排查）。
        workdir: 副作用发生时的工作目录（撤销检查点要靠它定位 shadow 仓库）。
        permission_mode: 发生时的权限模式（到点执行时沿用，不改判）。
        error: 原因文本（state=failed 是失败原因，state=cancelled 是作废成因）。
        updated_at: 最后一次状态变更时刻（读时合并的先后依据）。

    @author aceFelix
    """

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=_new_id)
    ts: float = field(default_factory=time.time)
    preview: str = ""
    reversible: bool = False
    undo_spec: dict[str, Any] = field(default_factory=dict)
    state: str = STATE_APPLIED
    due_at: float = 0.0
    task_id: str = ""
    session_id: str = ""
    workdir: str = ""
    permission_mode: str = "default"
    error: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """序列化成一行 JSONL（字段全量写出，读时按 id 合并）。"""
        return {
            "id": self.id, "ts": self.ts, "tool": self.tool, "args": self.args,
            "preview": self.preview, "reversible": self.reversible,
            "undo_spec": self.undo_spec, "state": self.state, "due_at": self.due_at,
            "task_id": self.task_id, "session_id": self.session_id,
            "workdir": self.workdir, "permission_mode": self.permission_mode,
            "error": self.error, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EffectRecord":
        """从 JSONL 行还原。

        容错口径：只认已知字段、缺省即用默认值，坏字段直接忽略。账本是可恢复
        的观测数据，任何一行写坏都不该让整个账本读不出来。
        @author aceFelix
        """
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in known}
        rec = cls(tool=str(kwargs.pop("tool", "")), **kwargs)
        if rec.state not in ALL_STATES:
            rec.state = STATE_APPLIED
        return rec

    @property
    def held(self) -> bool:
        """是否是"窗口内待执行"的动作（这类能被 cancel 掉）。"""
        return self.state == STATE_HELD

    @property
    def label(self) -> str:
        """展示用一行：优先 preview，缺失时退化成工具名。"""
        return self.preview or self.tool

    @property
    def undoable(self) -> bool:
        """是否有可执行的补偿计划（checkpoint 类带有效 cid，或配置回写类带原值）。"""
        if not self.reversible:
            return False
        kind = self.undo_spec.get("kind")
        if kind == UNDO_CHECKPOINT:
            return bool(self.undo_spec.get("cid"))
        if kind == UNDO_CONFIG:
            return bool(self.undo_spec.get("before"))
        return False

    def human_time(self) -> str:
        """发生时刻的可读形式（/undo 列表用，本地时区）。"""
        try:
            return time.strftime("%m-%d %H:%M:%S", time.localtime(self.ts))
        except (ValueError, OSError):
            return "-"


class ActionJournal:
    """副作用账本（JSON Lines 事件流，追加写、读时按 id 合并最新状态）。

    为什么用事件流而不是一整个 JSON 文件：状态变更（held→applied→undone）要
    能追加，覆盖写整个文件在崩溃时会把整本账一起丢掉；追加写坏了最多坏最后一行，
    读时跳过即可。

    @author aceFelix
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        enabled: bool = True,
        max_entries: int = _MAX_ENTRIES,
    ) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        self._enabled = enabled
        self._max_entries = max(50, int(max_entries))
        self._lock = threading.Lock()
        # 账本目录建不出来（只读文件系统/权限不足）时降级为禁用，不抛异常：
        # 记账是辅助能力，不能因为日志问题炸掉工具执行主流程
        if self._enabled:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def path(self) -> Path:
        return self._path

    # ---- 写入 ----

    def record(self, rec: EffectRecord) -> bool:
        """落一条副作用（整量快照）。返回是否真的写进账本。"""
        if not self._enabled:
            return False
        return self._append(rec.to_dict())

    def set_state(
        self,
        rec_id: str,
        state: str,
        *,
        error: str = "",
        due_at: float | None = None,
    ) -> EffectRecord | None:
        """改一条记录的状态（追加一份新快照，不改写历史行）。

        Args:
            rec_id: 记录 ID。
            state: 新状态（ALL_STATES 之一）。
            error: 原因文本（state=failed / cancelled 时留存，其他状态清掉）。
            due_at: 需要顺带修正到期时刻时传入（None=保持原值）。

        Returns:
            合并后的新记录；记录不存在或账本禁用时返回 None。
        @author aceFelix
        """
        if not self._enabled or state not in ALL_STATES:
            return None
        current = self.get(rec_id)
        if current is None:
            return None
        current.state = state
        # 原因文本在 failed 与 cancelled 两种终态都留着：被作废的预约动作同样需要
        # 告诉用户“为什么没发出去”（只有重启这一种成因），清掉就只剩一个干瘪的状态
        current.error = error if state in (STATE_FAILED, STATE_CANCELLED) else ""
        current.updated_at = time.time()
        if due_at is not None:
            current.due_at = float(due_at)
        self._append(current.to_dict())
        return current

    # ---- 读取 ----

    def get(self, rec_id: str) -> EffectRecord | None:
        """按 ID（支持唯一前缀）取一条记录的最新状态。"""
        records = self._load()
        if rec_id in records:
            return records[rec_id]
        if rec_id:
            hits = [r for rid, r in records.items() if rid.startswith(rec_id)]
            if len(hits) == 1:
                return hits[0]
        return None

    def recent(self, limit: int = 20) -> list[EffectRecord]:
        """最近 N 条（新的在前），/undo 列表与"撤上一步"都读它。"""
        ordered = sorted(self._load().values(), key=lambda r: r.updated_at)
        return list(reversed(ordered[-limit:])) if limit > 0 else []

    def last(self, *states: str) -> EffectRecord | None:
        """最近一条符合给定状态集合的记录（``/undo`` 撤上一步的取值口径）。"""
        wanted = set(states) or {STATE_APPLIED}
        for rec in self.recent(50):
            if rec.state in wanted:
                return rec
        return None

    def held_records(self) -> list[EffectRecord]:
        """当前所有还在窗口里的待发动作。"""
        return [r for r in self._load().values() if r.state == STATE_HELD]

    def task_records(self, task_id: str) -> list[EffectRecord]:
        """某个任务脊柱目标名下的全部副作用（按发生顺序，用于批量补偿）。"""
        rows = sorted(self._load().values(), key=lambda r: r.ts)
        return [r for r in rows if r.task_id and r.task_id == task_id]

    def same_checkpoint(self, cid: str) -> list[EffectRecord]:
        """同一个检查点（同一轮）下的全部记录，按发生顺序。

        文件类撤销回滚的是**整个检查点**，这一批的效果会一起消失。不把它们一并标记
        就会出现“同轮 A 已撤销、B 还写着可撤销”的矛盾（2026-10-26 实测：用户的
        `/undo list` 里 69cb6e79a538 已撤销，同轮的 5d4b253534ef 仍显示可撤销）。

        @author aceFelix
        """
        if not cid:
            return []
        rows = sorted(self._load().values(), key=lambda r: r.ts)
        return [r for r in rows if str((r.undo_spec or {}).get("cid") or "") == cid]

    def counts(self) -> dict[str, int]:
        """各状态计数（诊断与走查清单据此核对，不返回明细避免泄露内容）。"""
        out = {s: 0 for s in ALL_STATES}
        for rec in self._load().values():
            out[rec.state] = out.get(rec.state, 0) + 1
        return out

    # ---- 内部：读时合并 / 追加 / 轮转 ----

    def _load(self) -> dict[str, EffectRecord]:
        """读全账并按 id 合并（后写覆盖前写），坏行静默跳过。"""
        if not self._path.exists():
            return {}
        merged: dict[str, EffectRecord] = {}
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue  # 崩溃时截断的最后一行，跳过而不放弃整本
            if not isinstance(data, dict):
                continue
            # 没 id 的行不能要：``EffectRecord.from_dict`` 会给缺 id 的行现造一个新随机
            # id，于是每次重读账本都会从同一行编出一条“看起来合法”的幽灵记录，
            # /undo list 会多出一堆撤不动的空白项（手写/截断的行就是这种）
            raw_id = str(data.get("id") or "").strip()
            if not raw_id:
                continue
            try:
                rec = EffectRecord.from_dict(data)
            except Exception:
                continue
            merged[rec.id] = rec
        return merged

    def _append(self, payload: dict[str, Any]) -> bool:
        """追加一行并视大小轮转；任何 IO 失败都只返回 False 不抛。"""
        try:
            line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
            with self._lock:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line)
                if self._path.stat().st_size > _ROTATE_BYTES:
                    self._rotate()
            return True
        except OSError:
            return False

    def _rotate(self) -> None:
        """超限时只保留最近 max_entries 条（按 id 合并后计数，不留重复行）。"""
        try:
            records = list(self._load().values())
            keep = records[-self._max_entries:]
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for rec in keep:
                    f.write(json.dumps(rec.to_dict(), ensure_ascii=False, default=str) + "\n")
            # 临时文件 + os.replace：轮转中途崩也不会留下半本账
            tmp.replace(self._path)
        except OSError:
            try:
                self._path.unlink()  # 清掉坏文件，下次从空账开始记
            except OSError:
                pass


class DelayWindow:
    """进程内延时窗口登记表：把"到点真执行"编成可取消的一次性动作。

    只登记在进程内、不落盘是有意为之：30 秒窗口的意义在于"用户此刻还坐在
    前面，能喊停"。进程重启后没有人能喊停，所以 ``resume_pending`` 把遗留的
    待发动作**作废并记入报告**，而不是自动补发（对不可逆的对外动作，默认不做
    用户没当场确认过一次的事）。

    @author aceFelix
    """

    def __init__(self) -> None:
        self._handles: dict[str, Any] = {}
        # 在窗动作的展示文本：权限层要解释"为什么突然要确认"时用它，
        # 不能为了取一个名字去重读整本账（那是每次 Bash 权限判定都要走的路上）
        self._labels: dict[str, str] = {}
        self._lock = threading.Lock()

    def schedule(
        self,
        rec_id: str,
        window_s: float,
        fire: Callable[[], None],
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        label: str = "",
    ) -> None:
        """登记一个到期动作。

        Args:
            rec_id: 账本记录 ID（取消时用它）。
            window_s: 窗口秒数；<=0 视为立即执行（配置关掉延时时的通路）。
            fire: 到期回调（生产路径由 orchestrator 传入"在自己循环里建 task"的闭包）。
            loop: 目标事件循环；省略时尝试取当前运行循环，取不到退回线程定时器。
            label: 在窗动作的展示文本（供权限层解释收紧原因，可空）。

        @author aceFelix
        """
        if window_s <= 0:
            fire()
            return
        try:
            target = loop or asyncio.get_running_loop()
        except RuntimeError:
            target = None
        with self._lock:
            self._labels[rec_id] = label or ""
            if target is not None:
                # asyncio 定时器：回调跑在循环线程里，可直接 create_task
                self._handles[rec_id] = target.call_later(window_s, self._run, rec_id, fire)
            else:
                # 无运行循环（同步宿主/单测）：退化成守护线程定时器
                timer = threading.Timer(max(0.01, float(window_s)), self._run, (rec_id, fire))
                timer.daemon = True
                self._handles[rec_id] = timer
                timer.start()

    def _run(self, rec_id: str, fire: Callable[[], None]) -> None:
        """到期执行：先摘登记再回调，避免回调里再登记同一 id 被误删。"""
        with self._lock:
            self._handles.pop(rec_id, None)
            self._labels.pop(rec_id, None)
        try:
            fire()
        except Exception:
            # 回调异常不能冒泡进事件循环/定时器线程；账本状态由调用方自己记 failed
            pass

    def cancel(self, rec_id: str) -> bool:
        """撤回一个还在窗口里的动作。返回是否真的撤掉了。"""
        with self._lock:
            handle = self._handles.pop(rec_id, None)
            self._labels.pop(rec_id, None)
        if handle is None:
            return False
        try:
            handle.cancel()
        except Exception:
            return False
        return True

    def pending(self) -> list[str]:
        """当前登记在册的待发记录 ID。"""
        with self._lock:
            return list(self._handles.keys())

    def pending_labels(self) -> list[str]:
        """在窗动作的展示文本（非空项，供权限层解释为什么不再自动放行）。

        只读进程内登记表，不碰账本文件：它处在每一次命令权限判定的路上。
        @author aceFelix
        """
        with self._lock:
            return [lb for lb in self._labels.values() if lb]

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._handles)

    def clear(self) -> int:
        """取消全部登记（退出宿主/单测收尾用），返回撤掉的数量。"""
        with self._lock:
            ids = list(self._handles)
            self._labels = {k: v for k, v in self._labels.items() if k not in set(ids)}
        n = 0
        for rec_id in ids:
            if self.cancel(rec_id):
                n += 1
        return n


def resume_pending(journal: ActionJournal, window: DelayWindow) -> list[EffectRecord]:
    """作废上一次进程遗留的待发动作。

    这些动作已经过了确认但没到点，进程重启后不再有可喊停的人，因此一律转
    ``cancelled`` 并由调用方播报——既不会"用户不知道的情况下把邮件发出去"，
    也不会让账本里躺着永远无法到达的 held 记录。

    Returns:
        被作废的记录列表（空表示无遗留）。
    @author aceFelix
    """
    stale = [r for r in journal.held_records() if r.id not in set(window.pending())]
    out: list[EffectRecord] = []
    for rec in stale:
        updated = journal.set_state(
            rec.id, STATE_CANCELLED, error="进程已重启，延时窗口内未发出的动作按取消处理",
        )
        if updated is not None:
            out.append(updated)
    return out


# ---- 撤销执行：把 undo_spec 变成一次真实回滚 ----

def manager_for(workdir: str, settings: Any = None, session_name: str = "journal"):
    """按记录里的 workdir 构造检查点管理器（惰性 import，避免循环依赖）。

    公开给 ``/undo`` 用：命令需要先 describe 展示回滚影响面、问过用户，再调
    undo_record 真正落回——两者必须共用同一个管理器口径。

    shadow git 存储按 workdir 指纹定位，所以这里新建实例不会另开一份仓库，
    只是把 ``/undo`` 的调用方与引擎的每轮快照接上同一条 ref 空间。

    字段取值不在此处写：它转给 ``checkpoint.manager_from_settings``，与 REPL /
    工作台同一个工厂（否则一边改配额、另一边看不到刚打的快照）。
    @author aceFelix
    """
    from agent.core.checkpoint import manager_from_settings

    return manager_from_settings(
        settings, workdir=workdir, session_name=session_name, fallback_session="journal",
    )


def describe_undo(rec: EffectRecord) -> str:
    """撤销不了的项要说清原因，而不是含糊一句"失败"。"""
    spec = rec.undo_spec or {}
    if spec.get("kind") == UNDO_NONE:
        return str(spec.get("reason") or "此项无法撤销")
    if not rec.reversible:
        return "此项未登记补偿计划，无法撤销"
    return "补偿计划不完整，无法撤销"


def undo_record(
    rec: EffectRecord,
    *,
    settings: Any = None,
    session_name: str = "",
    journal: ActionJournal | None = None,
) -> tuple[bool, str]:
    """按 undo_spec 撤销一条副作用。

    Returns:
        (是否撤销成功, 说明)。不可撤销项返回 False 并明确给出原因文案。
    @author aceFelix
    """
    journal = journal or get_journal()
    if rec.state == STATE_HELD:
        return False, "该项还在延时窗口里、尚未执行，请用 /undo cancel 撤回"
    if rec.state == STATE_UNDONE:
        return False, "该项已经撤销过了"
    if not rec.undoable:
        return False, describe_undo(rec)

    spec = rec.undo_spec
    if str(spec.get("kind") or "") == UNDO_CONFIG:
        return _undo_config(rec, spec, settings, journal)
    mgr = manager_for(rec.workdir, settings, session_name or rec.session_id)
    if not mgr.available():
        return False, "文件回滚不可用（未启用 checkpoint 或缺少 git）"
    cid = str(spec.get("cid", ""))
    if not mgr.has_checkpoint(cid):
        return False, "本轮检查点已被配额修剪，无法回退工作区"
    ok, note = mgr.restore(cid)
    if ok:
        # 整轮回滚：同一检查点下其它条目的效果也一起被撤了，只标被点的那一条，
        # `/undo list` 会继续对同轮其它条目显示“可撤销”——用户以为还有一层能撤
        # （2026-10-26 实测）。这里一并标记，回执里说清连带了几项。
        siblings = [
            r for r in journal.same_checkpoint(cid)
            if r.id != rec.id and r.state == STATE_APPLIED
        ]
        for other in siblings:
            journal.set_state(other.id, STATE_UNDONE)
        journal.set_state(rec.id, STATE_UNDONE)
        if siblings:
            note = f"{note}（整轮回滚：同轮另 {len(siblings)} 项一并记为已撤销）"
        return True, note
    journal.set_state(rec.id, STATE_FAILED, error=note)
    return False, note


def _undo_config(
    rec: EffectRecord,
    spec: dict[str, Any],
    settings: Any,
    journal: ActionJournal,
) -> tuple[bool, str]:
    """配置变更的回退：按 ``before`` 快照逐项回写（落盘 + 运行时）。

    与工作区回滚的区别：配置不是文件树快照，而是写死的几行文本，所以回退就是
    “把原值再写一遍”——没必要也不应该去动 git 检查点。

    @author aceFelix
    """
    from agent.config.config_specs import apply_value, get_spec, human_value, save_value

    before = spec.get("before") or {}
    if not isinstance(before, dict) or not before:
        return False, "这条配置变更没有记录原值，无法回退"
    restored: list[str] = []
    failed: list[str] = []
    for key, old in before.items():
        sp = get_spec(str(key))
        if sp is None:
            failed.append(f"{key}（已不在可改白名单）")
            continue
        if not save_value(sp, old):
            failed.append(f"{sp.label}（落盘失败）")
            continue
        apply_value(settings, sp, old)
        restored.append(f"{sp.label} → {human_value(sp, old)}")
    if failed:
        journal.set_state(rec.id, STATE_FAILED, error="；".join(failed))
        return False, "配置回退部分失败：" + "；".join(failed)
    journal.set_state(rec.id, STATE_UNDONE)
    return True, "配置已回退：" + "；".join(restored)


def undo_task(
    task_id: str,
    *,
    settings: Any = None,
    session_name: str = "",
    journal: ActionJournal | None = None,
) -> list[tuple[EffectRecord, bool, str]]:
    """撤销一个任务目标的全部可撤销副作用（按发生顺序补偿）。

    不可撤销项照样返回，带"此项无法撤销"的原因 —— 让调用方能如实汇总
    "撤了 3 件，另有 1 封邮件已经发出去了"，而不是静默少报。
    @author aceFelix
    """
    journal = journal or get_journal()
    out: list[tuple[EffectRecord, bool, str]] = []
    for rec in journal.task_records(task_id):
        if rec.state not in (STATE_APPLIED, STATE_UNDONE, STATE_FAILED):
            continue
        ok, note = undo_record(
            rec, settings=settings, session_name=session_name, journal=journal,
        )
        out.append((rec, ok, note))
    return out


# ---- 全局单例（测试可用 reset_actions_state 换新路径） ----

_journal: ActionJournal | None = None
_window = DelayWindow()


def get_journal() -> ActionJournal:
    """全局账本单例。禁用态由配置在装配时通过 configure_journal 落地。"""
    global _journal
    if _journal is None:
        _journal = ActionJournal()
    return _journal


def get_window() -> DelayWindow:
    """全局延时窗口登记表单例。"""
    return _window


def configure_journal(
    *,
    enabled: bool = True,
    path: str | Path | None = None,
    clear_window: bool = False,
) -> ActionJournal:
    """按 Settings 重建账本单例（宿主启动时调用一次）。

    Args:
        enabled: ``[actions].journal_enabled``。
        path: 自定义账本路径（测试注入用；None 用默认用户目录）。
        clear_window: 是否顺手清空延时窗口登记表（测试收尾用）。

    @author aceFelix
    """
    global _journal
    _journal = ActionJournal(path, enabled=enabled)
    if clear_window:
        _window.clear()
    return _journal
