"""语音厂商注册表（阶段一：只搬运 DashScope，行为零变化）。

## 为什么单独一个模块

语音侧的厂商差异原先散在模块级常量里（``realtime_talk.DEFAULT_WS_URL`` /
``DEFAULT_VOICE``、``stt`` 里写死的模型名、``engine_talk`` 里写死的报错文案）。
这和 LLM 层以前「每个文件各自 ``cfg.get``」是同一类问题：新增一家就要改多处，
漏一处就是静默失效。

本模块沿用 ``agent/llm/provider_registry.py`` 的范式——**表驱动 + 能力元数据**，
只把语音侧已经存在的隐式契约显式化，不改状态机。

## 本阶段范围（对应 plans/voice/voice-provider-decoupling.md §1）

- 覆盖 **realtime（/talk 全双工）** 与 **STT（/voice 的"听"）**；
- **不覆盖 TTS**：CosyVoice 走 Inference 协议，与 realtime 事件体系无关，
  属阶段二独立立项。因此本表没有 tts 条目，``kind="tts"`` 会 fail-closed。

## 键的粒度

``(vendor, series)`` 而不是 ``vendor``：同为 DashScope，Qwen-Audio 与
Qwen3-ASR 的端点相同但模型、modalities、能力不同。按厂商建表装不下同厂商的
两种形态（S1 发现 ②）。

## 铁律

- 未知厂商/系列、或 ``verified=False`` 的条目：**fail-closed 抛错**，绝不静默
  回落到 DashScope——静默回落会让用户以为换了厂商，实际还在用旧的那家；
- 默认值从现有常量原样搬运，本阶段不改任何数值；
- TTS 不参与解析。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 未配置时的默认厂商（老用户零迁移）。
DEFAULT_VENDOR = "dashscope"

#: 语音厂商的展示名（报错文案用，不写死在调用点）。
VENDOR_LABELS: dict[str, str] = {
    "dashscope": "DashScope（阿里云百炼）",
}


class VoiceProviderError(ValueError):
    """未知/未验证的语音厂商或系列（fail-closed）。

    继承 ``ValueError``：调用方既可按具体类型捕获，也可按参数错误处理。
    """


@dataclass(frozen=True)
class VoiceProviderMeta:
    """一家厂商的一个系列（realtime / stt 二选一或有各自的模型清单）。

    Attributes:
        vendor: 厂商标识（与 ``Settings.voice_vendor`` 取值一致）。
        series: 系列名（同一厂商可有多条，如 qwen-audio / qwen3-asr）。
        label: 给人看的名字。
        ws_url: 会话端点；STT 与 realtime 共用（S1 发现 ①）。
        auth_scheme: ``bearer`` / ``query_key`` / ``custom_headers``。
        realtime_models: /talk 可用的模型；空表示该系列不做 realtime。
        stt_models: /voice 可用的识别模型；空表示不做 STT。
        modalities: ``audio`` + ``text``（对话）或仅 ``text``（纯识别）。
        default_voice: 默认音色名（realtime 用；STT 为空）。
        voice_namespace: 音色所属命名空间（TTS 阶段二会用到）。
        turn_detection_supported: 支持的轮次检测方式。
        vad_threshold_range: ``vad_threshold`` 的量纲；跨厂商不能照搬。
        session_update_semantic: ``sparse``（增量更新）或 ``full``（每次重放全量）。
        input_sample_rate / output_sample_rate: 音频参数（Hz）。
        supports_hotwords: ``session.update`` 是否支持热词。
        requires_sdk: 必需的可选依赖包名；空表示纯 WebSocket。
        key_env: 该厂商 key 的环境变量名。
        verified: 官方规范是否一手核实过；``False`` 的条目禁止被选中。

    @author aceFelix
    """

    vendor: str
    series: str
    label: str
    ws_url: str
    auth_scheme: str = "bearer"
    realtime_models: tuple[str, ...] = ()
    stt_models: tuple[str, ...] = ()
    modalities: tuple[str, ...] = ("audio", "text")
    default_voice: str = ""
    voice_namespace: str = ""
    turn_detection_supported: tuple[str, ...] = ("server_vad",)
    vad_threshold_range: tuple[float, float] = (-1.0, 1.0)
    session_update_semantic: str = "sparse"
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    supports_hotwords: bool = True
    requires_sdk: str = ""
    key_env: str = ""
    verified: bool = True

    @property
    def key(self) -> tuple[str, str]:
        """注册表键 ``(vendor, series)``。"""
        return (self.vendor, self.series)

    def has_kind(self, kind: str) -> bool:
        """该系列是否提供指定能力（``realtime`` / ``stt``）。"""
        if kind == "realtime":
            return bool(self.realtime_models)
        if kind == "stt":
            return bool(self.stt_models)
        return False


#: DashScope 的实时语音端点（原 ``realtime_talk.DEFAULT_WS_URL``）。
_DASHSCOPE_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

#: 系列 1：Qwen-Audio 实时对话（原 /talk 的全部默认值）。
DASHSCOPE_QWEN_AUDIO = VoiceProviderMeta(
    vendor="dashscope",
    series="qwen-audio",
    label="DashScope 百炼（Qwen-Audio 实时对话）",
    ws_url=_DASHSCOPE_WS_URL,
    realtime_models=(
        "qwen-audio-3.0-realtime-flash",
        "qwen-audio-3.0-realtime-plus",
    ),
    modalities=("audio", "text"),
    default_voice="longanqian",
    voice_namespace="cosyvoice",
    turn_detection_supported=("server_vad", "smart_turn"),
    vad_threshold_range=(-1.0, 1.0),
    session_update_semantic="sparse",
    input_sample_rate=16000,
    output_sample_rate=24000,
    supports_hotwords=True,
    requires_sdk="dashscope",
    key_env="DASHSCOPE_API_KEY",
    verified=True,
)

#: 系列 2：Qwen3-ASR 实时识别（原 ``stt.qwen`` 的默认模型）。
DASHSCOPE_QWEN3_ASR = VoiceProviderMeta(
    vendor="dashscope",
    series="qwen3-asr",
    label="DashScope 百炼（Qwen3-ASR 实时识别）",
    ws_url=_DASHSCOPE_WS_URL,
    stt_models=("qwen3-asr-flash-realtime",),
    modalities=("text",),
    turn_detection_supported=("server_vad",),
    vad_threshold_range=(0.0, 1.0),
    session_update_semantic="sparse",
    input_sample_rate=16000,
    output_sample_rate=0,
    supports_hotwords=True,
    requires_sdk="dashscope",
    key_env="DASHSCOPE_API_KEY",
    verified=True,
)

#: 全部条目。新增厂商只加一行（与 LLM 注册表同一范式）。
VOICE_REGISTRY: dict[tuple[str, str], VoiceProviderMeta] = {
    meta.key: meta for meta in (DASHSCOPE_QWEN_AUDIO, DASHSCOPE_QWEN3_ASR)
}


def vendor_label(vendor: str) -> str:
    """厂商展示名（未知厂商原样返回，便于报错时说清是哪个值）。"""
    return VENDOR_LABELS.get(vendor, vendor or "<空>")


def resolve_vendor(settings: Any, *, kind: str = "realtime") -> str:
    """取本机生效的语音厂商。

    解析顺序（对应计划 §1.1）：更细粒度覆盖 > ``voice_vendor`` > 默认值。
    阶段一只有 ``voice_vendor`` 一个配置项；``voice_stt_vendor`` /
    ``voice_tts_vendor`` 仅在代码里预留，不写进 Settings——将来加第二个 TTS
    厂商时不必改 ``voice_vendor`` 的含义。

    Args:
        settings: 任意带 ``voice_vendor`` 属性的对象；None 取默认值。
        kind: ``realtime`` / ``stt`` / ``tts``，只影响细粒度覆盖键名。

    @author aceFelix
    """
    if settings is None:
        return DEFAULT_VENDOR
    override = str(getattr(settings, f"voice_{kind}_vendor", "") or "").strip()
    if override:
        return override.lower()
    base = str(getattr(settings, "voice_vendor", "") or "").strip()
    return (base or DEFAULT_VENDOR).lower()


def provider_for(vendor: str, *, kind: str = "realtime") -> VoiceProviderMeta:
    """按厂商与能力取系列条目；取不到或未核实一律 fail-closed。

    Args:
        vendor: 厂商标识（大小写不敏感）。
        kind: ``realtime`` / ``stt``；``tts`` 本阶段没有条目，会抛错。

    Raises:
        VoiceProviderError: 未知厂商、该厂商无此能力、或条目 ``verified=False``。

    @author aceFelix
    """
    name = (vendor or "").strip().lower()
    candidates = [
        meta for (v, _series), meta in VOICE_REGISTRY.items()
        if v == name and meta.has_kind(kind)
    ]
    if not candidates:
        raise VoiceProviderError(
            f"不支持的语音厂商/能力：vendor={name or '<空>'} kind={kind}；"
            f"已知条目：{sorted(VOICE_REGISTRY)}"
        )
    meta = candidates[0]
    if not meta.verified:
        raise VoiceProviderError(
            f"语音条目尚未一手核实，禁止启用：{meta.vendor}/{meta.series}；"
            "核实官方事件全集后再改 verified=true"
        )
    return meta


def resolve_provider(settings: Any, *, kind: str = "realtime") -> VoiceProviderMeta:
    """从 settings 解析出当前生效的系列条目（阶段一的统一入口）。"""
    return provider_for(resolve_vendor(settings, kind=kind), kind=kind)


def default_ws_url(vendor: str = DEFAULT_VENDOR) -> str:
    """该厂商 realtime 的默认端点（/talk 兜底用）。"""
    return provider_for(vendor, kind="realtime").ws_url


def default_voice(vendor: str = DEFAULT_VENDOR) -> str:
    """该厂商 realtime 的默认音色（/talk 兜底用）。"""
    return provider_for(vendor, kind="realtime").default_voice


def default_realtime_model(vendor: str = DEFAULT_VENDOR) -> str:
    """该厂商 realtime 的首选模型（/talk 兜底用）。"""
    return provider_for(vendor, kind="realtime").realtime_models[0]


def default_stt_model(vendor: str = DEFAULT_VENDOR) -> str:
    """该厂商 STT 的首选模型（/voice 兜底用）。"""
    return provider_for(vendor, kind="stt").stt_models[0]


def validate_turn_detection(meta: VoiceProviderMeta, mode: str) -> str:
    """轮次检测能力门：不支持则抛错，绝不静默替换。

    静默替换会让「配了 smart_turn 却跑 server_vad」变成看不见的行为差异，
    与仓库既有的「配置静默失效」陷阱同类。
    """
    value = str(mode or "").strip()
    if not value:
        return meta.turn_detection_supported[0]
    if value not in meta.turn_detection_supported:
        raise VoiceProviderError(
            f"{meta.label} 不支持 turn_detection={value}；"
            f"可选：{list(meta.turn_detection_supported)}"
        )
    return value


__all__ = [
    "DEFAULT_VENDOR",
    "DASHSCOPE_QWEN3_ASR",
    "DASHSCOPE_QWEN_AUDIO",
    "VOICE_REGISTRY",
    "VENDOR_LABELS",
    "VoiceProviderError",
    "VoiceProviderMeta",
    "default_realtime_model",
    "default_stt_model",
    "default_voice",
    "default_ws_url",
    "provider_for",
    "resolve_provider",
    "resolve_vendor",
    "validate_turn_detection",
    "vendor_label",
]
