"""STT 包拆分契约回归测试（aceFelix）。

原 agent/voice/stt.py 单文件 1087 行、超出「单文件 800 行」上限，
按识别后端拆成 agent/voice/stt/ 包（common / paraformer / qwen / funasr）。

拆分是纯重构，对外契约必须一模一样，本文件锁定四条契约：
1. 拆分前的顶层符号在包内仍可达（调用方全是 `from agent.voice import stt`
   再取属性，漏一个就是运行时 AttributeError）
2. 停止标志跨后端共享同一个 Event 对象——上层用 `_request_stop()` 打断
   阻塞在 pyaudio 里的录音循环，标志一旦不再唯一，ESC / Ctrl+C 会失灵
3. create_stt() 按 model 名分派的后端与拆分前一致
4. 三个后端的 listen() 关键字参数齐全——voice_loop 以关键字调用，
   少一个参数直接 TypeError
"""

from __future__ import annotations

import inspect

import pytest

from agent.voice import stt as stt_pkg
from agent.voice.stt import common as common_mod
from agent.voice.stt import funasr as funasr_mod
from agent.voice.stt import paraformer as paraformer_mod
from agent.voice.stt import qwen as qwen_mod

# 拆分前 stt.py 的全部顶层定义（符号级清单，缺一即断契约）
_LEGACY_TOP_LEVEL = [
    # 类与工厂
    "ParaformerSTT", "QwenASR", "FunASRFlashSTT", "create_stt",
    "_RecognitionCallback", "_QwenASRCallback",
    # 延迟导入
    "_import_stt_deps", "_import_qwen_omni", "_import_pyaudio",
    # 工具函数
    "_rms", "_pcm_to_wav",
    # 停止标志
    "_stop_flag", "_is_stopped", "_request_stop", "_reset_stop",
    # 音频与静音常量
    "_PCM_RATE", "_PCM_CHANNELS", "_PCM_WIDTH", "_FRAMES_PER_BUFFER",
    "_SILENCE_THRESHOLD", "_SILENCE_SECONDS", "_MAX_SECONDS",
    # Qwen 专用
    "_VAD_THRESHOLD", "_VAD_LEAD_IN_SECONDS", "_QWEN_REALTIME_URL",
]


class TestSttPackagePublicApi:
    """包对外符号与拆分前的单文件模块等价。"""

    @pytest.mark.parametrize("name", _LEGACY_TOP_LEVEL)
    def test_legacy_symbols_reachable(self, name: str) -> None:
        """拆分前的每个顶层名字，都能从 agent.voice.stt 上取到。"""
        assert hasattr(stt_pkg, name), f"包缺失原顶层符号: {name}"

    def test_symbols_are_same_object_as_submodule(self) -> None:
        """re-export 必须是别名而非副本，否则 monkeypatch 会失效。"""
        assert stt_pkg.ParaformerSTT is paraformer_mod.ParaformerSTT
        assert stt_pkg.QwenASR is qwen_mod.QwenASR
        assert stt_pkg.FunASRFlashSTT is funasr_mod.FunASRFlashSTT
        assert stt_pkg._rms is common_mod._rms

    def test_agent_voice_top_level_export_intact(self) -> None:
        """agent.voice 包级的便捷导出不受拆分影响。"""
        from agent.voice import ParaformerSTT, QwenASR, create_stt

        assert create_stt is stt_pkg.create_stt
        assert ParaformerSTT is stt_pkg.ParaformerSTT
        assert QwenASR is stt_pkg.QwenASR


class TestStopFlagContract:
    """停止标志：跨后端唯一的通信契约。"""

    def test_single_shared_event(self) -> None:
        """包、common、三个后端看到的必须是同一个 Event 实例。"""
        assert stt_pkg._stop_flag is common_mod._stop_flag
        # 后端模块引用的 _is_stopped 必须是 common 的别名，而不是各自另实现
        # 一份（否则一旦后端本地有独立 _stop_flag，ESC / Ctrl+C 打断会失灵）
        for mod in (paraformer_mod, qwen_mod, funasr_mod):
            assert mod._is_stopped is common_mod._is_stopped, mod.__name__
            assert mod._is_stopped.__globals__["_stop_flag"] is common_mod._stop_flag

    def test_request_is_reset_cycle(self) -> None:
        """_request_stop 置位 → 各后端轮询到 → _reset_stop 清零。"""
        try:
            stt_pkg._request_stop()
            assert stt_pkg._is_stopped()
            assert paraformer_mod._is_stopped()
            assert qwen_mod._is_stopped()
            assert funasr_mod._is_stopped()
        finally:
            stt_pkg._reset_stop()
        assert not stt_pkg._is_stopped()
        assert not funasr_mod._is_stopped()


class TestCreateSttDispatch:
    """工厂按 model 名挑选后端，规则与拆分前逐条一致。"""

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("paraformer-realtime-v2", paraformer_mod.ParaformerSTT),
            ("fun-asr-realtime", paraformer_mod.ParaformerSTT),  # 同为 Recognition 实时后端
            ("qwen3-asr-flash-realtime", qwen_mod.QwenASR),
            ("fun-asr-flash-2026-06-15", funasr_mod.FunASRFlashSTT),
            ("some-unknown-model", paraformer_mod.ParaformerSTT),  # 兜底默认
            ("Qwen3-ASR-Flash-Realtime", qwen_mod.QwenASR),  # 大小写不敏感
        ],
    )
    def test_dispatch(self, model: str, expected: type) -> None:
        """给定 model 名返回预期后端。"""
        stt = stt_pkg.create_stt(api_key="sk-test", model=model)
        assert type(stt) is expected
        assert stt.model == model


class TestBackendListenSignature:
    """三后端 listen() 关键字参数齐全（voice_loop 以关键字调用）。"""

    #: voice_loop / voice_commands 实际传入的关键字
    _CALL_KWARGS = ("max_seconds", "silence_seconds", "silence_threshold", "on_partial", "on_open")

    @pytest.mark.parametrize(
        "backend",
        [
            paraformer_mod.ParaformerSTT,
            qwen_mod.QwenASR,
            funasr_mod.FunASRFlashSTT,
        ],
        ids=lambda c: c.__name__,
    )
    def test_listen_accepts_all_kwargs(self, backend: type) -> None:
        """listen() 能接受上层会传的全部关键字。"""
        params = inspect.signature(backend.listen).parameters
        missing = [k for k in self._CALL_KWARGS if k not in params]
        assert not missing, f"{backend.__name__}.listen 缺参数: {missing}"

    @pytest.mark.parametrize(
        "backend",
        [paraformer_mod.ParaformerSTT, qwen_mod.QwenASR],
        ids=lambda c: c.__name__,
    )
    def test_transcribe_file_available(self, backend: type) -> None:
        """流式后端保留文件识别入口（FunASR 本就是文件式，无此方法）。"""
        assert hasattr(backend, "transcribe_file")

    def test_defaults_use_shared_constants(self) -> None:
        """默认超时/静音参数取自共享常量，而非各后端各自硬编码。"""
        for backend in (
            paraformer_mod.ParaformerSTT,
            qwen_mod.QwenASR,
            funasr_mod.FunASRFlashSTT,
        ):
            params = inspect.signature(backend.listen).parameters
            assert params["max_seconds"].default == common_mod._MAX_SECONDS
            assert params["silence_seconds"].default == common_mod._SILENCE_SECONDS
            assert params["silence_threshold"].default == common_mod._SILENCE_THRESHOLD
