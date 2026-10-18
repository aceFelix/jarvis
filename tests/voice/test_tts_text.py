"""clean_for_tts 整段 TTS 清洗单元测试（主动播报朗读等非流式场景）。

覆盖：
- markdown 符号剥离（标题/列表/引用/加粗/链接/裸 URL）
- <think> 闭合与未闭合标签整段丢弃
- 工具调用占位符标签（<bash> 等）整段丢弃
- 代码围栏内容丢弃、围栏外文本保留
- 空文本安全返回
- 口播归一化（Windows 路径取末段、`.exe` 与文件后缀、`17:20` 改读法、
  末尾补句号让语调落下）

@author aceFelix
"""

from __future__ import annotations

from agent.voice.tts_text import clean_for_tts, normalize_for_speech


def test_clean_markdown_symbols():
    """标题/列表/引用/加粗/链接/裸 URL 均剥离，正文保留。"""
    out = clean_for_tts(
        "# 标题\n- **要点** 一\n> 引用\n[链接](http://a.b) 裸 https://x.y 完"
    )
    assert "**" not in out and "#" not in out and ">" not in out
    assert "http" not in out
    assert "标题" in out and "要点 一" in out and "引用" in out and "链接" in out
    # 末尾补了句号：朗读时语调要落下，不能像被截断一样悬着
    assert out.endswith("完。")


def test_clean_think_tags():
    """闭合 <think> 与未闭合兜底均整段丢弃。"""
    assert "秘密" not in clean_for_tts("前<think>秘密\n</think>\n后")
    assert clean_for_tts("abc<think>xyz") == "abc。"   # 末尾补句号（语调落下）


def test_clean_tool_tags():
    """工具调用占位符标签块整段丢弃，自闭合标签也剥离。"""
    out = clean_for_tts("ok<bash>rm -rf /</bash>end<mcp__x/>尾")
    assert "rm -rf" not in out
    assert "mcp__x" not in out
    assert out == "okend尾。"


def test_clean_code_fence():
    """代码围栏内内容不朗读，围栏外文本保留。"""
    out = clean_for_tts("说明\n```python\nprint(1)\n```\n结尾")
    assert "print" not in out
    assert "说明" in out and "结尾" in out


def test_clean_empty():
    """空串安全返回空串。"""
    assert clean_for_tts("") == ""


def test_clean_plain_text_untouched():
    """纯文本（无标记、已有句末标点）原样保留；冒号不重写。"""
    text = "先生，提醒您：十分钟后开会。"
    assert clean_for_tts(text) == text


def test_normalize_windows_path_to_tail():
    """Windows 路径只念末段：目录补“文件夹”，文件去扩展名。"""
    assert normalize_for_speech("切到 explorer：C:\\Users\\许发明\\.jarvis") \
        == "切到 explorer：jarvis 文件夹。"
    assert normalize_for_speech("C:\\Users\\me\\settings.toml 已更新") \
        == "settings 已更新。"


def test_normalize_exe_and_suffix_dropped():
    """`.exe` 与常见文件后缀不朗读了（否则念成“点 e x e”）。"""
    assert normalize_for_speech("Qoder CN IDE.exe 在前台") == "Qoder CN IDE 在前台。"
    assert ".exe" not in normalize_for_speech("explorer.exe")
    assert ".html" not in normalize_for_speech("snake - 副本 (2).html")


def test_normalize_time_reading():
    """`HH:MM` 改读法，不让合成器把冒号念成比号或停顿不明。"""
    assert normalize_for_speech("现在是 17:20，该休息了") == "现在是 17点20分，该休息了。"


def test_normalize_keeps_prose_punctuation():
    """散文里的冒号与顿号是停顿，不得全局改写（旧版一度把它们全换成逗号）。"""
    text = "新增文件：a、b、c"
    assert normalize_for_speech(text) == text + "。"


def test_normalize_empty():
    """空串不报错也不补句号。"""
    assert normalize_for_speech("") == ""
    assert normalize_for_speech("   ") == ""
