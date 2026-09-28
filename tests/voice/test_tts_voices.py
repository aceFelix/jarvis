"""/tts-voice 音色-模型适配测试。

覆盖：
- voice_model_matches / aligned_tts_model 匹配与联动算法（家族前缀、多值、边界）
- all_tts_voices 合并自定义音色的 model 字段
- find_voice / apply_voice_switch 三层共用公共层（终端/工作台/serve 同源）
- save_custom_voice / save_tts_model / remove_custom_voice 落盘（含 [tts] 节其他字段保留）
- _switch_tts_voice 切换音色时联动校正 tts_model

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


# ── 匹配算法 ──────────────────────────────────────────────

class TestVoiceModelMatches:
    """音色适配模型与当前 TTS 模型的兼容性判定。"""

    def test_empty_model_always_matches(self):
        """空 model = 不限，恒兼容。"""
        from agent.voice.tts_voices import voice_model_matches
        assert voice_model_matches("", "cosyvoice-v2") is True

    def test_exact_match(self):
        from agent.voice.tts_voices import voice_model_matches
        assert voice_model_matches("cosyvoice-v3-flash", "cosyvoice-v3-flash") is True

    def test_family_prefix_match(self):
        """家族前缀 cosyvoice-v3 匹配 v3-flash / v3.5-plus。"""
        from agent.voice.tts_voices import voice_model_matches
        assert voice_model_matches("cosyvoice-v3", "cosyvoice-v3-flash") is True
        assert voice_model_matches("cosyvoice-v3", "cosyvoice-v3.5-plus") is True

    def test_family_prefix_boundary(self):
        """前缀后必须紧跟 '-' 或 '.'，不误匹 'cosyvoice-v30' 这类异族名。"""
        from agent.voice.tts_voices import voice_model_matches
        assert voice_model_matches("cosyvoice-v3", "cosyvoice-v30") is False
        assert voice_model_matches("cosyvoice-v3", "cosyvoice-v2") is False

    def test_comma_multi_values(self):
        """逗号分隔多值任一命中即兼容。"""
        from agent.voice.tts_voices import voice_model_matches
        assert voice_model_matches(
            "cosyvoice-v3-plus,cosyvoice-v3.5-plus", "cosyvoice-v3-plus") is True
        assert voice_model_matches(
            "cosyvoice-v3-plus,cosyvoice-v3.5-plus", "cosyvoice-v3-flash") is False

    def test_concrete_no_cross_variant(self):
        """具体模型不跨变体：复刻绑定 target_model 时 plus 音色不能配 flash 模型。"""
        from agent.voice.tts_voices import voice_model_matches
        assert voice_model_matches("cosyvoice-v3-plus", "cosyvoice-v3-flash") is False


class TestAlignedTtsModel:
    """切换音色时计算的联动目标模型。"""

    def test_no_switch_when_compatible(self):
        from agent.voice.tts_voices import aligned_tts_model
        assert aligned_tts_model("cosyvoice-v3", "cosyvoice-v3-plus") is None
        assert aligned_tts_model("", "cosyvoice-v2") is None

    def test_family_maps_to_concrete_default(self):
        """家族前缀本身不是合法 API 模型名，落到家族默认具体模型。"""
        from agent.voice.tts_voices import aligned_tts_model
        assert aligned_tts_model("cosyvoice-v3", "cosyvoice-v2") == "cosyvoice-v3-flash"

    def test_concrete_target_used_directly(self):
        from agent.voice.tts_voices import aligned_tts_model
        assert aligned_tts_model("cosyvoice-v3-plus", "cosyvoice-v2") == "cosyvoice-v3-plus"

    def test_multi_values_uses_first_token(self):
        from agent.voice.tts_voices import aligned_tts_model
        assert aligned_tts_model(
            "cosyvoice-v3-plus,cosyvoice-v3.5-plus", "cosyvoice-v2") == "cosyvoice-v3-plus"


# ── 音色目录合并 ──────────────────────────────────────────────

class TestAllTtsVoices:
    """内置 + 自定义音色合并后的 model 字段。"""

    def test_builtin_voices_carry_model(self):
        from agent.voice.tts_voices import all_tts_voices
        settings = SimpleNamespace(custom_voices={})
        voices = all_tts_voices(settings)
        assert voices["longanlang_v3"]["model"] == "cosyvoice-v3"

    def test_custom_voice_merges_model(self):
        from agent.voice.tts_voices import all_tts_voices
        settings = SimpleNamespace(custom_voices={
            "我的声音": {
                "voice_id": "my-clone-001", "vendor": "dashscope",
                "model": "cosyvoice-v3-flash", "description": "复刻",
            },
            "旧音色": {"voice_id": "legacy", "description": "无 model 字段"},
        })
        voices = all_tts_voices(settings)
        assert voices["我的声音"]["model"] == "cosyvoice-v3-flash"
        # 历史自定义音色没有 model 字段 → 视为不限（空串）
        assert voices["旧音色"]["model"] == ""

    def test_builtin_copy_isolation(self):
        """all_tts_voices 返回深一层拷贝，改自定义合并不污染内置 VOICE_CATALOG。"""
        from agent.voice.tts_voices import VOICE_CATALOG, all_tts_voices
        settings = SimpleNamespace(custom_voices={"longanlang_v3": {"voice_id": "x"}})
        voices = all_tts_voices(settings)
        assert voices["longanlang_v3"]["voice_id"] == "x"
        assert VOICE_CATALOG["longanlang_v3"]["voice_id"] == "longanlang_v3"


# ── 持久化落盘 ──────────────────────────────────────────────

@pytest.fixture()
def jarvis_dir(tmp_path, monkeypatch):
    """把 model_registry 的 Path.home 指到 tmp，settings.toml 预置 [tts] 节。"""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".jarvis").mkdir()
    (home / ".jarvis" / "settings.toml").write_text(
        '[tts]\nmodel = "cosyvoice-v3-flash"\nvoice = "longanlang_v3"\nvolume = 50\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home / ".jarvis" / "settings.toml"


class TestVoicePersistence:
    """save_tts_model / save_custom_voice 的外科式写入。"""

    def test_save_tts_model_replaces_key_line_only(self, jarvis_dir):
        """只改 model 行，voice/volume 与注释保留（回归 _save_config 覆盖陷阱）。"""
        from agent.config.model_registry import save_tts_model
        assert save_tts_model("cosyvoice-v3-plus") is True
        text = jarvis_dir.read_text(encoding="utf-8")
        assert 'model = "cosyvoice-v3-plus"' in text
        assert 'voice = "longanlang_v3"' in text
        assert "volume = 50" in text

    def test_save_custom_voice_includes_model(self, jarvis_dir):
        from agent.config.model_registry import save_custom_voice
        ok = save_custom_voice("我的声音", {
            "voice_id": "my-clone", "vendor": "dashscope",
            "model": "cosyvoice-v3-flash", "description": "复刻",
        })
        assert ok is True
        text = jarvis_dir.read_text(encoding="utf-8")
        assert '[tts.custom_voices."我的声音"]' in text
        assert 'model = "cosyvoice-v3-flash"' in text
        # [tts] 主节未被子表写入破坏
        assert 'voice = "longanlang_v3"' in text

    def test_remove_custom_voice_deletes_section(self, jarvis_dir):
        """remove_custom_voice 外科式剪掉目标段，相邻段与 [tts] 主节保留。"""
        from agent.config.model_registry import remove_custom_voice, save_custom_voice
        save_custom_voice("音色A", {"voice_id": "a-id", "model": "cosyvoice-v3"})
        save_custom_voice("音色B", {"voice_id": "b-id", "model": "cosyvoice-v2"})
        assert remove_custom_voice("音色A") is True
        text = jarvis_dir.read_text(encoding="utf-8")
        assert "音色A" not in text and "a-id" not in text
        # 相邻段与主节字段不受影响
        assert '[tts.custom_voices."音色B"]' in text and "b-id" in text
        assert 'voice = "longanlang_v3"' in text

    def test_remove_custom_voice_missing_returns_false(self, jarvis_dir):
        """段不存在时返回 False（不抛异常，内存已清即可继续）。"""
        from agent.config.model_registry import remove_custom_voice
        assert remove_custom_voice("不存在的音色") is False


# ── 公共层：查找与切换 ────────────────────────────────────────

class TestApplyVoiceSwitch:
    """终端 / 工作台 / serve 三层共用的音色切换公共实现。"""

    def _settings(self):
        return SimpleNamespace(tts_voice="longanlang_v3", tts_model="cosyvoice-v2",
                               custom_voices={})

    def test_find_voice_by_name_and_id(self):
        from agent.voice.tts_voices import find_voice
        settings = self._settings()
        settings.custom_voices = {"我的声音": {"voice_id": "my-clone", "model": ""}}
        assert find_voice("我的声音", settings)[1]["voice_id"] == "my-clone"
        assert find_voice("my-clone", settings)[0] == "我的声音"  # 按 voice_id 反查
        assert find_voice("不存在", settings) is None
        assert find_voice("", settings) is None

    def test_switch_returns_linkage_result(self, jarvis_dir):
        """v2 模型切 v3 音色 → 返回联动信息并双字段落盘。"""
        from agent.voice.tts_voices import apply_voice_switch
        settings = self._settings()
        res = apply_voice_switch(settings, "longxiaochun_v3")
        assert res == {
            "name": "longxiaochun_v3", "voice_id": "longxiaochun_v3",
            "linked_model": "cosyvoice-v3-flash", "old_model": "cosyvoice-v2",
        }
        assert settings.tts_voice == "longxiaochun_v3"
        assert settings.tts_model == "cosyvoice-v3-flash"
        text = jarvis_dir.read_text(encoding="utf-8")
        assert 'voice = "longxiaochun_v3"' in text
        assert 'model = "cosyvoice-v3-flash"' in text

    def test_switch_unknown_voice_returns_none(self):
        """目录未命中 → None，调用方自行退回仅持久化分支。"""
        from agent.voice.tts_voices import apply_voice_switch
        assert apply_voice_switch(self._settings(), "不存在的音色") is None


# ── 切换联动 ──────────────────────────────────────────────

class TestSwitchLinkage:
    """/tts-voice 终端切换入口（现委托 apply_voice_switch，四参签名）。"""

    class _UI:
        def __init__(self):
            self.messages: list[str] = []

        def info(self, msg):
            self.messages.append(str(msg))

        def warn(self, msg):
            self.messages.append(str(msg))

    def _settings(self):
        return SimpleNamespace(tts_voice="longanlang_v3", tts_model="cosyvoice-v2",
                               custom_voices={})

    def test_switch_links_model_and_persists(self, jarvis_dir):
        """v2 模型 + v3 音色 → 联动切到 cosyvoice-v3-flash 并落盘。"""
        from agent.commands.handlers.voice_commands import _switch_tts_voice
        ui, settings = self._UI(), self._settings()
        assert _switch_tts_voice(ui, settings, "longxiaochun_v3", "龙小淳")
        assert settings.tts_voice == "longxiaochun_v3"
        assert settings.tts_model == "cosyvoice-v3-flash"
        assert any("联动" in m for m in ui.messages)
        assert 'model = "cosyvoice-v3-flash"' in jarvis_dir.read_text(encoding="utf-8")

    def test_switch_compatible_keeps_model(self, jarvis_dir):
        """模型已兼容 → 不动 tts_model、不发联动提示。"""
        from agent.commands.handlers.voice_commands import _switch_tts_voice
        ui, settings = self._UI(), self._settings()
        settings.tts_model = "cosyvoice-v3-plus"
        assert _switch_tts_voice(ui, settings, "longcheng_v3", "龙城")
        assert settings.tts_model == "cosyvoice-v3-plus"
        assert not any("联动" in m for m in ui.messages)

    def test_switch_unbounded_model_no_linkage(self, jarvis_dir):
        """自定义音色 model 为空（不限）→ 永不联动。"""
        from agent.commands.handlers.voice_commands import _switch_tts_voice
        ui, settings = self._UI(), self._settings()
        settings.custom_voices = {"复刻": {"voice_id": "any-clone", "model": ""}}
        assert _switch_tts_voice(ui, settings, "any-clone", "复刻")
        assert settings.tts_voice == "any-clone"
        assert settings.tts_model == "cosyvoice-v2"

    def test_switch_unknown_voice_fallback_persist(self, jarvis_dir):
        """目录未命中的历史直传 voice_id → 退回仅持久化，不阻断。"""
        from agent.commands.handlers.voice_commands import _switch_tts_voice
        ui, settings = self._UI(), self._settings()
        assert _switch_tts_voice(ui, settings, "legacy-id", "legacy-id")
        assert settings.tts_voice == "legacy-id"
        assert settings.tts_model == "cosyvoice-v2"
        assert 'voice = "legacy-id"' in jarvis_dir.read_text(encoding="utf-8")
