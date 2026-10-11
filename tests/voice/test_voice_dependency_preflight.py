"""语音可选依赖缺失时的降级回归测试。

背景（见 docs/fixlogs/voice-missing-deps-degrade-fix.md）：`dashscope` /
`pyaudio` 属 [voice] 可选依赖，且均为「方法内懒导入」——包能 import 成功、
`create_stt()` 也能返回对象，直到 `stt.listen()` 内部才抛 ModuleNotFoundError。
2.1.1 实测：缺依赖时 `/voice` 会进入「聆听→识别失败→再聆听」的无限刷屏。

修复后行为：
1. `voice_loop()` 前置依赖检查：缺依赖直接拒绝进入语音模式并给安装指引；
2. `_voice_loop_round()` 遇永久性（依赖缺失）识别错误上抛
   `_VoiceDependencyError`，由 voice_loop 干净退出；普通瞬时错误仍继续下一轮。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
from types import SimpleNamespace

import pytest

from agent.voice import stt as stt_module
from agent.voice.voice_events import CollectingVoiceEvents

# 包 __init__ 用同名函数重导出遮蔽了模块属性，需从 sys.modules 取模块本体
import agent.voice.voice_loop  # noqa: F401,E402

vl = sys.modules["agent.voice.voice_loop"]


class _FakeSTT:
    """最小 STT 假件：listen() 固定返回带 error 的结果。"""

    def __init__(self, error: str) -> None:
        self._error = error

    def listen(self, **kwargs) -> dict:  # noqa: ANN003
        return {"text": "", "duration": 0.0, "error": self._error}


@pytest.fixture(autouse=True)
def _reset_stop_flag():
    """隔离 STT 全局停止标志：避免其它用例残留导致误判。"""
    stt_module._reset_stop()
    yield
    stt_module._reset_stop()


def test_preflight_refuses_when_deps_missing(monkeypatch):
    """dashscope/pyaudio 缺失时 voice_loop 应前置拒绝，不进入状态机。"""
    real_find_spec = importlib.util.find_spec

    def _fake_find_spec(name, *args, **kwargs):
        if name in ("dashscope", "pyaudio"):
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec)

    events = CollectingVoiceEvents()
    asyncio.run(vl.voice_loop(events, SimpleNamespace(), None, None))

    joined = " ".join(str(p) for _, p in events.records)
    assert "未安装" in joined, "应提示依赖未安装"
    assert "jarvis-agent[voice]" in joined, "应给出安装指引"
    assert events.states() == [], "前置拒绝后不应进入语音状态机"


def test_round_raises_voice_dependency_error_on_missing_module():
    """STT 返回依赖缺失错误时，_voice_loop_round 应上抛致命错误而非继续循环。"""
    events = CollectingVoiceEvents()
    settings = SimpleNamespace(voice_max_seconds=5, stt_silence_seconds=1.0)
    stt = _FakeSTT("dashscope 未安装: No module named 'dashscope'")

    with pytest.raises(vl._VoiceDependencyError):
        asyncio.run(
            vl._voice_loop_round(
                events, settings, SimpleNamespace(), SimpleNamespace(),
                SimpleNamespace(), stt, threading.Event(), threading.Event(),
            )
        )


def test_round_continues_on_transient_error():
    """普通瞬时识别错误仍应继续下一轮（不被当作致命错误退出）。"""
    events = CollectingVoiceEvents()
    settings = SimpleNamespace(voice_max_seconds=5, stt_silence_seconds=1.0)
    stt = _FakeSTT("network timeout")

    cont = asyncio.run(
        vl._voice_loop_round(
            events, settings, SimpleNamespace(), SimpleNamespace(),
            SimpleNamespace(), stt, threading.Event(), threading.Event(),
        )
    )
    assert cont is True, "瞬时错误应继续聆听"
    assert any(k == "error" for k, _ in events.records), "应外抛识别失败事件"
