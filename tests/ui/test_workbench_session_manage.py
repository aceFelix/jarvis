"""workbench 引擎会话管理（改名/删除）回归测试。

覆盖 ChatEngine._handle_rename 与 _handle_delete 的核心分支：
- 改名当前会话同步引擎态（_session_name/_title_generated）并推 session_renamed
- 改名取消未落地的自动标题任务（防覆盖用户自定义名）
- 目标名占用拒绝、源会话不存在拒绝、空名/未变化拒绝
- 删除非当前会话推 session_deleted（不新建）
- 删除当前会话复用 _handle_new_session（清消息 + session_new）
- 删除不存在会话告警

store 层函数在 handler 内以 `from agent.core.memory.store import ...` 延迟导入，
故 monkeypatch `store_mod` 的属性即可拦截（调用时取模块最新值）。

@author aceFelix
"""

from __future__ import annotations

import queue

from agent.config.settings import Settings
from agent.core.memory import store as store_mod
from agent.ui.workbench.engine import ChatEngine


def _make_engine(session_name: str = "session-old") -> tuple[ChatEngine, queue.Queue]:
    """构造不启动线程的引擎实例，预置会话态属性（正常由 _ensure_session 设置）。"""
    event_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(Settings(), event_queue, queue.Queue())
    engine._session_name = session_name
    engine._messages = []
    engine._dialog_count = 0
    engine._title_generated = False
    return engine, event_queue


def _drain(event_queue: queue.Queue) -> list[dict]:
    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    return events


def _types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


class _FakeTask:
    """假 asyncio.Task：记录 cancel() 是否被调用，done() 可配。"""

    def __init__(self, done: bool = False) -> None:
        self._done = done
        self.cancelled = False

    def done(self) -> bool:
        return self._done

    def cancel(self) -> None:
        self.cancelled = True


# ---- 改名 ----

def test_rename_current_session_syncs_engine_state(monkeypatch) -> None:
    """改名当前会话：盘上改名 + 引擎态同步 + 推 session_renamed。"""
    engine, eq = _make_engine("session-old")
    monkeypatch.setattr(store_mod, "session_exists", lambda n: n == "session-old")
    renamed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        store_mod, "rename_session", lambda o, n: renamed.append((o, n)) or True
    )

    engine._handle_rename("session-old", "我的会话")

    assert renamed == [("session-old", "我的会话")]
    assert engine._session_name == "我的会话"
    assert engine._title_generated is True  # 抢占自动标题，防覆盖
    events = _drain(eq)
    assert "session_renamed" in _types(events)
    assert {"type": "session_renamed", "payload": {"name": "我的会话"}} in events


def test_rename_cancels_pending_title_task(monkeypatch) -> None:
    """改名当前会话时取消未落地的自动标题任务。"""
    engine, eq = _make_engine("session-old")
    task = _FakeTask(done=False)
    engine._title_task = task
    monkeypatch.setattr(store_mod, "session_exists", lambda n: n == "session-old")
    monkeypatch.setattr(store_mod, "rename_session", lambda o, n: True)

    engine._handle_rename("session-old", "新名字")

    assert task.cancelled is True


def test_rename_finished_title_task_not_cancelled(monkeypatch) -> None:
    """标题任务已完成（done）时不再 cancel（幂等安全）。"""
    engine, eq = _make_engine("session-old")
    task = _FakeTask(done=True)
    engine._title_task = task
    monkeypatch.setattr(store_mod, "session_exists", lambda n: n == "session-old")
    monkeypatch.setattr(store_mod, "rename_session", lambda o, n: True)

    engine._handle_rename("session-old", "新名字")

    assert task.cancelled is False
    assert engine._session_name == "新名字"


def test_rename_target_occupied_rejected(monkeypatch) -> None:
    """目标名已存在（存盘占用）：拒绝，告警，引擎态不变。"""
    engine, eq = _make_engine("session-old")
    monkeypatch.setattr(store_mod, "session_exists", lambda n: n == "目标名")
    monkeypatch.setattr(store_mod, "rename_session", lambda o, n: True)

    engine._handle_rename("session-old", "目标名")

    assert engine._session_name == "session-old"  # 未变
    events = _drain(eq)
    assert "session_renamed" not in _types(events)
    assert any(
        e["type"] == "warn" and "已存在" in e["payload"] for e in events
    )


def test_rename_source_missing_rejected(monkeypatch) -> None:
    """源会话不存在且非当前会话：拒绝并告警。"""
    engine, eq = _make_engine("session-cur")
    monkeypatch.setattr(store_mod, "session_exists", lambda n: False)
    monkeypatch.setattr(store_mod, "rename_session", lambda o, n: True)

    engine._handle_rename("不存在的会话", "新名字")

    events = _drain(eq)
    assert "session_renamed" not in _types(events)
    assert any(
        e["type"] == "warn" and "不存在" in e["payload"] for e in events
    )


def test_rename_empty_or_unchanged_rejected(monkeypatch) -> None:
    """空名或名字未变化：直接告警忽略，不触达 store。"""
    engine, eq = _make_engine("session-old")
    called = False
    monkeypatch.setattr(store_mod, "session_exists", lambda n: True)

    def _boom(o, n):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(store_mod, "rename_session", _boom)

    engine._handle_rename("", "x")
    engine._handle_rename("session-old", "session-old")

    assert called is False  # 未变化不应改名
    events = _drain(eq)
    assert "session_renamed" not in _types(events)
    assert sum(1 for e in events if e["type"] == "warn") >= 2


def test_rename_current_not_yet_persisted(monkeypatch) -> None:
    """当前会话尚未落盘（盘上不存在但等于当前名）：仍同步引擎态并推事件。"""
    engine, eq = _make_engine("session-live")
    monkeypatch.setattr(store_mod, "session_exists", lambda n: False)
    renamed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        store_mod, "rename_session", lambda o, n: renamed.append((o, n)) or True
    )

    engine._handle_rename("session-live", "落地名")

    assert renamed == []  # 盘上没有，无需 rename
    assert engine._session_name == "落地名"
    assert "session_renamed" in _types(_drain(eq))


# ---- 删除 ----

def test_delete_other_session_emits_deleted_only(monkeypatch) -> None:
    """删除非当前会话：推 session_deleted，不新建会话。"""
    engine, eq = _make_engine("session-cur")
    monkeypatch.setattr(store_mod, "delete_session", lambda n: True)

    engine._handle_delete("其他会话")

    events = _drain(eq)
    assert {"type": "session_deleted", "payload": {"name": "其他会话"}} in events
    assert "session_new" not in _types(events)
    assert engine._session_name == "session-cur"  # 当前会话未受影响


def test_delete_current_session_resets(monkeypatch) -> None:
    """删除当前会话：session_deleted + 复用新建语义（清消息 + session_new）。"""
    engine, eq = _make_engine("session-cur")
    engine._messages = ["m1", "m2"]  # 类型无关，仅验证被清空
    engine._dialog_count = 3
    monkeypatch.setattr(store_mod, "delete_session", lambda n: True)

    engine._handle_delete("session-cur")

    events = _drain(eq)
    types = _types(events)
    assert "session_deleted" in types
    assert "session_new" in types
    assert engine._messages == []
    assert engine._dialog_count == 0
    assert engine._title_generated is False
    assert engine._session_name != "session-cur"  # 已换新会话名


def test_delete_missing_session_warns(monkeypatch) -> None:
    """删除不存在会话（盘上无、非当前）：告警，不推 session_deleted。"""
    engine, eq = _make_engine("session-cur")
    monkeypatch.setattr(store_mod, "delete_session", lambda n: False)

    engine._handle_delete("幽灵会话")

    events = _drain(eq)
    assert "session_deleted" not in _types(events)
    assert any(
        e["type"] == "warn" and "不存在" in e["payload"] for e in events
    )


def test_delete_empty_name_noop(monkeypatch) -> None:
    """空会话名：直接返回，不触达 store 也不推事件。"""
    engine, eq = _make_engine("session-cur")
    called = False
    monkeypatch.setattr(store_mod, "delete_session", lambda n: True)

    def _spy(n):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(store_mod, "delete_session", _spy)

    engine._handle_delete("   ")

    assert called is False
    assert _drain(eq) == []
