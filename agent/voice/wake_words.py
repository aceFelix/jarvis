"""唤醒词 / 退下词 / 打断词的配置解析与「听起来像」模糊匹配（aceFelix）。

为什么单独一个模块
------------------
1. **词表不能写死**：用户想怎么称呼这个助手，应该由他自己在
   ``~/.jarvis/settings.toml`` 的 ``[voice]`` 表里定。原先三个词表全是模块级
   常量，改一个词要改代码。
2. **「喊了没反应」的根因是近音字**：ASR 常常把「贾维斯」听成「加维思」
   「贾伟斯」「假维斯」「佳维丝」…… 字面子串匹配永远对不上，用户只能反复喊。
   这里做**两层判断**——先字面（认配置里写的原样写法），再**拼音归一化**
   （把所有同音字折叠成同一个拼音串，命中即算叫到了）。

拼音归一化实测（2026-10，见 ``tests/voice/test_wake_words.py``）：
同一批近音字归一化后与「贾维斯」完全相同 → ``jiaweisi``；14 条日常语句
（「加个微信」「甲维集团」「价格维持不变」…）零误报。

依赖
----
拼音层用 ``pypinyin``（``pip install "jarvis-agent[voice]"`` 已含）。**没装也能用**：
``_pinyin()`` 返回空串，``matches()`` 自动退化为「只做字面匹配」——
覆盖变窄但绝不报错，不把可选依赖变成硬依赖。

@author aceFelix
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from agent.voice.voice_config import (
    _EXIT_WORDS,
    _INTERRUPT_WORDS,
    _WAKE_WORDS,
    _contains_any,
)

#: 汉字区间（拼音匹配只对有这么多汉字以上的词开放，见下）。
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")

#: 参与拼音模糊匹配的最少汉字数。
#:
#: 两字词的拼音串只有 6 个字母（「贾维」→ ``jiawei``），而 ``jiawei`` 是
#: ``jiaweijituan``（甲维集团）、``jiaweixin``（加微信）这类**正常语句**的子串，
#: 放开匹配必然误唤醒。三个汉字（``jiaweisi``）足够长，实测零误报。
#: 短词并没有被禁掉——它仍走字面匹配那条路，用户真说了「贾维」照样唤醒。
_MIN_HAN_FOR_PINYIN = 3

#: 词表之间允许的分隔符（用户可能写成一个字符串而不是 TOML 数组）。
_SPLIT_RE = re.compile(r"[,，、;；\s]+")


@lru_cache(maxsize=512)
def _pinyin(text: str) -> str:
    """把文本折成无声调拼音串；不含汉字或 pypinyin 缺失时返回空串。

    非汉字字符（标点、空格、英文、数字）原样保留——这样「JARVIS!!!」折出来
    仍是 ``jarvis!!!``，与唤醒词 ``jarvis`` 的子串关系不被打断。

    Returns:
        小写拼音串；pypinyin 不可用时返回 ``""``（调用方据此跳过拼音层）。
    """
    if not text:
        return ""
    try:
        from pypinyin import Style, lazy_pinyin
    except Exception:  # pragma: no cover - 未装可选依赖时的降级分支
        return ""
    try:
        return "".join(
            str(part) for part in lazy_pinyin(text, style=Style.NORMAL, errors="default")
        ).lower()
    except Exception:  # pragma: no cover - pypinyin 内部异常时不影响唤醒
        return ""


def _han_count(text: str) -> int:
    """汉字个数。"""
    return len(_HAN_RE.findall(text or ""))


def _pinyin_hit(text: str, words: tuple[str, ...] | list[str]) -> bool:
    """拼音层面：``text`` 的拼音串里是否含着某个词的同音串。"""
    haystack = _pinyin(text)
    if not haystack:
        return False
    for word in words:
        if _han_count(word) < _MIN_HAN_FOR_PINYIN:
            # 太短的词不参与拼音匹配（会把「甲维集团」认成「贾维」），
            # 它仍可由调用方前面的字面匹配命中。
            continue
        needle = _pinyin(word)
        if needle and needle in haystack:
            return True
    return False


def matches(text: str, words: Any) -> bool:
    """``text`` 是否「在叫」词表里的某个词：先字面命中，再拼音同音命中。

    Args:
        text: ASR 识别出的文本（可能含标点、英汉混合）。
        words: 词表（任何可迭代的字符串集合）。

    Returns:
        命中返回 True。空文本或空词表恒为 False。

    @author aceFelix
    """
    if not text or not words:
        return False
    words = tuple(words)
    if _contains_any(text, words):
        return True
    return _pinyin_hit(text, words)


def _configured(settings: Any, attr: str) -> tuple[str, ...]:
    """读出用户在 ``[voice]`` 表里配的词表。

    TOML 数组解析出来是 list，用户也可能图省事直接写成一个用顿号/逗号分隔的
    字符串——两种形态都接受，统一成去空的元组。

    Args:
        settings: ``Settings`` 实例；``None`` 时视作未配置。
        attr: ``Settings`` 上的属性名（如 ``voice_wake_words``）。

    Returns:
        去空白、去空项后的元组；未配置返回空元组。
    """
    raw = getattr(settings, attr, None) if settings is not None else None
    if not raw:
        return ()
    if isinstance(raw, str):
        items = _SPLIT_RE.split(raw)
    else:
        try:
            items = [str(item) for item in raw]
        except TypeError:
            return ()
    return tuple(item.strip() for item in items if item and item.strip())


def resolve_wake_words(settings: Any = None) -> tuple[str, ...]:
    """唤醒词表：用户配置优先，未配置则回退内置默认词表。

    @author aceFelix
    """
    return _configured(settings, "voice_wake_words") or _WAKE_WORDS


def resolve_exit_words(settings: Any = None) -> tuple[str, ...]:
    """退下词表（"退下"/"拜拜"/"不用了"…）：用户配置优先，否则内置默认。

    @author aceFelix
    """
    return _configured(settings, "voice_exit_words") or _EXIT_WORDS


def resolve_interrupt_words(settings: Any = None) -> tuple[str, ...]:
    """打断词表（TTS 播报期间 "闭嘴"/"停"… 立刻停止）：用户配置优先。

    @author aceFelix
    """
    return _configured(settings, "voice_interrupt_words") or _INTERRUPT_WORDS


def build_asr_hotwords(words: Any) -> str:
    """把唤醒词拼成 ASR 上下文增强语料。

    对应 DashScope 的 ``input_audio_transcription.corpus.text``：把词表在
    ``session.update`` 里一并送给识别模型，让它**一开始就倾向于输出这个词**，
    而不是先听成近音字再靠拼音层兜回来。这是治本的一层，拼音匹配是兜底。

    实测（2026-10）：``qwen3-asr-flash-realtime`` 接受该字段，
    ``update_session`` 不报错、服务端零 error 事件。

    去重保序（词表里可能重复写了「贾维斯」和「贾维」）。

    @author aceFelix
    """
    uniq: list[str] = []
    for word in words or ():
        word = str(word or "").strip()
        if word and word not in uniq:
            uniq.append(word)
    return "、".join(uniq)
