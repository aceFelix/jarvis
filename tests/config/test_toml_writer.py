"""settings.toml 通用外科式写入工具（agent/config/toml_writer.py）。

这是三个配置域（桌面设置面板 / 邮箱服务 / W2 感知面板）共用的唯一落盘实现，
所以它自己要有独立单测：域测试里那些"保住注释、只动目标行"的断言本质上都在
考这里。覆盖字面量生成（bool/int/str 转义/list 数组与空数组）、节的三种命中
路径（替换、补插、追加）、文件缺失创建、空补丁不碰磁盘、IO 失败返回 False、
以及 `settings_path()` 必须每次现取 home（测试靠 patch home 隔离）。

@author aceFelix
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

from agent.config.toml_writer import (
    bool_literal,
    list_literal,
    save_section,
    settings_path,
    str_literal,
    upsert_section,
)


# ---- 字面量 ----

def test_scalar_literals():
    """三类标量字面量的写法（float 不在这里，是 desktop_settings 的特例）。"""
    assert bool_literal(True) == "true" and bool_literal(False) == "false"
    assert str_literal('say "hi"') == '"say \\"hi\\""'
    assert str_literal("C:\\path") == '"C:\\\\path"'


def test_list_literal_quotes_every_item():
    """数组逐项转义，空数组也要是合法的 `[]`。"""
    assert list_literal(["a", 'b"c']) == '["a", "b\\"c"]'
    assert list_literal([]) == "[]"


# ---- 节内改写 ----

def test_upsert_replaces_existing_and_keeps_siblings():
    content = '[percept]\nenabled = false  # 注释\ndedup_window_s = 300\n'
    out = upsert_section(content, "percept", {"enabled": "true"})
    assert 'enabled = true\n' in out
    assert "dedup_window_s = 300" in out
    assert "# 注释" not in out          # 被改写那一行的行内注释随整行替换丢失


def test_upsert_inserts_missing_field_right_after_header():
    content = "[email]\nsmtp_host = \"smtp.163.com\"\n"
    out = upsert_section(content, "email", {"imap_port": "993"})
    lines = out.splitlines()
    assert lines[0] == "[email]" and lines[1] == "imap_port = 993"


def test_upsert_appends_section_and_parses_back():
    """没有该节 → 追加到文件末尾，且结果仍是可解析的 TOML。"""
    out = upsert_section('model = "qwen-plus"\n', "tts", {"volume": "80"})
    assert "[tts]\nvolume = 80" in out
    assert tomllib.loads(out) == {"model": "qwen-plus", "tts": {"volume": 80}}


# ---- 落盘 ----

def test_save_creates_missing_file(tmp_path):
    with patch.object(Path, "home", return_value=tmp_path):
        assert save_section("percept", {"enabled": "true"}) is True
        assert (tmp_path / ".jarvis" / "settings.toml").read_text(
            encoding="utf-8"
        ) == "[percept]\nenabled = true\n"


def test_save_empty_patch_touches_nothing(tmp_path):
    with patch.object(Path, "home", return_value=tmp_path):
        assert save_section("percept", {}) is True
        assert not (tmp_path / ".jarvis" / "settings.toml").exists()


def test_save_returns_false_on_io_error(tmp_path):
    """目标路径是个目录 → OSError 被吞成 False（调用方据此报错且不动运行时）。"""
    blocker = tmp_path / ".jarvis"
    blocker.mkdir(parents=True)
    with patch.object(Path, "home", return_value=tmp_path):
        (blocker / "settings.toml").mkdir()          # 用目录占住文件路径
        assert save_section("percept", {"enabled": "true"}) is False


def test_settings_path_resolved_at_call_time(tmp_path):
    """home 必须在调用时求值：import 时拼死路径会让所有隔离测试写进真实目录。"""
    with patch.object(Path, "home", return_value=tmp_path):
        assert settings_path() == tmp_path / ".jarvis" / "settings.toml"
