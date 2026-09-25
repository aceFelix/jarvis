"""serve 宿主卡死修复与停止回复（reply.abort）回归测试。

覆盖三处修复（背景见 docs/fixlogs/serve-bash-hang-fix.md）：
- BashTool._call_normal 子进程 stdin 必须显式 DEVNULL（继承 serve 宿主的
  永不关闭管道会让 MSYS2 bash 挂死），且取消时回收子进程；
- WorkbenchUI.ask_user_async 等待回答期间宿主事件循环保持可调度
  （同步版 ask_user 会饿死同环串行的 _command_loop → 自死锁 600s）；
- ChatEngine.abort_current_reply 线程安全取消当前 send 轮次，
  _command_loop 捕获 CancelledError 后继续服务后续指令。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import queue
import time

import pytest

from agent.config.settings import Settings
from agent.tools.bash import BashTool
from agent.ui.workbench.bridge import WorkbenchUI, _EventEmitter
from agent.ui.workbench.engine import ChatEngine


# ---- BashTool：stdin=DEVNULL + 取消回收 ----

class _FakeProc:
    """假子进程：记录 kill 调用，communicate 返回固定输出。"""

    returncode = 0

    def __init__(self) -> None:
        self.killed = False

    async def communicate(self):
        return b"out", b""

    def kill(self) -> None:
        self.killed = True


@pytest.mark.asyncio
async def test_bash_normal_uses_devnull_stdin(monkeypatch):
    """_call_normal 必须显式 stdin=DEVNULL：继承宿主管道会挂死 MSYS2 bash。"""
    captured: dict = {}
    proc = _FakeProc()

    async def fake_exec(*args, **kwargs):
        captured.update(kwargs)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await BashTool()._call_normal("echo hi", ".", 30)
    assert not result.is_error
    assert captured["stdin"] == asyncio.subprocess.DEVNULL
    assert captured["stdout"] == asyncio.subprocess.PIPE


@pytest.mark.asyncio
async def test_bash_normal_cancellation_kills_child(monkeypatch):
    """reply.abort 取消穿透 communicate 等待时必须回收子进程再抛取消。"""

    class _HangingProc(_FakeProc):
        async def communicate(self):
            await asyncio.sleep(60)

    proc = _HangingProc()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    task = asyncio.create_task(BashTool()._call_normal("sleep 100", ".", 30))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed


# ---- WorkbenchUI.ask_user_async：不饿死宿主事件循环 ----

@pytest.mark.asyncio
async def test_ask_user_async_keeps_loop_responsive():
    """ask_user_async 等待回答期间，同环其他任务（模拟 _command_loop 消费
    answer_user 与心跳）必须能继续调度；同步版 ask_user 在此场景自死锁。"""
    eq: queue.Queue = queue.Queue()
    ui = WorkbenchUI(_EventEmitter(eq))
    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    async def answer_soon() -> None:
        # 模拟前端经指令循环回填答案：只有循环不被饿死才能跑到这里
        await asyncio.sleep(0.1)
        ui.answer_user("y")

    hb = asyncio.create_task(heartbeat())
    try:
        result = await asyncio.wait_for(
            asyncio.gather(ui.ask_user_async("允许执行吗？"), answer_soon()),
            timeout=3,
        )
    finally:
        hb.cancel()
    assert result[0] == "y"
    assert ticks > 0  # 等待期间事件循环保持心跳（未被阻塞饿死）
    events = []
    while not eq.empty():
        events.append(eq.get_nowait())
    assert any(e["type"] == "ask_user" and e["payload"] == "允许执行吗？" for e in events)


# ---- ChatEngine.abort_current_reply：停止回复且指令循环存活 ----

def test_engine_abort_current_reply(monkeypatch):
    """send 进行中 abort → 任务被取消、assistant_done 仍收尾、
    info『已停止回复』外抛，且后续指令继续被消费（循环未死）。"""
    settings = Settings()
    settings.enable_mcp = False  # 启动预热不真连用户 MCP server（hermetic）
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)

    class _HangingLoop:
        """假 QueryLoop：run 长睡模拟长回复，等待被取消。"""

        async def run(self, text, ctx, images=None):
            await asyncio.sleep(60)

    # 预置会话状态跳过重型装配（与既有引擎测试同法）
    engine._session_ready = True
    engine._query_loop = _HangingLoop()
    engine._ctx = object()
    engine._messages = []  # new_session 存活断言会 clear 它，预置防空
    # 屏蔽一轮结束后的持久化/标题生成（非本测试关注点）
    monkeypatch.setattr(engine, "_after_turn", lambda: None)

    engine.start()
    try:
        command_queue.put_nowait({"cmd": "send", "text": "长任务"})
        # 等 send 轮次真正开跑（_send_task 句柄就位）
        for _ in range(200):
            if engine._send_task is not None:
                break
            time.sleep(0.02)
        assert engine._send_task is not None

        # 从测试线程（非引擎循环线程）中止当前回复
        assert engine.abort_current_reply() is True

        # 断言收尾事件序列：assistant_done（finally 兜底）+ 已停止回复
        events = []
        for _ in range(200):
            try:
                events.append(event_queue.get(timeout=0.05))
            except queue.Empty:
                pass
            types = [e["type"] for e in events]
            if "assistant_done" in types and any(
                e["type"] == "info" and e["payload"] == "已停止回复" for e in events
            ):
                break
        types = [e["type"] for e in events]
        assert "assistant_done" in types
        assert any(
            e["type"] == "info" and e["payload"] == "已停止回复" for e in events
        )
        assert engine._send_task is None

        # 指令循环存活：abort 之后的指令仍被消费
        # （answer_user 不发事件，用副作用断言；new_session 发 session_new）
        command_queue.put_nowait({"cmd": "answer_user", "text": "pong"})
        command_queue.put_nowait({"cmd": "new_session"})
        got = False
        for _ in range(100):
            try:
                e = event_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if e["type"] == "session_new":
                got = True
                break
        assert got
        assert engine._ui._answer_text == "pong"
    finally:
        engine.stop()


def test_abort_without_active_reply_returns_false():
    """无进行中回复时 abort 返回 False（前端据此忽略，不报错）。"""
    settings = Settings()
    engine = ChatEngine(settings, queue.Queue(), queue.Queue())
    assert engine.abort_current_reply() is False
