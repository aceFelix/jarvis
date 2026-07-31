"""工具操作审计日志。

S-02 改进项：扩展现有沙箱审计，覆盖所有工具调用。
特别记录 yolo 模式下的写操作（FileWrite/FileEdit/Bash/DeleteFile）。

日志存储: ~/.jarvis/tool_audit.jsonl（JSON Lines 格式）
沙箱审计仍独立存储在 ~/.jarvis/sandbox_audit.jsonl。

@author aceFelix
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# 写操作工具名（需特别审计）
_WRITE_TOOLS = frozenset({
    "Bash", "BashTool",
    "FileWrite", "FileEdit", "DeleteFile",
    "Write", "edit_file",
})

# 最大记录数
_DEFAULT_MAX_ENTRIES = 1000


class ToolAuditor:
    """工具操作审计器。

    以 JSON Lines 格式追加写入审计日志，记录所有工具调用的：
    - 工具名、输入参数（截断）
    - 权限模式（yolo / ask / default）
    - 执行耗时、成功/失败
    - 写操作额外标记

    用法::

        auditor = ToolAuditor()
        auditor.log_call(
            tool_name="FileWrite", args={"path": "/tmp/test.txt"},
            permission_mode="yolo", duration_ms=150, success=True,
        )
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
            self._log_path = Path.home() / ".jarvis" / "tool_audit.jsonl"
        self._max_entries = max_entries
        self._enabled = enabled
        # 目录创建失败（权限不足/只读文件系统）时降级为禁用，不抛异常——
        # 审计是辅助功能，绝不能因日志目录问题炸掉主流程（CI 无 root 权限时曾触发）
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def log_call(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        permission_mode: str = "",
        duration_ms: float = 0,
        success: bool = True,
        error_msg: str = "",
    ) -> None:
        """记录一次工具调用。

        @author aceFelix
        """
        if not self._enabled:
            return

        entry: dict[str, Any] = {
            "ts": time.time(),
            "tool": tool_name,
            "perm": permission_mode,
            "ok": success,
        }
        if duration_ms:
            entry["dur_ms"] = round(duration_ms, 1)
        if args:
            # 截断参数，避免日志过大
            args_str = json.dumps(args, ensure_ascii=False, default=str)
            entry["args"] = args_str[:500]
        if error_msg:
            entry["err"] = error_msg[:200]
        if tool_name in _WRITE_TOOLS:
            entry["write_op"] = True

        self._append(entry)

    def get_recent(self, limit: int = 50, write_only: bool = False) -> list[dict[str, Any]]:
        """获取最近 N 条审计记录。

        Args:
            limit: 最大返回条数
            write_only: 仅返回写操作记录

        @author aceFelix
        """
        if not self._log_path.exists():
            return []

        entries = []
        try:
            for line in self._log_path.read_text(encoding="utf-8").strip().splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if not write_only or entry.get("write_op"):
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue
        except OSError:
            return []

        return entries[-limit:]

    def _append(self, entry: dict[str, Any]) -> None:
        """追加一条记录到日志文件，自动轮转。"""
        try:
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(line)

            # 简单轮转：超过 max_entries * 1.5 时截断
            if self._log_path.stat().st_size > 1024 * 1024:  # 1MB 触发截断
                self._rotate()
        except OSError:
            pass  # 审计日志写入失败不影响主流程

    def _rotate(self) -> None:
        """截断旧记录，保留最近 max_entries 条。"""
        try:
            lines = self._log_path.read_text(encoding="utf-8").strip().splitlines()
            if len(lines) > self._max_entries:
                keep = lines[-self._max_entries:]
                self._log_path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            pass


# 全局单例
_auditor: ToolAuditor | None = None


def get_tool_auditor() -> ToolAuditor:
    """获取全局工具审计器单例。

    @author aceFelix
    """
    global _auditor
    if _auditor is None:
        _auditor = ToolAuditor()
    return _auditor
