"""唤醒词可配置 + 拼音同音模糊匹配的回归测试（aceFelix）。

锁定的用户痛点（2026-10）：
「有时候喊贾维斯他没反应 —— 我说 jarvis、贾维斯在吗、贾维斯，或者别的听起来像
贾维斯的话，他应该都能识别出来是叫他才对。」

两个根因，两层修复：
1. **词表写死**：``_WAKE_WORDS`` 是模块级常量，用户改不了。→ 迁到
   ``[voice] wake_words``，留空回退内置默认（老配置零影响）。
2. **字面子串匹配对不上近音字**：ASR 把「贾维斯」听成「加维思」「贾伟斯」
   「假维斯」时，``"贾维斯" in text`` 永远为假。→ 加一层**拼音归一化**，
   把同音字折叠成同一个拼音串再比。

第 2 层有个关键边界：两字词（如内置词表里的「贾维」）拼音串只有 ``jiawei``，
它是「甲维集团」(``jiaweijituan``)、「加个微信」(``jiaweixin``) 这类**正常语句**
的子串，放开匹配必然误唤醒，所以拼音层只对 ≥3 个汉字的词生效（短词仍走字面）。

覆盖：
- 近音字命中 / 日常语句零误报（含上面那条边界）
- pypinyin 未安装时优雅降级（不报错，只是覆盖变窄）
- 词表解析：配置优先、字符串形态、留空回退默认
- 热词语料生成（去重保序）
- 真跑 voice_loop：待机时喊成近音字能唤醒；待机提示显示用户配的词

@author aceFelix
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
from types import SimpleNamespace

import pytest

import agent.voice as voice_pkg
import agent.voice.stream_tts as stream_tts_module
import agent.voice.voice_state as voice_state_module
import agent.voice.voice_loop  # noqa: F401  —— 包 __init__ 用同名函数遮蔽了模块属性
from agent.voice import wake_words as ww
from agent.voice.voice_events import STATE_DIALOG, STATE_STANDBY, CollectingVoiceEvents

vl = sys.modules["agent.voice.voice_loop"]

# pypinyin 是 [voice] 可选依赖：没装的环境跳过「纯拼音」用例，
# 但字面匹配 / 配置解析 / 热词生成这些**不依赖 pypinyin** 的用例必须照跑。
_HAS_PINYIN = importlib.util.find_spec("pypinyin") is not None
requires_pinyin = pytest.mark.skipif(
    not _HAS_PINYIN, reason='pypinyin 未安装（pip install "jarvis-agent[voice]"）'
)

#: 真跑 ``voice_loop`` 的用例还需要语音运行时依赖。
#: 缺 dashscope / pyaudio 时 voice_loop 开头就直接返回「语音功能依赖未安装」，
#: 状态序列与播报文案都无从产生——那是「本机没装依赖」，不是代码坏了，
#: 所以必须是 skip 而不是 fail（2026-10 CI 上正是这条漏了报红）。
_VOICE_MODULES = ("dashscope", "pyaudio")
_MISSING_VOICE = tuple(
    m for m in _VOICE_MODULES if importlib.util.find_spec(m) is None
)
requires_voice_runtime = pytest.mark.skipif(
    bool(_MISSING_VOICE),
    reason=f"语音运行时依赖未安装: {', '.join(_MISSING_VOICE)}"
    '（pip install "jarvis-agent[voice]"）',
)

# ---------------------------------------------------------------- 词表样本 ----

#: 用户可能怎么说「贾维斯」——同一批近音字归一化后都是 ``jiaweisi``。
HOMOPHONES = (
    "贾维斯", "加维思", "贾伟斯", "假维斯", "佳维丝", "嘉维思",
    "贾薇丝", "贾威斯", "加威斯", "佳伟斯", "贾维司", "加维司", "贾为斯",
)

#: 带上下文的自然说法（唤醒词不必在句首）。
IN_SENTENCE = ("贾维斯在吗", "贾维斯，现在几点", "那个贾维斯帮我看下", "喂贾维斯")

#: 日常语句：**一条都不许唤醒**（宁可漏也不要随便插话）。
DAILY_TALK = (
    "加个微信", "今天天气怎么样", "帮我打开浏览器", "价格维持不变",
    "甲维集团", "贾先生你好", "维多利亚的秘密", "你叫什么名字",
    "这次考试很重要", "家伟说他要来", "维嘉斯是个人名吗",
)


# ------------------------------------------------------- 拼音同音匹配用例 ----

@requires_pinyin
class TestPinyinHomophones:
    """近音写法都要算作「在叫 jarvis」。"""

    @pytest.mark.parametrize("text", HOMOPHONES)
    def test_homophone_wakes(self, text: str) -> None:
        assert ww.matches(text, ("贾维斯",)), f"{text!r} 应被识别为唤醒"

    @pytest.mark.parametrize("text", IN_SENTENCE)
    def test_wake_word_inside_sentence(self, text: str) -> None:
        """唤醒词不必在句首：夹在句子里也算叫到。"""
        assert ww.matches(text, ("贾维斯",)), f"{text!r} 应被识别为唤醒"

    @pytest.mark.parametrize("text", ("jarvis", "hey jarvis", "JARVIS!!!"))
    def test_english_wake_word(self, text: str) -> None:
        """英文写法走字面匹配（大小写不敏感），不依赖拼音层。"""
        assert ww.matches(text, ("Jarvis",)), f"{text!r} 应被识别为唤醒"

    def test_pinyin_string_is_stable(self) -> None:
        """归一化结果本身就是契约：三音节的同音字折出来是同一个串。"""
        assert ww._pinyin("贾维斯") == ww._pinyin("加维思") == "jiaweisi"


class TestNoFalseWake:
    """正常说话不能被误判成唤醒——误唤醒比漏唤醒更烦人。"""

    @pytest.mark.parametrize("text", DAILY_TALK)
    def test_daily_talk_does_not_wake(self, text: str) -> None:
        assert not ww.matches(text, ("贾维斯",)), f"{text!r} 不该唤醒 jarvis"

    def test_short_word_is_not_pinyin_matched(self) -> None:
        """两字唤醒词「贾维」只走字面：不能把「甲维集团」听成「贾维」。"""
        assert ww.matches("甲维集团", ("贾维",)) is False, "短词拼音匹配会误唤醒"
        # 但用户真的只说了「贾维」两个字 → 字面匹配仍然有效
        assert ww.matches("贾维", ("贾维",)) is True

    def test_empty_inputs_are_safe(self) -> None:
        assert ww.matches("", ("贾维斯",)) is False
        assert ww.matches("贾维斯", ()) is False
        assert ww.matches("贾维斯", None) is False


class TestPinyinUnavailable:
    """没装 pypinyin 时优雅降级：不报错，只退化为字面匹配。"""

    def test_falls_back_to_literal(self, monkeypatch) -> None:
        # sys.modules["pypinyin"] = None → 之后 `import pypinyin` 抛 ImportError
        monkeypatch.setitem(sys.modules, "pypinyin", None)
        ww._pinyin.cache_clear()
        try:
            # 字面写法照样命中（不依赖拼音层）
            assert ww.matches("贾维斯", ("贾维斯",)) is True
            # 近音字降级为不命中——覆盖率变窄，但绝不误判
            assert ww.matches("加维思", ("贾维斯",)) is False
        finally:
            ww._pinyin.cache_clear()


class TestResolveWords:
    """词表解析：配置优先、字符串形态、留空回退内置默认。"""

    def test_configured_wins(self) -> None:
        settings = SimpleNamespace(voice_wake_words=("星期五", "Friday"))
        assert ww.resolve_wake_words(settings) == ("星期五", "Friday")

    def test_accepts_comma_string(self) -> None:
        """用户可能图省事写成字符串而不是 TOML 数组。"""
        settings = SimpleNamespace(voice_wake_words="星期五， Friday; fri")
        assert ww.resolve_wake_words(settings) == ("星期五", "Friday", "fri")

    def test_blank_falls_back_to_default(self) -> None:
        assert ww.resolve_wake_words(SimpleNamespace(voice_wake_words=())) == ww._WAKE_WORDS
        assert ww.resolve_wake_words(SimpleNamespace()) == ww._WAKE_WORDS
        assert ww.resolve_wake_words(None) == ww._WAKE_WORDS

    def test_exit_and_interrupt_resolve_independently(self) -> None:
        settings = SimpleNamespace(
            voice_exit_words=["收工"],
            voice_interrupt_words=["小声点"],
        )
        assert ww.resolve_exit_words(settings) == ("收工",)
        assert ww.resolve_interrupt_words(settings) == ("小声点",)
        # 未配的那张表回退默认
        assert ww.resolve_wake_words(settings) == ww._WAKE_WORDS

    @requires_pinyin
    def test_custom_wake_word_hits_by_pinyin(self) -> None:
        """换个称呼（「星期五」）同样享受拼音容错。"""
        assert ww.matches("星期五在吗", ("星期五",)) is True
        assert ww.matches("星其舞", ("星期五",)) is True   # xing qi wu


class TestBuildHotwords:
    """ASR 上下文增强语料生成。"""

    def test_joins_with_dedupe(self) -> None:
        assert ww.build_asr_hotwords(("贾维斯", "贾维", "贾维斯")) == "贾维斯、贾维"

    def test_blank_items_dropped(self) -> None:
        assert ww.build_asr_hotwords(("", "  ", "贾维斯")) == "贾维斯"

    def test_empty_is_empty(self) -> None:
        assert ww.build_asr_hotwords(()) == ""
        assert ww.build_asr_hotwords(None) == ""


# --------------------------------------------------------- 集成：待机唤醒 ----
# 假件尽量最小：只实现 voice_loop 真正触及的方法，不碰麦克风 / 网络 / 磁盘。


class _FakeMsg:
    """assistant 消息假件：voice_loop 用 get_text() 检测 <standby/>。"""

    def __init__(self, role: str, text: str) -> None:
        self.role = role
        self._text = text

    def get_text(self) -> str:
        return self._text

    def get_thinking(self) -> str:
        return ""


class _ScriptedSTT:
    """按脚本依次返回识别文本；脚本用完后置位 stop_event 收工。

    脚本就是用户的完整操作序列：先喊「退下吧」进待机，待机时把「贾维斯」
    说成近音字，然后结束。
    """

    def __init__(self, texts: list[str], stop_event: threading.Event) -> None:
        self._texts = list(texts)
        self._stop = stop_event
        self.calls = 0

    def listen(self, **kwargs) -> dict:  # noqa: ANN003
        self.calls += 1
        if not self._texts:
            self._stop.set()
            return {"text": ""}
        return {"text": self._texts.pop(0)}


class _StandbyLoop:
    """假 QueryLoop：回复「告别语 + <standby/>」，让本轮判定为退下。"""

    def is_thinking_enabled(self) -> bool:
        return False

    def set_thinking_enabled(self, on: bool) -> None:  # noqa: ARG002
        pass

    async def run(self, text, ctx, images=None):  # noqa: ANN001, ANN201
        ctx.messages.append(_FakeMsg("assistant", "好的，我先退下了<standby/>"))
        return SimpleNamespace(stopped_reason="end", iterations=1, tool_calls=0)


class _FakeStreamPlayer:
    """流式 TTS 替身：不出声、不联网。"""

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        pass

    def start(self) -> bool:
        return True

    def feed(self, text: str) -> None:  # noqa: ARG002
        pass

    def stop(self) -> None:
        pass

    def finish(self) -> dict:
        return {}


class _FakeCtx:
    """最小 ToolContext 假件：消息列表 + abort_event + feeder 挂点。"""

    def __init__(self) -> None:
        self.messages: list = []
        self.abort_event = asyncio.Event()
        self.on_assistant_text = None
        self.tts_ok = True


def _voice_settings(**overrides) -> SimpleNamespace:
    """最小 settings 假件：仅含 voice_loop 启动阶段触及的字段。"""
    base = dict(
        api_key="test-key",
        verbose=False,
        voice_max_seconds=5,
        stt_silence_seconds=1.0,
        stt_model="fake-stt",
        tts_model="fake-tts",
        tts_voice="fake-voice",
        tts_volume=50,
        tts_speech_rate=1.0,
        tts_pitch_rate=1.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _run_voice_loop(monkeypatch, settings, script: list[str]) -> CollectingVoiceEvents:
    """跑完整 voice_loop（对话→待机→唤醒→收工），返回收集到的事件。

    Args:
        script: STT 依次返回的识别文本。脚本用完后 STT 自置停止信号，
            循环干净退出——所以停止信号必须与 STT **共用同一个对象**
            （两处各建一个 Event 会让收工信号永远到不了循环）。
    """
    stop = threading.Event()
    stt = _ScriptedSTT(script, stop)

    # voice_loop 内部写的是 `from agent.voice import create_stt`，
    # 所以 patch 的是包属性 agent.voice.create_stt（不是 voice_loop 模块属性）。
    monkeypatch.setattr(voice_pkg, "create_stt", lambda **kwargs: stt)
    monkeypatch.setattr(stream_tts_module, "StreamTTSPlayer", _FakeStreamPlayer)
    # 语音互斥锁是进程级文件锁，测试里不能真抢（会写 ~/.jarvis/voice.lock）
    monkeypatch.setattr(voice_state_module, "acquire_voice_lock", lambda: (True, "test"))
    monkeypatch.setattr(voice_state_module, "release_voice_lock", lambda: None)

    events = CollectingVoiceEvents()
    asyncio.run(
        vl.voice_loop(events, settings, _StandbyLoop(), _FakeCtx(), None, stop_event=stop)
    )
    return events


@requires_voice_runtime
class TestStandbyWakeIntegration:
    """真跑 voice_loop：待机 → 唤醒判定 → 回到对话。"""

    @requires_pinyin
    def test_homophone_wakes_from_standby(self, monkeypatch) -> None:
        """待机时喊「加维思」（ASR 把「贾维斯」听成近音字）→ 必须唤醒。"""
        # 第 1 句进待机；第 2 句是待机轮的近音字唤醒；之后脚本空 → 收工
        events = _run_voice_loop(monkeypatch, _voice_settings(), ["退下吧", "加维思"])

        states = events.states()
        assert STATE_STANDBY in states, "「退下吧」应进入待机"
        # standby 之后必须再出现 dialog —— 即被唤醒了
        idx_standby = states.index(STATE_STANDBY)
        assert STATE_DIALOG in states[idx_standby + 1:], (
            f"喊近音字没唤醒，状态序列: {states}"
        )
        assert any("回到对话模式" in str(p) for _, p in events.records), "应播报唤醒提示"

    def test_daily_talk_does_not_wake_from_standby(self, monkeypatch) -> None:
        """待机时聊日常（「甲维集团」）不能被误唤醒。"""
        events = _run_voice_loop(monkeypatch, _voice_settings(), ["退下吧", "甲维集团怎么样"])

        states = events.states()
        idx_standby = states.index(STATE_STANDBY)
        assert STATE_DIALOG not in states[idx_standby + 1:], (
            f"日常语句误唤醒，状态序列: {states}"
        )
        # 启动横幅里本来就有「唤醒」二字，所以只断言真正的唤醒事件文案
        assert not any("回到对话模式" in str(p) for _, p in events.records)

    def test_wake_hint_uses_configured_word(self, monkeypatch) -> None:
        """待机提示要显示用户配的词，而不是写死的「贾维斯」。"""
        settings = _voice_settings(voice_wake_words=("星期五",))

        events = _run_voice_loop(monkeypatch, settings, ["退下吧"])

        joined = " ".join(str(p) for _, p in events.records)
        assert "星期五" in joined, "待机提示未使用配置的唤醒词"
        assert "说「贾维斯」唤醒我" not in joined, "仍残留写死的「贾维斯」"

    @requires_pinyin
    def test_configured_wake_word_is_detected(self, monkeypatch) -> None:
        """换成自定义称呼后，喊它（哪怕近音）也能唤醒。"""
        settings = _voice_settings(voice_wake_words=("星期五",))
        # 「星其舞」= xing qi wu，与「星期五」同音
        events = _run_voice_loop(monkeypatch, settings, ["退下吧", "星其舞"])

        states = events.states()
        idx_standby = states.index(STATE_STANDBY)
        assert STATE_DIALOG in states[idx_standby + 1:], "自定义唤醒词未生效"
