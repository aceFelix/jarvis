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
