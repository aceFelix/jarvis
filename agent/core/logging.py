"""统一日志模块 —— 替代散落的 print() 和静默异常。

E-03/E-04 改进项：消除 except Exception: pass，关键路径用 logging 替代 print。

配置：
- 默认级别 WARNING，避免刷屏
- debug=True 时升到 DEBUG 级别
- 日志文件 ~/.jarvis/jarvis.log（按天轮转 5MB）

@author aceFelix
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_logger: logging.Logger | None = None


def get_logger(name: str = "jarvis") -> logging.Logger:
    """获取或创建 Jarvis 统一日志器。

    首次调用时初始化 handler 和格式。
    """
    global _logger
    if _logger is not None:
        return _logger

    _logger = logging.getLogger(name)
    _logger.setLevel(logging.DEBUG)  # logger 自身设 DEBUG，handler 控制实际输出级别

    # 控制台输出（默认 WARNING，避免刷屏）
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter(
        "[%(levelname)s] %(name)s: %(message)s"
    ))
    _logger.addHandler(console)

    # 文件输出（DEBUG 级别，持久化）
    try:
        log_dir = Path.home() / ".jarvis"
        log_dir.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            log_dir / "jarvis.log",
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        _logger.addHandler(file_handler)
    except Exception:
        pass  # 日志文件不可用不影响功能

    return _logger


def set_debug(enabled: bool = True) -> None:
    """开关 DEBUG 日志输出（终端也显示 DEBUG 级别）。"""
    logger = get_logger()
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setLevel(logging.DEBUG if enabled else logging.WARNING)


def log_exception(logger: logging.Logger, msg: str, exc: Exception | None = None) -> None:
    """统一异常记录：warning 级别 + 可选的调试堆栈。

    替代 except Exception: pass 的静默吞噬。
    """
    if exc:
        logger.warning("%s: %s: %s", msg, type(exc).__name__, exc)
    else:
        logger.warning(msg)
