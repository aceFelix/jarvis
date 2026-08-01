"""TTS 音色目录 —— /tts-voice 命令的数据源。

内置 DashScope（阿里云百炼 CosyVoice v3）常用系统音色 + 用户自定义音色
（settings.custom_voices，持久化在 ~/.jarvis/settings.toml 的 [tts.custom_voices]）。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.config.settings import Settings


# ── 内置音色（DashScope CosyVoice v3，仅支持阿里云厂商）──
# name → {vendor, voice_id, description}
# 内置音色 name 即 voice_id（DashScope 请求参数）。
VOICE_CATALOG: dict[str, dict[str, str]] = {
    "longanlang_v3": {
        "vendor": "dashscope",
        "voice_id": "longanlang_v3",
        "description": "龙安朗（沉稳男声，默认）",
    },
    "longxiaochun_v3": {
        "vendor": "dashscope",
        "voice_id": "longxiaochun_v3",
        "description": "龙小淳（活泼女声）",
    },
    "longxiaoleng_v3": {
        "vendor": "dashscope",
        "voice_id": "longxiaoleng_v3",
        "description": "龙小冷（冷艳女声）",
    },
    "longcheng_v3": {
        "vendor": "dashscope",
        "voice_id": "longcheng_v3",
        "description": "龙城（成熟男声）",
    },
    "loongyuuna_v3": {
        "vendor": "dashscope",
        "voice_id": "loongyuuna_v3",
        "description": "Yuuna（日语女声）",
    },
    "Ono Anna": {
        "vendor": "dashscope",
        "voice_id": "Ono Anna",
        "description": "小野杏（日式漫画音）",
    },
    "loongriko_v3": {
        "vendor": "dashscope",
        "voice_id": "loongriko_v3",
        "description": "Riko（日语甜妹）",
    },
}


def all_tts_voices(settings: Any) -> dict[str, dict[str, str]]:
    """合并内置 + 自定义音色，返回 {name: {vendor, voice_id, description}}。

    Args:
        settings: Settings 实例（读取 custom_voices）。

    Returns:
        音色名 → 配置 dict。
    """
    voices: dict[str, dict[str, str]] = dict(VOICE_CATALOG)
    for cname, cfg in getattr(settings, "custom_voices", {}).items():
        if not isinstance(cfg, dict):
            continue
        voices[cname] = {
            "vendor": str(cfg.get("vendor", "dashscope")),
            "voice_id": str(cfg.get("voice_id", cname)),
            "description": str(cfg.get("description", "") or cname),
        }
    return voices


def resolve_voice_id(name: str, settings: Any) -> str | None:
    """把音色名（内置或自定义）解析为实际的 DashScope voice 参数。

    Args:
        name: 音色名或 voice_id。
        settings: Settings 实例。

    Returns:
        voice 参数值，未找到返回 None。
    """
    if not name:
        return None
    # 自定义音色优先（可用 name 或 voice_id 命中）
    for cname, cfg in getattr(settings, "custom_voices", {}).items():
        if not isinstance(cfg, dict):
            continue
        cid = str(cfg.get("voice_id", cname))
        if name == cname or name == cid or cname.lower().startswith(name.lower()):
            return cid
    # 内置音色
    for bname, bcfg in VOICE_CATALOG.items():
        if name == bname or bname.lower().startswith(name.lower()):
            return bcfg["voice_id"]
    return None
