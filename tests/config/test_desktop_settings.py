"""桌面设置白名单模块（agent.config.desktop_settings）单元测试。

覆盖：
- validate_setting：bool/int/float/time 四类的类型、范围、HH:MM 格式校验
- read_setting：运行时取值与类型规范化、属性缺失返回 None
- _toml_literal：TOML 字面量渲染（float 保留小数点）
- save_setting：外科式落盘——替换节内字段（保留注释与其他字段）、
  节内插新字段、缺节追加、缺文件创建最小文件

落盘测试用 patch Path.home 重定向到临时目录，不触碰真实 ~/.jarvis。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent.config.desktop_settings import (
    DESKTOP_SETTING_SPECS,
    SCHEDULE_KEYS,
    SPEC_BY_KEY,
    _toml_literal,
    read_setting,
    save_setting,
    validate_setting,
)


# ---------------------------------------------------------------------------
# schema 自身
# ---------------------------------------------------------------------------


class TestSchema:
    """白名单 schema 的完整性约束。"""

    def test_keys_unique(self):
        keys = [s.key for s in DESKTOP_SETTING_SPECS]
        assert len(keys) == len(set(keys))

    def test_schedule_keys_subset_of_whitelist(self):
        """SCHEDULE_KEYS 必须都在白名单内（防拼写漂移）。"""
        assert SCHEDULE_KEYS <= set(SPEC_BY_KEY)

    def test_no_secret_keys(self):
        """密钥/路径类字段永不入白名单（安全边界）。"""
        for spec in DESKTOP_SETTING_SPECS:
            assert "key" not in spec.field.lower() or spec.field == "hotkey"
            assert "api" not in spec.field.lower()
            assert "password" not in spec.field.lower()
            assert "token" not in spec.field.lower()


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


class TestValidate:
    """validate_setting 类型 + 范围 + 时间格式校验。"""

    def test_bool_ok_and_bad(self):
        assert validate_setting("briefing_enabled", True) is True
        assert validate_setting("briefing_enabled", False) is False
        with pytest.raises(ValueError):
            validate_setting("briefing_enabled", "yes")
        with pytest.raises(ValueError):
            validate_setting("briefing_enabled", 1)  # int 不冒充 bool

    def test_int_range(self):
        assert validate_setting("tts_volume", 0) == 0
        assert validate_setting("tts_volume", 100) == 100
        with pytest.raises(ValueError):
            validate_setting("tts_volume", -1)
        with pytest.raises(ValueError):
            validate_setting("tts_volume", 101)
        with pytest.raises(ValueError):
            validate_setting("tts_volume", 50.5)
        with pytest.raises(ValueError):
            validate_setting("tts_volume", True)  # bool 不冒充 int

    def test_float_range_and_coercion(self):
        assert validate_setting("tts_speech_rate", 1.25) == 1.25
        assert validate_setting("tts_speech_rate", 1) == 1.0  # int 收窄为 float
        with pytest.raises(ValueError):
            validate_setting("tts_speech_rate", 0.4)
        with pytest.raises(ValueError):
            validate_setting("tts_speech_rate", 2.1)
        with pytest.raises(ValueError):
            validate_setting("tts_speech_rate", "fast")

    def test_time_format(self):
        assert validate_setting("briefing_time", "07:15") == "07:15"
        assert validate_setting("deadline_check_time", "23:59") == "23:59"
        for bad in ("24:00", "7:15", "07:60", "0715", "", None, 715):
            with pytest.raises(ValueError):
                validate_setting("briefing_time", bad)

    def test_unknown_key(self):
        with pytest.raises(ValueError):
            validate_setting("api_key", "sk-xxx")


# ---------------------------------------------------------------------------
# 运行时读取
# ---------------------------------------------------------------------------


class TestReadSetting:
    """read_setting：Settings 实例取值与规范化。"""

    class _FakeSettings:
        briefing_enabled = True
        tts_volume = 50
        tts_speech_rate = 1.0
        briefing_time = "08:30"

    def test_reads_and_normalizes(self):
        fake = self._FakeSettings()
        assert read_setting(fake, SPEC_BY_KEY["briefing_enabled"]) is True
        assert read_setting(fake, SPEC_BY_KEY["tts_volume"]) == 50
        assert read_setting(fake, SPEC_BY_KEY["tts_speech_rate"]) == 1.0
        assert read_setting(fake, SPEC_BY_KEY["briefing_time"]) == "08:30"

    def test_missing_attr_returns_none(self):
        """属性缺失 → None（桌面壳据此显离线态，不抛异常）。"""
        assert read_setting(object(), SPEC_BY_KEY["tts_volume"]) is None


# ---------------------------------------------------------------------------
# TOML 字面量
# ---------------------------------------------------------------------------


class TestTomlLiteral:
    def test_bool(self):
        assert _toml_literal(SPEC_BY_KEY["briefing_enabled"], True) == "true"
        assert _toml_literal(SPEC_BY_KEY["briefing_enabled"], False) == "false"

    def test_int(self):
        assert _toml_literal(SPEC_BY_KEY["tts_volume"], 80) == "80"

    def test_float_keeps_decimal_point(self):
        """整值 float 也必须带 .0，否则 TOML 解析成 int 破坏类型口径。"""
        assert _toml_literal(SPEC_BY_KEY["tts_speech_rate"], 1.0) == "1.0"
        assert _toml_literal(SPEC_BY_KEY["tts_speech_rate"], 1.25) == "1.25"

    def test_time_quoted(self):
        assert _toml_literal(SPEC_BY_KEY["briefing_time"], "07:15") == '"07:15"'


# ---------------------------------------------------------------------------
# 外科式落盘
# ---------------------------------------------------------------------------


def _write_toml(home: Path, content: str) -> Path:
    toml_path = home / ".jarvis" / "settings.toml"
    toml_path.parent.mkdir(parents=True, exist_ok=True)
    toml_path.write_text(content, encoding="utf-8")
    return toml_path


class TestSaveSetting:
    """save_setting：只动目标字段行，注释/其他字段/其他节原样保留。"""

    def test_replace_existing_field_preserves_comments(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            toml_path = _write_toml(
                tmp_path,
                "# 用户注释\n[daemon]\nbriefing_time = \"08:30\"  # 行内注释前的值\n"
                "briefing_enabled = true\n\n[tts]\nvolume = 50\n",
            )
            assert save_setting(SPEC_BY_KEY["briefing_time"], "07:15") is True
            content = toml_path.read_text(encoding="utf-8")
            assert '# 用户注释' in content
            assert 'briefing_time = "07:15"' in content
            assert "08:30" not in content
            assert "briefing_enabled = true" in content  # 同节其他字段不动
            assert "volume = 50" in content  # 其他节不动

    def test_insert_into_section_without_field(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            toml_path = _write_toml(tmp_path, '[deadline]\nenabled = true\n')
            assert save_setting(SPEC_BY_KEY["deadline_check_time"], "21:00") is True
            content = toml_path.read_text(encoding="utf-8")
            # 新字段落在 [deadline] 节内
            assert content.index('check_time = "21:00"') > content.index("[deadline]")
            assert "enabled = true" in content

    def test_append_missing_section(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            toml_path = _write_toml(tmp_path, 'model = "qwen-plus"\n')
            assert save_setting(SPEC_BY_KEY["tts_volume"], 80) is True
            content = toml_path.read_text(encoding="utf-8")
            assert "[tts]\nvolume = 80" in content
            assert 'model = "qwen-plus"' in content

    def test_create_minimal_file(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            assert save_setting(SPEC_BY_KEY["tts_speech_rate"], 1.25) is True
            content = (tmp_path / ".jarvis" / "settings.toml").read_text(encoding="utf-8")
            assert content == "[tts]\nspeech_rate = 1.25\n"

    def test_similar_field_name_not_clobbered(self, tmp_path):
        """同前缀字段（volume_floor）不被 volume 的替换误伤。"""
        with patch.object(Path, "home", return_value=tmp_path):
            toml_path = _write_toml(tmp_path, "[tts]\nvolume = 50\nvolume_floor = 10\n")
            assert save_setting(SPEC_BY_KEY["tts_volume"], 70) is True
            content = toml_path.read_text(encoding="utf-8")
            assert "volume = 70" in content
            assert "volume_floor = 10" in content
