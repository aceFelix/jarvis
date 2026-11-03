"""RichCLI 思考面板收尾（agent.ui.cli）的单元测试。

盯的是 2026-11-03 实机缺陷：失败轮（正文一个字都没到）时面板停在首帧，用户只看到
`│ 用户 │` 两个字——因为收尾以前只隐含在「第一条正文增量到达」那条路径里。

全部用例在非交互终端（静态模式）下跑：Rich Live 的原地擦除只在真实终端可用，
静态模式恰好在收尾时一次性打印面板，因此可直接断言输出。

@author aceFelix
"""

from __future__ import annotations

from agent.ui.cli import RichCLI


def _cli() -> RichCLI:
    """不播启动动画的 RichCLI（避免测试里做终端动画）。"""
    return RichCLI(boot_animation=False)


def test_end_thinking_prints_full_buffer_when_no_text_arrives(capsys) -> None:
    """只有思考增量、正文一字没来：收尾仍要输出完整思考内容。

    这是实机那一轮（思考 69 字 / 正文 0 字）的最小还原。
    """
    ui = _cli()
    ui.assistant_thinking("\n用户")  # 前导换行是思考块的常态开头
    ui.assistant_thinking("问现在几点了。")

    ui.end_thinking()

    out = capsys.readouterr().out
    assert "问现在几点了" in out  # 不能被截在首帧「用户」
    assert "思考过程" in out


def test_end_thinking_is_idempotent(capsys) -> None:
    """收尾可重复调用：finally 与 assistant_text 两处都会调，不得打印两遍或报错。"""
    ui = _cli()
    ui.assistant_thinking("问现在几点了。")
    ui.end_thinking()
    first = capsys.readouterr().out
    assert "问现在几点了" in first

    ui.end_thinking()
    ui.end_thinking()

    assert capsys.readouterr().out == ""


def test_thinking_ended_by_first_text_delta_still_prints_once(capsys) -> None:
    """正常路径不变：第一条正文增量到达即收尾，面板只出现一次。"""
    ui = _cli()
    ui.assistant_thinking("用户问现在几点了。")

    ui.assistant_text("现在是 17:50。")
    after_text = capsys.readouterr().out

    assert "用户问现在几点了" in after_text
    assert "现在是 17:50" in after_text
    assert after_text.count("思考过程") == 1

    # finally 里再收尾一次（QueryLoop 的真实调用顺序）：不得重复打印
    ui.end_thinking()
    assert "思考过程" not in capsys.readouterr().out


def test_blank_only_thinking_prints_nothing(capsys) -> None:
    """整段都是空白增量时不输出空面板（既有行为，收尾之后仍应如此）。"""
    ui = _cli()
    ui.assistant_thinking("\n\n   ")

    ui.end_thinking()

    assert capsys.readouterr().out == ""
