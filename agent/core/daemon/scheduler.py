"""定时任务调度器 —— 贾维斯的"生物钟"。

阶段五第三刀（主动感知）的核心基础设施。让贾维斯能在指定时间主动行动，
而不是被动等用户提问。

核心概念:
1. **ScheduleTask**: 一个定时任务。含触发时间、内容、重复模式、状态。
2. **Scheduler**: 后台线程，每秒检查到期任务，触发回调。持久化到
   ``~/.jarvis/schedule.json``，daemon 重启不丢失任务。
3. **重复模式**: 一次性（once）、每日（daily）、每周（weekly）。
   到期触发后，重复任务自动计算下一次触发时间。

工作流:
    用户对贾维斯说"明天下午3点提醒我开会"
      → 主 agent 调 ScheduleReminderTool
      → LLM 解析自然语言时间，转成 ISO 时间字符串
      → ScheduleReminderTool 创建 ScheduleTask 加入 Scheduler
      → Scheduler 后台线程轮询，到点触发回调
      → 回调执行：托盘通知 + 语音播报"先生，提醒您：开会"
      → 一次性任务标记完成；重复任务计算下次时间

设计要点:
- **线程安全**: _lock 保护 _tasks 列表（后台线程读写 + 工具线程增删）
- **持久化**: 每次增删改后立即写盘；启动时加载未完成任务
- **错过补偿**: daemon 关闭期间错过的任务，启动时补触发（一次性任务且不超过1小时）
- **轻量**: 用 threading.Thread + time.sleep 轮询，不引入第三方调度库（APScheduler 太重）
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# 任务数据模型
# ---------------------------------------------------------------------------


@dataclass
class ScheduleTask:
    """一个定时任务。

    Attributes:
        id: 任务唯一 ID（uuid hex）。
        trigger_at: 触发时间（ISO 8601 字符串，如 "2026-06-20T15:00:00"）。
        content: 提醒内容（如"开会"）。
        repeat: 重复模式。"once"=一次性, "daily"=每日, "weekly"=每周。
            重复任务触发后自动计算下次时间。
        status: 任务状态。"pending"=待触发, "fired"=已触发(一次性),
            "cancelled"=已取消。
        created_at: 创建时间（ISO 字符串）。
        note: 备注（可选，LLM 可附加说明）。
        acknowledged: 用户是否已确认此提醒（P2-3 提醒升级机制）。
        escalate_count: 已升级（重复通知）次数。
        max_escalate: 最大升级次数（超过后停止重复通知，避免轰炸）。
        last_fired_at: 上次触发时间（ISO 字符串，用于计算升级间隔）。
    """

    id: str = ""
    trigger_at: str = ""
    content: str = ""
    repeat: str = "once"  # once / daily / weekly
    status: str = "pending"  # pending / fired / cancelled
    created_at: str = ""
    note: str = ""
    # P2-3 提醒升级/确认机制
    acknowledged: bool = False
    escalate_count: int = 0
    max_escalate: int = 3
    last_fired_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ScheduleTask:
        return cls(
            id=d.get("id", ""),
            trigger_at=d.get("trigger_at", ""),
            content=d.get("content", ""),
            repeat=d.get("repeat", "once"),
            status=d.get("status", "pending"),
            created_at=d.get("created_at", ""),
            note=d.get("note", ""),
            acknowledged=d.get("acknowledged", False),
            escalate_count=d.get("escalate_count", 0),
            max_escalate=d.get("max_escalate", 3),
            last_fired_at=d.get("last_fired_at", ""),
        )

    @property
    def trigger_time(self) -> datetime | None:
        """解析 trigger_at 为 datetime 对象。失败返回 None。"""
        if not self.trigger_at:
            return None
        try:
            return datetime.fromisoformat(self.trigger_at)
        except (ValueError, TypeError):
            return None


# ---------------------------------------------------------------------------
# 调度器
# ---------------------------------------------------------------------------


def _schedule_file() -> Path:
    """定时任务持久化文件路径: ~/.jarvis/schedule.json"""
    return Path.home() / ".jarvis" / "schedule.json"


class Scheduler:
    """定时任务调度器。

    后台 daemon 线程每秒检查到期任务，触发回调。

    用法::

        scheduler = Scheduler(on_fire=lambda task: print(f"提醒: {task.content}"))
        scheduler.start()
        scheduler.add_task(content="开会", trigger_at="2026-06-20T15:00:00")
        # ... daemon 运行中 ...
        scheduler.stop()
    """

    def __init__(self, on_fire: Callable[[ScheduleTask], None]) -> None:
        """
        Args:
            on_fire: 任务到期触发的回调。接收 ScheduleTask 参数。
                回调在调度器线程执行，应快速返回（重活另起线程）。
        """
        self._on_fire = on_fire
        self._tasks: list[ScheduleTask] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False

    # ---- 生命周期 ----

    def start(self) -> None:
        """启动调度器后台线程。加载持久化任务，开始轮询。"""
        if self._started:
            return
        self._started = True
        self._load()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="jarvis-scheduler")
        self._thread.start()

    def stop(self) -> None:
        """停止调度器。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._started = False

    # ---- 任务管理 ----

    def add_task(
        self,
        *,
        content: str,
        trigger_at: str | datetime,
        repeat: str = "once",
        note: str = "",
    ) -> ScheduleTask:
        """添加一个定时任务。

        Args:
            content: 提醒内容。
            trigger_at: 触发时间。ISO 字符串或 datetime 对象。
            repeat: "once" / "daily" / "weekly"。
            note: 备注。

        Returns: 创建的 ScheduleTask。
        """
        if isinstance(trigger_at, datetime):
            trigger_at = trigger_at.strftime("%Y-%m-%dT%H:%M:%S")

        task = ScheduleTask(
            id=uuid.uuid4().hex[:12],
            trigger_at=trigger_at,
            content=content,
            repeat=repeat,
            status="pending",
            created_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            note=note,
        )

        with self._lock:
            self._tasks.append(task)
            self._save()
        return task

    def cancel_task(self, task_id: str) -> bool:
        """取消一个任务。返回是否找到并取消成功。"""
        with self._lock:
            for t in self._tasks:
                if t.id == task_id and t.status == "pending":
                    t.status = "cancelled"
                    self._save()
                    return True
        return False

    def list_pending(self) -> list[ScheduleTask]:
        """列出所有待触发任务（按触发时间排序）。"""
        with self._lock:
            pending = [t for t in self._tasks if t.status == "pending"]
        pending.sort(key=lambda t: t.trigger_at)
        return pending

    def list_all(self) -> list[ScheduleTask]:
        """列出所有任务（含已触发/已取消）。"""
        with self._lock:
            return list(self._tasks)

    def acknowledge(self, task_id: str) -> bool:
        """确认一个提醒任务（停止升级重复通知）。返回是否找到。"""
        with self._lock:
            for t in self._tasks:
                if t.id == task_id:
                    t.acknowledged = True
                    self._save()
                    return True
        return False

    def get_unacknowledged_fired(self) -> list[ScheduleTask]:
        """获取已触发但未确认的任务（用于升级检查）。"""
        with self._lock:
            return [
                t for t in self._tasks
                if t.status == "fired" and not t.acknowledged
                and t.escalate_count < t.max_escalate
            ]

    def clear_completed(self, keep_recent: int = 50) -> int:
        """清理已触发/已取消的任务，保留最近 keep_recent 条记录。

        Returns: 清理的数量。
        """
        with self._lock:
            before = len(self._tasks)
            # 保留 pending + 最近的非 pending
            pending = [t for t in self._tasks if t.status == "pending"]
            done = [t for t in self._tasks if t.status != "pending"]
            done.sort(key=lambda t: t.trigger_at, reverse=True)
            self._tasks = pending + done[:keep_recent]
            cleaned = before - len(self._tasks)
            if cleaned > 0:
                self._save()
            return cleaned

    # ---- 内部 ----

    def _run(self) -> None:
        """调度器主循环。每秒检查到期任务。"""
        while not self._stop_event.is_set():
            try:
                self._check_and_fire()
            except Exception:
                # 调度器不能因单次异常挂掉
                pass
            self._stop_event.wait(timeout=1.0)

    def _check_and_fire(self) -> None:
        """检查到期任务并触发。"""
        now = datetime.now()
        to_fire: list[ScheduleTask] = []

        with self._lock:
            for t in self._tasks:
                if t.status != "pending":
                    continue
                trigger_time = t.trigger_time
                if trigger_time is None:
                    continue
                if trigger_time <= now:
                    to_fire.append(t)

        for task in to_fire:
            self._fire_task(task)

    def _fire_task(self, task: ScheduleTask) -> None:
        """触发一个任务。更新状态/计算下次时间，调回调。"""
        # 重复任务：先计算下次时间，再触发当前
        next_trigger = self._calc_next_trigger(task)

        with self._lock:
            if next_trigger is not None:
                # 重复任务：更新触发时间，保持 pending
                task.trigger_at = next_trigger.strftime("%Y-%m-%dT%H:%M:%S")
            else:
                # 一次性任务：标记已触发
                task.status = "fired"
            # 记录触发时间（用于升级间隔计算）
            task.last_fired_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            # 重置确认状态（重复任务每次触发都需要重新确认）
            task.acknowledged = False
            task.escalate_count = 0
            self._save()

        # 触发回调（锁外执行，避免回调里操作任务死锁）
        try:
            self._on_fire(task)
        except Exception:
            pass

    @staticmethod
    def _calc_next_trigger(task: ScheduleTask) -> datetime | None:
        """计算重复任务的下次触发时间。一次性任务返回 None。"""
        current = task.trigger_time
        if current is None:
            return None

        if task.repeat == "daily":
            return current + timedelta(days=1)
        if task.repeat == "weekly":
            return current + timedelta(weeks=1)
        # once 或未知 → 无下次
        return None

    # ---- 持久化 ----

    def _save(self) -> None:
        """保存任务列表到 schedule.json。调用方需持锁。"""
        try:
            path = _schedule_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = [t.to_dict() for t in self._tasks]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        """从 schedule.json 加载任务。启动时调用。"""
        path = _schedule_file()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            with self._lock:
                self._tasks = [ScheduleTask.from_dict(d) for d in data if isinstance(d, dict)]
        except Exception:
            self._tasks = []

        # 错过补偿：一次性任务且错过不超过1小时，保留 pending（启动后立即触发）
        # 错过超过1小时的标记 fired（避免开机就一堆提醒轰炸）
        now = datetime.now()
        with self._lock:
            for t in self._tasks:
                if t.status != "pending":
                    continue
                trigger_time = t.trigger_time
                if trigger_time is None:
                    continue
                if t.repeat == "once" and trigger_time < now:
                    missed = (now - trigger_time).total_seconds()
                    if missed > 3600:  # 超过1小时
                        t.status = "fired"
            self._save()
