"""Bash 工具的后台命令脱离与输出收集回归测试。

背景（见 docs/fixlogs/bash-background-command-wait-fix.md）：
- MSYS2/Git Bash 的 `bash -c 'cmd &'` **不会**立刻返回，它会等这个后台 job
  （实测 `sleep 3 &` = 3.18s，`sleep 3 >/dev/null &` = 0.21s）。
  于是模型写 `firefox.exe URL &` 时，「打开一个网页」照样卡满 timeout。
- 输出收集不能只等 `proc.wait()`：管道缓冲只有 64KB，大输出会让 shell 卡在
  write 上永不退出；也不能用 `communicate()`，它等的是 EOF，而后台 GUI 进程
  继承写端会让 EOF 永不到来。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import platform
import time
from types import SimpleNamespace

import pytest

from agent.core.context import ToolContext
from agent.core.result import ToolResult
from agent.tools.bash import BashTool, _detach_background


# ---- _detach_background：纯函数改写规则 ----


class TestDetachBackground:
    def test_appends_redirect_to_trailing_ampersand(self) -> None:
        """以 & 结尾的后台命令补上 > /dev/null 2>&1。"""
        out, is_background = _detach_background("firefox.exe https://x.com &")
        assert is_background is True
        assert out == "firefox.exe https://x.com > /dev/null 2>&1 &"

    def test_keeps_existing_full_redirect(self) -> None:
        """模型自己写好完整重定向的：不改写，但仍然是后台命令。

        第二项语义是「是否后台命令」而不是「是否改写过」——沙箱路由要用它，
        否则模型自己写全时会被当成前景命令而进 Job Object，GUI 又被回收。
        """
        cmd = "firefox.exe https://x.com > /dev/null 2>&1 &"
        out, is_background = _detach_background(cmd)
        assert is_background is True
        assert out == cmd

    def test_leaves_foreground_command_alone(self) -> None:
        """前台命令一律不改写，输出照常捕获。"""
        for cmd in ("echo hi", "ls -la", "git status"):
            out, is_background = _detach_background(cmd)
            assert is_background is False
            assert out == cmd

    def test_does_not_treat_and_and_as_background(self) -> None:
        """`a && b` 是连接符，不是后台化。"""
        cmd = "cd /tmp && ls"
        out, is_background = _detach_background(cmd)
        assert is_background is False
        assert out == cmd

    def test_pipeline_background_redirect_lands_on_last_stage(self) -> None:
        out, is_background = _detach_background("make build | tee log.txt &")
        assert is_background is True
        assert out == "make build | tee log.txt > /dev/null 2>&1 &"


# ---- 真实子进程行为 ----


def _has_shell() -> bool:
    """本机是否有可用的 shell（Windows 需 Git Bash）。"""
    if platform.system() != "Windows":
        return True
    return BashTool._find_bash() is not None


requires_shell = pytest.mark.skipif(not _has_shell(), reason="本机没有可用的 shell")


@pytest.mark.asyncio
@requires_shell
async def test_background_command_returns_promptly() -> None:
    """`sleep N &` 必须立刻返回，而不是等 N 秒。

    旧行为：git bash 等后台 job → 等满 timeout（实机 120 秒）。
    新行为：自动补重定向让它真正脱离，几十毫秒返回。
    """
    ctx = ToolContext(workdir=".", messages=[])
    t0 = time.monotonic()
    result = await BashTool()._call_normal("sleep 3 &", ".", 30, ctx)
    elapsed = time.monotonic() - t0

    assert not result.is_error, result.data
    assert elapsed < 3.0, f"后台命令等了 {elapsed:.2f}s，应当立刻返回"
    assert "已在后台启动" in str(result.data)


@pytest.mark.asyncio
@requires_shell
async def test_large_output_does_not_deadlock() -> None:
    """输出超过 64KB 管道缓冲时必须完整返回、不死锁。

    这条守的是「边等边读」：若只等 proc.wait() 而不吸管道，
    shell 会阻塞在 write 上永远不退出，直到 timeout。
    """
    ctx = ToolContext(workdir=".", messages=[])
    result = await BashTool()._call_normal(
        "seq 1 20000", ".", 30, ctx
    )

    assert not result.is_error, result.data
    body = str(result.data)
    assert "1" in body and "20000" in body


@pytest.mark.asyncio
@requires_shell
async def test_abort_works_even_while_command_runs() -> None:
    """命令执行中置位 abort_event，必须很快返回「已取消」。

    用 ``exec sleep`` 让 bash 自己被 sleep 替换：否则 kill 只杀掉 bash，
    孙进程 sleep 会继续活着影响后续用例。
    """
    ctx = ToolContext(workdir=".", messages=[])
    task = asyncio.ensure_future(
        BashTool()._call_normal("exec sleep 30", ".", 60, ctx)
    )
    await asyncio.sleep(0.3)
    ctx.abort_event.set()
    result = await asyncio.wait_for(task, timeout=5.0)

    assert result.is_error
    assert "已取消" in str(result.data)
    assert result.retryable is False


# ---- 沙箱路由：后台命令不进 Job Object ----


@pytest.mark.asyncio
async def test_background_command_skips_sandbox(monkeypatch) -> None:
    """以 & 结尾的后台命令不进沙箱，其余命令照旧。

    沙箱在 Windows 上就是 Job Object，且带 KILL_ON_JOB_CLOSE：命令一结束
    就把组内所有进程杀掉，后台启动的 GUI 于是「开了又消失」。
    实机里模型只能改走 schtasks 委托系统服务才绕出去——那是沙箱逃逸，
    不该成为产品的解法。
    """
    calls: list[str] = []

    async def fake_sandboxed(self, command, work_dir, timeout, risk, ctx):
        calls.append("sandboxed")
        return ToolResult.ok("sandboxed")

    async def fake_normal(self, command, work_dir, timeout, ctx):
        calls.append("normal")
        return ToolResult.ok("normal")

    monkeypatch.setattr(BashTool, "_call_sandboxed", fake_sandboxed)
    monkeypatch.setattr(BashTool, "_call_normal", fake_normal)

    ctx = ToolContext(workdir=".", messages=[])
    ctx.settings = SimpleNamespace(sandbox_enabled=True, sandbox_auto_allow_medium=True)
    tool = BashTool()

    # 后台命令：不进沙箱
    await tool.call(
        {"command": "firefox.exe https://x > /dev/null 2>&1 &"}, ctx
    )
    assert calls == ["normal"]

    # 非后台的高风险命令：照旧进沙箱
    calls.clear()
    await tool.call({"command": "rm -rf build"}, ctx)
    assert calls == ["sandboxed"]

    # 只读命令：两者都不进沙箱（走普通路径），这是既有行为
    calls.clear()
    await tool.call({"command": "ls -la"}, ctx)
    assert calls == ["normal"]
