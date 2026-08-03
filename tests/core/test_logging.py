"""统一日志模块（agent.core.logging）单元测试。

覆盖:
- get_logger 单例返回、logger 级别、控制台/file handler 配置
- 日志目录不可用时优雅降级（仅控制台）
- set_debug 开关控制台 DEBUG 输出
- log_exception 统一异常记录（带/不带异常对象）

文件 handler 通过 patching Path.home 重定向到 tmp_path，不写真实主目录。

@author aceFelix
"""

from __future__ import annotations

import logging

import pytest

from agent.core.logging import get_logger, log_exception, set_debug


@pytest.fixture
def fresh_logger(monkeypatch, tmp_path):
    """重置模块级单例并把日志文件目录重定向到 tmp_path。"""
    import agent.core.logging as log_mod

    # 清掉上次测试残留的 handler，避免重复添加
    old = logging.getLogger("jarvis")
    old.handlers.clear()

    monkeypatch.setattr(log_mod, "_logger", None)
    monkeypatch.setattr(log_mod.Path, "home", classmethod(lambda cls: tmp_path))
    return log_mod


def _handlers(logger):
    """按 handler 类型拆分。

    注意：RotatingFileHandler 继承自 FileHandler → StreamHandler，
    必须先排除 FileHandler 再算控制台 handler。
    """
    stream = [
        h for h in logger.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    file = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    return stream, file


class TestGetLogger:
    """get_logger 初始化与配置。"""

    def test_returns_singleton(self, fresh_logger):
        a = get_logger()
        b = get_logger()
        assert a is b

    def test_logger_level_is_debug(self, fresh_logger):
        assert get_logger().level == logging.DEBUG

    def test_console_handler_warning_level(self, fresh_logger):
        stream, _ = _handlers(get_logger())
        assert len(stream) == 1
        assert stream[0].level == logging.WARNING
        assert stream[0].formatter is not None

    def test_file_handler_debug_level_and_rotation(self, fresh_logger, tmp_path):
        logger = get_logger()
        _, file_handlers = _handlers(logger)
        assert len(file_handlers) == 1
        fh = file_handlers[0]
        assert fh.level == logging.DEBUG
        assert fh.maxBytes == 5 * 1024 * 1024
        assert fh.backupCount == 3
        # 日志文件已创建
        assert (tmp_path / ".jarvis" / "jarvis.log").exists()

    def test_console_formatter_contains_level_and_name(self, fresh_logger):
        stream, _ = _handlers(get_logger())
        fmt = stream[0].formatter._fmt
        assert "%(levelname)s" in fmt
        assert "%(name)s" in fmt

    def test_file_dir_failure_degrades_gracefully(self, monkeypatch):
        """日志目录创建失败时仍返回带控制台 handler 的 logger。"""
        import agent.core.logging as log_mod

        old = logging.getLogger("jarvis")
        old.handlers.clear()
        monkeypatch.setattr(log_mod, "_logger", None)
        monkeypatch.setattr(
            log_mod.Path, "home",
            classmethod(lambda cls: (_ for _ in ()).throw(OSError("无法访问主目录"))),
        )

        logger = get_logger()
        stream, file_handlers = _handlers(logger)
        assert len(stream) == 1
        assert file_handlers == []


class TestSetDebug:
    """set_debug 开关。"""

    def test_set_debug_on(self, fresh_logger):
        get_logger()
        set_debug(True)
        stream, _ = _handlers(get_logger())
        assert stream[0].level == logging.DEBUG

    def test_set_debug_off(self, fresh_logger):
        get_logger()
        set_debug(True)
        set_debug(False)
        stream, _ = _handlers(get_logger())
        assert stream[0].level == logging.WARNING


class TestLogException:
    """log_exception 统一异常记录。"""

    def test_with_exception_records_type(self, fresh_logger, caplog):
        with caplog.at_level(logging.WARNING, logger="test-logger"):
            log_exception(logging.getLogger("test-logger"), "操作失败", ValueError("参数错误"))
        assert any("操作失败" in r.message for r in caplog.records)
        assert any("ValueError" in r.message for r in caplog.records)
        assert any("参数错误" in r.message for r in caplog.records)

    def test_without_exception(self, fresh_logger, caplog):
        with caplog.at_level(logging.WARNING, logger="test-logger"):
            log_exception(logging.getLogger("test-logger"), "仅文案")
        assert any(r.message == "仅文案" for r in caplog.records)
