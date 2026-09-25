"""clean_for_tts 整段 TTS 清洗单元测试（主动播报朗读等非流式场景）。

覆盖：
- markdown 符号剥离（标题/列表/引用/加粗/链接/裸 URL）
- <think> 闭合与未闭合标签整段丢弃
- 工具调用占位符标签（<bash> 等）整段丢弃
- 代码围栏内容丢弃、围栏外文本保留
- 空文本安全返回

@author aceFelix
"""

from __future__ import annotations

from agent.voice.tts_text import clean_for_tts


def test_clean_markdown_symbols():
    """标题/列表/引用/加粗/链接/裸 URL 均剥离，正文保留。"""
    out = clean_for_tts(
        "# 标题\n- **要点** 一\n> 引用\n[链接](http://a.b) 裸 https://x.y 完"
    )
    assert "**" not in out and "#" not in out and ">" not in out
    assert "http" not in out
    assert "标题" in out and "要点 一" in out and "引用" in out and "链接" in out
    assert out.endswith("完")


def test_clean_think_tags():
    """闭合 <think> 与未闭合兜底均整段丢弃。"""
    assert "秘密" not in clean_for_tts("前<think>秘密\n</think>\n后")
    assert clean_for_tts("abc<think>xyz") == "abc"


def test_clean_tool_tags():
    """工具调用占位符标签块整段丢弃，自闭合标签也剥离。"""
    out = clean_for_tts("ok<bash>rm -rf /</bash>end<mcp__x/>尾")
    assert "rm -rf" not in out
    assert "mcp__x" not in out
    assert out == "okend尾"


def test_clean_code_fence():
    """代码围栏内内容不朗读，围栏外文本保留。"""
    out = clean_for_tts("说明\n```python\nprint(1)\n```\n结尾")
    assert "print" not in out
    assert "说明" in out and "结尾" in out


def test_clean_empty():
    """空串安全返回空串。"""
    assert clean_for_tts("") == ""


def test_clean_plain_text_untouched():
    """纯文本（无标记）原样保留。"""
    text = "先生，提醒您：十分钟后开会。"
    assert clean_for_tts(text) == text
