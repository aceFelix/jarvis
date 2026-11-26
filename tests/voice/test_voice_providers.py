"""语音厂商注册表（voice_providers）的契约测试。

覆盖四件事：

1. **搬运等价**：注册表里的 dashscope 条目必须与改造前的模块级常量逐值一致
   （`DEFAULT_WS_URL` / `DEFAULT_VOICE` / 模型名），证明「只搬家、不改行为」；
2. **键的粒度**：realtime 与 STT 取到不同系列，TTS 本阶段没有条目；
3. **fail-closed**：未知厂商、无此能力、`verified=False` 一律抛
   `VoiceProviderError`，绝不静默回落到 DashScope；
4. **配置语义**：`voice_vendor` 默认 dashscope，细粒度覆盖优先。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.audio import providers as vp


class TestRegistryInvariants:
    """注册表条目必须自洽（新增厂商只加一行，但不能加出半条）。"""

    def test_every_entry_is_complete(self):
        assert vp.VOICE_REGISTRY, "注册表不能为空"
        for (vendor, series), meta in vp.VOICE_REGISTRY.items():
            assert meta.key == (vendor, series)
            assert meta.label and meta.vendor and meta.series
            assert meta.ws_url.startswith("wss://")
            assert meta.realtime_models or meta.stt_models
            assert meta.turn_detection_supported
            low, high = meta.vad_threshold_range
            assert low < high
            if meta.verified:
                assert meta.key_env, f"{meta.key} 已核实却没有 key_env"

    def test_dashscope_has_realtime_and_stt_series(self):
        realtime = vp.provider_for("dashscope", kind="realtime")
        stt = vp.provider_for("dashscope", kind="stt")
        assert realtime.series == "qwen-audio"
        assert stt.series == "qwen3-asr"
        assert realtime.realtime_models and not realtime.stt_models
        assert stt.stt_models and not stt.realtime_models

    def test_tts_is_out_of_scope(self):
        """TTS 走 Inference 协议，本阶段不建条目——不能被误当成 realtime。"""
        with pytest.raises(vp.VoiceProviderError):
            vp.provider_for("dashscope", kind="tts")


class TestCarriedOverConstants:
    """搬运等价：旧常量与注册表逐值一致（行为零变化）。"""

    def test_module_constants_come_from_registry(self):
        from agent.voice import realtime_talk as rt

        meta = vp.provider_for("dashscope", kind="realtime")
        assert rt.DEFAULT_WS_URL == meta.ws_url
        assert rt.DEFAULT_VOICE == meta.default_voice
        assert rt.DEFAULT_MODEL == meta.realtime_models[0]

    def test_stt_default_model(self):
        meta = vp.provider_for("dashscope", kind="stt")
        assert vp.default_stt_model() == meta.stt_models[0]


class TestResolveVendor:
    """配置解析顺序：细粒度覆盖 > voice_vendor > dashscope。"""

    def test_default_is_dashscope(self):
        assert vp.resolve_vendor(None) == "dashscope"
        assert vp.resolve_vendor(SimpleNamespace()) == "dashscope"

    def test_voice_vendor_is_normalized(self):
        s = SimpleNamespace(voice_vendor="  DashScope ")
        assert vp.resolve_vendor(s) == "dashscope"

    def test_kind_specific_override_wins(self):
        s = SimpleNamespace(voice_vendor="dashscope", voice_stt_vendor="other")
        assert vp.resolve_vendor(s) == "dashscope"
        assert vp.resolve_vendor(s, kind="stt") == "other"


class TestFailClosed:
    """未知/未核实：抛错而不是回落到 DashScope。"""

    def test_unknown_vendor_raises(self):
        with pytest.raises(vp.VoiceProviderError) as exc:
            vp.provider_for("nobody", kind="realtime")
        assert "nobody" in str(exc.value)

    def test_unverified_entry_is_refused(self, monkeypatch):
        meta = replace(
            vp.DASHSCOPE_QWEN_AUDIO,
            vendor="future",
            series="future-series",
            verified=False,
        )
        monkeypatch.setitem(vp.VOICE_REGISTRY, meta.key, meta)
        with pytest.raises(vp.VoiceProviderError) as exc:
            vp.provider_for("future", kind="realtime")
        assert "verified" in str(exc.value) or "核实" in str(exc.value)


class TestTurnDetectionGate:
    """能力门：不支持就报错，绝不静默替换。"""

    def test_supported_value_passes_through(self):
        meta = vp.provider_for("dashscope", kind="realtime")
        assert vp.validate_turn_detection(meta, "smart_turn") == "smart_turn"

    def test_empty_uses_first_supported(self):
        meta = vp.provider_for("dashscope", kind="realtime")
        assert vp.validate_turn_detection(meta, "") == "server_vad"

    def test_unsupported_value_raises(self):
        stt = vp.provider_for("dashscope", kind="stt")
        with pytest.raises(vp.VoiceProviderError):
            vp.validate_turn_detection(stt, "smart_turn")


class TestCreateSttDispatch:
    """create_stt 走注册表：默认仍是 QwenASR，未知厂商 fail-closed。"""

    def test_default_returns_qwen(self):
        from agent.voice import create_stt

        stt = create_stt(api_key="sk-test")
        assert type(stt).__name__ == "QwenASR"

    def test_unknown_vendor_raises(self):
        from agent.voice import create_stt

        with pytest.raises(vp.VoiceProviderError):
            create_stt(api_key="sk-test", vendor="nobody")
