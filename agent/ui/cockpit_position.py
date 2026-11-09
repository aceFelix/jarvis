"""主 HUD 位置记忆：``~/.jarvis/ui_state.json`` 的读写。

从 ``cockpit_window.py`` 拆出（该文件接近 900 行上限，2026-11-09）。只做一件事：
把驾驶舱窗口最后一次位置落盘/恢复，任何异常都静默（位置记忆坏了不能影响启动）。

@author aceFelix
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["load_position", "save_position", "state_path"]


def state_path() -> Path:
    """位置记忆文件路径（与其它 UI 状态共用 ui_state.json）。"""
    return Path.home() / ".jarvis" / "ui_state.json"


def load_position() -> tuple[int, int] | None:
    """读取保存的 HUD 位置。

    Returns:
        ``(x, y)``；文件缺失、字段不全或内容损坏时返回 ``None``（用默认位置）。
    """
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        cockpit = data.get("cockpit") or {}
        return int(cockpit["x"]), int(cockpit["y"])
    except Exception:
        return None


def save_position(x: int, y: int) -> None:
    """写入 HUD 位置；目录不存在时自动创建，失败静默。

    Args:
        x: 窗口左上角屏幕坐标 X。
        y: 窗口左上角屏幕坐标 Y。
    """
    try:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"cockpit": {"x": int(x), "y": int(y)}}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
