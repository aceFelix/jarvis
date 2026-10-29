"""serve 宿主卡死修复与停止回复（reply.abort）回归测试。

覆盖三处修复（背景见 docs/fixlogs/serve-bash-hang-fix.md）：
- BashTool._call_normal 子进程 stdin 必须显式 DEVNULL（继承 serve 宿主的
  永不关闭管道会让 MSYS2 bash 挂死），且取消时回收子进程；
- WorkbenchUI.ask_user_async 等待回答期间宿主事件循环保持可调度
  （同步版 ask_user 会饿死同环串行的 _command_loop → 自死锁 600s）；
- ChatEngine.abort_current_reply 线程安全取消当前 send 轮次，
  _command_loop 捕获 CancelledError 后继续服务后续指令。

引擎预置会话时用真 ``ToolContext``：``_handle_send`` 轮前会把本轮检查点 cid
与会话名写进 ``ctx.extra``（W3.2 副作用账本要用），空对象没有这个字段。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time

import pytest

from agent.config.settings import Settings
from agent.core.context import ToolContext
from agent.tools.bash import BashTool
from agent.ui.workbench.bridge import WorkbenchUI, _EventEmitter
from agent.ui.workbench.engine import ChatEngine


# ---- BashTool：stdin=DEVNULL + 取消回收 ----

class _FakeProc:
    """假子进程：记录 kill 调用，wait 立即返回，输出用真 StreamReader 喂。"""

    returncode = 0

    def __init__(self, output: bytes = b"out") -> None:
        self.killed = False
        self.stdout = _make_stream(output)
        self.stderr = _make_stream(b"")

    async def wait(self):
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def _make_stream(data: bytes) -> asyncio.StreamReader:
    """造一个已喂入数据且带 EOF 的 StreamReader（读完立即返回）。"""
    sr = asyncio.StreamReader()
    sr.feed_data(data)
    sr.feed_eof()
    return sr


class _HangingProc(_FakeProc):
    """子进程永不自行退出；被 kill 时才“退出”。"""

    def __init__(self) -> None:
        super().__init__()
        self._exited = asyncio.Event()

    async def wait(self):
        await self._exited.wait()
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self._exited.set()


@pytest.mark.asyncio
async def test_bash_normal_uses_devnull_stdin(monkeypatch):
    """_call_normal 必须显式 stdin=DEVNULL：继承宿主管道会挂死 MSYS2 bash。"""
    captured: dict = {}
    proc = _FakeProc()

    async def fake_exec(*args, **kwargs):
        captured.update(kwargs)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await BashTool()._call_normal("echo hi", ".", 30, ToolContext(workdir=".", messages=[]))
    assert not result.is_error
    assert captured["stdin"] == asyncio.subprocess.DEVNULL
    assert captured["stdout"] == asyncio.subprocess.PIPE


@pytest.mark.asyncio
async def test_bash_normal_cancellation_kills_child(monkeypatch):
    """reply.abort 取消穿透等待时必须回收子进程再抛取消。"""

    proc = _HangingProc()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    task = asyncio.create_task(
        BashTool()._call_normal("sleep 100", ".", 30, ToolContext(workdir=".", messages=[]))
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed


@pytest.mark.asyncio
async def test_bash_normal_aborts_on_abort_event(monkeypatch):
    """ctx.abort_event 置位时立即回收子进程并返回「已取消」。

    语音模式下 SIGINT/ESC 只能置标志（在主线程 raise 会撕裂 asyncio.run）。
    工具层是长操作里唯一能感知它的地方：旧实现只等 communicate，命令跑满
    timeout 期间按 Ctrl+C / ESC 毫无反应（实机：等满 120 秒 + 重试再等 120 秒）。
    """

    proc = _HangingProc()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    ctx = ToolContext(workdir=".", messages=[])
    task = asyncio.create_task(BashTool()._call_normal("sleep 100", ".", 120, ctx))
    await asyncio.sleep(0.05)
    ctx.abort_event.set()
    result = await asyncio.wait_for(task, timeout=2.0)

    assert result.is_error
    assert "已取消" in str(result.data)
    assert result.retryable is False
    assert proc.killed


@pytest.mark.asyncio
async def test_bash_timeout_marks_not_retryable(monkeypatch):
    """命令跑满超时必须标记 retryable=False，否则自愈层会重跑一遍。

    重跑的代价是双倍等时 + 副作用重复（前台拉起浏览器会被打开两次）。
    """

    proc = _HangingProc()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await BashTool()._call_normal("sleep 100", ".", 1, ToolContext(workdir=".", messages=[]))

    assert result.is_error
    assert "超时" in str(result.data)
    assert result.retryable is False
    assert proc.killed


@pytest.mark.asyncio
async def test_bash_returns_when_shell_exits_despite_open_pipe(monkeypatch):
    """shell 退出即返回，不等管道 EOF。

    这是「打开网页也要卡两分钟」的根因：`firefox.exe URL &` 会让 shell 立即退出，
    但 firefox 继承了 stdout/stderr 写端，管道 EOF 永不到来。旧实现用
    communicate()（等 EOF）就卡满 timeout。现在只等 shell 退出 + 读走已有输出。
    """

    class _ShellExitsButPipeOpen(_FakeProc):
        """shell 已退出，但 stdout 上还有一个永不关闭的写端（模拟后台 GUI 进程）。"""

        def __init__(self) -> None:
            super().__init__()
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(b"launched\n")
            # 刻意 feed_eof()：没有 EOF，读第二个 chunk 会挂起

    proc = _ShellExitsButPipeOpen()

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    started = time.monotonic()
    result = await BashTool()._call_normal(
        "firefox.exe https://www.bilibili.com &",
        ".",
        120,
        ToolContext(workdir=".", messages=[]),
    )
    elapsed = time.monotonic() - started

    assert not result.is_error
    assert "launched" in str(result.data)
    # 远小于 timeout（120s）；drain 宽限是 0.05s
    assert elapsed < 5.0


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
    engine._ctx = ToolContext(workdir="", messages=[])
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
    engine._ctx = ToolContext(workdir="", messages=[])
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
