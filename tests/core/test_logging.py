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

from agent.core.logging import (
    _attach_agent_namespace,
    get_logger,
    log_exception,
    set_debug,
)


@pytest.fixture
def fresh_logger(monkeypatch, tmp_path):
    """重置模块级单例并把日志文件目录重定向到 tmp_path。"""
    import agent.core.logging as log_mod

    # 清掉上次测试残留的 handler，避免重复添加
    old = logging.getLogger("jarvis")
    old.handlers.clear()
    # `agent` 命名空间的 handler 与 "jarvis" 是共享对象，不一起清会让下个用例
    # 拿到指向上一个 tmp_path 的旧文件 handler。
    ns = logging.getLogger("agent")
    ns.handlers.clear()
    ns.propagate = True

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


class TestAgentNamespace:
    """裸 `getLogger(__name__)` 的模块也必须落盘（2026-11-03 实机「503 无痕」的回归）。

    全仓 19 个模块（含 query_loop / sandbox.audit / checkpoint / keyring_store /
    error_recovery / tools.bash）取的是自己名字的 logger，旧实现里 handler 只挂在
    "jarvis" 上，它们的消息只能命中 lastResort：终端看得到、文件里没有。
    """

    def test_bare_module_logger_reaches_log_file(self, fresh_logger, tmp_path):
        """端到端：用裸 logger 写的 WARNING 与 INFO 都得真的落进 jarvis.log。"""
        logger = get_logger()  # 触发初始化 + 接命名空间
        probe = logging.getLogger("agent.core.query_loop")
        probe.warning("HTTP 503 probe marker")
        probe.info("LLM 计时 probe marker")  # INFO 也要落（文件 handler 是 DEBUG 级）
        for handler in logger.handlers:
            handler.flush()

        text = (tmp_path / ".jarvis" / "jarvis.log").read_text(encoding="utf-8")
        assert "HTTP 503 probe marker" in text
        assert "LLM 计时 probe marker" in text
        # logger name 得保留：诊断时仍看得出来源，而不是全并成 jarvis
        assert "agent.core.query_loop" in text

    def test_namespace_attached_once_and_not_propagated(self, fresh_logger) -> None:
        """重复挂同一批 handler 不产生重复；命名空间不再向 root 传播。"""
        get_logger()
        ns = logging.getLogger("agent")
        first = list(ns.handlers)
        assert first, "get_logger() 应当已把 handler 接到 agent 命名空间"

        _attach_agent_namespace(first)  # 再挂一次
        assert ns.handlers == first
        assert ns.propagate is False
        assert ns.level == logging.DEBUG

    def test_debug_console_shares_handler_with_namespace(self, fresh_logger) -> None:
        """命名空间上的控制台 handler 与 jarvis 是同一对象，set_debug 能一并生效。"""
        logger = get_logger()
        ns = logging.getLogger("agent")
        stream, _ = _handlers(logger)
        ns_stream, _ = _handlers(ns)
        assert ns_stream[0] is stream[0]

        set_debug(True)
        assert ns_stream[0].level == logging.DEBUG


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

    def test_set_debug_off_keeps_file_handler_debug(self, fresh_logger):
        """回归：关 DEBUG 只应动控制台，不能把**文件** handler 也降级。

        FileHandler 是 StreamHandler 的子类，旧实现一个 isinstance 就把两者一起改了，
        于是 set_debug(False) 后 INFO/DEBUG 静默不再落盘——正好吃掉「LLM 计时」
        这类事后定位慢请求唯一的实测数据。
        """
        logger = get_logger()
        set_debug(False)
        _, file_handlers = _handlers(logger)
        assert file_handlers[0].level == logging.DEBUG


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
