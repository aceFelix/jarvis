"""可视化窗口的位置记忆：``~/.jarvis/ui_state.json`` 的读写。

从 ``cockpit_window.py`` 拆出（该文件接近 900 行上限，2026-11-09）。只做一件事：
把窗口最后一次位置落盘/恢复，任何异常都静默（位置记忆坏了不能影响启动）。

2026-11-11 图片窗口接入后改为**分段读-改-写**：原先 ``save_position`` 是整文件覆盖，
只写了 ``cockpit`` 段；现在同时存在 ``image`` 段，整文件覆盖会让两个窗口互相冲掉
对方的位置。所以写入统一走 ``_write_section``（先读回整份、只改自己那一段）。

@author aceFelix
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = [
    "load_image_position",
    "load_position",
    "save_image_position",
    "save_position",
    "state_path",
]


def state_path() -> Path:
    """位置记忆文件路径（与其它 UI 状态共用 ui_state.json）。"""
    return Path.home() / ".jarvis" / "ui_state.json"


def _read_state() -> dict:
    """读回整份状态；文件缺失/损坏/非对象一律当空表（不抛）。"""
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_section(name: str, payload: dict) -> None:
    """只改 ``name`` 这一段，其余段原样保留。"""
    try:
        data = _read_state()
        data[name] = payload
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _read_section(name: str) -> tuple[int, int] | None:
    """读某个窗口段的位置；字段缺失或类型不对返回 ``None``。"""
    try:
        section = _read_state().get(name) or {}
        return int(section["x"]), int(section["y"])
    except Exception:
        return None


def load_position() -> tuple[int, int] | None:
    """读取保存的 HUD 位置。

    Returns:
        ``(x, y)``；文件缺失、字段不全或内容损坏时返回 ``None``（用默认位置）。
    """
    return _read_section("cockpit")


def save_position(x: int, y: int) -> None:
    """写入 HUD 位置（保留其它段）。

    Args:
        x: 窗口左上角屏幕坐标 X。
        y: 窗口左上角屏幕坐标 Y。
    """
    _write_section("cockpit", {"x": int(x), "y": int(y)})


def load_image_position() -> tuple[int, int] | None:
    """读取图片窗口位置（独立段 ``image``）。"""
    return _read_section("image")


def save_image_position(x: int, y: int) -> None:
    """写入图片窗口位置（保留 ``cockpit`` 段）。"""
    _write_section("image", {"x": int(x), "y": int(y)})
