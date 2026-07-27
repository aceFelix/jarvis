"""截止日期追踪器 —— 贾维斯的"deadline 意识"。

P2-3 主动提醒系统核心模块之一。让贾维斯能追踪用户注册的截止日期，
在临近时分级提醒（提前 7 天 / 3 天 / 1 天 / 当天 / 逾期）。

核心概念:
1. **Deadline**: 一个截止日期条目。含标题、到期日、提醒天数列表、状态。
2. **DeadlineTracker**: 管理所有 deadline 的增删改查 + 每日检查逻辑。
   持久化到 ``~/.jarvis/deadlines.json``。

工作流:
    用户对贾维斯说"下周五之前交项目报告"
      → 主 agent 调 AddDeadline 工具
      → DeadlineTracker 创建 Deadline 条目
      → ProactiveEngine 每天 09:00 调 check_deadlines()
      → 计算距 due_date 天数差，匹配 remind_days
      → 命中则生成提醒文本，通过 on_notify 回调播报

分级提醒规则:
- 距 due_date N 天（N 在 remind_days 列表中）→ "距离 XX 还有 N 天"
- 当天（days_left == 0）→ "今天是 XX 截止日！"
- 逾期（days_left < 0）→ "XX 已逾期 N 天"，每天提醒直到标记完成
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class Deadline:
    """一个截止日期条目。

    Attributes:
        id: 唯一 ID（uuid hex 前 12 位）。
        title: 标题（如"Q3 项目交付"）。
        due_date: 到期日（ISO date 字符串，如 "2026-08-15"）。
        remind_days: 提前几天提醒的列表。默认 [7, 3, 1, 0]。
            0 表示当天提醒。逾期后每天自动提醒（不受此列表控制）。
        status: 状态。"active"=活跃, "done"=已完成, "overdue"=已逾期。
        created_at: 创建时间（ISO 字符串）。
        note: 备注（可选）。
        reminded_dates: 已提醒过的日期列表（防同一天重复提醒）。
    """

    id: str = ""
    title: str = ""
    due_date: str = ""
    remind_days: list[int] = field(default_factory=lambda: [7, 3, 1, 0])
    status: str = "active"  # active / done / overdue
    created_at: str = ""
    note: str = ""
    reminded_dates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Deadline":
        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            due_date=d.get("due_date", ""),
            remind_days=d.get("remind_days", [7, 3, 1, 0]),
            status=d.get("status", "active"),
            created_at=d.get("created_at", ""),
            note=d.get("note", ""),
            reminded_dates=d.get("reminded_dates", []),
        )

    @property
    def due(self) -> date | None:
        """解析 due_date 为 date 对象。失败返回 None。"""
        if not self.due_date:
            return None
        try:
            return date.fromisoformat(self.due_date)
        except (ValueError, TypeError):
            return None

    @property
    def days_left(self) -> int | None:
        """距到期日还剩几天。负数=已逾期。None=解析失败。"""
        due = self.due
        if due is None:
            return None
        return (due - date.today()).days


# ---------------------------------------------------------------------------
# 持久化路径
# ---------------------------------------------------------------------------


def _deadlines_file() -> Path:
    """截止日期持久化文件路径: ~/.jarvis/deadlines.json"""
    return Path.home() / ".jarvis" / "deadlines.json"


# ---------------------------------------------------------------------------
# DeadlineTracker
# ---------------------------------------------------------------------------


class DeadlineTracker:
    """截止日期追踪器。

    管理所有 deadline 的增删改查，提供每日检查逻辑。
    线程安全（_lock 保护 _deadlines 列表）。

    用法::

        tracker = DeadlineTracker()
        tracker.add(title="项目交付", due_date="2026-08-15")
        messages = tracker.check_today()  # 返回今天需要提醒的文本列表
    """

    def __init__(self) -> None:
        self._deadlines: list[Deadline] = []
        self._lock = threading.Lock()
        self._load()

    # ---- 增删改查 ----

    def add(
        self,
        *,
        title: str,
        due_date: str,
        remind_days: list[int] | None = None,
        note: str = "",
    ) -> Deadline:
        """添加一个截止日期。

        Args:
            title: 标题。
            due_date: 到期日（ISO date，如 "2026-08-15"）。
            remind_days: 提前几天提醒。默认 [7, 3, 1, 0]。
            note: 备注。

        Returns: 创建的 Deadline。

        Raises:
            ValueError: due_date 格式错误。
        """
        # 校验日期格式
        try:
            date.fromisoformat(due_date)
        except (ValueError, TypeError):
            raise ValueError(f"due_date 格式错误: {due_date}（需要 YYYY-MM-DD）")

        deadline = Deadline(
            id=uuid.uuid4().hex[:12],
            title=title,
            due_date=due_date,
            remind_days=remind_days if remind_days is not None else [7, 3, 1, 0],
            status="active",
            created_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            note=note,
        )

        with self._lock:
            self._deadlines.append(deadline)
            self._save()
        return deadline

    def complete(self, deadline_id: str) -> bool:
        """标记截止日期为已完成。返回是否找到。"""
        with self._lock:
            for d in self._deadlines:
                if d.id == deadline_id and d.status in ("active", "overdue"):
                    d.status = "done"
                    self._save()
                    return True
        return False

    def remove(self, deadline_id: str) -> bool:
        """删除一个截止日期。返回是否找到。"""
        with self._lock:
            for i, d in enumerate(self._deadlines):
                if d.id == deadline_id:
                    self._deadlines.pop(i)
                    self._save()
                    return True
        return False

    def list_active(self) -> list[Deadline]:
        """列出所有活跃的截止日期（按到期日排序）。"""
        with self._lock:
            active = [d for d in self._deadlines if d.status in ("active", "overdue")]
        active.sort(key=lambda d: d.due_date)
        return active

    def list_all(self) -> list[Deadline]:
        """列出所有截止日期（含已完成）。"""
        with self._lock:
            return list(self._deadlines)

    # ---- 每日检查 ----

    def check_today(self) -> list[str]:
        """检查今天需要提醒的截止日期，返回提醒文本列表。

        逻辑:
        - 遍历所有 active/overdue 的 deadline
        - 计算 days_left
        - 如果 days_left 在 remind_days 中，或已逾期，生成提醒
        - 同一天不重复提醒（通过 reminded_dates 去重）
        - 逾期自动更新 status 为 "overdue"
        """
        today_str = date.today().isoformat()
        messages: list[str] = []

        with self._lock:
            for d in self._deadlines:
                if d.status == "done":
                    continue

                days_left = d.days_left
                if days_left is None:
                    continue

                # 已逾期：更新状态
                if days_left < 0 and d.status == "active":
                    d.status = "overdue"

                # 判断是否需要提醒
                should_remind = False
                if days_left < 0:
                    # 逾期：每天提醒
                    should_remind = True
                elif days_left in d.remind_days:
                    # 命中提醒天数
                    should_remind = True

                if not should_remind:
                    continue

                # 去重：同一天不重复提醒
                if today_str in d.reminded_dates:
                    continue

                # 生成提醒文本
                if days_left < 0:
                    msg = f"⚠️ 「{d.title}」已逾期 {abs(days_left)} 天（截止日: {d.due_date}）"
                elif days_left == 0:
                    msg = f"🔴 今天是「{d.title}」的截止日！"
                else:
                    msg = f"📌 距离「{d.title}」还有 {days_left} 天（截止日: {d.due_date}）"

                if d.note:
                    msg += f"\n   备注: {d.note}"

                messages.append(msg)
                d.reminded_dates.append(today_str)

            if messages:
                self._save()

        return messages

    def get_summary(self) -> str:
        """获取活跃截止日期的摘要文本（用于每日简报）。"""
        active = self.list_active()
        if not active:
            return ""

        lines = []
        for d in active:
            days_left = d.days_left
            if days_left is None:
                continue
            if days_left < 0:
                lines.append(f"  ⚠️ {d.title}: 已逾期 {abs(days_left)} 天")
            elif days_left == 0:
                lines.append(f"  🔴 {d.title}: 今天截止！")
            else:
                lines.append(f"  📌 {d.title}: 还剩 {days_left} 天")

        return "\n".join(lines)

    # ---- 持久化 ----

    def _save(self) -> None:
        """保存到 deadlines.json。调用方需持锁。"""
        try:
            path = _deadlines_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = [d.to_dict() for d in self._deadlines]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        """从 deadlines.json 加载。"""
        path = _deadlines_file()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            with self._lock:
                self._deadlines = [Deadline.from_dict(d) for d in data if isinstance(d, dict)]
        except Exception:
            self._deadlines = []
