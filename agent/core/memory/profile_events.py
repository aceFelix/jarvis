"""事件轨（P2c）：append-only 的“临时 / 带时间点”事实记录。

## 它解决什么

画像库（`profile.json`）回答“他是什么样的人”；事件轨回答“最近在忙什么、
什么时候有什么事”。两者物理分离：

- 画像库：可改、可去重、可软删除、参与注入；
- 事件轨：只追加、按保留期清理、**默认不注入** system prompt。

## 存储

`~/.jarvis/memory/events.jsonl`，一行一条 JSON：

```json
{"ts": 1.79e9, "category": "schedule", "content": "下周三面试",
 "source_session": "面试安排", "expire_at": 1.79e9}
```

- 写入来源（P2c）：提炼裁决为 `lifecycle=episodic` 的事实（带 `expire_days`）；
- 读取：`/memory events [n]` 查看最近未过期事件；
- 清理：每日维护按 `profile_event_retention_days` 删除“已到期或超过保留期”的记录。

## 铁律

- **任何 IO/解析失败都静默**：事件轨是旁路，不能影响提炼、会话保存或维护任务；
- 脏行跳过不报错：文件被手工编辑过也要能继续读；
- 跨进程串行写：复用 `file_lock`；读不持锁（append 单行写入足够小）。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from agent.core.diag import diag_warn
from agent.utils.filelock import ensure_dir, file_lock

_COMPONENT = "memory.events"

#: 默认保留天数（配置键的兜底值）
DEFAULT_RETENTION_DAYS = 90

#: 单条内容上限（防脏数据撑爆行）
_MAX_CONTENT_CHARS = 200

#: 测试注入的路径（None = 默认 ~/.jarvis/memory/events.jsonl）
_path_override: Path | None = None

#: 进程内串行化 append（跨进程靠 file_lock）
_write_lock = threading.Lock()


@dataclass
class ProfileEvent:
    """一条事件记录（只追加，不修改）。"""

    ts: float
    category: str
    content: str
    source_session: str = ""
    expire_at: float = 0.0    # 0 = 不过期（仅受保留期约束）


def events_path() -> Path:
    """事件轨路径（默认 `~/.jarvis/memory/events.jsonl`）。"""
    if _path_override is not None:
        return _path_override
    return Path.home() / ".jarvis" / "memory" / "events.jsonl"


def configure_events_path(path: Path | None) -> None:
    """测试/嵌入用：改写事件轨路径。传 None 恢复默认。"""
    global _path_override
    _path_override = path


def reset_for_tests() -> None:
    """恢复默认路径（测试 teardown 用）。"""
    configure_events_path(None)


def _lock_file() -> Path:
    path = events_path()
    return path.parent / (path.name + ".lock")


def _coerce(raw: object) -> ProfileEvent | None:
    """把一行 JSON 洗成事件对象；不是合法记录返回 None。"""
    if not isinstance(raw, dict):
        return None
    content = str(raw.get("content", "") or "").strip()
    if not content:
        return None
    content = content[:_MAX_CONTENT_CHARS]
    try:
        ts = float(raw.get("ts", 0.0) or 0.0)
    except (TypeError, ValueError):
        ts = 0.0
    try:
        expire_at = max(0.0, float(raw.get("expire_at", 0.0) or 0.0))
    except (TypeError, ValueError):
        expire_at = 0.0
    return ProfileEvent(
        ts=ts,
        category=str(raw.get("category", "other") or "other"),
        content=content,
        source_session=str(raw.get("source_session", "") or ""),
        expire_at=expire_at,
    )


def append_event(
    category: str,
    content: str,
    *,
    source_session: str = "",
    ts: float | None = None,
    expire_at: float = 0.0,
) -> bool:
    """追加一条事件（失败静默）。返回是否写入成功。

    Args:
        category: 画像类别（与 profile 库同一套类别名）。
        content: 事件内容（截断到 200 字）。
        source_session: 来源会话（可追溯）。
        ts: 发生/记录时间（默认当前时间，测试可注入）。
        expire_at: 到期时间戳（0 = 不过期，只受保留期约束）。

    @author aceFelix
    """
    text = str(content or "").strip()[:_MAX_CONTENT_CHARS]
    if not text:
        return False
    event = ProfileEvent(
        ts=time.time() if ts is None else float(ts),
        category=str(category or "other"),
        content=text,
        source_session=str(source_session or ""),
        expire_at=max(0.0, float(expire_at or 0.0)),
    )
    path = events_path()
    try:
        with _write_lock:
            ensure_dir(path.parent)
            with file_lock(_lock_file()):
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        return True
    except Exception as e:  # noqa: BLE001 - 事件轨是旁路，绝不打断主流程
        diag_warn(_COMPONENT, f"事件写入失败: {type(e).__name__}: {e}")
        return False


def load_events() -> list[ProfileEvent]:
    """读取全部合法事件（按 ts 升序；坏行跳过）。失败返回空表。"""
    path = events_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except Exception as e:  # noqa: BLE001
        diag_warn(_COMPONENT, f"事件读取失败: {type(e).__name__}: {e}")
        return []
    events: list[ProfileEvent] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = _coerce(json.loads(line))
        except Exception:  # noqa: BLE001 - 脏行跳过
            continue
        if event is not None:
            events.append(event)
    events.sort(key=lambda e: e.ts)
    return events


def recent_events(limit: int = 5, *, now: float | None = None) -> list[ProfileEvent]:
    """最近 `limit` 条**未过期**事件（按 ts 升序返回，便于阅读）。"""
    if limit <= 0:
        return []
    now_ts = time.time() if now is None else float(now)
    alive = [e for e in load_events() if not (e.expire_at and now_ts >= e.expire_at)]
    return alive[-limit:]


def clear_events() -> int:
    """整轨清空（`/memory clear` 调用），返回被删掉的记录条数。

    为什么必须连同事件轨一起清：`/memory clear` 的语义是“把画像忘干净”，而
    事件轨同属画像派生数据。只清 `profile.json` 会留下“已清空但 /memory events
    还能看到旧行程”的不一致。

    实现上**删文件**而不是写空文件：空文件与“从未写过”在语义上没区别，留着
    反而让下一次 `append_event` 的目录检查多此一举。

    与 `append_event` 共用进程锁与跨进程文件锁；文件不存在 / IO 失败都静默
    返回 0（事件轨是旁路，不能拖挂清空操作）。

    @author aceFelix
    """
    path = events_path()
    if not path.is_file():
        return 0
    removed = len(load_events())
    try:
        with _write_lock:
            with file_lock(_lock_file()):
                path.unlink(missing_ok=True)
        return removed
    except Exception as e:  # noqa: BLE001 - 旁路日志，失败不影响清空画像
        diag_warn(_COMPONENT, f"事件清空失败: {type(e).__name__}: {e}")
        return 0


def prune_events(
    retention_days: int = DEFAULT_RETENTION_DAYS, *, now: float | None = None
) -> int:
    """清理事件轨：删掉“已到期或超过保留期”的记录，返回删除条数。

    `retention_days <= 0` 时视为关闭清理（只删已到期的）。失败静默。
    """
    now_ts = time.time() if now is None else float(now)
    events = load_events()
    if not events:
        return 0
    cutoff = now_ts - max(0, int(retention_days)) * 86400 if retention_days > 0 else 0.0

    def _keep(e: ProfileEvent) -> bool:
        if e.expire_at and now_ts >= e.expire_at:
            return False
        if cutoff and e.ts < cutoff:
            return False
        return True

    kept = [e for e in events if _keep(e)]
    removed = len(events) - len(kept)
    if not removed:
        return 0
    path = events_path()
    tmp = path.parent / (path.name + ".tmp")
    try:
        with _write_lock:
            ensure_dir(path.parent)
            with file_lock(_lock_file()):
                tmp.write_text(
                    "".join(json.dumps(asdict(e), ensure_ascii=False) + "\n" for e in kept),
                    encoding="utf-8",
                )
                os.replace(tmp, path)
        return removed
    except Exception as e:  # noqa: BLE001
        diag_warn(_COMPONENT, f"事件清理失败: {type(e).__name__}: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return 0
