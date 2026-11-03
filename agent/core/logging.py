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


def _attach_agent_namespace(handlers: list[logging.Handler]) -> logging.Logger:
    """把同一对 handler 挂到 `agent` 顶层命名空间 logger 上（幂等）。

    为什么挂在命名空间根上而不是逐个改调用点（2026-11-03 实测）：全仓有 **19 个模块**用
    `logging.getLogger(__name__)` 取裸 logger——`core/query_loop.py`、
    `core/sandbox/audit.py`、`core/checkpoint.py`、`config/keyring_store.py`、
    `core/error_recovery.py`、`tools/bash.py` 等。它们不在 "jarvis" 这个 logger 之下，
    而 root 又没人挂 handler，于是每条消息只命中 Python 的 **lastResort**：原样甩到 stderr
    （终端里因此看不到 `[WARNING]` 前缀）、**且不写文件**。

    实机后果：用户在终端连着看到 `HTTP 503` 与完整 cause 链，`jarvis.log` 里搜
    `HTTP 503` 却是 0 条——事故留不下任何痕迹，次数、时间、厂商全部无法事后统计。
    `query_loop.py` 顶部那段「以前日志里只剩几个字符 provider_error」的注释，当初就是
    为了修这个才加的 logger，结果它一直没进到日志文件。

    挂到 `agent` 命名空间上，一处修好全部，各模块的 logger name 原样保留（诊断时仍
    看得出来源）；handler 是共享对象，所以 `set_debug` 改级别会同时作用于两侧。

    @author aceFelix
    """
    ns = logging.getLogger("agent")
    ns.setLevel(logging.DEBUG)  # 实际输出级别仍由 handler 控制
    # 关掉向 root 的传播：handler 已在命名空间上接住了，不然将来第三方给 root 挂
    # handler 时同一条日志会写两遍。
    ns.propagate = False
    for handler in handlers:
        if handler not in ns.handlers:
            ns.addHandler(handler)
    return ns


def get_logger(name: str = "jarvis") -> logging.Logger:
    """获取或创建 Jarvis 统一日志器。

    首次调用时初始化 handler 和格式，并把这些 handler 同时接到 `agent` 命名空间上
    （见 `_attach_agent_namespace`）。
    """
    global _logger
    if _logger is not None:
        return _logger

    _logger = logging.getLogger(name)
    _logger.setLevel(logging.DEBUG)  # logger 自身设 DEBUG，handler 控制实际输出级别

    attached: list[logging.Handler] = []

    # 控制台输出（默认 WARNING，避免刷屏）
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter(
        "[%(levelname)s] %(name)s: %(message)s"
    ))
    _logger.addHandler(console)
    attached.append(console)

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
        attached.append(file_handler)
    except Exception:
        pass  # 日志文件不可用不影响功能

    # 让裸 `getLogger(__name__)` 的 19 个模块也能落盘，原因见 _attach_agent_namespace
    _attach_agent_namespace(attached)

    return _logger


def set_debug(enabled: bool = True) -> None:
    """开关 DEBUG 日志输出（终端也显示 DEBUG 级别）。"""
    logger = get_logger()
    for handler in logger.handlers:
        # FileHandler 也是 StreamHandler 的子类：不排除的话这里会把**文件** handler 的
        # 级别一并改成 WARNING，于是关 DEBUG 后连 INFO/DEBUG 都不再落盘——与 P5 同一
        # 主题的事故痕迹丢失（「LLM 计时」这类 INFO 行会在下次开/关 DEBUG 后静默消失）。
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            handler.setLevel(logging.DEBUG if enabled else logging.WARNING)


def log_exception(logger: logging.Logger, msg: str, exc: Exception | None = None) -> None:
    """统一异常记录：warning 级别 + 可选的调试堆栈。

    替代 except Exception: pass 的静默吞噬。
    """
    if exc:
        logger.warning("%s: %s: %s", msg, type(exc).__name__, exc)
    else:
        logger.warning(msg)
