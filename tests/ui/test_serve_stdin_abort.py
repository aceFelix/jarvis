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
import threading
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


def test_abort_during_lock_wait_returns_lock_and_closes_turn(monkeypatch):
    """取消落在「等 query 锁」窗口时的收尾与归还。

    _handle_send 先把当前任务登记为 _send_task、再 await run_in_executor(acquire)。
    高负载下线程池排队会拉长这个等待窗口，reply.abort 的取消正好落在窗口内时，
    如果 acquire 不在 try/finally 里：线程池随后仍会拿到锁却无人归还（三通道后续
    query 永久卡死），本轮也不发 assistant_done（前端气泡悬着）。既有用例的偶发
    失败就是这条路径，故用慢 acquire 锁把它变成确定性复现。
    """
    settings = Settings()
    settings.enable_mcp = False  # 启动预热不真连用户 MCP server（hermetic）
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)

    class _SlowLock:
        """acquire 前睡一段，放大「已登记 _send_task、尚未拿到锁」的竞态窗口。"""

        def __init__(self) -> None:
            self._inner = threading.Lock()

        def acquire(self, block: bool = True, timeout: float = -1) -> bool:
            time.sleep(0.3)
            return self._inner.acquire(block, timeout)

        def release(self) -> None:
            self._inner.release()

    class _NeverRunLoop:
        """拿到锁才会跑；本用例要求在拿到锁之前被取消，故只统计调用次数。"""

        calls = 0

        async def run(self, text, ctx, images=None):
            _NeverRunLoop.calls += 1
            await asyncio.sleep(60)

    _NeverRunLoop.calls = 0
    engine._query_lock = _SlowLock()
    # 预置会话状态跳过重型装配（与既有引擎测试同法）
    engine._session_ready = True
    engine._query_loop = _NeverRunLoop()
    engine._ctx = object()
    engine._messages = []
    monkeypatch.setattr(engine, "_after_turn", lambda: None)

    engine.start()
    try:
        command_queue.put_nowait({"cmd": "send", "text": "等锁时被停止"})
        # 等本轮登记好任务句柄（尚未拿到锁）
        for _ in range(200):
            if engine._send_task is not None:
                break
            time.sleep(0.02)
        assert engine._send_task is not None

        assert engine.abort_current_reply() is True

        # 本轮必须收尾：assistant_done（前端气泡不能悬着）+ 已停止回复
        events = []
        deadline = time.time() + 5
        while time.time() < deadline:
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
        assert "assistant_done" in types, f"取消落在等锁窗口时本轮未收尾：{types}"
        assert any(
            e["type"] == "info" and e["payload"] == "已停止回复" for e in events
        ), f"未下发停止回执：{types}"

        # 锁必须已归还：拿锁的线程池任务不能把锁带走
        assert engine._query_lock._inner.acquire(timeout=2) is True, "query 锁被永久占用"
        engine._query_lock._inner.release()
        assert _NeverRunLoop.calls == 0, "取消后不应真的开跑一轮"
        assert engine._send_task is None

        # 指令循环存活：后续指令仍被消费（新会话会发 session_new）
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
        assert got, "等锁期间被取消后指令循环不再消费后续指令"
    finally:
        engine.stop()
