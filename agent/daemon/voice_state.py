"""跨进程语音开关状态文件。

daemon 以 detached 子进程方式运行（Windows DETACHED_PROCESS / macOS setsid），
voice_loop 在子进程内执行，托盘菜单在... 等等，托盘和 voice_loop 其实
在同一个进程内（main.py --daemon → JarvisDaemon.run → _run_voice_session）。
但 voice_loop 的 stt.listen() 会阻塞主线程，托盘回调运行在 pystray 的
独立线程里 — threading.Event 理论上可以跨线程，但为确保万无一失且未来
架构调整（如语音拆子进程）也能用，这里用文件做单一可信源（SSOT）。

状态文件: ~/.jarvis/voice_enabled
内容: "true"（开启）或 "false"（关闭），默认 "true"

@author aceFelix
"""

from __future__ import annotations

import os
from pathlib import Path


def _state_file() -> Path:
    """返回语音状态文件路径 ~/.jarvis/voice_enabled。"""
    return Path.home() / ".jarvis" / "voice_enabled"


def is_voice_enabled() -> bool:
    """读取语音开关状态。文件不存在或内容非 "false" 时默认返回 True。

    Returns:
        True 表示语音开启（正常对话），False 表示语音关闭（待机，只听唤醒词）。
    """
    try:
        f = _state_file()
        if not f.exists():
            return True
        content = f.read_text(encoding="utf-8").strip().lower()
        return content != "false"
    except Exception:
        return True


def set_voice_enabled(enabled: bool) -> None:
    """写入语音开关状态到文件。

    Args:
        enabled: True 开启语音（正常对话），False 关闭语音（待机）。
    """
    try:
        f = _state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        # 原子写入: 先写临时文件再 rename，避免子进程读到半截内容
        tmp = f.with_suffix(".tmp")
        tmp.write_text("true" if enabled else "false", encoding="utf-8")
        os.replace(str(tmp), str(f))
    except Exception:
        pass


# ---- 语音互斥锁（跨进程）----
# 防止 CLI /talk 和 daemon 托盘同时开启语音模式导致麦克风/扬声器冲突。
# 文件锁 + PID 记录，进程崩溃后锁自动过期（无 PID 或 PID 不存在）。

_LOCK_FILE = Path(".jarvis") / "voice.lock"
_LOCK_TTL = 60  # 锁过期秒数（进程崩溃后的兜底）


def acquire_voice_lock() -> bool:
    """尝试获取语音独占锁。成功返回 True，失败返回 False 并返回占用者信息。

    调用方拿到 False 时应提示用户并放弃进入语音模式。
    """
    import signal

    try:
        lock_path = _state_file().parent / "voice.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if lock_path.exists():
            try:
                content = lock_path.read_text(encoding="utf-8").strip()
                pid_str, ts_str = content.split(",", 1)
                pid = int(pid_str)
                ts = float(ts_str)
                # 检查进程是否还活着
                os.kill(pid, 0)  # signal 0 = 不发送信号，只检查存在
                # PID 存在且未过期 → 锁有效
                now = __import__("time").time()
                if now - ts < _LOCK_TTL:
                    return False
            except (ValueError, OSError):
                # 文件损坏或 PID 不存在 → 锁已失效，覆盖
                pass

        # 写入当前 PID + 时间戳
        pid = os.getpid()
        ts = __import__("time").time()
        lock_path.write_text(f"{pid},{ts}", encoding="utf-8")
        return True
    except Exception:
        return True  # 锁文件写入失败不影响功能，放行


def release_voice_lock() -> None:
    """释放语音独占锁。voice_loop 退出时必须调用。"""
    try:
        lock_path = _state_file().parent / "voice.lock"
        if lock_path.exists():
            lock_path.unlink()
    except Exception:
        pass
