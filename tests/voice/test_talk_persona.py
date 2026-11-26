"""/talk 人设指令回归测试（星期五 Friday）。

2026-11-26 起 /talk 的默认人设由「贾维斯」改为「星期五（Friday）」——贾维斯之外的
第二位智能管家。人设硬编码在 realtime_talk 的两份指令常量里，终端路径与桌面全双工
路径共用 default_instructions() 回退，因此这里同时锁住：

1. 两种 tools_mode 的身份段都是星期五，且**完全一致**（切工具模式不该换人格）；
2. RealtimeTalk 未显式传 instructions 时确实回退到该人设（不是空串 → 服务端"小云"）；
3. 显式传入的 instructions 不被覆盖；
4. 改人设时不能顺手丢掉退出意图门与「先告别再退出」的话术约束。

@author aceFelix
@since 2026-11-26
"""

from __future__ import annotations

import pytest

from agent.voice import realtime_talk as rt_mod

#: 身份锚点：只钉「自称星期五 + 英文名 Friday」，不钉具体措辞（全称/缩写可调整）
_NAME = "你是星期五"
_EN = "Friday"


@pytest.mark.parametrize("mode", ["builtin", "all"])
def test_default_persona_is_friday(mode: str) -> None:
    """两种工具模式的默认人设都是星期五，且不再自称贾维斯。"""
    text = rt_mod.default_instructions(mode)
    assert text.startswith(_NAME)
    assert _EN in text
    assert "第二位智能管家" in text
    assert "你是贾维斯" not in text


def test_both_modes_share_same_identity() -> None:
    """身份段必须逐字一致：切 tools_mode 只该换工具面，不该换人格。"""
    builtin = rt_mod.default_instructions("builtin").split("\n")[0]
    all_mode = rt_mod.default_instructions("all").split("\n")[0]
    assert builtin == all_mode


def test_unknown_mode_falls_back_to_builtin() -> None:
    """未知 tools_mode 走 builtin 分支（与实现同口径），人设仍是星期五。"""
    assert rt_mod.default_instructions("nonsense") == rt_mod.default_instructions("builtin")


@pytest.mark.parametrize("mode", ["builtin", "all"])
def test_talk_fallback_uses_friday_persona(mode: str) -> None:
    """终端适配器不传 instructions 时回退到星期五人设，而不是留空给服务端默认。"""
    rt = rt_mod.RealtimeTalk("sk-test", tools_mode=mode)
    assert rt._instructions.startswith(_NAME)
    assert _EN in rt._instructions


def test_explicit_instructions_win() -> None:
    """显式传入的 instructions 优先，不被默认人设覆盖（自定义人设入口）。"""
    rt = rt_mod.RealtimeTalk("sk-test", instructions="你是一位旅行顾问")
    assert rt._instructions == "你是一位旅行顾问"


@pytest.mark.parametrize("mode", ["builtin", "all"])
def test_exit_gate_and_farewell_kept(mode: str) -> None:
    """改人设不能丢掉退出意图门与「先告别再退出」约束（2026-11-25 交付的行为）。"""
    text = rt_mod.default_instructions(mode)
    assert "绝不能调用 end_conversation" in text
    assert "告别" in text
