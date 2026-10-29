"""沙箱路径的超时与中断契约（aceFelix）。

锁定的实机故障（2026-10-29）：语音模式下让 jarvis 用火狐无头模式截图，
命令卡住后整台机器上表现为「jarvis 卡死、按 Ctrl+C 毫无反应、退不出去」，
最后只能强杀进程。两个根因都落在 `_call_sandboxed`：

1. **判断顺序**：`if result.error` 排在 `if result.timed_out` 之前，而 executor
   超时时**同时**设了 `timed_out=True` 和 `error="命令超时（...）"`。于是永远走
   error 分支，返回一个 `retryable` 默认 True 的结果 —— 下面那条
   `retryable=False` 形同虚设，自愈层把同一条命令再跑一遍。
2. **缺 abort 竞速**：`_call_normal` 早就有「与 ctx.abort_event 竞速」这一层，
   沙箱路径一直没有。命令跑满 timeout 期间中断完全无效。

两者叠加的效果：120 秒超时 × 2 次重试 + 1 秒间隔 ≈ 241 秒完全无法中断。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import agent.tools.bash as bash_mod
from agent.core.context import ToolContext
from agent.core.sandbox.executor import SandboxResult
from agent.tools.bash import BashTool


class _FakeAuditor:
    """审计桩：本测试不关心留痕内容。"""

    def log_execution(self, **_kwargs) -> None:
        pass

    def log_snapshot(self, *_args, **_kwargs) -> None:
        pass


class _FakeExecutor:
    """返回预设结果的假沙箱执行器；hang=True 时永不返回，模拟卡住。"""

    def __init__(self, result: SandboxResult | None = None, *, hang: bool = False):
        self._result = result
        self._hang = hang
        self.killed = False

    async def run(self, command: str, cwd: str, timeout: int) -> SandboxResult:
        if self._hang:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.killed = True
                raise
        assert self._result is not None
        return self._result


#: 低风险桩：needs_snapshot=False 以免触发真实的 FileGuard 快照
_LOW_RISK = SimpleNamespace(needs_snapshot=False, name="LOW", label="低")


def _install(monkeypatch, executor: _FakeExecutor) -> None:
    monkeypatch.setattr(bash_mod, "get_sandbox_executor", lambda settings: executor)
    monkeypatch.setattr(bash_mod, "get_sandbox_auditor", lambda settings: _FakeAuditor())


async def test_sandboxed_timeout_is_not_retryable(monkeypatch) -> None:
    """沙箱超时必须标 retryable=False，否则自愈层会把同一条命令再跑一遍。

    executor 的超时结果**同时**带 error 和 timed_out，这正是判断顺序写反后
    静默退化的地方 —— 这条用例盯的是那个顺序，不是文案。
    """
    executor = _FakeExecutor(
        SandboxResult(
            timed_out=True,
            sandboxed=True,
            error="命令超时（1秒）: firefox --headless --screenshot x.png",
        )
    )
    _install(monkeypatch, executor)
    ctx = ToolContext(workdir=".", messages=[])

    res = await BashTool()._call_sandboxed(
        "firefox --headless --screenshot x.png", ".", 1, _LOW_RISK, ctx
    )

    assert res.is_error is True
    assert res.retryable is False, "超时又被判成可重试（判断顺序退化了）"
    assert "超时" in str(res.data)


async def test_sandboxed_honors_abort(monkeypatch) -> None:
    """命令卡住时置位 abort_event，必须很快返回「已取消」，不能等满 timeout。"""
    executor = _FakeExecutor(hang=True)
    _install(monkeypatch, executor)
    ctx = ToolContext(workdir=".", messages=[])

    async def _abort_soon() -> None:
        await asyncio.sleep(0.3)
        ctx.abort_event.set()

    t0 = time.monotonic()
    abort_task = asyncio.ensure_future(_abort_soon())
    try:
        res = await BashTool()._call_sandboxed(
            "sleep 600", ".", 600, _LOW_RISK, ctx
        )
    finally:
        abort_task.cancel()
    elapsed = time.monotonic() - t0

    assert elapsed < 5, f"中断没生效，白等了 {elapsed:.1f} 秒"
    assert "已取消" in str(res.data)
    assert res.retryable is False, "被用户打断的命令不该自动重跑"
    assert executor.killed is True, "取消后没有回收执行任务（会留下孤儿进程）"
