"""W1 共享运行时状态 —— 无人值守（away）标志与待批清单。

CLI / serve(hub) / daemon 是不同进程，away 状态与待批清单落盘
`~/.jarvis/policy_state.json`、`~/.jarvis/pending_approvals.json` 共享；
读取带 mtime 缓存，进程内无需重启即可感知另一侧的 /away /back 切换。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_STATE_FILE = Path.home() / ".jarvis" / "policy_state.json"
_PENDING_FILE = Path.home() / ".jarvis" / "pending_approvals.json"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_write(path: Path, payload: Any) -> None:
    """临时文件 + os.replace 原子落盘，避免半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def brief_args(args: dict[str, Any], limit: int = 120) -> str:
    """工具入参摘要（待批清单/日志展示用），超长截断。

    原在 policy/engine.py，策略引擎并入五层管线后迁到这里。
    @author aceFelix
    """
    try:
        s = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(args)
    return s[:limit] + ("…" if len(s) > limit else "")


class PolicyStateStore:
    """away 状态 + 最近活动时间。

    判定优先级：手动 /away（manual）> 空闲时长推断（idle_minutes>0 时，
    距 last_activity 超过阈值即视为 away）。VisionWatcher 人脸离开接入留
    给阶段 9（W2 事件源），本期不动摄像头。

    @author aceFelix
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else _STATE_FILE
        self._mtime: float = -1.0
        self._cache: dict[str, Any] = {}

    # ---- 读写 ----

    def _load(self) -> dict[str, Any]:
        try:
            m = self._path.stat().st_mtime
        except OSError:
            return {}
        if m == self._mtime:
            return self._cache  # 文件未变，用缓存（高频工具调用零 IO）
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self._mtime, self._cache = m, data
        return data

    def set_away(self, active: bool, source: str = "manual") -> None:
        """置/清 away 标志（/away /back 命令入口）。"""
        data = dict(self._load())
        data["away"] = {"active": bool(active), "since": _now_iso(), "source": source}
        # 手动回到（back）同时刷新活动时间，避免 idle 判定立刻又判成 away
        if not active:
            data["last_activity"] = _now_iso()
        _atomic_write(self._path, data)
        self._mtime = -1.0  # 强制下次重读

    def touch_activity(self, throttle_s: float = 60.0) -> None:
        """刷新最近活动时间（工具执行即视为"现场有活动"）。

        落盘节流：60s 内重复调用不再写文件，降低高频路径的 IO。
        """
        now = time.time()
        if self._cache.get("_last_touch", 0) and now - self._cache["_last_touch"] < throttle_s:
            return
        data = dict(self._load())
        data["last_activity"] = _now_iso()
        _atomic_write(self._path, data)
        self._mtime = -1.0
        self._load()
        self._cache["_last_touch"] = now

    # ---- 查询 ----

    def is_away(self, idle_minutes: int = 0) -> tuple[bool, str]:
        """当前是否无人值守。返回 (away, 依据说明)。

        idle_minutes<=0 时仅认手动标志（本期默认，空闲推断可配置开启）。
        """
        data = self._load()
        away = data.get("away", {})
        if away.get("active") and away.get("source") == "manual":
            return True, "手动 /away"
        if idle_minutes > 0:
            last = data.get("last_activity", "")
            if last:
                try:
                    idle = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 60
                    if idle >= idle_minutes:
                        return True, f"空闲 {int(idle)} 分钟（阈值 {idle_minutes}）"
                except ValueError:
                    pass
        # 空闲推断出的 away 未成立时，手动标志以外的 active 不再算数
        return False, ""


class PendingApprovalStore:
    """待批清单：away 期间被拦下的 ASK/dry_run 决策入库留底，/back 时给出简报。

    本期只存储与呈现，不自动重放执行——重放属于"活过单次对话"的任务脊柱
    能力（W3 阶段 10），届时本清单就是它的初始队列。

    @author aceFelix
    """

    def __init__(self, path: Path | str | None = None, max_items: int = 50) -> None:
        self._path = Path(path) if path else _PENDING_FILE
        self._max = max_items

    def add(self, *, tool: str, args_brief: str, reason: str, effect: str) -> None:
        """记录一条被拦下的调用请求。"""
        items = self._load()
        items.append({
            "ts": _now_iso(), "tool": tool, "args": args_brief,
            "reason": reason, "effect": effect,
        })
        _atomic_write(self._path, items[-self._max:])

    def drain(self) -> list[dict[str, Any]]:
        """取走并清空全部待批项（/back 简报用）。"""
        items = self._load()
        if items:
            _atomic_write(self._path, [])
        return items

    def list(self) -> list[dict[str, Any]]:
        return self._load()

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []


# ---- 进程内单例（lazy）----

_state_singleton: PolicyStateStore | None = None
_pending_singleton: PendingApprovalStore | None = None


def get_policy_state(path: Path | str | None = None) -> PolicyStateStore:
    """取状态单例；传 path 强制重建（测试隔离/自定义目录）。"""
    global _state_singleton
    if _state_singleton is None or path is not None:
        _state_singleton = PolicyStateStore(path)
    return _state_singleton


def get_pending_approvals(path: Path | str | None = None) -> PendingApprovalStore:
    """取待批清单单例；传 path 强制重建。"""
    global _pending_singleton
    if _pending_singleton is None or path is not None:
        _pending_singleton = PendingApprovalStore(path)
    return _pending_singleton
