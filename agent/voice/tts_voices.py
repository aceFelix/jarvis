"""TTS 音色目录 —— /tts-voice 命令的数据源。

内置 DashScope（阿里云百炼 CosyVoice v3）常用系统音色 + 用户自定义音色
（settings.custom_voices，持久化在 ~/.jarvis/settings.toml 的 [tts.custom_voices]）。

音色与模型是硬约束：DashScope 系统音色按模型系列隔离（_v2 音色只能配
cosyvoice-v2，_v3 音色配 cosyvoice-v3-* 系列），声音复刻的 voice_id 更绑定
创建时指定的 target_model。故每个音色带 model 字段（适配模型，支持逗号分隔
多值与家族前缀），切换音色时据此联动校正 settings.tts_model（见
voice_model_matches / aligned_tts_model）。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.config.settings import Settings


# ── 内置音色（DashScope CosyVoice v3，仅支持阿里云厂商）──
# name → {vendor, voice_id, model, description}
# 内置音色 name 即 voice_id（DashScope 请求参数）。
# model="cosyvoice-v3" 为家族前缀：v3-flash / v3-plus / v3.5-plus 均可用
#（家族边界见 voice_model_matches：前缀后须紧跟 '-' 或 '.'）。
VOICE_CATALOG: dict[str, dict[str, str]] = {
    "longanlang_v3": {
        "vendor": "dashscope",
        "voice_id": "longanlang_v3",
        "model": "cosyvoice-v3",
        "description": "龙安朗（沉稳男声，默认）",
    },
    "longxiaochun_v3": {
        "vendor": "dashscope",
        "voice_id": "longxiaochun_v3",
        "model": "cosyvoice-v3",
        "description": "龙小淳（活泼女声）",
    },
    "longxiaoleng_v3": {
        "vendor": "dashscope",
        "voice_id": "longxiaoleng_v3",
        "model": "cosyvoice-v3",
        "description": "龙小冷（冷艳女声）",
    },
    "longcheng_v3": {
        "vendor": "dashscope",
        "voice_id": "longcheng_v3",
        "model": "cosyvoice-v3",
        "description": "龙城（成熟男声）",
    },
    "loongyuuna_v3": {
        "vendor": "dashscope",
        "voice_id": "loongyuuna_v3",
        "model": "cosyvoice-v3",
        "description": "Yuuna（日语女声）",
    },
    "Ono Anna": {
        "vendor": "dashscope",
        "voice_id": "Ono Anna",
        "model": "cosyvoice-v3",
        "description": "小野杏（日式漫画音）",
    },
    "loongriko_v3": {
        "vendor": "dashscope",
        "voice_id": "loongriko_v3",
        "model": "cosyvoice-v3",
        "description": "Riko（日语甜妹）",
    },
}

# 家族前缀 → 联动切换时的默认具体模型（前缀本身不是合法 API 模型名，
# 需落到家族内最通用的一个）。
FAMILY_DEFAULT_MODELS: dict[str, str] = {
    "cosyvoice-v3": "cosyvoice-v3-flash",
}


def all_tts_voices(settings: Any) -> dict[str, dict[str, str]]:
    """合并内置 + 自定义音色，返回 {name: {vendor, voice_id, model, description}}。

    Args:
        settings: Settings 实例（读取 custom_voices）。

    Returns:
        音色名 → 配置 dict（model 为空串表示不限适配模型）。
    """
    voices: dict[str, dict[str, str]] = {
        n: dict(cfg) for n, cfg in VOICE_CATALOG.items()
    }
    for cname, cfg in getattr(settings, "custom_voices", {}).items():
        if not isinstance(cfg, dict):
            continue
        voices[cname] = {
            "vendor": str(cfg.get("vendor", "dashscope")),
            "voice_id": str(cfg.get("voice_id", cname)),
            "model": str(cfg.get("model", "")),
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


def voice_model_matches(voice_model: str, current_model: str) -> bool:
    """判断音色适配模型与当前 TTS 模型是否兼容。

    匹配规则（音色 model 字段支持逗号分隔多值）：
    - 空值 = 不限适配模型，恒兼容；
    - 精确相等；
    - 家族前缀：token 是 current 的前缀且紧跟 '-' 或 '.'
      （如 "cosyvoice-v3" 匹配 "cosyvoice-v3-flash"/"cosyvoice-v3.5-plus"，
      但不会误匹 "cosyvoice-v30"）。

    Args:
        voice_model: 音色适配模型（家族前缀或具体模型，逗号分隔多值）。
        current_model: 当前 settings.tts_model。

    Returns:
        True 表示可直接用该音色合成，无需改模型。

    @author aceFelix
    """
    if not voice_model:
        return True
    for token in str(voice_model).split(","):
        token = token.strip()
        if not token:
            continue
        if token == current_model:
            return True
        if current_model.startswith(token) and len(current_model) > len(token) \
                and current_model[len(token)] in ("-", "."):
            return True
    return False


def aligned_tts_model(voice_model: str, current_model: str) -> str | None:
    """切换音色时计算需要联动切换的 TTS 模型。

    不兼容时优先落到第一个具体模型 token；若是家族前缀（非合法 API 模型名）
    则经 FAMILY_DEFAULT_MODELS 映射到家族内默认具体模型。

    Args:
        voice_model: 音色适配模型（空 = 不限，不联动）。
        current_model: 当前 settings.tts_model。

    Returns:
        需要切换到的模型名；无需切换返回 None。

    @author aceFelix
    """
    if voice_model_matches(voice_model, current_model):
        return None
    tokens = [t.strip() for t in str(voice_model).split(",") if t.strip()]
    if not tokens:
        return None
    target = tokens[0]
    return FAMILY_DEFAULT_MODELS.get(target, target)


def find_voice(name: str, settings: Any) -> tuple[str, dict[str, str]] | None:
    """按音色名或 voice_id 精确查找目录项（内置 + 自定义）。

    Args:
        name: 音色名或 voice_id。
        settings: Settings 实例。

    Returns:
        (音色名, cfg) 元组；未找到返回 None。

    @author aceFelix
    """
    if not name:
        return None
    voices = all_tts_voices(settings)
    if name in voices:
        return name, voices[name]
    for vname, cfg in voices.items():
        if cfg["voice_id"] == name:
            return vname, cfg
    return None


def apply_voice_switch(settings: Any, name: str) -> dict[str, Any] | None:
    """切换 TTS 音色：模型联动 + 双字段持久化（传输/UI 无关公共实现）。

    终端 /tts-voice、pywebview 工作台与 serve 桌面壳（voices.select）共用，
    避免三处各写一遍音色-模型硬约束逻辑。调用方只负责把自己的 UI 反馈
    文案拼出来。持久化失败不阻断切换（内存值仍生效，重启后回退旧值）。

    Args:
        settings: Settings 实例（改 tts_voice/tts_model）。
        name: 音色名或 voice_id（需在目录内，前缀匹配由调用方先解析）。

    Returns:
        {name, voice_id, linked_model, old_model}；未联动时 linked_model 为
        None、old_model 为当前 tts_model；音色未找到返回 None。

    @author aceFelix
    """
    entry = find_voice(name, settings)
    if entry is None:
        return None
    vname, cfg = entry
    settings.tts_voice = cfg["voice_id"]
    try:
        from agent.config.model_registry import save_tts_voice
        save_tts_voice(cfg["voice_id"])
    except Exception:
        pass
    old_model = settings.tts_model
    linked = aligned_tts_model(cfg.get("model", ""), old_model)
    if linked:
        settings.tts_model = linked
        try:
            from agent.config.model_registry import save_tts_model
            save_tts_model(linked)
        except Exception:
            pass
    return {
        "name": vname,
        "voice_id": cfg["voice_id"],
        "linked_model": linked,
        "old_model": old_model,
    }
