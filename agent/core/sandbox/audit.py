"""沙箱审计日志。

记录所有沙箱相关操作:
- 命令执行（沙箱内/外）
- 风险评分结果
- 资源超限事件
- 文件快照/回滚
- 权限决策（自动放行/用户确认/拒绝）

日志存储: ~/.jarvis/sandbox_audit.jsonl（JSON Lines 格式）
自动轮转: 超过 max_entries 条时截断旧记录。

用法::

    auditor = SandboxAuditor()
    auditor.log_execution(command="rm -rf build/", risk_level="HIGH", sandboxed=True)
    auditor.log_violation(command="...", violation_type="memory_exceeded")
    history = auditor.get_recent(limit=20)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 默认最大记录数
_DEFAULT_MAX_ENTRIES = 500


@dataclass
class AuditEntry:
    """单条审计记录。"""
    timestamp: float
    event_type: str          # execution / violation / snapshot / rollback / permission
    command: str = ""
    risk_level: str = ""
    sandboxed: bool = False
    exit_code: int | None = None
    timed_out: bool = False
    resource_exceeded: bool = False
    snapshot_id: str = ""
    permission_decision: str = ""   # allow / deny / ask / auto_allow_sandbox
    detail: str = ""
    cwd: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ts": self.timestamp,
            "type": self.event_type,
        }
        if self.command:
            d["cmd"] = self.command[:200]  # 截断过长命令
        if self.risk_level:
            d["risk"] = self.risk_level
        if self.sandboxed:
            d["sandboxed"] = True
        if self.exit_code is not None:
            d["exit"] = self.exit_code
        if self.timed_out:
            d["timeout"] = True
        if self.resource_exceeded:
            d["res_exceeded"] = True
        if self.snapshot_id:
            d["snap"] = self.snapshot_id
        if self.permission_decision:
            d["perm"] = self.permission_decision
        if self.detail:
            d["detail"] = self.detail[:200]
        if self.cwd:
            d["cwd"] = self.cwd
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditEntry":
        return cls(
            timestamp=data.get("ts", 0),
            event_type=data.get("type", "unknown"),
            command=data.get("cmd", ""),
            risk_level=data.get("risk", ""),
            sandboxed=data.get("sandboxed", False),
            exit_code=data.get("exit"),
            timed_out=data.get("timeout", False),
            resource_exceeded=data.get("res_exceeded", False),
            snapshot_id=data.get("snap", ""),
            permission_decision=data.get("perm", ""),
            detail=data.get("detail", ""),
            cwd=data.get("cwd", ""),
        )


class SandboxAuditor:
    """沙箱审计器。

    以 JSON Lines 格式追加写入审计日志。
    """

    def __init__(
        self,
        log_path: str | None = None,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        enabled: bool = True,
    ) -> None:
        if log_path:
            self._log_path = Path(log_path)
        else:
            self._log_path = Path.home() / ".jarvis" / "sandbox_audit.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_entries = max_entries
        self._enabled = enabled
        self._entry_count = self._count_entries()

    @property
    def log_path(self) -> Path:
        return self._log_path

    @property
    def enabled(self) -> bool:
        return self._enabled

    def log_execution(
        self,
        command: str,
        risk_level: str,
        sandboxed: bool,
        exit_code: int | None = None,
        timed_out: bool = False,
        resource_exceeded: bool = False,
        cwd: str = "",
    ) -> None:
        """记录命令执行。"""
        self._append(AuditEntry(
            timestamp=time.time(),
            event_type="execution",
            command=command,
            risk_level=risk_level,
            sandboxed=sandboxed,
            exit_code=exit_code,
            timed_out=timed_out,
            resource_exceeded=resource_exceeded,
            cwd=cwd,
        ))

    def log_violation(
        self,
        command: str,
        violation_type: str,
        detail: str = "",
    ) -> None:
        """记录违规事件。"""
        self._append(AuditEntry(
            timestamp=time.time(),
            event_type="violation",
            command=command,
            detail=f"{violation_type}: {detail}",
        ))

    def log_snapshot(
        self,
        snapshot_id: str,
        paths: list[str],
        reason: str = "",
    ) -> None:
        """记录快照创建。"""
        self._append(AuditEntry(
            timestamp=time.time(),
            event_type="snapshot",
            snapshot_id=snapshot_id,
            detail=f"{reason} | {', '.join(paths[:5])}",
        ))

    def log_rollback(self, snapshot_id: str, success: bool) -> None:
        """记录回滚操作。"""
        self._append(AuditEntry(
            timestamp=time.time(),
            event_type="rollback",
            snapshot_id=snapshot_id,
            detail="success" if success else "failed",
        ))

    def log_permission(
        self,
        command: str,
        decision: str,
        risk_level: str = "",
        detail: str = "",
    ) -> None:
        """记录权限决策。"""
        self._append(AuditEntry(
            timestamp=time.time(),
            event_type="permission",
            command=command,
            risk_level=risk_level,
            permission_decision=decision,
            detail=detail,
        ))

    def get_recent(self, limit: int = 20, event_type: str = "") -> list[AuditEntry]:
        """获取最近的审计记录。

        Args:
            limit: 返回条数
            event_type: 过滤事件类型（空字符串=全部）
        """
        if not self._log_path.exists():
            return []

        entries: list[AuditEntry] = []
        try:
            lines = self._log_path.read_text(encoding="utf-8").strip().split("\n")
            for line in reversed(lines):  # 从最新开始
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    entry = AuditEntry.from_dict(data)
                    if event_type and entry.event_type != event_type:
                        continue
                    entries.append(entry)
                    if len(entries) >= limit:
                        break
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass

        return entries

    def get_stats(self) -> dict[str, Any]:
        """获取统计摘要。"""
        if not self._log_path.exists():
            return {"total": 0}

        stats: dict[str, int] = {
            "total": 0,
            "sandboxed": 0,
            "violations": 0,
            "timeouts": 0,
            "resource_exceeded": 0,
        }
        risk_counts: dict[str, int] = {}

        try:
            lines = self._log_path.read_text(encoding="utf-8").strip().split("\n")
            for line in lines:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    stats["total"] += 1
                    if data.get("sandboxed"):
                        stats["sandboxed"] += 1
                    if data.get("type") == "violation":
                        stats["violations"] += 1
                    if data.get("timeout"):
                        stats["timeouts"] += 1
                    if data.get("res_exceeded"):
                        stats["resource_exceeded"] += 1
                    risk = data.get("risk", "")
                    if risk:
                        risk_counts[risk] = risk_counts.get(risk, 0) + 1
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass

        stats["risk_distribution"] = risk_counts  # type: ignore
        return stats

    def clear(self) -> None:
        """清空审计日志。"""
        if self._log_path.exists():
            self._log_path.write_text("", encoding="utf-8")
            self._entry_count = 0

    # ---- 内部方法 ----

    def _append(self, entry: AuditEntry) -> None:
        """追加一条记录。"""
        if not self._enabled:
            return

        try:
            line = json.dumps(entry.to_dict(), ensure_ascii=False)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._entry_count += 1

            # 轮转检查
            if self._entry_count > self._max_entries:
                self._rotate()
        except OSError as e:
            logger.debug(f"[SandboxAuditor] 写入失败: {e}")

    def _rotate(self) -> None:
        """日志轮转: 保留最新的 max_entries 条。"""
        try:
            lines = self._log_path.read_text(encoding="utf-8").strip().split("\n")
            keep = lines[-self._max_entries:]
            self._log_path.write_text(
                "\n".join(keep) + "\n", encoding="utf-8"
            )
            self._entry_count = len(keep)
        except OSError:
            pass

    def _count_entries(self) -> int:
        """统计当前记录数。"""
        if not self._log_path.exists():
            return 0
        try:
            content = self._log_path.read_text(encoding="utf-8")
            return len([l for l in content.strip().split("\n") if l.strip()])
        except OSError:
            return 0
