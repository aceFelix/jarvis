"""环境变量覆盖。

把 ``JARVIS_*`` / ``MY_AGENT_*`` 环境变量映射到 Settings 字段。
从 settings.py 拆分出来，独立维护环境变量覆盖逻辑。

@author aceFelix
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.config.settings import Settings


def apply_env_overrides(s: "Settings") -> "Settings":
    """环境变量覆盖。JARVIS_PROVIDER / JARVIS_MODEL / 等（兼容 MY_AGENT_*）。

    @author aceFelix
    """
    updates: dict[str, object] = {}

    def _env(*names: str) -> str | None:
        for n in names:
            v = os.environ.get(n)
            if v:
                return v
        return None

    provider = _env("JARVIS_PROVIDER", "MY_AGENT_PROVIDER")
    if provider:
        updates["provider"] = provider
    model = _env("JARVIS_MODEL", "MY_AGENT_MODEL")
    if model:
        updates["model"] = model

    # 常见 LLM API key 环境变量直通
    # 顺序: 先认各家专属变量（DASHSCOPE_API_KEY / ANTHROPIC_API_KEY），
    # 再认通用变量（OPENAI_API_KEY / JARVIS_API_KEY / MY_AGENT_API_KEY）。
    # 实时语音/多模态等 DashScope 专属能力需要独立的 dashscope_api_key。
    dashscope_key = os.environ.get("DASHSCOPE_API_KEY")
    if dashscope_key and not s.dashscope_api_key:
        updates["dashscope_api_key"] = dashscope_key
    if not s.api_key:
        for key_env in (
            # 厂商专属变量（优先级最高）
            "DASHSCOPE_API_KEY",    # 阿里云百炼 DashScope
            "DEEPSEEK_API_KEY",     # DeepSeek
            "ZAI_API_KEY",          # 智谱 AI (GLM)
            "ANTHROPIC_API_KEY",    # Anthropic Claude
            "KIMI_API_KEY",         # Moonshot (Kimi)
            "MINIMAX_API_KEY",      # MiniMax
            "MIMO_API_KEY",         # 小米 MiMo
            "OPENAI_API_KEY",        # OpenAI 官方及兼容服务（通用兜底）
            "JARVIS_API_KEY",        # 通用兜底（新名）
            "MY_AGENT_API_KEY",      # 通用兜底（兼容旧名）
        ):
            kv = os.environ.get(key_env)
            if kv:
                updates["api_key"] = kv
                break
        # S-01: 环境变量也没找到 → 尝试系统 keyring（Windows 凭据管理器等）
        if "api_key" not in updates:
            keyring_key = _try_keyring(s.provider)
            if keyring_key:
                updates["api_key"] = keyring_key

    base_url = _env("JARVIS_BASE_URL", "MY_AGENT_BASE_URL")
    if base_url:
        updates["base_url"] = base_url

    mode = _env("JARVIS_PERMISSION_MODE", "MY_AGENT_PERMISSION_MODE")
    if mode:
        from agent.permissions.modes import parse_mode
        updates["permission_mode"] = parse_mode(mode)

    debug = _env("JARVIS_DEBUG", "MY_AGENT_DEBUG")
    if debug:
        updates["debug"] = debug.lower() in ("1", "true", "yes")

    boot = _env("JARVIS_BOOT_ANIMATION")
    if boot:
        updates["boot_animation"] = boot.lower() in ("1", "true", "yes")

    compaction = _env("JARVIS_CONTEXT_COMPACTION")
    if compaction:
        updates["context_compaction"] = compaction.lower() in ("1", "true", "yes")

    return s.with_overrides(**updates) if updates else s


def _try_keyring(provider: str) -> str | None:
    """S-01: 尝试从系统 keyring 读取 API Key。"""
    try:
        from agent.config.keyring_store import load_api_key
        return load_api_key(provider)
    except Exception:
        return None
