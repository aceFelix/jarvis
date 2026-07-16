"""共享任务列表 —— 团队协作的任务追踪系统。

核心概念：
1. **TodoTask**: 任务数据模型——id、标题、描述、状态、owner、依赖链。
2. **依赖链**: blocks（此任务阻塞谁）/ blockedBy（谁阻塞此任务）。
3. **文件锁**: 使用 portalocker 跨平台文件锁，保证并发安全。
4. **High Water Mark**: 防止任务 ID 重用（已删除的 ID 不再分配）。
5. **持久化**: ~/.jarvis/tasks/{team_name}/ 下每个任务一个 JSON 文件。

状态流：
    pending → in_progress → completed
    （任何状态 → deleted 删除）

依赖工作流示例：
    Task #1 "探索认证模块" (pending)
    Task #2 "修改登录逻辑" (pending, blockedBy=[#1])
    → #1 completed → #2 解除阻塞 → #2 可被领取
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

# 任务状态枚举值
TASK_STATUS_PENDING = "pending"
TASK_STATUS_IN_PROGRESS = "in_progress"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_DELETED = "deleted"

VALID_STATUSES = {
    TASK_STATUS_PENDING,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_DELETED,
}

ACTIVE_STATUSES = {TASK_STATUS_PENDING, TASK_STATUS_IN_PROGRESS}


@dataclass
class TodoTask:
    """共享任务列表中的一个任务。

    Attributes:
        id: 任务 ID（递增数字字符串 "1","2",...）。
        subject: 简短标题（祈使句，如 "探索认证模块"）。
        description: 详细描述、验收标准。
        status: 当前状态 (pending/in_progress/completed)。
        owner: 任务拥有者（agent 名），None = 未分配。
        blocks: 此任务阻塞的任务 ID 列表。
        blocked_by: 阻塞此任务的任务 ID 列表。
        active_form: 进行时描述（如 "正在探索认证模块"）。
        metadata: 自由扩展字段。
        created_at: 创建时间戳。
        updated_at: 最后更新时间戳。
    """
    id: str
    subject: str
    description: str = ""
    status: str = TASK_STATUS_PENDING
    owner: Optional[str] = None
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    active_form: Optional[str] = None
    metadata: Optional[dict] = None
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def is_blocked(self) -> bool:
        """是否有未完成的阻塞任务。"""
        return len(self.blocked_by) > 0

    @property
    def is_available(self) -> bool:
        """是否可被领取（pending + 无阻塞）。"""
        return self.status == TASK_STATUS_PENDING and len(self.blocked_by) == 0

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "blocks": self.blocks,
            "blockedBy": self.blocked_by,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }
        if self.owner:
            d["owner"] = self.owner
        if self.active_form:
            d["activeForm"] = self.active_form
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, d: dict) -> TodoTask:
        return cls(
            id=d["id"],
            subject=d["subject"],
            description=d.get("description", ""),
            status=d.get("status", TASK_STATUS_PENDING),
            owner=d.get("owner"),
            blocks=d.get("blocks", []),
            blocked_by=d.get("blockedBy", []),
            active_form=d.get("activeForm"),
            metadata=d.get("metadata"),
            created_at=d.get("createdAt", 0.0),
            updated_at=d.get("updatedAt", 0.0),
        )


# ---------------------------------------------------------------------------
# 文件锁工具
# ---------------------------------------------------------------------------


def _ensure_dir(path: Path) -> None:
    """确保目录存在。"""
    path.mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def _file_lock(lock_path: Path, max_retries: int = 30):
    """跨平台文件锁上下文管理器。

    使用临时 lock 文件 + 轮询实现 Windows 兼容锁。
    Python 的 fcntl/msvcrt 在不同 Python 版本行为不一致，
    这里用 O_CREAT | O_EXCL 原子创建作为简单互斥锁。
    """
    import asyncio
    import os as _os

    _ensure_dir(lock_path.parent)

    # 轮询尝试创建 lock 文件（O_CREAT | O_EXCL = 原子创建）
    for attempt in range(max_retries + 1):
        try:
            fd = _os.open(
                str(lock_path),
                _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY,
                0o666,
            )
            _os.close(fd)
            break  # 获取锁成功
        except FileExistsError:
            if attempt >= max_retries:
                raise TimeoutError(
                    f"无法在 {max_retries} 次重试内获取文件锁: {lock_path}"
                )
            # 等待后重试（指数退避）
            time.sleep(min(0.005 * (2 ** attempt), 0.1))

    try:
        yield
    finally:
        # 释放锁：删除 lock 文件
        try:
            _os.unlink(str(lock_path))
        except FileNotFoundError:
            pass  # 已被删除


# ---------------------------------------------------------------------------
# 路径工具
# ---------------------------------------------------------------------------


def _jarvis_home() -> Path:
    home = Path(os.path.expanduser("~")) / ".jarvis"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _task_list_dir(task_list_id: str) -> Path:
    """任务列表目录。"""
    from agent.core.team import sanitize_name
    return _jarvis_home() / "tasks" / sanitize_name(task_list_id)


def _task_path(task_list_id: str, task_id: str) -> Path:
    """单个任务文件路径。"""
    return _task_list_dir(task_list_id) / f"{task_id}.json"


def _task_lock_path(task_list_id: str) -> Path:
    """任务列表锁文件路径。"""
    return _task_list_dir(task_list_id) / ".lock"


def _highwatermark_path(task_list_id: str) -> Path:
    """高水位线文件路径。"""
    return _task_list_dir(task_list_id) / ".highwatermark"


# ---------------------------------------------------------------------------
# 低层 CRUD
# ---------------------------------------------------------------------------


def _read_task_raw(task_list_id: str, task_id: str) -> Optional[dict]:
    """读取任务原始 JSON，不存在返回 None。"""
    path = _task_path(task_list_id, task_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return None


def _write_task_raw(task_list_id: str, data: dict) -> None:
    """原子写入任务 JSON。"""
    path = _task_path(task_list_id, data["id"])
    _ensure_dir(path.parent)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _delete_task_raw(task_list_id: str, task_id: str) -> None:
    """删除任务文件。"""
    path = _task_path(task_list_id, task_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _read_highwatermark(task_list_id: str) -> int:
    """读取高水位线。"""
    path = _highwatermark_path(task_list_id)
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, FileNotFoundError):
        return 0


def _write_highwatermark(task_list_id: str, value: int) -> None:
    """写入高水位线。"""
    path = _highwatermark_path(task_list_id)
    _ensure_dir(path.parent)
    path.write_text(str(value), encoding="utf-8")


def _find_highest_existing_id(task_list_id: str) -> int:
    """扫描目录找最高已用 ID。"""
    d = _task_list_dir(task_list_id)
    if not d.exists():
        return 0
    max_id = 0
    for f in d.iterdir():
        if f.suffix == ".json" and f.stem.isdigit():
            max_id = max(max_id, int(f.stem))
    return max_id


# ---------------------------------------------------------------------------
# 高层 API
# ---------------------------------------------------------------------------


class TaskList:
    """共享任务列表管理器。

    用法::

        tl = TaskList("my-project")
        tid = tl.create("探索认证模块", "找所有 auth 相关的代码")
        tl.update(tid, status="in_progress", owner="researcher")
        tasks = tl.list_all()
        tl.delete(tid)
    """

    def __init__(self, task_list_id: str) -> None:
        self._id = task_list_id
        # hook: 任务完成/删除时调用的回调（用于给 owner 发通知等）
        self._on_task_completed: Optional[Callable[[TodoTask], None]] = None
        self._on_task_deleted: Optional[Callable[[TodoTask], None]] = None
        self._on_owner_changed: Optional[Callable[[TodoTask, Optional[str]], None]] = None

    # ---- 生命周期 ----

    def ensure_dir(self) -> None:
        """确保任务列表目录存在。"""
        _ensure_dir(_task_list_dir(self._id))

    def reset(self) -> None:
        """重置任务列表（清空所有任务）。"""
        import shutil

        d = _task_list_dir(self._id)
        if d.exists():
            shutil.rmtree(d)
        _ensure_dir(d)

    # ---- CRUD ----

    def read(self, task_id: str) -> Optional[TodoTask]:
        """读取单个任务。"""
        data = _read_task_raw(self._id, task_id)
        if data is None:
            return None
        return TodoTask.from_dict(data)

    def list_all(self) -> list[TodoTask]:
        """列出所有非删除状态的任务。"""
        d = _task_list_dir(self._id)
        if not d.exists():
            return []
        tasks: list[TodoTask] = []
        for f in sorted(d.iterdir(), key=lambda x: x.name):
            if f.suffix == ".json" and f.stem.isdigit():
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    task = TodoTask.from_dict(data)
                    if task.status != TASK_STATUS_DELETED:
                        tasks.append(task)
                except (json.JSONDecodeError, KeyError):
                    pass
        return tasks

    def create(
        self,
        subject: str,
        description: str = "",
        *,
        active_form: Optional[str] = None,
        metadata: Optional[dict] = None,
        owner: Optional[str] = None,
    ) -> str:
        """创建新任务，返回任务 ID。

        任务 ID 是递增数字字符串，取 highwatermark 和目录扫描的最大值 + 1。
        带文件锁，保证并发安全。
        """
        lock_path = _task_lock_path(self._id)

        with _file_lock(lock_path):
            hwm = _read_highwatermark(self._id)
            scanned = _find_highest_existing_id(self._id)
            next_id = max(hwm, scanned) + 1

            now = time.time()
            task = TodoTask(
                id=str(next_id),
                subject=subject,
                description=description,
                active_form=active_form,
                metadata=metadata,
                owner=owner,
                created_at=now,
                updated_at=now,
            )

            _write_task_raw(self._id, task.to_dict())
            _write_highwatermark(self._id, next_id)
            return str(next_id)

    def update(
        self,
        task_id: str,
        *,
        subject: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        owner: Optional[str] = None,
        active_form: Optional[str] = None,
        add_blocks: Optional[list[str]] = None,
        add_blocked_by: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> Optional[TodoTask]:
        """更新任务（部分字段）。

        status='deleted' 会删除任务文件并清理依赖引用。
        add_blocks 将指定任务 ID 添加到此任务的 blocks 列表。
        add_blocked_by 将指定任务 ID 添加到此任务的 blocked_by 列表。

        带文件锁保证并发安全。
        """
        lock_path = _task_lock_path(self._id)

        old_owner: Optional[str] = None
        old_status: str = ""

        with _file_lock(lock_path):
            task = self.read(task_id)
            if task is None:
                return None

            old_owner = task.owner
            old_status = task.status

            # 字段级更新
            if subject is not None:
                task.subject = subject
            if description is not None:
                task.description = description
            if active_form is not None:
                task.active_form = active_form
            if metadata is not None:
                task.metadata = metadata
            if owner is not None:
                task.owner = owner if owner else None

            # 依赖链
            if add_blocks:
                for bid in add_blocks:
                    if bid not in task.blocks:
                        task.blocks.append(bid)
                    # 反向：在目标任务的 blocked_by 中添加此任务
                    self._add_reverse_dep(task_id, bid, from_side="blocks")

            if add_blocked_by:
                for bid in add_blocked_by:
                    if bid not in task.blocked_by:
                        task.blocked_by.append(bid)
                    # 反向：在源任务的 blocks 中添加此任务
                    self._add_reverse_dep(task_id, bid, from_side="blocked_by")

            # 状态变更（处理 deleted 特殊逻辑）
            if status is not None:
                if status == TASK_STATUS_DELETED:
                    self._delete_inner(task)
                    return None
                task.status = status

            # 自动清除阻塞引用：当任务完成时，不再阻塞任何人
            if task.status == TASK_STATUS_COMPLETED and task.blocks:
                self._clear_blocks(task)

            task.updated_at = time.time()
            _write_task_raw(self._id, task.to_dict())

        # 锁外回调
        if old_status != TASK_STATUS_COMPLETED and task.status == TASK_STATUS_COMPLETED:
            if self._on_task_completed:
                self._on_task_completed(task)

        if owner is not None and owner != old_owner and owner:
            if self._on_owner_changed:
                self._on_owner_changed(task, old_owner)

        return task

    def delete(self, task_id: str) -> Optional[TodoTask]:
        """删除任务（带依赖清理）。"""
        lock_path = _task_lock_path(self._id)

        with _file_lock(lock_path):
            task = self.read(task_id)
            if task is None:
                return None
            self._delete_inner(task)

        if self._on_task_deleted:
            self._on_task_deleted(task)

        return task

    def _delete_inner(self, task: TodoTask) -> None:
        """内部删除逻辑（需持有锁）。"""
        # 1. 更新高水位线（防 ID 重用）
        hwm = _read_highwatermark(self._id)
        task_id_int = int(task.id)
        if task_id_int > hwm:
            _write_highwatermark(self._id, task_id_int)

        # 2. 清理依赖引用：
        #    - 所有被此任务阻塞的任务（其他任务的 blocked_by 中移除此 ID）
        self._clear_references(task.id, task.blocks, "blocked_by")
        #    - 所有阻塞此任务的任务（其他任务的 blocks 中移除此 ID）
        self._clear_references(task.id, task.blocked_by, "blocks")

        # 3. 标记为 deleted 并保存（保留记录）
        task.status = TASK_STATUS_DELETED
        task.updated_at = time.time()
        _write_task_raw(self._id, task.to_dict())

    # ---- 内部辅助 ----

    def _add_reverse_dep(self, source_id: str, target_id: str, *, from_side: str) -> None:
        """在目标任务的对应字段中添加源任务 ID。

        from_side="blocks" → 在 target 的 blocked_by 中添加 source_id
        from_side="blocked_by" → 在 target 的 blocks 中添加 source_id
        """
        target = self.read(target_id)
        if target is None:
            return

        if from_side == "blocks":
            if source_id not in target.blocked_by:
                target.blocked_by.append(source_id)
        elif from_side == "blocked_by":
            if source_id not in target.blocks:
                target.blocks.append(source_id)

        target.updated_at = time.time()
        _write_task_raw(self._id, target.to_dict())

    def _clear_references(self, task_id: str, task_ids: list[str], field: str) -> None:
        """从指定 field 中移除 task_id 引用。"""
        for tid in task_ids:
            target = self.read(tid)
            if target is None:
                continue
            ref_list: list[str] = getattr(target, field, [])
            if task_id in ref_list:
                ref_list.remove(task_id)
                target.updated_at = time.time()
                _write_task_raw(self._id, target.to_dict())

    def _clear_blocks(self, task: TodoTask) -> None:
        """完成时清理阻塞关系。"""
        self._clear_references(task.id, task.blocks, "blocked_by")
        task.blocks = []

    # ---- 查询 ----

    def get_available_tasks(self) -> list[TodoTask]:
        """获取可领取的任务（pending + 无阻塞）。"""
        return [t for t in self.list_all() if t.is_available]

    def get_tasks_by_owner(self, owner: str) -> list[TodoTask]:
        """获取指定 owner 的任务。"""
        return [t for t in self.list_all() if t.owner == owner]

    def get_blocking_tasks(self, task_id: str) -> list[TodoTask]:
        """获取阻塞此任务的所有任务。"""
        task = self.read(task_id)
        if task is None:
            return []
        return [t for bid in task.blocked_by if (t := self.read(bid))]

    # ---- 回调设置 ----

    def set_hooks(
        self,
        *,
        on_completed: Optional[Callable[[TodoTask], None]] = None,
        on_deleted: Optional[Callable[[TodoTask], None]] = None,
        on_owner_changed: Optional[Callable[[TodoTask, Optional[str]], None]] = None,
    ) -> None:
        """设置任务生命周期回调。"""
        if on_completed:
            self._on_task_completed = on_completed
        if on_deleted:
            self._on_task_deleted = on_deleted
        if on_owner_changed:
            self._on_owner_changed = on_owner_changed
