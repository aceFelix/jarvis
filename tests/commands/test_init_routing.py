"""斜杠命令路由：`/init` 的子流程必须真的能送到 handler（aceFelix）。

锁定的用户现象（2026-10 实测）：
    敲 `/init voice` —— 什么都没发生，直接回到提示符。

根因不在 `handle_init`（它早就写了 `full` 分流），而在**路由表**：
`dispatch_command` 先拿**整串**去 `COMMAND_HANDLERS` 精确查表，`"/init voice"`
查不到；`PREFIX_HANDLERS` 里又只注册了 `/init` 没有 `"/init "`，于是既不精确、
也不前缀，最后落到 skill 匹配 → 返回 False → **被当成普通对话发给模型**。

同一个缺口让 `/init full` 也一直是坏的（CLI 的 `jarvis --init full` 走的另一条路，
所以没人发现）。

本文件只用真实路由表 + 真实 `handle_init` 做端到端分发验证，不重复
`tests/config/test_voice_key.py::TestInitVoiceDispatch`（那里测的是 handle_init
内部的三路分流，前提是命令已经送到了它手上）。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent.commands import router
from agent.commands.handlers import init_command
from agent.config.settings import Settings


@pytest.fixture
def _spy(monkeypatch):
    """把 handle_init 的三个分支换成记录器，只关心「送到了哪一路」。"""
    hit: list[str] = []

    async def _voice(_ui, _settings=None):
        hit.append("voice")
        return True

    async def _full(_ctx):
        hit.append("full")
        return True

    async def _quick(_ui, _settings=None, **_kwargs):
        hit.append("quick")
        return True

    monkeypatch.setattr(init_command, "_handle_voice_init", _voice)
    monkeypatch.setattr(init_command, "_handle_full_init", _full)
    monkeypatch.setattr("agent.config.onboarding.run_quick_setup", _quick)
    return hit


def _dispatch(cmd: str) -> bool:
    """走真实 dispatch_command，只给 handler 真正会用到的字段。"""
    ctx = SimpleNamespace(ui=object(), settings=Settings(), should_exit=False)
    return asyncio.run(router.dispatch_command(ctx, cmd))  # type: ignore[arg-type]


class TestInitSubcommandsAreRouted:
    """带参数的 `/init ...` 必须被当成命令，而不是聊天内容。"""

    def test_bare_init_hits_exact_table(self):
        assert router.COMMAND_HANDLERS.get("/init") is init_command.handle_init

    @pytest.mark.parametrize("cmd", [
        "/init voice", "/init VOICE", "/init -v",
        "/init full", "/init FULL", "/init -f",
    ])
    def test_subcommand_is_dispatched(self, cmd, _spy):
        """**核心回归**：这几种写法以前全部落到「当普通对话」。"""
        assert _dispatch(cmd) is True, f"{cmd} 没有被当成命令处理"
        assert _spy, f"{cmd} 根本没进 handle_init"

    def test_voice_and_full_go_to_different_branches(self, _spy):
        assert _dispatch("/init voice") is True
        assert _dispatch("/init full") is True
        assert _spy == ["voice", "full"]

    def test_extra_spaces_still_work(self, _spy):
        """/init  + 多个空格 不该因为前缀带一个空格就漏掉。"""
        assert _dispatch("/init   voice") is True
        assert _spy == ["voice"]

    def test_prefix_does_not_swallow_unrelated_words(self):
        """前缀写 `"/init "`（带空格）才不会把 `/initxyz` 吃掉——那是别的命令/聊天。"""
        assert router.dispatch_command is not None
        low = "/initxyz"
        assert router.COMMAND_HANDLERS.get(low) is None
        prefixes = [p for p, _h in router.PREFIX_HANDLERS if low.startswith(p)]
        assert "/init " not in prefixes
