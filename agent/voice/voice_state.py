"""跨进程语音互斥锁。

防止多个 jarvis 进程（REPL /voice、`jarvis --talk` 窗口、未来 GUI 语音）
同时开启语音模式导致麦克风/扬声器冲突。

实现: ~/.jarvis/voice.lock 文件锁，内容 "PID,时间戳"。
- **心跳续约**: 持锁进程后台线程每 30 秒刷新时间戳，会话再长也不会误判过期。
- **失效判定**: 时间戳超过 60 秒未更新即视为持锁进程已崩溃，锁可被覆盖。

不依赖 os.kill(pid, 0) 做存活检测：Windows 上该调用对已死进程同样成功
（只要句柄可打开），检活不可靠；POSIX 可靠但为跨平台一致统一走心跳方案。

原位于 agent/daemon/voice_state.py，daemon 托盘下线后迁移至语音包；
纯托盘专用的语音开关状态文件（voice_enabled）随托盘一并移除。

@author aceFelix
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

_LOCK_TTL = 60          # 锁过期秒数（心跳中断超过此时长视为持锁进程已崩溃）
_HEARTBEAT_INTERVAL = 30  # 心跳续约间隔，须明显小于 _LOCK_TTL

# 心跳线程状态（模块级单例，同一进程内只会持有一把语音锁）
_heartbeat_thread: threading.Thread | None = None
_heartbeat_stop: threading.Event | None = None


def _lock_path() -> Path:
    """返回语音锁文件路径 ~/.jarvis/voice.lock。"""
    return Path.home() / ".jarvis" / "voice.lock"


def _write_lock() -> None:
    """写入/续约锁文件：当前 PID + 最新时间戳。"""
    _lock_path().write_text(f"{os.getpid()},{time.time()}", encoding="utf-8")


def _heartbeat_loop() -> None:
    """心跳线程：定期续约锁时间戳，直到 release_voice_lock() 通知停止。"""
    while not _heartbeat_stop.wait(_HEARTBEAT_INTERVAL):
        try:
            _write_lock()
        except Exception:
            pass  # 续约失败不致命，下个周期重试；最坏情况锁过期被接管


def acquire_voice_lock() -> tuple[bool, str]:
    """尝试获取语音独占锁，成功后启动心跳线程保活。

    Returns:
        (success, info) 元组。success=True 表示获取成功；
        success=False 时 info 包含占用者信息或失败原因。
    """
    global _heartbeat_thread, _heartbeat_stop
    try:
        lock_path = _lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if lock_path.exists():
            try:
                content = lock_path.read_text(encoding="utf-8").strip()
                pid_str, ts_str = content.split(",", 1)
                pid = int(pid_str)
                ts = float(ts_str)
                # 时间戳未过期 → 认为持锁进程仍存活（心跳在续约）
                if pid != os.getpid() and time.time() - ts < _LOCK_TTL:
                    return False, f"PID {pid} 正在占用语音锁"
            except (ValueError, OSError):
                # 文件损坏 → 锁已失效，覆盖
                pass

        # 抢占锁：写入当前 PID + 时间戳，并启动心跳续约
        _write_lock()
        _heartbeat_stop = threading.Event()
        _heartbeat_thread = threading.Thread(
            target=_heartbeat_loop, name="voice-lock-heartbeat", daemon=True
        )
        _heartbeat_thread.start()
        return True, ""
    except Exception as e:
        # 锁文件写入失败不影响功能，放行
        return True, f"锁文件异常但放行: {e}"


def release_voice_lock() -> None:
    """释放语音独占锁：停止心跳线程并删除锁文件。退出语音会话时必须调用。"""
    global _heartbeat_thread, _heartbeat_stop
    try:
        if _heartbeat_stop is not None:
            _heartbeat_stop.set()
        _heartbeat_thread = None
        _heartbeat_stop = None
        lock_path = _lock_path()
        if lock_path.exists():
            lock_path.unlink()
    except Exception:
        pass
