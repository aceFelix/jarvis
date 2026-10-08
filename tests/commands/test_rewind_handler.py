"""/rewind 升级后的联动回滚测试（core_commands.handle_rewind）。

覆盖三条路径 + 参数校验：
- 有有效检查点 + 用户确认 y → restore 后再弹消息
- 确认 N / 确认异常 → 仅回退对话（restore 不调用）
- --chat-only → 跳过询问，不触 mgr
- 无 mgr / mgr 不可用 / 范围内无有效检查点 → 直接纯对话回退
- restore 失败 → warn 但消息照常回退（终端语义：文件回滚是加强项）
- 无效参数 / 消息数不足 → warn 且不弹消息

@author aceFelix
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent.commands.handlers.core_commands import handle_rewind
from agent.core.message import Message, TextContent


class _FakeUI:
    def __init__(self, answer: str = "") -> None:
        self.answer = answer
        self.infos: list[str] = []
        self.warns: list[str] = []

    def info(self, text: str = "", **kw) -> None:
        self.infos.append(str(text))

    def warn(self, text: str = "", **kw) -> None:
        self.warns.append(str(text))

    async def read_user_input_async(self, prompt: str = "") -> str:
        if self.answer == "raise":
            raise EOFError("no input")
        return self.answer


class _FakeMgr:
    def __init__(self, available=True, live_ids=(), restore_ok=True) -> None:
        self._available = available
        self.live_ids = set(live_ids)
        self._restore_ok = restore_ok
        self.restored: list[str] = []

    def available(self) -> bool:
        return self._available

    def has_checkpoint(self, cid) -> bool:
        return bool(cid) and cid in self.live_ids

    def describe(self, cid):
        return {"files": [{"status": "M", "path": "a.txt"}], "untracked": []}

    def restore(self, cid):
        self.restored.append(cid)
        return (True, "工作区已恢复") if self._restore_ok else (False, "回滚失败：超时")


def _user(cid: str = "") -> Message:
    m = Message(role="user", content=[TextContent(text="问题")])
    if cid:
        m.extra["checkpoint_id"] = cid
    return m


def _assistant() -> Message:
    return Message(role="assistant", content=[TextContent(text="回答")])


def _make_ctx(messages: list[Message], ui: _FakeUI, mgr=None) -> SimpleNamespace:
    tool_ctx = SimpleNamespace(extra={"checkpoint_mgr": mgr} if mgr is not None else {})
    return SimpleNamespace(ui=ui, messages=messages, ctx=tool_ctx)


def _run(ctx, stripped: str) -> None:
    assert asyncio.run(handle_rewind(ctx, stripped)) is True


def test_rewind_with_confirm_restores_files():
    """确认 y：回滚范围内（末 2 条）最早带有效检查点的消息，再弹消息。"""
    messages = [_user("cid1"), _assistant(), _user("cid2"), _assistant()]
    mgr = _FakeMgr(live_ids={"cid1", "cid2"})
    ctx = _make_ctx(messages, _FakeUI(answer="y"), mgr)
    _run(ctx, "/rewind 2")
    assert mgr.restored == ["cid2"]  # 范围 messages[-2:] 内最早的有效检查点
    assert len(messages) == 2


def test_rewind_wide_range_picks_earliest():
    """回退整轮（n 覆盖到更早消息）：取范围内最早检查点，回滚幅度最大。"""
    messages = [_user("cid1"), _assistant(), _user("cid2"), _assistant()]
    mgr = _FakeMgr(live_ids={"cid1", "cid2"})
    ctx = _make_ctx(messages, _FakeUI(answer="y"), mgr)
    _run(ctx, "/rewind 4")
    assert mgr.restored == ["cid1"]
    assert messages == []


def test_rewind_declined_keeps_chat_only():
    """确认 N：不回滚文件，消息照常回退。"""
    messages = [_user("cid1"), _assistant(), _user("cid2"), _assistant()]
    mgr = _FakeMgr(live_ids={"cid1", "cid2"})
    ctx = _make_ctx(messages, _FakeUI(answer="n"), mgr)
    _run(ctx, "/rewind 2")
    assert mgr.restored == []
    assert len(messages) == 2
    assert any("仅回退对话" in s for s in ctx.ui.infos)


def test_rewind_confirm_error_degrades():
    """询问输入异常（EOF）：视同拒绝，仅回退对话。"""
    messages = [_user("cid1"), _assistant()]
    mgr = _FakeMgr(live_ids={"cid1"})
    ctx = _make_ctx(messages, _FakeUI(answer="raise"), mgr)
    _run(ctx, "/rewind 1")
    assert mgr.restored == []
    assert len(messages) == 1


def test_rewind_chat_only_skips_prompt():
    """--chat-only：完全跳过询问，不触 mgr。"""
    messages = [_user("cid1"), _assistant(), _user("cid2"), _assistant()]
    mgr = _FakeMgr(live_ids={"cid1", "cid2"})
    ctx = _make_ctx(messages, _FakeUI(answer="y"), mgr)
    _run(ctx, "/rewind 4 --chat-only")
    assert mgr.restored == []
    assert messages == []
    assert ctx.ui.answer == "y"  # 从未被询问


def test_rewind_no_mgr_pure_chat():
    """未注入 mgr（旧入口/降级）：维持纯对话回退。"""
    messages = [_user("cid1"), _assistant()]
    ctx = _make_ctx(messages, _FakeUI(), mgr=None)
    _run(ctx, "/rewind 1")
    assert len(messages) == 1


def test_rewind_no_valid_checkpoint_skips_prompt():
    """范围内检查点已被修剪：不询问、不回滚，直接回退对话。"""
    messages = [_user("cid-gone"), _assistant()]
    mgr = _FakeMgr(live_ids=set())
    ctx = _make_ctx(messages, _FakeUI(answer="y"), mgr)
    _run(ctx, "/rewind 1")
    assert mgr.restored == []
    assert len(messages) == 1


def test_rewind_restore_failure_still_pops():
    """文件回滚失败：warn 提示，但消息回退照常完成。"""
    messages = [_user("cid1"), _assistant()]
    mgr = _FakeMgr(live_ids={"cid1"}, restore_ok=False)
    ctx = _make_ctx(messages, _FakeUI(answer="y"), mgr)
    _run(ctx, "/rewind 2")
    assert mgr.restored == ["cid1"]
    assert messages == []
    assert any("回滚失败" in s for s in ctx.ui.warns)


def test_rewind_invalid_args():
    """无效 n / 消息数不足：warn 且不弹任何消息。"""
    messages = [_user(), _assistant()]
    ctx = _make_ctx(messages, _FakeUI())
    _run(ctx, "/rewind abc")
    assert ctx.ui.warns
    assert len(messages) == 2
    ctx2 = _make_ctx(messages, _FakeUI())
    _run(ctx2, "/rewind 0")
    assert len(messages) == 2
    ctx3 = _make_ctx(messages, _FakeUI())
    _run(ctx3, "/rewind 5")
    assert len(messages) == 2
    assert any("不足" in s for s in ctx3.ui.warns)
