"""harness 启用/禁用状态（`~/.jarvis/cli_anything/disabled.json`）。

从 `market.py` 拆出（2026-11-05）：market.py 当时 905 行，已越过仓库的 900 行上限，
而这一块本来就是独立职责——它不查 registry、不下载 SKILL.md、不跑 pip，只维护一份
id 列表。留在原处只会让那个文件继续长。

`market.py` 仍把下面四个函数重导出，所以既有调用方（`agent.cli_anything.market`
的直接导入、serve 侧的延迟导入、以及按 `market.enable_harness` 打桩的用例）都不用改。

设计纪律两条：

- **禁用 ≠ 卸载**：只停止加载，文件留在原地，`enable` 就能回来；
- **生效时机要写进回执**：状态是加载期读的，改完必须重启或 `/reset` 才见效。
  不写这句，用户会当场试一次然后认定功能坏了。

@author aceFelix
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent.cli_anything.ids import normalize_harness_id

logger = logging.getLogger(__name__)

#: 禁用清单落盘位置。与 harness 安装目录分开：这份状态是用户意图，重装不该抹掉。
_HARNESS_STATE_FILE = Path.home() / ".jarvis" / "cli_anything" / "disabled.json"


def _load_harness_disabled_state() -> dict[str, Any]:
    """加载 harness 禁用状态；读不到或读坏了都退回"没有任何禁用"。

    这里吞异常是有意的：状态文件损坏不该让 `/harness` 列表整个打不开。
    """
    try:
        if _HARNESS_STATE_FILE.exists():
            return json.loads(_HARNESS_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"disabled": []}


def _save_harness_disabled_state(state: dict[str, Any]) -> None:
    """持久化 harness 禁用状态；写失败只告警，不假装成功。"""
    try:
        _HARNESS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _HARNESS_STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("保存 harness 禁用状态失败: %s", e)


def enable_harness(harness_id: str) -> dict[str, Any]:
    """启用被禁用的 harness。

    Returns:
        {"success": bool, "message": str}

    @author aceFelix
    """
    cid = normalize_harness_id(harness_id)
    if not cid:
        return {"success": False, "message": "harness ID 为空"}

    state = _load_harness_disabled_state()
    if cid not in state.get("disabled", []):
        return {"success": True, "message": f"harness '{cid}' 已是启用状态。"}

    state["disabled"] = [x for x in state["disabled"] if x != cid]
    _save_harness_disabled_state(state)
    return {"success": True, "message": f"harness '{cid}' 已启用。重启或 /reset 后生效。"}


def disable_harness(harness_id: str) -> dict[str, Any]:
    """禁用 harness（不卸载，仅停止加载）。

    Returns:
        {"success": bool, "message": str}

    @author aceFelix
    """
    cid = normalize_harness_id(harness_id)
    if not cid:
        return {"success": False, "message": "harness ID 为空"}

    state = _load_harness_disabled_state()
    if cid in state.get("disabled", []):
        return {"success": True, "message": f"harness '{cid}' 已是禁用状态。"}

    state.setdefault("disabled", []).append(cid)
    _save_harness_disabled_state(state)
    return {"success": True, "message": f"harness '{cid}' 已禁用。重启或 /reset 后生效。"}


def is_harness_disabled(harness_id: str) -> bool:
    """检查 harness 是否被禁用（比较前先归一化 id，否则大小写不同的输入会漏判）。"""
    cid = normalize_harness_id(harness_id)
    if not cid:
        return False
    state = _load_harness_disabled_state()
    return cid in state.get("disabled", [])


def list_disabled_harnesses() -> list[str]:
    """返回被禁用的 harness ID 列表。"""
    state = _load_harness_disabled_state()
    return state.get("disabled", [])
