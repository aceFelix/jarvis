"""STT 包精简后的契约回归测试（aceFelix）。

背景：
1. 原 agent/voice/stt.py 单文件 1087 行、超出「单文件 800 行」上限，
   按识别后端拆成 agent/voice/stt/ 包（common / paraformer / qwen / funasr）。
2. 随后按「一个模型、一个后端、不留无用实现」的决策精简为单一后端
   QwenASR，下线内容包括（本文件负责防止它们回潮）：
   - ParaformerSTT（Recognition WS + 客户端 RMS 静音检测）
   - FunASRFlashSTT（HTTP POST 整段 WAV，不适用于 /voice 循环）
   - `fun-asr-realtime` 别名分派
   - `silence_threshold` 死参数链（配置字段 + listen 形参 + 调用传参）

本文件锁定五条契约：
1. 调用方**实际依赖**的符号在包内仍可达（如 `from agent.voice.stt import _rms`）
2. 停止标志唯一——ESC / Ctrl+C 打断依赖同一个 Event，标志一分裂就失灵
3. create_stt() 只做参数透传，恒返回 QwenASR（不再按 model 名分派）
4. QwenASR.listen() 关键字参数与调用方一致，且不再有 silence_threshold
5. 已下线的后端模块、符号与配置字段确实不存在

@author aceFelix
"""

from __future__ import annotations

import importlib.util
import inspect

import pytest

from agent.voice import stt as stt_pkg
from agent.voice.stt import common as common_mod
from agent.voice.stt import qwen as qwen_mod

# 精简后包对外必须可达的符号（调用方逐个取属性，缺一即运行时 AttributeError）
_EXPECTED_SYMBOLS = [
    # 类与工厂
    "QwenASR", "create_stt",
    # 回调与延迟导入
    "_QwenASRCallback", "_import_qwen_omni", "_import_pyaudio",
    # 工具函数（_rms 被 barge_in._BargeInWatcher 复用）
    "_rms",
    # 停止标志
    "_stop_flag", "_is_stopped", "_request_stop", "_reset_stop",
    # 音频与时长常量
    "_PCM_RATE", "_PCM_CHANNELS", "_PCM_WIDTH", "_FRAMES_PER_BUFFER",
    "_SILENCE_SECONDS", "_MAX_SECONDS",
    # Qwen 专用
    "_VAD_THRESHOLD", "_VAD_LEAD_IN_SECONDS", "_QWEN_REALTIME_URL",
]

# 已下线的符号：必须取不到（防止再次引入）
_REMOVED_SYMBOLS = [
    "ParaformerSTT", "FunASRFlashSTT",
    "_RecognitionCallback", "_import_stt_deps",
    "_pcm_to_wav", "_SILENCE_THRESHOLD",
]

# 已下线的子模块：文件级删除，不应能导入
_REMOVED_MODULES = [
    "agent.voice.stt.paraformer",
    "agent.voice.stt.funasr",
]


def _module_exists(name: str) -> bool:
    """子模块是否可导入（find_spec 对已删模块返回 None 或抛 ImportError）。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError):
        return False


class TestSttPackagePublicApi:
    """包对外符号与实际依赖一致。"""

    @pytest.mark.parametrize("name", _EXPECTED_SYMBOLS)
    def test_expected_symbols_reachable(self, name: str) -> None:
        """调用方会用到的每个名字都能从 agent.voice.stt 上取到。"""
        assert hasattr(stt_pkg, name), f"包缺失约定符号: {name}"

    @pytest.mark.parametrize("name", _REMOVED_SYMBOLS)
    def test_removed_symbols_gone(self, name: str) -> None:
        """已下线的符号不应再暴露。"""
        assert not hasattr(stt_pkg, name), f"下线符号又出现了: {name}"
        assert not hasattr(common_mod, name), f"common 里仍有下线符号: {name}"

    @pytest.mark.parametrize("name", _REMOVED_MODULES)
    def test_removed_modules_gone(self, name: str) -> None:
        """已下线后端的子模块文件应已删除。"""
        assert not _module_exists(name), f"下线后端模块仍可导入: {name}"

    def test_symbols_are_same_object_as_submodule(self) -> None:
        """re-export 必须是别名而非副本，否则 monkeypatch 会失效。"""
        assert stt_pkg.QwenASR is qwen_mod.QwenASR
        assert stt_pkg._rms is common_mod._rms

    def test_agent_voice_top_level_export_intact(self) -> None:
        """agent.voice 包级便捷导出同步收敛到单后端。"""
        import agent.voice as voice_pkg

        assert voice_pkg.create_stt is stt_pkg.create_stt
        assert voice_pkg.QwenASR is stt_pkg.QwenASR
        assert not hasattr(voice_pkg, "ParaformerSTT")


class TestStopFlagContract:
    """停止标志：跨模块唯一的通信契约。"""

    def test_single_shared_event(self) -> None:
        """包、common、后端模块看到的必须是同一个 Event 实例。"""
        assert stt_pkg._stop_flag is common_mod._stop_flag
        # 后端引用的 _is_stopped 必须是 common 的别名，而不是本地另实现一份
        # （一旦后端有独立 _stop_flag，ESC / Ctrl+C 打断就会失灵）
        assert qwen_mod._is_stopped is common_mod._is_stopped
        assert qwen_mod._is_stopped.__globals__["_stop_flag"] is common_mod._stop_flag

    def test_request_is_reset_cycle(self) -> None:
        """_request_stop 置位 → 后端轮询到 → _reset_stop 清零。"""
        try:
            stt_pkg._request_stop()
            assert stt_pkg._is_stopped()
            assert qwen_mod._is_stopped()
        finally:
            stt_pkg._reset_stop()
        assert not stt_pkg._is_stopped()
        assert not qwen_mod._is_stopped()


class TestRmsUtility:
    """_rms 是 barge_in 的依赖，不属于可以被顺手删掉的「孤儿」。"""

    def test_rms_usable_for_barge_in(self) -> None:
        """静音帧 RMS 为 0、满幅帧 RMS 明显大于 0（barge_in 用它判开口）。"""
        from agent.voice.stt import _rms

        assert _rms(b"\x00\x00" * 100, 2) == 0
        assert _rms((1000).to_bytes(2, "little", signed=True) * 100, 2) > 500
        assert _rms(b"", 2) == 0


class TestCreateSttFactory:
    """工厂只做参数透传，恒返回 QwenASR。"""

    @pytest.mark.parametrize(
        "model",
        [
            "qwen3-asr-flash-realtime",
            "Qwen3-ASR-Flash-Realtime",  # 大小写不敏感（服务端校验）
            "paraformer-realtime-v2",    # 历史配置名：工厂不再分派，交由服务端拒绝
            "fun-asr-flash-2026-06-15",
        ],
    )
    def test_always_returns_qwen_asr(self, model: str) -> None:
        """无论 model 名如何，都返回 QwenASR 且原样透传 model。"""
        stt = stt_pkg.create_stt(api_key="sk-test", model=model)
        assert type(stt) is qwen_mod.QwenASR
        assert stt.model == model

    def test_default_model_is_qwen(self) -> None:
        """默认模型与 settings.py 默认值一致。"""
        from agent.config.settings import Settings

        stt = stt_pkg.create_stt(api_key="sk-test")
        assert stt.model == "qwen3-asr-flash-realtime"
        assert Settings().stt_model == stt.model

    def test_signature_has_no_backend_selector(self) -> None:
        """工厂签名保留 api_key / model / language 三个关键字。"""
        params = inspect.signature(stt_pkg.create_stt).parameters
        assert set(params) == {"api_key", "model", "language"}
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())


class TestQwenListenSignature:
    """QwenASR.listen() 参数与调用方一致。"""

    #: voice_loop / voice_commands / barge_in 实际传入的关键字
    _CALL_KWARGS = ("max_seconds", "silence_seconds", "on_partial", "on_open")

    def test_listen_accepts_all_kwargs(self) -> None:
        """listen() 能接受上层会传的全部关键字。"""
        params = inspect.signature(qwen_mod.QwenASR.listen).parameters
        missing = [k for k in self._CALL_KWARGS if k not in params]
        assert not missing, f"QwenASR.listen 缺参数: {missing}"

    def test_listen_has_no_silence_threshold(self) -> None:
        """silence_threshold 死参数已下线（服务端 VAD 不读它）。"""
        params = inspect.signature(qwen_mod.QwenASR.listen).parameters
        assert "silence_threshold" not in params

    def test_transcribe_file_available(self) -> None:
        """保留文件识别入口（/voice 之外的一次性识别用途）。"""
        assert hasattr(qwen_mod.QwenASR, "transcribe_file")

    def test_defaults_use_shared_constants(self) -> None:
        """默认时长参数取自共享常量，而非后端硬编码。"""
        params = inspect.signature(qwen_mod.QwenASR.listen).parameters
        assert params["max_seconds"].default == common_mod._MAX_SECONDS
        assert params["silence_seconds"].default == common_mod._SILENCE_SECONDS


class TestSettingsContract:
    """settings 侧同步收敛：默认模型、barge_in 默认值与废弃字段。"""

    def test_stt_model_default(self) -> None:
        """默认 STT 模型为 Qwen 实时识别。"""
        from agent.config.settings import Settings

        assert Settings().stt_model == "qwen3-asr-flash-realtime"

    def test_voice_barge_in_defaults_off(self) -> None:
        """麦克风 barge-in 默认关闭：watcher 会开第二个 PyAudio 实例。"""
        from agent.config.settings import Settings

        assert Settings().voice_barge_in is False
        assert Settings().voice_barge_in_key is True  # 键盘通道无风险，仍默认开

    def test_silence_threshold_field_removed(self) -> None:
        """stt_silence_threshold 字段已删除。"""
        from agent.config.settings import Settings

        assert "stt_silence_threshold" not in {f.name for f in Settings.__dataclass_fields__.values()}

    def test_legacy_toml_key_ignored(self) -> None:
        """老配置文件里的 [stt] silence_threshold 被静默忽略，不报错。"""
        from agent.config.settings import Settings, _apply_toml

        merged = _apply_toml(
            Settings(),
            {"stt": {"model": "qwen3-asr-flash-realtime", "silence_threshold": 500}},
        )
        assert merged.stt_model == "qwen3-asr-flash-realtime"
        assert not hasattr(merged, "stt_silence_threshold")
