"""工作台消息级回溯操作 checkpoint_ops 测试。

覆盖：
- _is_visible_user_message / find_rewind_start：纯工具回填（role=user）
  不计数，与桌面气泡口径对齐
- find_target_checkpoint：范围 [start:] 内取最早有效检查点，跳过失效
- preview：有检查点报文件清单 / 管理器不可用、无检查点均降级 ok=True
- rewind：仅截断、回滚成功、回滚失败不截断、无检查点降级四条路径；
  截断后 dialog_count 同步递减并 _auto_save
- 引擎 _dispatch checkpoint_rewind → rewound 事件（未装配直接失败回执）
- WorkbenchAPI.checkpoint_rewind 入队参数

全程 monkeypatch make_manager / _auto_save，不触真实 git 与用户目录。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import queue
from types import SimpleNamespace

from agent.config.settings import Settings
from agent.core.message import Message, TextContent, ToolResultContent
from agent.ui.workbench import checkpoint_ops
from agent.ui.workbench.api import WorkbenchAPI
from agent.ui.workbench.engine import ChatEngine


def _user(text: str, cid: str = "") -> Message:
    m = Message(role="user", content=[TextContent(text=text)])
    if cid:
        m.extra["checkpoint_id"] = cid
    return m


def _tool_result(tool_id: str = "t1") -> Message:
    return Message(role="user", content=[
        ToolResultContent(tool_use_id=tool_id, content="ok", is_error=False),
    ])


def _assistant(text: str) -> Message:
    return Message(role="assistant", content=[TextContent(text=text)])


class _FakeManager:
    """内存版检查点管理器（不触 git）。"""

    def __init__(self, available=True, live_ids=(), desc=None, restore_ok=True):
        self._available = available
        self.live_ids = set(live_ids)
        self._desc = desc if desc is not None else {
            "files": [{"status": "M", "path": "a.txt"}], "untracked": ["new.py"],
        }
        self._restore_ok = restore_ok
        self.restored: list[str] = []

    def available(self) -> bool:
        return self._available

    def has_checkpoint(self, cid) -> bool:
        return bool(cid) and cid in self.live_ids

    def describe(self, cid):
        return self._desc

    def restore(self, cid):
        self.restored.append(cid)
        return (True, "已恢复") if self._restore_ok else (False, "回滚失败：文件占用")


def _make_engine(messages: list[Message]) -> tuple[ChatEngine, queue.Queue]:
    event_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(Settings(), event_queue, queue.Queue())
    engine._messages = messages
    engine._session_name = "s1"
    engine._session_ready = True
    engine._dialog_count = 2
    engine._title_generated = True
    engine._model = "m1"
    engine._ui = SimpleNamespace()
    return engine, event_queue


def _drain(eq: queue.Queue) -> list[dict]:
    events = []
    while not eq.empty():
        events.append(eq.get_nowait())
    return events


# ---- 定位口径 ----

def test_find_rewind_start_skips_tool_result_messages():
    """纯工具回填不成气泡：倒数第 1/2 条"用户消息"只在文本消息中数。"""
    messages = [_user("第一条"), _assistant("答1"), _tool_result(),
                _user("第二条"), _assistant("答2"), _tool_result()]
    assert checkpoint_ops.find_rewind_start(messages, 1) == 3
    assert checkpoint_ops.find_rewind_start(messages, 2) == 0
    # 不足返回 -1
    assert checkpoint_ops.find_rewind_start(messages, 3) == -1
    assert checkpoint_ops.find_rewind_start([], 1) == -1


def test_find_rewind_start_skips_blank_text():
    """空白文本 user 消息不成气泡（与 render 过滤口径一致）。"""
    messages = [_user("  "), _user("真消息"), _assistant("答")]
    assert checkpoint_ops.find_rewind_start(messages, 1) == 1


def test_find_target_checkpoint_earliest_valid():
    """范围内取最早带有效检查点的用户消息；失效检查点跳过。"""
    messages = [_user("u1", "cid-old"), _assistant("a"),
                _user("u2", "cid-mid"), _user("u3", "cid-none")]
    mgr = _FakeManager(live_ids={"cid-mid"})
    assert checkpoint_ops.find_target_checkpoint(messages, 0, mgr) == "cid-mid"
    # 起点之后完全没有有效检查点 → 空串
    assert checkpoint_ops.find_target_checkpoint(messages, 3, mgr) == ""


# ---- preview ----

def test_preview_with_checkpoint(monkeypatch):
    """有检查点：返回改动文件清单供桌面确认弹窗展示。"""
    engine, _ = _make_engine([_user("问题", "cid1"), _assistant("答")])
    mgr = _FakeManager(live_ids={"cid1"})
    monkeypatch.setattr(checkpoint_ops, "make_manager", lambda e: mgr)
    out = checkpoint_ops.preview(engine, 1)
    assert out["ok"] is True and out["has_checkpoint"] is True
    assert out["checkpoint_id"] == "cid1"
    assert out["files"] == [{"status": "M", "path": "a.txt"}]
    assert out["untracked"] == ["new.py"]


def test_preview_degrades_when_unavailable(monkeypatch):
    """管理器不可用（无 git/未启用）：ok=True 降级说明，不报错。"""
    engine, _ = _make_engine([_user("问题", "cid1")])
    monkeypatch.setattr(checkpoint_ops, "make_manager", lambda e: _FakeManager(available=False))
    out = checkpoint_ops.preview(engine, 1)
    assert out["ok"] is True and out["has_checkpoint"] is False
    assert "git" in out["reason"] or "启用" in out["reason"]


def test_preview_no_checkpoint_in_range(monkeypatch):
    """范围消息全都没有有效检查点（被修剪）→ 仅能回退对话。"""
    engine, _ = _make_engine([_user("问题", "cid-gone")])
    monkeypatch.setattr(checkpoint_ops, "make_manager", lambda e: _FakeManager(live_ids=set()))
    out = checkpoint_ops.preview(engine, 1)
    assert out["ok"] is True and out["has_checkpoint"] is False
    assert "检查点" in out["reason"]


def test_preview_insufficient_messages(monkeypatch):
    """user_tail_count 超出消息数 / 非法 → ok=False。"""
    engine, _ = _make_engine([_user("只有一条")])
    assert checkpoint_ops.preview(engine, 5)["ok"] is False
    assert checkpoint_ops.preview(engine, 0)["ok"] is False


# ---- rewind ----

def _capture_auto_save(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import agent.session_manager as sm
    monkeypatch.setattr(
        sm, "_auto_save",
        lambda ui, msgs, **kw: calls.append({"count": len(msgs), **kw}),
    )
    return calls


def test_rewind_chat_only(monkeypatch):
    """restore_files=False：只截断消息，不碰工作区。"""
    engine, _ = _make_engine(
        [_user("u1", "cid1"), _assistant("a1"), _user("u2", "cid2"), _assistant("a2")])
    mgr = _FakeManager(live_ids={"cid1", "cid2"})
    monkeypatch.setattr(checkpoint_ops, "make_manager", lambda e: mgr)
    calls = _capture_auto_save(monkeypatch)
    result = asyncio.run(checkpoint_ops.rewind(engine, 1, restore_files=False))
    assert result == {"ok": True, "removed": 2, "removed_user": 1,
                      "files_restored": False, "reason": ""}
    assert len(engine._messages) == 2
    assert engine._dialog_count == 1
    assert mgr.restored == []
    assert calls and calls[0]["count"] == 2 and calls[0]["dialog_count"] == 1


def test_rewind_restores_earliest_checkpoint(monkeypatch):
    """撤回 2 轮：回滚到范围内最早检查点，消息全部截断。"""
    engine, _ = _make_engine(
        [_user("u1", "cid1"), _assistant("a1"), _user("u2", "cid2"), _assistant("a2")])
    mgr = _FakeManager(live_ids={"cid1", "cid2"})
    monkeypatch.setattr(checkpoint_ops, "make_manager", lambda e: mgr)
    _capture_auto_save(monkeypatch)
    result = asyncio.run(checkpoint_ops.rewind(engine, 2, restore_files=True))
    assert result["ok"] is True and result["files_restored"] is True
    assert mgr.restored == ["cid1"]
    assert engine._messages == []
    assert engine._dialog_count == 0


def test_rewind_restore_failure_keeps_messages(monkeypatch):
    """文件回滚失败 → 放弃撤回，消息不截断（前端保持原样）。"""
    engine, _ = _make_engine([_user("u1", "cid1"), _assistant("a1")])
    mgr = _FakeManager(live_ids={"cid1"}, restore_ok=False)
    monkeypatch.setattr(checkpoint_ops, "make_manager", lambda e: mgr)
    _capture_auto_save(monkeypatch)
    result = asyncio.run(checkpoint_ops.rewind(engine, 1, restore_files=True))
    assert result["ok"] is False and "占用" in result["reason"]
    assert len(engine._messages) == 2


def test_rewind_no_checkpoint_degrades_to_chat(monkeypatch):
    """要求回滚但无有效检查点 → 仍截断对话，reason 说明降级。"""
    engine, _ = _make_engine([_user("u1"), _assistant("a1")])
    monkeypatch.setattr(checkpoint_ops, "make_manager", lambda e: _FakeManager(live_ids=set()))
    _capture_auto_save(monkeypatch)
    result = asyncio.run(checkpoint_ops.rewind(engine, 1, restore_files=True))
    assert result["ok"] is True and result["files_restored"] is False
    assert "仅回退对话" in result["reason"]
    assert engine._messages == []


def test_rewind_insufficient_count(monkeypatch):
    """用户消息不足 → ok=False，不动消息。"""
    engine, _ = _make_engine([_user("u1"), _assistant("a1")])
    result = asyncio.run(checkpoint_ops.rewind(engine, 9, restore_files=True))
    assert result["ok"] is False
    assert len(engine._messages) == 2


# ---- 引擎接线 ----

def test_engine_dispatch_rewind_emits_event(monkeypatch):
    """checkpoint_rewind 指令 → 引擎执行 → rewound 事件带结果。"""
    engine, eq = _make_engine([_user("u1", "cid1"), _assistant("a1")])
    recorded: dict = {}

    async def fake_rewind(eng, n, restore):
        recorded["args"] = (n, restore)
        return {"ok": True, "removed": 2, "files_restored": False, "reason": ""}

    monkeypatch.setattr(checkpoint_ops, "rewind", fake_rewind)
    asyncio.run(engine._dispatch({"cmd": "checkpoint_rewind", "user_tail_count": 2,
                                  "restore_files": True}))
    assert recorded["args"] == (2, True)
    events = _drain(eq)
    assert {"type": "rewound", "payload": {"ok": True, "removed": 2,
            "files_restored": False, "reason": ""}} in events


def test_engine_rewind_session_not_ready(monkeypatch):
    """会话未装配：不执行截断，直接回执失败的 rewound 事件。"""
    engine, eq = _make_engine([_user("u1")])
    engine._session_ready = False
    monkeypatch.setattr(
        checkpoint_ops, "rewind",
        lambda *a, **k: asyncio.sleep(0),  # 不应被调用
    )
    asyncio.run(engine._handle_checkpoint_rewind(1, True))
    events = _drain(eq)
    assert events and events[0]["type"] == "rewound"
    assert events[0]["payload"]["ok"] is False
    assert "装配" in events[0]["payload"]["reason"]


# ---- API 入队 ----

def test_api_checkpoint_rewind_posts_command():
    """API 撤回：入队参数完整、回执 pending（真实结果走事件）。"""
    cq: queue.Queue = queue.Queue()
    engine = ChatEngine(Settings(), queue.Queue(), cq)
    api = WorkbenchAPI(queue.Queue(), cq, engine, Settings())
    out = api.checkpoint_rewind(3, True)
    assert out == {"ok": True, "pending": True}
    cmd = cq.get_nowait()
    assert cmd == {"cmd": "checkpoint_rewind", "user_tail_count": 3, "restore_files": True}


def test_api_checkpoint_preview_direct(monkeypatch):
    """API 预览：不入队，直调 checkpoint_ops.preview。"""
    cq: queue.Queue = queue.Queue()
    engine = ChatEngine(Settings(), queue.Queue(), cq)
    api = WorkbenchAPI(queue.Queue(), cq, engine, Settings())
    monkeypatch.setattr(
        checkpoint_ops, "preview",
        lambda eng, n: {"ok": True, "has_checkpoint": False, "n": n},
    )
    out = api.checkpoint_preview(2)
    assert out["n"] == 2
    assert cq.empty()
