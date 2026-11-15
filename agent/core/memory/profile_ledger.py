"""提炼水位台账（P0）。

## 为什么需要它

`_auto_save` 每轮对话都会触发 `maybe_refine_async`，而旧实现只有"距上次提炼 ≥600s"一道闸，
不记"上次提炼到第几条"。于是长会话每 10 分钟把最近 40 条**重喂一遍**，同一事实被反复
"发现"——这是画像记忆重复入库的源头（见 docs/roadmap/PLAN-profile-memory-upgrade.md §P0）。

本模块为每个会话记录 `refined_until`（已提炼到的消息下标），让提炼只处理 `messages[refined_until:]`
的增量。

## 存储

`~/.jarvis/memory/refine_ledger.json`，version=1：

```json
{"version": 1, "sessions": {
  "<session_id>": {"refined_until": 42, "last_msg_ts": 1.79e9,
                   "last_refine_ts": 1.79e9, "last_error": ""}
}}
```

## 铁律

- **只有 `advance()` 能增大 `refined_until`**；`touch()` 只在消息变少（`/rewind`、`/compact`
  截断）时下调它，避免水位永久悬空导致再也不提炼。
- **读-改-写全程持跨进程文件锁**：只锁写会让两个进程/线程互相覆盖（各读旧表、各写全表）。
- **任何 IO/锁失败都不得抛给调用方**：提炼是后台旁路，不能影响会话保存。
- 拿不到锁时**只更新进程内缓存**并记 diag；下次成功写入自动补齐。
- 台账损坏当作空表 → 退化为"全量提炼一次"再重建水位（漏提炼比漏记忆安全）。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from agent.core.diag import diag_warn
from agent.utils.filelock import ensure_dir, file_lock

#: 台账最多保留的会话数（按最后活跃时间淘汰）
MAX_SESSIONS = 50

_COMPONENT = "memory.ledger"

#: 测试注入的路径（None = 默认 ~/.jarvis/memory/refine_ledger.json）
_path_override: Path | None = None

#: 进程内缓存：文件读不到或写失败时作为兜底，保证同进程内的水位仍然推进
_cache: dict[str, "RefineState"] = {}
_cache_lock = threading.Lock()


@dataclass
class RefineState:
    """单个会话的提炼水位。"""

    refined_until: int = 0          # 已提炼到的消息下标（messages[refined_until:] 待处理）
    last_msg_ts: float = 0.0        # 最近一次 touch 的时间（空闲判定依据）
    last_attempt_ts: float = 0.0    # 最近一次**尝试**提炼的时间（节流依据：失败也算，防每轮重试）
    last_refine_ts: float = 0.0     # 最近一次**成功推进**的时间（诊断用）
    last_error: str = ""            # 最近一次失败原因（仅诊断用，不参与判定）


def ledger_path() -> Path:
    """台账路径（默认 `~/.jarvis/memory/refine_ledger.json`）。"""
    if _path_override is not None:
        return _path_override
    return Path.home() / ".jarvis" / "memory" / "refine_ledger.json"


def configure_ledger_path(path: Path | None) -> None:
    """测试/嵌入用：改写台账路径并清空缓存。传 None 恢复默认。"""
    global _path_override
    _path_override = path
    with _cache_lock:
        _cache.clear()


def reset_for_tests() -> None:
    """清空缓存并恢复默认路径（测试 teardown 用）。"""
    configure_ledger_path(None)


# ─────────────────────────────────────────────────────────────
# 内部：序列化
# ─────────────────────────────────────────────────────────────

def _coerce(raw: object) -> RefineState:
    """把磁盘上的 dict 防御性转成 RefineState（坏值一律回默认）。"""
    if not isinstance(raw, dict):
        return RefineState()
    try:
        refined_until = max(0, int(raw.get("refined_until", 0) or 0))
    except (TypeError, ValueError):
        refined_until = 0
    try:
        last_msg_ts = float(raw.get("last_msg_ts", 0.0) or 0.0)
    except (TypeError, ValueError):
        last_msg_ts = 0.0
    try:
        last_refine_ts = float(raw.get("last_refine_ts", 0.0) or 0.0)
    except (TypeError, ValueError):
        last_refine_ts = 0.0
    try:
        last_attempt_ts = float(raw.get("last_attempt_ts", 0.0) or 0.0)
    except (TypeError, ValueError):
        last_attempt_ts = 0.0
    return RefineState(
        refined_until=refined_until,
        last_msg_ts=last_msg_ts,
        last_attempt_ts=last_attempt_ts,
        last_refine_ts=last_refine_ts,
        last_error=str(raw.get("last_error", "") or ""),
    )


def _read_text_retry(path: Path, *, attempts: int = 5, delay: float = 0.02) -> str:
    """读文本，失败重试几次。

    Windows 上 `os.replace` 与读同时发生时，读者会拿到 `PermissionError`
    （共享冲突）——高频轮询（如空闲判定）会放大这个概率。写入本身已原子，
    重试一下即可读到旧版或新版，绝不能因一次时钟竞争就把水位当成 0。
    """
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise
        except PermissionError as e:
            last = e
            if i < attempts - 1:
                time.sleep(delay)
    assert last is not None
    raise last


def _replace_retry(tmp: Path, path: Path, *, attempts: int = 5, delay: float = 0.02) -> None:
    """`os.replace` 的 Windows 重试版（读者短暂占用时换不进去）。"""
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            last = e
            if i < attempts - 1:
                time.sleep(delay)
    assert last is not None
    raise last


def _read_file() -> dict[str, RefineState]:
    """读全表（调用方负责是否持锁）。文件缺失/损坏/无权限 → 空表 + diag（绝不抛）。"""
    path = ledger_path()
    try:
        data = json.loads(_read_text_retry(path))
    except FileNotFoundError:
        return {}
    except Exception as e:  # noqa: BLE001 - 台账坏了不能拦提炼
        diag_warn(_COMPONENT, f"台账读取失败，按空表处理: {type(e).__name__}: {e}")
        return {}
    sessions = data.get("sessions") if isinstance(data, dict) else None
    if not isinstance(sessions, dict):
        return {}
    return {str(k): _coerce(v) for k, v in sessions.items()}


def _write_file(sessions: dict[str, RefineState]) -> None:
    """原子写全表（调用方必须已持锁）。失败向上抛，由调用方决定降级。"""
    path = ledger_path()
    payload = {"version": 1, "sessions": {k: asdict(v) for k, v in sessions.items()}}
    tmp = path.parent / (path.name + ".tmp")
    ensure_dir(path.parent)
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _replace_retry(tmp, path)


def _prune(sessions: dict[str, RefineState], max_sessions: int = MAX_SESSIONS) -> dict[str, RefineState]:
    """按 last_msg_ts 保留最新 N 个会话（纯函数）。"""
    if len(sessions) <= max_sessions:
        return sessions
    kept = sorted(sessions.items(), key=lambda kv: kv[1].last_msg_ts, reverse=True)[:max_sessions]
    return dict(kept)


def _cache_store(sessions: dict[str, RefineState]) -> None:
    with _cache_lock:
        _cache.clear()
        for k, v in sessions.items():
            _cache[k] = RefineState(**asdict(v))


def _lock_file() -> Path:
    path = ledger_path()
    return path.parent / (path.name + ".lock")


def _update(session_id: str, mutate: Callable[[RefineState], None]) -> RefineState:
    """读-改-写在**同一把跨进程锁**内完成。

    拿锁超时 → 退化为"只改进程内缓存"：本次运行内水位仍然生效，下次成功写盘时补齐。
    任何异常都不会抛给调用方（提炼是旁路）。
    """
    sid = str(session_id or "default")
    path = ledger_path()
    try:
        ensure_dir(path.parent)
        with file_lock(_lock_file()):
            sessions = _read_file()
            state = sessions.get(sid) or RefineState()
            mutate(state)
            sessions[sid] = state
            sessions = _prune(sessions)
            _write_file(sessions)
        _cache_store(sessions)
        return RefineState(**asdict(state))
    except TimeoutError:
        diag_warn(_COMPONENT, "台账拿锁超时，本次只更新进程内缓存")
    except Exception as e:  # noqa: BLE001 - IO 失败不能拦提炼
        diag_warn(_COMPONENT, f"台账写入失败: {type(e).__name__}: {e}")

    # 降级路径：以缓存为底做同样的变更，保证同进程内可见
    with _cache_lock:
        state = _cache.get(sid) or RefineState()
        mutate(state)
        _cache[sid] = state
        return RefineState(**asdict(state))


# ─────────────────────────────────────────────────────────────
# 公开 API
# ─────────────────────────────────────────────────────────────

def load_state(session_id: str) -> RefineState:
    """读一个会话的水位；文件里没有时回落到进程内缓存，再回落到默认值。"""
    sid = str(session_id or "default")
    sessions = _read_file()
    if sid in sessions:
        with _cache_lock:
            _cache[sid] = RefineState(**asdict(sessions[sid]))
        return RefineState(**asdict(sessions[sid]))
    with _cache_lock:
        cached = _cache.get(sid)
        return RefineState(**asdict(cached)) if cached else RefineState()


def save_state(session_id: str, state: RefineState) -> None:
    """整条覆盖（导入/迁移用；日常走 touch/advance）。"""
    def _mutate(target: RefineState) -> None:
        target.refined_until = max(0, int(state.refined_until))
        target.last_msg_ts = float(state.last_msg_ts)
        target.last_attempt_ts = float(state.last_attempt_ts)
        target.last_refine_ts = float(state.last_refine_ts)
        target.last_error = str(state.last_error or "")

    _update(session_id, _mutate)


def touch(session_id: str, msg_count: int, *, now: float | None = None) -> RefineState:
    """每轮对话调用：刷新 last_msg_ts；消息变少时下调水位。

    消息变少的典型来源：`/rewind`、`/compact`、会话被重新载入。若不下调，
    `refined_until` 会永久大于实际消息数，此后 `delta` 恒为 0，再也不提炼。
    """
    ts = time.time() if now is None else float(now)
    count = max(0, int(msg_count))

    def _mutate(state: RefineState) -> None:
        state.last_msg_ts = ts
        if count < state.refined_until:
            diag_warn(
                _COMPONENT,
                f"消息变少({state.refined_until}->{count})，水位下调（会话={session_id!r}）",
            )
            state.refined_until = count

    return _update(session_id, _mutate)


def pending_delta(session_id: str, msg_count: int) -> int:
    """待提炼的消息条数（负数归一为 0）。"""
    state = load_state(session_id)
    return max(0, int(msg_count) - state.refined_until)


def advance(session_id: str, refined_until: int, *, now: float | None = None) -> RefineState:
    """提炼成功/可推进时调用：前移水位并刷新 last_refine_ts、清空 last_error。"""
    ts = time.time() if now is None else float(now)
    target = max(0, int(refined_until))

    def _mutate(state: RefineState) -> None:
        # 只增不减：并发下更旧的结果不应覆盖更新的水位
        state.refined_until = max(state.refined_until, target)
        state.last_refine_ts = ts
        state.last_error = ""

    return _update(session_id, _mutate)


def mark_attempt(session_id: str, *, now: float | None = None) -> RefineState:
    """记录一次**提炼尝试**（节流依据）。

    必须在启线程前调用，且失败也要算——否则 provider 持续报错时，每轮
    `_auto_save` 都会再起一个线程重试（对没有 key 的用户就是无限重试）。
    """
    ts = time.time() if now is None else float(now)

    def _mutate(state: RefineState) -> None:
        state.last_attempt_ts = ts

    return _update(session_id, _mutate)


def mark_error(session_id: str, error: str) -> None:
    """记录一次失败（不推进水位，下次重试同一段增量）。"""
    def _mutate(state: RefineState) -> None:
        state.last_error = str(error or "")[:500]

    _update(session_id, _mutate)


def idle_sessions(
    *,
    now: float,
    idle_minutes: int,
    min_delta: int,
    counts: dict[str, int],
) -> list[str]:
    """纯判定：哪些会话"够久没动且有足够增量"，需要补一次提炼。

    Args:
        now: 当前时间戳。
        idle_minutes: 空闲阈值（分钟）。
        min_delta: 至少多少条新消息才值得提炼。
        counts: `{session_id: 当前消息数}`，由宿主提供。

    Returns:
        满足条件的 session_id 列表（按 last_msg_ts 从旧到新）。
    """
    if idle_minutes <= 0 or min_delta <= 0:
        return []
    sessions = _read_file()
    with _cache_lock:
        for sid, v in _cache.items():
            sessions.setdefault(sid, v)
    threshold = float(idle_minutes) * 60.0
    due: list[tuple[float, str]] = []
    for sid, state in sessions.items():
        if state.last_msg_ts <= 0:
            continue
        if float(now) - state.last_msg_ts < threshold:
            continue
        total = int(counts.get(sid, state.refined_until))
        if total - state.refined_until >= int(min_delta):
            due.append((state.last_msg_ts, sid))
    due.sort()
    return [sid for _, sid in due]


def prune_sessions(max_sessions: int = MAX_SESSIONS) -> int:
    """立即按上限淘汰一次，返回被淘汰的会话数。"""
    path = ledger_path()
    try:
        ensure_dir(path.parent)
        with file_lock(_lock_file()):
            sessions = _read_file()
            before = len(sessions)
            pruned = _prune(sessions, max_sessions)
            if len(pruned) != before:
                _write_file(pruned)
        _cache_store(pruned)
        return before - len(pruned)
    except Exception as e:  # noqa: BLE001
        diag_warn(_COMPONENT, f"台账淘汰失败: {type(e).__name__}: {e}")
        return 0


def all_states() -> dict[str, RefineState]:
    """调试/测试用：返回全表快照（文件 + 缓存合并）。"""
    sessions = _read_file()
    with _cache_lock:
        for sid, v in _cache.items():
            sessions.setdefault(sid, v)
    return {k: RefineState(**asdict(v)) for k, v in sessions.items()}
