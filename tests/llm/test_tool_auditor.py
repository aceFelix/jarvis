"""工具审计器单元测试。

覆盖 ToolAuditor 的 log_call / get_recent / enabled / get_tool_auditor 单例。

@author aceFelix
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from agent.core.audit.tool_auditor import ToolAuditor, get_tool_auditor


class TestToolAuditor:
    """ToolAuditor 核心功能测试。"""

    def test_disabled_does_not_write(self) -> None:
        """关闭时不写入任何内容。"""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            tmp = Path(f.name)
        try:
            auditor = ToolAuditor(log_path=str(tmp), enabled=False)
            auditor.log_call("TestTool", args={"a": 1})
            content = tmp.read_text(encoding="utf-8").strip()
            assert content == ""
        finally:
            tmp.unlink(missing_ok=True)

    def test_log_call_writes_entry(self) -> None:
        """一次调用写入一条 JSON Lines 记录。"""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            tmp = Path(f.name)
        try:
            auditor = ToolAuditor(log_path=str(tmp))
            auditor.log_call("FileRead", args={"path": "/tmp/test.txt"}, permission_mode="yolo", duration_ms=150, success=True)
            content = tmp.read_text(encoding="utf-8").strip()
            assert content, "应写入内容"
            entry = json.loads(content)
            assert entry["tool"] == "FileRead"
            assert entry["perm"] == "yolo"
            assert entry["ok"] is True
            assert entry["dur_ms"] == 150
            assert "args" in entry
        finally:
            tmp.unlink(missing_ok=True)

    def test_write_operation_marked(self) -> None:
        """写操作（FileWrite / Bash / DeleteFile）标记 write_op=True。"""
        write_tools = ["FileWrite", "Bash", "DeleteFile", "FileEdit"]
        for tool_name in write_tools:
            with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
                tmp = Path(f.name)
            try:
                auditor = ToolAuditor(log_path=str(tmp))
                auditor.log_call(tool_name)
                content = tmp.read_text(encoding="utf-8").strip()
                entry = json.loads(content)
                assert entry.get("write_op") is True, f"{tool_name} should be marked write_op"
            finally:
                tmp.unlink(missing_ok=True)

    def test_read_only_not_marked(self) -> None:
        """只读工具不标记 write_op。"""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            tmp = Path(f.name)
        try:
            auditor = ToolAuditor(log_path=str(tmp))
            auditor.log_call("FileRead")
            content = tmp.read_text(encoding="utf-8").strip()
            entry = json.loads(content)
            assert "write_op" not in entry
        finally:
            tmp.unlink(missing_ok=True)

    def test_error_logged(self) -> None:
        """失败调用记录 error_msg。"""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            tmp = Path(f.name)
        try:
            auditor = ToolAuditor(log_path=str(tmp))
            auditor.log_call("Bash", success=False, error_msg="Permission denied")
            content = tmp.read_text(encoding="utf-8").strip()
            entry = json.loads(content)
            assert entry["ok"] is False
            assert entry["err"] == "Permission denied"
        finally:
            tmp.unlink(missing_ok=True)

    def test_args_truncated(self) -> None:
        """参数超过 500 字符时截断。"""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            tmp = Path(f.name)
        try:
            auditor = ToolAuditor(log_path=str(tmp))
            long_content = "x" * 600
            auditor.log_call("FileWrite", args={"content": long_content})
            content = tmp.read_text(encoding="utf-8").strip()
            entry = json.loads(content)
            assert len(entry["args"]) <= 520  # JSON 序列化可能略超
        finally:
            tmp.unlink(missing_ok=True)

    def test_error_msg_truncated(self) -> None:
        """错误消息超过 200 字符时截断。"""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            tmp = Path(f.name)
        try:
            auditor = ToolAuditor(log_path=str(tmp))
            long_err = "e" * 300
            auditor.log_call("Bash", success=False, error_msg=long_err)
            content = tmp.read_text(encoding="utf-8").strip()
            entry = json.loads(content)
            assert len(entry["err"]) <= 210
        finally:
            tmp.unlink(missing_ok=True)

    def test_get_recent_returns_entries(self) -> None:
        """get_recent 返回最近记录。"""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            tmp = Path(f.name)
        try:
            auditor = ToolAuditor(log_path=str(tmp))
            for i in range(5):
                auditor.log_call(f"Tool{i}")
            entries = auditor.get_recent(limit=3)
            assert len(entries) == 3
            assert entries[-1]["tool"] == "Tool4"
        finally:
            tmp.unlink(missing_ok=True)

    def test_get_recent_write_only(self) -> None:
        """write_only=True 仅返回写操作记录。"""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            tmp = Path(f.name)
        try:
            auditor = ToolAuditor(log_path=str(tmp))
            auditor.log_call("FileRead")
            auditor.log_call("FileWrite")
            auditor.log_call("Grep")
            write_entries = auditor.get_recent(write_only=True)
            assert len(write_entries) == 1
            assert write_entries[0]["tool"] == "FileWrite"
        finally:
            tmp.unlink(missing_ok=True)

    def test_get_recent_nonexistent_file(self) -> None:
        """目录无法创建时优雅降级（不抛异常），文件不存在返回空列表。"""
        auditor = ToolAuditor(log_path="/nonexistent/path/audit.jsonl")
        assert auditor.get_recent() == []

    def test_uncreatable_dir_disables_auditor(self) -> None:
        """目录创建失败（权限不足/只读）时自动禁用审计器。"""
        with patch("agent.core.audit.tool_auditor.Path.mkdir", side_effect=PermissionError):
            auditor = ToolAuditor(log_path="/any/path/audit.jsonl")
            assert auditor.enabled is False


class TestGetToolAuditor:
    """全局单例测试。"""

    def test_singleton(self) -> None:
        """get_tool_auditor 返回同一实例。"""
        a1 = get_tool_auditor()
        a2 = get_tool_auditor()
        assert a1 is a2
