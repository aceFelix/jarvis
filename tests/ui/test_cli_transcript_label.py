"""终端 /talk 转写标签必须与星期五人设一致（2026-11-26）。

实机：人设已改星期五，但终端仍打「🤖 贾维斯:」，用户怀疑身份错乱。
本用例把标签钉死，避免再次漂移。

@author aceFelix
@since 2026-11-26
"""

from __future__ import annotations

import io

import pytest


def test_ai_transcript_label_is_friday() -> None:
    """on_ai_transcript 打印「🤖 星期五:」，不得出现「贾维斯」。"""
    pytest.importorskip("rich")
    from rich.console import Console

    from agent.ui.cli import RichCLI

    buf = io.StringIO()
    ui = RichCLI(boot_animation=False)
    ui._console = Console(
        file=buf, force_terminal=False, width=100, color_system=None
    )

    ui.on_ai_transcript("在的，先生。")

    out = buf.getvalue()
    assert "星期五" in out
    assert "贾维斯" not in out
