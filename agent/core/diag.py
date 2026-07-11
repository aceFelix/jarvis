"""诊断日志（Diagnostic Logging）。

对应原项目 utils/diagLogs.ts + utils/errorLogSink.ts。

设计目标:
1. 与 ui.info/warn/error 解耦 —— 诊断日志给开发者排查问题用，不干扰用户
2. 分类（component + level），便于过滤
3. 落盘到 ~/.jarvis/logs/diag.log，按大小轮转
4. 失败静默 —— 日志本身不能再抛异常影响主流程
5. 全局开关 —— settings.debug=True 时也输出到 stderr

用法::

    from agent.core.diag import diag_log
    diag_log("hooks", "钩子超时", level="warn")
    diag_log("provider", f"切换到 fallback: {name}")

    # 异常时
    diag_log("orchestrator", f"工具异常: {e}", level="error", exc_info=True)

日志格式（一行一条，便于 grep）:
    2026-07-04T10:30:45.123 [hooks] WARN 钩子超时
    2026-07-04T10:30:45.456 [orchestrator] ERROR 工具异常: ValueError: ...
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Literal

Level = Literal["debug", "info", "warn", "error"]

# 级别数值（便于比较）
_LEVEL_VALUE: dict[str, int] = {
    "debug": 10,
    "info": 20,
    "warn": 30,
    "error": 40,
}

# 默认级别：debug=False 时记录 info 及以上；debug=True 时记录 debug 及以上
_DEFAULT_LEVEL_VALUE = _LEVEL_VALUE["info"]

# 日志文件最大大小（1 MB），超过则轮转
_MAX_LOG_SIZE = 1 * 1024 * 1024
# 保留的旧日志文件数
_MAX_LOG_FILES = 3

# 全局状态
_lock = Lock()
_log_file: Path | None = None
_min_level: int = _DEFAULT_LEVEL_VALUE
_debug_to_stderr: bool = False
_initialized: bool = False


def _ensure_init() -> None:
    """惰性初始化日志文件路径。

    避免在 import 时就创建目录（如测试环境）。
    """
    global _initialized, _log_file
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        try:
            log_dir = Path.home() / ".jarvis" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            _log_file = log_dir / "diag.log"
        except Exception:
            _log_file = None  # 创建失败时禁用文件日志
        _initialized = True


def set_debug(enabled: bool) -> None:
    """设置 debug 模式。

    enabled=True 时:
    - 最低级别降到 debug
    - 同时输出到 stderr（便于实时调试）
    """
    global _min_level, _debug_to_stderr
    with _lock:
        _min_level = _LEVEL_VALUE["debug"] if enabled else _DEFAULT_LEVEL_VALUE
        _debug_to_stderr = enabled


def _format_line(component: str, msg: str, level: str, exc_info: bool) -> str:
    """格式化一行日志。"""
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"
    level_tag = level.upper()
    line = f"{ts} [{component}] {level_tag} {msg}"
    if exc_info:
        tb = traceback.format_exc()
        if tb and tb.strip() != "NoneType: None":
            line += "\n" + tb.rstrip()
    return line


def _rotate_if_needed(path: Path) -> None:
    """日志文件超大小则轮转。"""
    try:
        if not path.exists() or path.stat().st_size < _MAX_LOG_SIZE:
            return
        # diag.log.1 → diag.log.2, ..., diag.log.{N-1} → diag.log.N（删除最老的）
        for i in range(_MAX_LOG_FILES - 1, 0, -1):
            src = path.with_suffix(f".log.{i}")
            dst = path.with_suffix(f".log.{i + 1}")
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.rename(dst)
        # diag.log → diag.log.1
        path.rename(path.with_suffix(".log.1"))
    except Exception:
        pass  # 轮转失败不影响写入


def diag_log(
    component: str,
    msg: str,
    *,
    level: Level = "info",
    exc_info: bool = False,
) -> None:
    """写一条诊断日志。

    Args:
        component: 组件名（如 hooks/provider/orchestrator/voice/...）
        msg: 日志消息
        level: debug/info/warn/error
        exc_info: 是否附加当前异常的 traceback（在 except 块中调用时设 True）

    失败静默：任何 IO 异常都不抛出，确保不影响主流程。
    """
    try:
        lvl = _LEVEL_VALUE.get(level, 20)
        if lvl < _min_level:
            return

        _ensure_init()
        line = _format_line(component, msg, level, exc_info)

        # 写文件
        if _log_file is not None:
            with _lock:
                _rotate_if_needed(_log_file)
                with _log_file.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")

        # debug 模式同时输出 stderr
        if _debug_to_stderr or lvl >= _LEVEL_VALUE["warn"]:
            print(line, file=sys.stderr)
    except Exception:
        pass  # 绝对不能再抛


# ---- 便捷封装 ----

def diag_debug(component: str, msg: str, **kwargs) -> None:
    diag_log(component, msg, level="debug", **kwargs)


def diag_info(component: str, msg: str, **kwargs) -> None:
    diag_log(component, msg, level="info", **kwargs)


def diag_warn(component: str, msg: str, **kwargs) -> None:
    diag_log(component, msg, level="warn", **kwargs)


def diag_error(component: str, msg: str, **kwargs) -> None:
    diag_log(component, msg, level="error", **kwargs)


def get_log_path() -> Path | None:
    """返回当前诊断日志文件路径（供 /doctor 命令展示）。"""
    _ensure_init()
    return _log_file


def read_recent_logs(max_lines: int = 200) -> list[str]:
    """读取最近的日志行（供 /doctor 命令展示）。

    优先读 diag.log，不够再读 diag.log.1。
    """
    _ensure_init()
    if _log_file is None:
        return []
    lines: list[str] = []
    try:
        if _log_file.exists():
            with _log_file.open("r", encoding="utf-8") as f:
                all_lines = f.readlines()
            lines = all_lines[-max_lines:]
        if len(lines) < max_lines:
            rotated = _log_file.with_suffix(".log.1")
            if rotated.exists():
                with rotated.open("r", encoding="utf-8") as f:
                    old_lines = f.readlines()
                need = max_lines - len(lines)
                lines = old_lines[-need:] + lines
    except Exception:
        pass
    return lines
