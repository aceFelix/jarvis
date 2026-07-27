"""文件保护模块 —— 快照与回滚。

在高风险操作执行前，对目标文件/目录创建快照（备份副本）。
如果操作失败或用户要求撤销，可以从快照恢复。

设计:
- 快照存储在 ~/.jarvis/sandbox_snapshots/<timestamp>_<hash>/ 下
- 每个快照记录: 原始路径、备份路径、时间戳、操作描述
- 自动清理: 保留最近 N 个快照（默认 20），超出的自动删除
- 轻量级: 小文件直接复制，大文件（>50MB）只记录元数据不备份

用法::

    guard = FileGuard()
    snap_id = guard.snapshot("E:\\project\\config.toml", reason="修改配置")
    # ... 执行操作 ...
    guard.rollback(snap_id)  # 恢复
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 快照元数据文件名
_MANIFEST = "manifest.json"
# 大文件阈值（超过此大小不备份内容，只记录元数据）
_LARGE_FILE_THRESHOLD = 50 * 1024 * 1024  # 50MB
# 默认保留快照数
_DEFAULT_MAX_SNAPSHOTS = 20


@dataclass
class SnapshotEntry:
    """单个文件/目录的快照记录。"""
    original_path: str
    backup_path: str          # 备份文件路径（空字符串表示大文件未备份）
    is_directory: bool = False
    existed: bool = True      # 原始文件是否存在（False 表示是新建的）
    size: int = 0
    mtime: float = 0.0


@dataclass
class Snapshot:
    """一次快照操作（可能包含多个文件）。"""
    id: str
    timestamp: float
    reason: str
    entries: list[SnapshotEntry] = field(default_factory=list)
    rolled_back: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "reason": self.reason,
            "rolled_back": self.rolled_back,
            "entries": [
                {
                    "original_path": e.original_path,
                    "backup_path": e.backup_path,
                    "is_directory": e.is_directory,
                    "existed": e.existed,
                    "size": e.size,
                    "mtime": e.mtime,
                }
                for e in self.entries
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Snapshot":
        entries = [
            SnapshotEntry(
                original_path=e["original_path"],
                backup_path=e["backup_path"],
                is_directory=e.get("is_directory", False),
                existed=e.get("existed", True),
                size=e.get("size", 0),
                mtime=e.get("mtime", 0.0),
            )
            for e in data.get("entries", [])
        ]
        return cls(
            id=data["id"],
            timestamp=data["timestamp"],
            reason=data.get("reason", ""),
            entries=entries,
            rolled_back=data.get("rolled_back", False),
        )


class FileGuard:
    """文件保护器。

    提供快照创建、回滚、清理功能。
    """

    def __init__(
        self,
        snapshot_dir: str | None = None,
        max_snapshots: int = _DEFAULT_MAX_SNAPSHOTS,
    ) -> None:
        if snapshot_dir:
            self._base_dir = Path(snapshot_dir)
        else:
            self._base_dir = Path.home() / ".jarvis" / "sandbox_snapshots"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._max_snapshots = max_snapshots

    @property
    def snapshot_dir(self) -> Path:
        return self._base_dir

    def snapshot(
        self,
        paths: str | list[str],
        reason: str = "",
    ) -> str:
        """为一个或多个路径创建快照。

        Args:
            paths: 单个路径或路径列表
            reason: 操作描述（如 "修改配置文件"）

        Returns:
            快照 ID（用于回滚）
        """
        if isinstance(paths, str):
            paths = [paths]

        snap_id = self._generate_id(reason)
        snap_dir = self._base_dir / snap_id
        snap_dir.mkdir(parents=True, exist_ok=True)

        snapshot = Snapshot(
            id=snap_id,
            timestamp=time.time(),
            reason=reason,
        )

        for i, path in enumerate(paths):
            entry = self._snapshot_one(path, snap_dir, i)
            snapshot.entries.append(entry)

        # 写入 manifest
        manifest_path = snap_dir / _MANIFEST
        manifest_path.write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        logger.info(f"[FileGuard] 快照已创建: {snap_id} ({len(paths)} 个路径, 原因: {reason})")

        # 自动清理旧快照
        self._cleanup_old_snapshots()

        return snap_id

    def rollback(self, snap_id: str) -> bool:
        """回滚到指定快照。

        Returns:
            是否成功回滚
        """
        snap_dir = self._base_dir / snap_id
        manifest_path = snap_dir / _MANIFEST

        if not manifest_path.exists():
            logger.warning(f"[FileGuard] 快照不存在: {snap_id}")
            return False

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        snapshot = Snapshot.from_dict(data)

        if snapshot.rolled_back:
            logger.warning(f"[FileGuard] 快照已回滚过: {snap_id}")
            return False

        success = True
        for entry in snapshot.entries:
            try:
                self._rollback_one(entry)
            except Exception as e:
                logger.error(f"[FileGuard] 回滚失败 {entry.original_path}: {e}")
                success = False

        # 标记已回滚
        snapshot.rolled_back = True
        manifest_path.write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        logger.info(f"[FileGuard] 回滚{'成功' if success else '部分失败'}: {snap_id}")
        return success

    def list_snapshots(self) -> list[dict[str, Any]]:
        """列出所有快照（按时间倒序）。"""
        snapshots = []
        for d in self._base_dir.iterdir():
            if d.is_dir() and (d / _MANIFEST).exists():
                try:
                    data = json.loads((d / _MANIFEST).read_text(encoding="utf-8"))
                    snapshots.append({
                        "id": data["id"],
                        "timestamp": data["timestamp"],
                        "reason": data.get("reason", ""),
                        "files": len(data.get("entries", [])),
                        "rolled_back": data.get("rolled_back", False),
                    })
                except (json.JSONDecodeError, KeyError):
                    continue
        snapshots.sort(key=lambda s: s["timestamp"], reverse=True)
        return snapshots

    def delete_snapshot(self, snap_id: str) -> bool:
        """删除指定快照。"""
        snap_dir = self._base_dir / snap_id
        if snap_dir.exists():
            shutil.rmtree(snap_dir, ignore_errors=True)
            return True
        return False

    def get_snapshot_info(self, snap_id: str) -> Snapshot | None:
        """获取快照详情。"""
        manifest_path = self._base_dir / snap_id / _MANIFEST
        if not manifest_path.exists():
            return None
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return Snapshot.from_dict(data)

    # ---- 内部方法 ----

    def _snapshot_one(self, path: str, snap_dir: Path, index: int) -> SnapshotEntry:
        """快照单个路径。"""
        p = Path(path).resolve()
        is_dir = p.is_dir()
        existed = p.exists()

        entry = SnapshotEntry(
            original_path=str(p),
            backup_path="",
            is_directory=is_dir,
            existed=existed,
        )

        if not existed:
            # 文件不存在（可能是新建操作），无需备份
            return entry

        # 获取文件信息
        if is_dir:
            entry.size = self._dir_size(p)
        else:
            stat = p.stat()
            entry.size = stat.st_size
            entry.mtime = stat.st_mtime

        # 大文件不备份内容
        if entry.size > _LARGE_FILE_THRESHOLD:
            logger.info(f"[FileGuard] 跳过大文件备份: {p} ({entry.size / 1024 / 1024:.1f}MB)")
            return entry

        # 执行备份
        backup_name = f"{index}_{p.name}"
        backup_path = snap_dir / backup_name

        try:
            if is_dir:
                shutil.copytree(str(p), str(backup_path), dirs_exist_ok=True)
            else:
                shutil.copy2(str(p), str(backup_path))
            entry.backup_path = str(backup_path)
        except Exception as e:
            logger.warning(f"[FileGuard] 备份失败 {p}: {e}")

        return entry

    def _rollback_one(self, entry: SnapshotEntry) -> None:
        """回滚单个文件。"""
        original = Path(entry.original_path)

        if not entry.existed:
            # 原始不存在 → 删除新建的文件
            if original.exists():
                if original.is_dir():
                    shutil.rmtree(str(original), ignore_errors=True)
                else:
                    original.unlink(missing_ok=True)
            return

        if not entry.backup_path:
            # 没有备份（大文件），无法回滚
            logger.warning(f"[FileGuard] 无备份，无法回滚: {entry.original_path}")
            return

        backup = Path(entry.backup_path)
        if not backup.exists():
            logger.warning(f"[FileGuard] 备份文件丢失: {entry.backup_path}")
            return

        # 恢复
        if original.exists():
            if original.is_dir():
                shutil.rmtree(str(original), ignore_errors=True)
            else:
                original.unlink()

        if entry.is_directory:
            shutil.copytree(str(backup), str(original))
        else:
            shutil.copy2(str(backup), str(original))

    def _cleanup_old_snapshots(self) -> None:
        """清理超出保留数量的旧快照。"""
        snapshots = []
        for d in self._base_dir.iterdir():
            if d.is_dir() and (d / _MANIFEST).exists():
                try:
                    mtime = d.stat().st_mtime
                    snapshots.append((mtime, d))
                except OSError:
                    continue

        if len(snapshots) <= self._max_snapshots:
            return

        # 按时间排序，删除最旧的
        snapshots.sort(key=lambda x: x[0])
        to_delete = snapshots[: len(snapshots) - self._max_snapshots]
        for _, d in to_delete:
            shutil.rmtree(str(d), ignore_errors=True)
            logger.debug(f"[FileGuard] 清理旧快照: {d.name}")

    @staticmethod
    def _generate_id(reason: str) -> str:
        """生成快照 ID: 时间戳_哈希前缀。"""
        ts = time.strftime("%Y%m%d_%H%M%S")
        h = hashlib.md5(f"{reason}{time.time()}".encode()).hexdigest()[:6]
        return f"{ts}_{h}"

    @staticmethod
    def _dir_size(path: Path) -> int:
        """计算目录总大小。"""
        total = 0
        try:
            for f in path.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
        except (PermissionError, OSError):
            pass
        return total
