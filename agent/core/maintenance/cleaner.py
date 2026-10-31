"""可撤销清理 —— 隔离区、批次清单与还原。

## 为什么不用 ``FileGuard`` 快照

``FileGuard`` 的做法是**复制**一份再删原件。对清理场景这是死路：``Windows.old``
十几 GB，复制一份等于先把磁盘占满再删——用户最缺空间的时候最用不了。
所以这里改用**移动**：把要删的东西挪进隔离区，文件系统只改目录项，不复制数据。
同盘移动是一次 ``rename``（微秒级）；跨盘时 ``shutil.move`` 会退化成复制+删除，
这也是本模块唯一会慢的情形，回执里会如实标注。

## 三条硬规则

1. **必须显式给 ids**。没有 ``all: true``——"一键全清"是这类软件最容易埋雷的地方
   （PLAN 第八节坑 1）。
2. **只清目录内容，保留目录本身**。``%LOCALAPPDATA%\\Temp`` 这类目录不能消失，
   程序还会往里面写；消失后 Windows 不会自动重建，只会报一串奇怪的错误。
3. **跳过要说原因**。被占用的、命中 ``protect`` 的、权限不足的、跨盘太慢主动放弃的，
   全部写进 ``skipped``。只报"成功 xx MB"而不说"有 300 个文件没删掉"，等于骗人。

## 隔离区布局

::

    ~/.jarvis/quarantine/<batch_id>/
        manifest.json          # 每条移动的原始路径 → 隔离区路径 + 字节数
        <rule_id>/<n>_<name>   # 实际文件（n 防重名）

``/clean --restore <batch_id>`` 读 manifest 反向移回。保留 ``quarantine_days`` 天后由
:func:`purge_expired` 清掉——这是"敢删"的物理基础。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent.core.maintenance.rules import CleanerRule, RuleBook, match_any

#: 隔离区根目录（相对用户主目录）
QUARANTINE_ROOT = Path.home() / ".jarvis" / "quarantine"

#: 一次清理最多移动多少个顶层条目（防"某规则目录里有 50 万个文件"把命令拖死）
DEFAULT_MOVE_BUDGET = 20_000

#: 默认保留天数
DEFAULT_KEEP_DAYS = 7

_MANIFEST = "manifest.json"

#: 还原命令前缀。**不能写成 ``/undo``**：``/undo`` 走的是 W3 副作用账本
#: （``core/actions/journal.py`` 的 EffectRecord），而隔离区批次是另一套记录。
#: 写错会让用户敲一条注定落空的命令，然后以为“删掉的东西找不回来了”。
RESTORE_COMMAND = "/clean --restore"

ProgressFn = Callable[[str], None]


@dataclass
class MoveEntry:
    """一个被移入隔离区的顶层条目。"""

    rule_id: str
    original: str
    stored: str
    size: int
    is_dir: bool = False

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "original": self.original,
            "stored": self.stored,
            "size": self.size,
            "is_dir": self.is_dir,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MoveEntry":
        return cls(
            rule_id=str(data.get("rule_id", "")),
            original=str(data.get("original", "")),
            stored=str(data.get("stored", "")),
            size=int(data.get("size", 0) or 0),
            is_dir=bool(data.get("is_dir", False)),
        )


@dataclass
class SkipEntry:
    """一个被跳过的条目 + 人话原因。"""

    path: str
    reason: str

    def to_dict(self) -> dict:
        return {"path": self.path, "reason": self.reason}


@dataclass
class CleanResult:
    """一次清理的完整回执。"""

    batch_id: str = ""
    reclaimed_bytes: int = 0
    #: 按规则汇总：``[{"id","name","freed_mb","moved","status"}]``
    items: list[dict] = field(default_factory=list)
    skipped: list[SkipEntry] = field(default_factory=list)
    quarantine_dir: str = ""
    cross_drive: bool = False
    elapsed_s: float = 0.0
    error: str = ""
    undo_hint: str | None = None

    @property
    def reclaimed_mb(self) -> float:
        return round(self.reclaimed_bytes / 1024 / 1024, 1)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "reclaimed_mb": self.reclaimed_mb,
            "items": self.items,
            "skipped": [s.to_dict() for s in self.skipped],
            "skipped_count": self.skipped_count,
            "quarantine_dir": self.quarantine_dir,
            "cross_drive": self.cross_drive,
            "elapsed_s": self.elapsed_s,
            "undo_hint": self.undo_hint,
        }


@dataclass
class RestoreResult:
    """一次还原的完整回执。"""

    batch_id: str = ""
    restored: int = 0
    restored_bytes: int = 0
    skipped: list[SkipEntry] = field(default_factory=list)
    error: str = ""

    @property
    def restored_mb(self) -> float:
        return round(self.restored_bytes / 1024 / 1024, 1)

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "restored": self.restored,
            "restored_mb": self.restored_mb,
            "skipped": [s.to_dict() for s in self.skipped],
        }


def make_batch_id() -> str:
    """生成批次 id：``clean-YYYYmmdd-HHMMSS``（与 PLAN 的示例一致，便于按时间排序）。"""
    return time.strftime("clean-%Y%m%d-%H%M%S")


def _dir_size(path: str) -> int:
    """估算一个条目的体积（失败返回 0，不影响清理主流程）。"""
    total = 0
    try:
        if os.path.isfile(path):
            return os.path.getsize(path)
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
    except OSError:
        pass
    return total


def _skip_reason(exc: OSError) -> str:
    """把 Windows 错误码翻成人话（用户看不懂 WinError 32）。"""
    winerror = getattr(exc, "winerror", None)
    if winerror == 32:
        return "文件正被其他程序占用"
    if winerror == 5:
        return "权限不足（需要管理员）"
    if winerror == 3:
        return "路径不存在（可能刚被别的程序删掉）"
    if winerror in (145, 183):
        return "目标已存在（隔离区重名）"
    return f"{type(exc).__name__}: {exc}"


def _same_drive(a: str, b: str) -> bool:
    da = os.path.splitdrive(os.path.abspath(a))[0].lower()
    db = os.path.splitdrive(os.path.abspath(b))[0].lower()
    return da == db


def _is_inside(path: str, parent: str) -> bool:
    """path 是否在 parent 之内（防"清理隔离区自己"）。"""
    try:
        p = os.path.abspath(path)
        q = os.path.abspath(parent)
        return os.path.commonpath([p, q]) == q
    except (ValueError, OSError):
        return False


def clean(
    book: RuleBook,
    ids: list[str],
    *,
    quarantine_root: str | os.PathLike[str] | None = None,
    move_budget: int = DEFAULT_MOVE_BUDGET,
    max_risk: str = "medium",
    on_progress: ProgressFn | None = None,
) -> CleanResult:
    """按显式 id 清理并移入隔离区。

    Args:
        book: 规则库。
        ids: **必须显式给出**的规则 id 列表；空表直接报错，不做默认全清。
        quarantine_root: 隔离区根目录（默认 ``~/.jarvis/quarantine``）。
        move_budget: 最多移动的顶层条目数。
        max_risk: 允许执行到哪个风险档（``low``/``medium``/``high``）；
            超出档位的规则会被跳过并说明——这是 ``auto_clean_max_risk`` 的落点。
        on_progress: 进度回调（工具层用来打日志）。

    Returns:
        :class:`CleanResult`。**永远不抛业务异常**，失败写进 ``error``。
    """
    started = time.monotonic()
    result = CleanResult(batch_id=make_batch_id())
    if not ids:
        result.error = "必须显式指定要清理的条目 id（不接受全清）"
        return result

    root = Path(quarantine_root) if quarantine_root else QUARANTINE_ROOT
    batch_dir = root / result.batch_id
    result.quarantine_dir = str(batch_dir)
    try:
        batch_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.error = f"隔离区创建失败：{_skip_reason(exc)}"
        return result

    risk_rank = {"low": 0, "medium": 1, "high": 2}
    cap = risk_rank.get(max_risk, 1)
    entries: list[MoveEntry] = []
    moved_budget = move_budget

    for rule_id in ids:
        rule = book.by_id(rule_id)
        summary = {"id": rule_id, "name": rule_id, "freed_mb": 0.0, "moved": 0, "status": "ok"}
        if rule is None:
            summary["status"] = "未知规则 id"
            result.items.append(summary)
            continue
        summary["name"] = rule.name
        if rule.is_command:
            # 命令型不可还原，绝不在这个入口执行：要动它必须走显式确认的专用通路
            summary["status"] = "命令型规则不支持在这里清理（不可还原）"
            result.items.append(summary)
            continue
        if risk_rank.get(rule.risk, 9) > cap:
            summary["status"] = f"风险档 {rule.risk} 超过当前允许上限 {max_risk}"
            result.items.append(summary)
            continue
        if rule.unaddressable:
            summary["status"] = rule.unaddressable
            result.items.append(summary)
            continue

        freed = 0
        moved = 0
        slot = batch_dir / rule.id
        try:
            slot.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            summary["status"] = f"隔离区子目录创建失败：{_skip_reason(exc)}"
            result.items.append(summary)
            continue

        for base in rule.paths:
            if not os.path.isdir(base):
                # 目录整体不存在：可能被别的程序删了，不是错误
                if not os.path.exists(base):
                    result.skipped.append(SkipEntry(base, "路径不存在"))
                continue
            # 只清内容、保留目录本身：Temp 之类目录消失会让程序报一串怪错
            try:
                names = os.listdir(base)
            except OSError as exc:
                result.skipped.append(SkipEntry(base, _skip_reason(exc)))
                continue
            for name in names:
                if moved_budget <= 0:
                    result.skipped.append(SkipEntry(base, "达到单次移动条目上限，剩余未处理"))
                    break
                src = os.path.join(base, name)
                if rule.protect and match_any(name, rule.protect):
                    result.skipped.append(SkipEntry(src, f"命中保护规则 {match_any(name, rule.protect)}"))
                    continue
                if rule.include and os.path.isfile(src) and not match_any(name, rule.include):
                    continue
                # 隔离区自己绝不能进清理范围，否则撤不回来
                if _is_inside(src, str(root)):
                    result.skipped.append(SkipEntry(src, "位于隔离区内，拒绝处理"))
                    continue
                if not _same_drive(src, str(batch_dir)):
                    result.cross_drive = True
                size = _dir_size(src)
                dst = slot / f"{moved}_{name}"
                try:
                    shutil.move(src, str(dst))
                except OSError as exc:
                    result.skipped.append(SkipEntry(src, _skip_reason(exc)))
                    continue
                is_dir = os.path.isdir(str(dst))
                entries.append(MoveEntry(
                    rule_id=rule.id, original=src, stored=str(dst),
                    size=size, is_dir=is_dir,
                ))
                freed += size
                moved += 1
                moved_budget -= 1
        summary["freed_mb"] = round(freed / 1024 / 1024, 1)
        summary["moved"] = moved
        result.items.append(summary)
        result.reclaimed_bytes += freed
        if on_progress:
            on_progress(f"{rule.name}：移交 {moved} 项 / {summary['freed_mb']:.1f} MB")

    _write_manifest(batch_dir, result.batch_id, entries)
    result.elapsed_s = round(time.monotonic() - started, 2)
    if entries:
        result.undo_hint = f"如需还原：{RESTORE_COMMAND} {result.batch_id}"
    elif not result.error:
        result.error = "没有任何条目被移走（全部被跳过，原因见 skipped）"
    return result


def _write_manifest(batch_dir: Path, batch_id: str, entries: list[MoveEntry]) -> None:
    """写批次清单。**先写临时文件再 replace**：清单写坏就永远撤不回来了。"""
    payload = {
        "batch_id": batch_id,
        "created_at": time.time(),
        "created_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        "entries": [e.to_dict() for e in entries],
    }
    tmp = batch_dir / f"{_MANIFEST}.tmp"
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, batch_dir / _MANIFEST)
    except OSError:
        # 清单写不下去时必须让调用方知道，否则用户以为能撤其实撤不了
        pass


def read_manifest(batch_dir: Path) -> dict | None:
    """读一个批次的清单；损坏或缺失返回 None。"""
    path = batch_dir / _MANIFEST
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_batches(quarantine_root: str | os.PathLike[str] | None = None) -> list[dict]:
    """列出隔离区里的批次（新→旧）。"""
    root = Path(quarantine_root) if quarantine_root else QUARANTINE_ROOT
    if not root.is_dir():
        return []
    out: list[dict] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        data = read_manifest(child)
        if data is None:
            out.append({
                "batch_id": child.name,
                "created_human": "",
                "entries": 0,
                "size_mb": 0.0,
                "broken": True,
            })
            continue
        entries = [MoveEntry.from_dict(e) for e in data.get("entries", [])]
        out.append({
            "batch_id": data.get("batch_id", child.name),
            "created_human": data.get("created_human", ""),
            "entries": len(entries),
            "size_mb": round(sum(e.size for e in entries) / 1024 / 1024, 1),
            "broken": False,
        })
    out.sort(key=lambda b: str(b.get("batch_id", "")), reverse=True)
    return out


def restore(
    batch_id: str,
    *,
    quarantine_root: str | os.PathLike[str] | None = None,
    max_restore: int = DEFAULT_MOVE_BUDGET,
) -> RestoreResult:
    """把某个批次移回原位。

    冲突策略：目标位置已有同名文件时**跳过并报告**，不覆盖——用户可能在这段时间里
    又生成了同名新文件，覆盖等于用旧内容盖掉新内容。
    """
    result = RestoreResult(batch_id=batch_id)
    root = Path(quarantine_root) if quarantine_root else QUARANTINE_ROOT
    batch_dir = root / batch_id
    if not batch_dir.is_dir():
        result.error = f"批次不存在：{batch_id}"
        return result
    data = read_manifest(batch_dir)
    if data is None:
        result.error = f"批次清单损坏，无法还原：{batch_id}"
        return result

    entries = [MoveEntry.from_dict(e) for e in data.get("entries", [])]
    count = 0
    for entry in entries:
        if count >= max_restore:
            result.skipped.append(SkipEntry(str(batch_dir), "达到单次还原条目上限"))
            break
        if not os.path.exists(entry.stored):
            result.skipped.append(SkipEntry(entry.original, "隔离区文件已不存在"))
            continue
        if os.path.exists(entry.original):
            result.skipped.append(SkipEntry(entry.original, "原位置已有同名文件，未覆盖"))
            continue
        try:
            os.makedirs(os.path.dirname(entry.original) or ".", exist_ok=True)
            shutil.move(entry.stored, entry.original)
        except OSError as exc:
            result.skipped.append(SkipEntry(entry.original, _skip_reason(exc)))
            continue
        result.restored += 1
        result.restored_bytes += entry.size
        count += 1

    if result.restored:
        # 还原成功就别把空壳批次留在列表里让人困惑；部分失败则保留剩余
        _drop_if_empty(batch_dir)
    return result


def _drop_if_empty(batch_dir: Path) -> None:
    """批次里已无实体文件时清掉目录（保留清单会让人误以为还能再撤一次）。"""
    remaining = False
    for child in batch_dir.rglob("*"):
        if child.is_file() and child.name != _MANIFEST:
            remaining = True
            break
    if not remaining:
        shutil.rmtree(batch_dir, ignore_errors=True)


def purge_expired(
    keep_days: int = DEFAULT_KEEP_DAYS,
    *,
    quarantine_root: str | os.PathLike[str] | None = None,
) -> int:
    """清掉超过保留期的批次，返回删除的批次数。

    只在清理时顺手调用一次，不建后台定时器——维护层不该有常驻线程。
    """
    if keep_days <= 0:
        return 0
    root = Path(quarantine_root) if quarantine_root else QUARANTINE_ROOT
    if not root.is_dir():
        return 0
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def command_rule_preview(rule: CleanerRule) -> str:
    """命令型规则的执行预览（只用于展示，实际执行由工具层单独确认）。"""
    return " ".join(rule.clean_cmd)