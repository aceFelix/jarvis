"""agent/core/memory/recovery.py 单元测试。

覆盖恢复点的保存/标记正常退出/清除/加载，以及 format_recovery_summary 摘要生成。
通过 monkeypatch 把 `recovery.sessions_dir` 重定向到 tmp_path，避免污染真实用户目录。

@author aceFelix
"""

import json
import time

import pytest

from agent.core.message import Message, TextContent
from agent.core.memory import recovery
from agent.core.memory.recovery import (
    RecoveryPoint,
    clear_recovery_point,
    format_recovery_summary,
    load_recovery_point,
    mark_clean_exit,
    save_recovery_point,
)


@pytest.fixture
def recovery_dir(tmp_path, monkeypatch):
    """把 recovery.sessions_dir 重定向到 tmp_path 下的 sessions 目录。"""
    d = tmp_path / "sessions"
    monkeypatch.setattr(recovery, "sessions_dir", lambda: d)
    return d


def _messages() -> list[Message]:
    """构造测试消息列表。"""
    return [
        Message(role="user", content=[TextContent(text="帮我看看这个项目")]),
        Message(role="assistant", content=[TextContent(text="好的，稍等")]),
    ]


class TestSaveRecoveryPoint:
    """恢复点写入。"""

    def test_save_creates_json_file(self, recovery_dir) -> None:
        """保存后生成 .recovery.json，字段与消息序列化正确。"""
        save_recovery_point(
            _messages(),
            workdir="/tmp/wd",
            model="qwen-max",
            provider="dashscope",
            dialog_count=3,
        )
        path = recovery_dir / ".recovery.json"
        assert path.exists()

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["clean_exit"] is False
        assert data["workdir"] == "/tmp/wd"
        assert data["model"] == "qwen-max"
        assert data["provider"] == "dashscope"
        assert data["dialog_count"] == 3
        assert data["saved_at"] > 0
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"

    def test_save_failure_silent(self, recovery_dir, monkeypatch) -> None:
        """写入失败（如目录创建失败）静默不抛异常。"""

        def _boom() -> str:
            raise OSError("disk full")

        monkeypatch.setattr(recovery, "sessions_dir", _boom)
        # 不抛异常即为通过
        save_recovery_point(_messages(), workdir="", model="", provider="", dialog_count=0)


class TestMarkCleanExit:
    """标记正常退出。"""

    def test_mark_clean_exit_updates_file(self, recovery_dir) -> None:
        save_recovery_point(_messages(), workdir="", model="", provider="", dialog_count=1)
        mark_clean_exit()
        data = json.loads((recovery_dir / ".recovery.json").read_text(encoding="utf-8"))
        assert data["clean_exit"] is True

    def test_mark_clean_exit_no_file_no_error(self, recovery_dir) -> None:
        """恢复点不存在时不报错。"""
        mark_clean_exit()

    def test_mark_clean_exit_corrupt_file_silent(self, recovery_dir) -> None:
        """恢复点损坏时静默失败。"""
        recovery_dir.mkdir(parents=True, exist_ok=True)
        (recovery_dir / ".recovery.json").write_text("{bad", encoding="utf-8")
        mark_clean_exit()  # 不抛异常


class TestClearRecoveryPoint:
    """清除恢复点。"""

    def test_clear_deletes_file(self, recovery_dir) -> None:
        save_recovery_point(_messages(), workdir="", model="", provider="", dialog_count=1)
        clear_recovery_point()
        assert not (recovery_dir / ".recovery.json").exists()

    def test_clear_no_file_no_error(self, recovery_dir) -> None:
        clear_recovery_point()  # 不抛异常


class TestLoadRecoveryPoint:
    """恢复点加载。"""

    def test_load_missing_returns_none(self, recovery_dir) -> None:
        assert load_recovery_point() is None

    def test_load_clean_exit_deletes_and_returns_none(self, recovery_dir) -> None:
        """clean_exit=True 时删除恢复点并返回 None。"""
        save_recovery_point(_messages(), workdir="", model="", provider="", dialog_count=1)
        mark_clean_exit()
        assert load_recovery_point() is None
        assert not (recovery_dir / ".recovery.json").exists()

    def test_load_corrupt_json_returns_none(self, recovery_dir) -> None:
        recovery_dir.mkdir(parents=True, exist_ok=True)
        (recovery_dir / ".recovery.json").write_text("{not json", encoding="utf-8")
        assert load_recovery_point() is None

    def test_load_ok_returns_point(self, recovery_dir) -> None:
        """正常恢复点返回 RecoveryPoint，消息还原。"""
        save_recovery_point(
            _messages(),
            workdir="/tmp/wd",
            model="qwen-max",
            provider="dashscope",
            dialog_count=5,
        )
        point = load_recovery_point()
        assert point is not None
        assert isinstance(point, RecoveryPoint)
        assert point.clean_exit is False
        assert point.workdir == "/tmp/wd"
        assert point.model == "qwen-max"
        assert point.provider == "dashscope"
        assert point.dialog_count == 5
        assert len(point.messages) == 2
        assert point.messages[0].content[0].text == "帮我看看这个项目"

    def test_load_expired_cleans_and_returns_none(self, recovery_dir, monkeypatch) -> None:
        """过期恢复点被清理并返回 None。"""
        t0 = 1_700_000_000.0
        monkeypatch.setattr(recovery.time, "time", lambda: t0)
        save_recovery_point(_messages(), workdir="", model="", provider="", dialog_count=1)
        # 时间前进 8 天（超过 7 天 TTL）
        monkeypatch.setattr(recovery.time, "time", lambda: t0 + 8 * 86400)
        assert load_recovery_point() is None
        assert not (recovery_dir / ".recovery.json").exists()

    def test_load_malformed_message_silent(self, recovery_dir) -> None:
        """messages 解析失败时返回 None（不抛异常）。"""
        recovery_dir.mkdir(parents=True, exist_ok=True)
        path = recovery_dir / ".recovery.json"
        path.write_text(
            json.dumps({
                "clean_exit": False,
                "saved_at": time.time(),
                "workdir": "",
                "model": "",
                "provider": "",
                "dialog_count": 0,
                "messages": ["not-a-dict"],
            }),
            encoding="utf-8",
        )
        assert load_recovery_point() is None


class TestRecoveryPointProperties:
    """RecoveryPoint 属性。"""

    def test_age_seconds_and_is_expired(self, monkeypatch) -> None:
        monkeypatch.setattr(recovery.time, "time", lambda: 2_000_000.0)
        fresh = RecoveryPoint(
            clean_exit=False, saved_at=1_999_000.0, workdir="", model="",
            provider="", dialog_count=0, messages=[],
        )
        assert fresh.age_seconds == 1000.0
        assert fresh.is_expired is False

        # saved_at 在未来 → age 取 0
        future = RecoveryPoint(
            clean_exit=False, saved_at=2_100_000.0, workdir="", model="",
            provider="", dialog_count=0, messages=[],
        )
        assert future.age_seconds == 0.0

        monkeypatch.setattr(recovery.time, "time", lambda: 2_000_000.0)
        old = RecoveryPoint(
            clean_exit=False, saved_at=1_000_000.0, workdir="", model="",
            provider="", dialog_count=0, messages=[],
        )
        assert old.is_expired is True


class TestFormatRecoverySummary:
    """恢复点摘要格式化。"""

    @staticmethod
    def _point(saved_at: float, messages: list[Message] | None = None) -> RecoveryPoint:
        return RecoveryPoint(
            clean_exit=False,
            saved_at=saved_at,
            workdir="/tmp/wd",
            model="",
            provider="",
            dialog_count=3,
            messages=messages or [],
        )

    def test_summary_minutes_ago(self, monkeypatch) -> None:
        """10 分钟前 → 分钟单位。"""
        monkeypatch.setattr(recovery.time, "time", lambda: 2_000_000.0)
        summary = format_recovery_summary(self._point(saved_at=2_000_000.0 - 600))
        assert "10 分钟前" in summary
        assert "3 轮对话" in summary

    def test_summary_hours_ago(self, monkeypatch) -> None:
        """2 小时前 → 小时单位。"""
        monkeypatch.setattr(recovery.time, "time", lambda: 2_000_000.0)
        summary = format_recovery_summary(self._point(saved_at=2_000_000.0 - 7200))
        assert "2 小时前" in summary

    def test_summary_days_ago(self, monkeypatch) -> None:
        """3 天前 → 天单位。"""
        monkeypatch.setattr(recovery.time, "time", lambda: 2_000_000.0)
        summary = format_recovery_summary(self._point(saved_at=2_000_000.0 - 3 * 86400))
        assert "3 天前" in summary

    def test_summary_includes_first_user_message(self, monkeypatch) -> None:
        """摘要包含首条用户消息文本（截断 60 字）。"""
        monkeypatch.setattr(recovery.time, "time", lambda: 2_000_000.0)
        long_text = "用户" * 40  # 80 字
        msgs = [
            Message(role="assistant", content=[TextContent(text="先说话的是助手")]),
            Message(role="user", content=[TextContent(text=long_text)]),
        ]
        summary = format_recovery_summary(self._point(2_000_000.0 - 60, msgs))
        assert "首条:" in summary
        assert long_text[:60] in summary

    def test_summary_without_user_message(self, monkeypatch) -> None:
        """无用户消息时不输出首条摘要。"""
        monkeypatch.setattr(recovery.time, "time", lambda: 2_000_000.0)
        msgs = [Message(role="assistant", content=[TextContent(text="只有助手")])]
        summary = format_recovery_summary(self._point(2_000_000.0 - 60, msgs))
        assert "首条:" not in summary
        assert "workdir: /tmp/wd" in summary
        assert "1 条消息" in summary
