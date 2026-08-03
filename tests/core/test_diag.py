"""诊断日志（Diagnostic Logging）单元测试。

覆盖 diag_log 的级别过滤、debug 开关、落盘格式、轮转、便捷封装
（diag_debug/info/warn/error）、get_log_path / read_recent_logs 与失败静默。

@author aceFelix
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import agent.core.diag as diag


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    """把全局日志文件指向临时文件，并恢复全局状态。"""
    target = tmp_path / "diag.log"
    old_file = diag._log_file
    old_init = diag._initialized
    old_min = diag._min_level
    old_stderr = diag._debug_to_stderr
    diag._log_file = target
    diag._initialized = True
    yield target
    diag._log_file = old_file
    diag._initialized = old_init
    diag._min_level = old_min
    diag._debug_to_stderr = old_stderr


class TestDiagLog:
    """diag_log 核心写入行为。"""

    def test_writes_line_to_file(self, log_path: Path) -> None:
        diag.diag_log("hooks", "钩子超时", level="warn")
        content = log_path.read_text(encoding="utf-8")
        assert "[hooks]" in content
        assert "WARN" in content
        assert "钩子超时" in content

    def test_below_min_level_is_dropped(self, log_path: Path) -> None:
        """默认级别 info：debug 日志不落盘。"""
        diag.diag_log("hooks", "debug detail", level="debug")
        assert log_path.exists() is False

    def test_debug_mode_records_debug(self, log_path: Path) -> None:
        diag.set_debug(True)
        diag.diag_log("hooks", "debug detail", level="debug")
        content = log_path.read_text(encoding="utf-8")
        assert "debug detail" in content
        assert "DEBUG" in content

    def test_default_level_is_info(self, log_path: Path) -> None:
        diag.diag_log("provider", "info msg", level="info")
        content = log_path.read_text(encoding="utf-8")
        assert "info msg" in content

    def test_unknown_level_falls_back_to_info(self, log_path: Path) -> None:
        """未知级别回退到 info（value 20），高于默认 min_level 会写入。"""
        diag.diag_log("provider", "unknown level msg", level="verbose")
        content = log_path.read_text(encoding="utf-8")
        assert "unknown level msg" in content

    def test_exc_info_appends_traceback(self, log_path: Path) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            diag.diag_log("orchestrator", "工具异常", level="error", exc_info=True)
        content = log_path.read_text(encoding="utf-8")
        assert "工具异常" in content
        assert "ValueError" in content

    def test_failure_is_silent(self, log_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """日志写入失败也不能抛异常。"""
        def bad_open(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(diag.Path, "open", bad_open)
        # 不抛异常即通过
        diag.diag_log("hooks", "写入失败测试", level="warn")

    def test_stderr_output_on_warn(self, log_path: Path, capsys: pytest.CaptureFixture) -> None:
        """warn 及以上级别同时输出到 stderr。"""
        diag.diag_log("hooks", "warn to stderr", level="warn")
        err = capsys.readouterr().err
        assert "warn to stderr" in err

    def test_no_stderr_for_info_by_default(self, capsys: pytest.CaptureFixture) -> None:
        """默认非 debug 模式下 info 不写 stderr。"""
        diag.diag_log("hooks", "info silent", level="info")
        assert "info silent" not in capsys.readouterr().err


class TestConvenienceWrappers:
    """便捷封装函数。"""

    def test_diag_debug(self, log_path: Path) -> None:
        diag.set_debug(True)
        diag.diag_debug("c", "debug msg")
        assert "debug msg" in log_path.read_text(encoding="utf-8")

    def test_diag_info(self, log_path: Path) -> None:
        diag.diag_info("c", "info msg")
        assert "info msg" in log_path.read_text(encoding="utf-8")

    def test_diag_warn(self, log_path: Path) -> None:
        diag.diag_warn("c", "warn msg")
        assert "warn msg" in log_path.read_text(encoding="utf-8")

    def test_diag_error(self, log_path: Path) -> None:
        diag.diag_error("c", "error msg")
        assert "error msg" in log_path.read_text(encoding="utf-8")


class TestRotation:
    """日志轮转。"""

    def test_rotate_when_over_size(self, log_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(diag, "_MAX_LOG_SIZE", 10)
        # 第一次写入后文件已超过 10 字节
        diag.diag_log("c", "first line content", level="info")
        # 第二次写入时检测到超限，触发轮转：diag.log -> diag.log.1
        diag.diag_log("c", "second line content", level="info")
        rotated = log_path.with_suffix(".log.1")
        assert rotated.exists()
        assert "first line content" in rotated.read_text(encoding="utf-8")


class TestReadAndPath:
    """get_log_path 与 read_recent_logs。"""

    def test_get_log_path(self, log_path: Path) -> None:
        assert diag.get_log_path() == log_path

    def test_get_log_path_init_when_not_initialized(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """未初始化时 _ensure_init 会尝试建目录。"""
        monkeypatch.setattr(diag.Path, "home", classmethod(lambda cls: tmp_path))
        monkeypatch.setattr(diag, "_initialized", False)
        monkeypatch.setattr(diag, "_log_file", None)
        p = diag.get_log_path()
        assert p is not None
        assert p.parent.exists()

    def test_read_recent_logs(self, log_path: Path) -> None:
        for i in range(5):
            diag.diag_log("c", f"line {i}", level="info")
        lines = diag.read_recent_logs(max_lines=3)
        assert len(lines) == 3
        assert "line 4" in lines[-1]

    def test_read_recent_logs_empty_file(self, tmp_path: Path) -> None:
        target = tmp_path / "diag.log"
        old = diag._log_file
        old_init = diag._initialized
        diag._log_file = target
        diag._initialized = True
        try:
            assert diag.read_recent_logs() == []
        finally:
            diag._log_file = old
            diag._initialized = old_init

    def test_read_recent_logs_merges_rotated(self, log_path: Path) -> None:
        """当前文件不足时从 diag.log.1 补充。"""
        log_path.write_text("new1\nnew2\n", encoding="utf-8")
        log_path.with_suffix(".log.1").write_text("old1\nold2\n", encoding="utf-8")
        lines = diag.read_recent_logs(max_lines=3)
        assert lines == ["old2\n", "new1\n", "new2\n"]
