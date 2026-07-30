"""LLM 厂商元数据注册表 —— 配置表驱动的 Provider 管理。

A-04 改进项：将分散在 _build_provider / _derive_name 中的 if-else 收敛到一张表。
新增厂商只需在 PROVIDER_REGISTRY 加一行，无需修改任何 Provider 或工厂函数。

设计要点:
- ProviderMeta 描述一个厂商的类路径、构造参数、URL 检测规则、思考支持
- PROVIDER_REGISTRY 是唯一的厂商信息源
- create() 方法通过延迟导入避免循环依赖

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderMeta:
    """单个 LLM 厂商的完整元数据。

    @author aceFelix
    """

    # API 格式标识（对应 settings.api_format）
    api_format: str = ""
    # 显示名称（对应的 THINKING_CONFIGS key 也以此为准）
    name: str = ""
    # Provider 类的完整导入路径（"agent.llm.openai_provider.OpenAIProvider"）
    module_path: str = ""
    # 构造函数需要的 Settings 字段名（model_type 是特殊值，由调用方传入）
    init_keys: tuple[str, ...] = ()
    # base_url 子串匹配规则 → 用于 _derive_name 自动检测厂商
    url_patterns: tuple[str, ...] = ()
    # 思考模式支持（None = 不支持，对应 THINKING_CONFIGS 中的 key）
    thinking_key: str | None = None
    # 是否支持多模态（默认 True，纯文本模型为 "text"）
    model_type: str = "multimodal"

    def create(self, **kwargs: Any):
        """延迟导入 Provider 类并实例化。

        延迟导入避免 provider_registry.py 被 import 时触发
        所有 Provider 模块的加载（可能有重依赖如 openai / anthropic SDK）。

        Args:
            **kwargs: 匹配 init_keys 的关键字参数

        Returns:
            LLMProvider 实例

        @author aceFelix
        """
        parts = self.module_path.rsplit(".", 1)
        mod = __import__(parts[0], fromlist=[parts[1]])
        cls = getattr(mod, parts[1])
        return cls(**kwargs)


# ═══════════════════════════════════════════════════════════════
# 厂商注册表 —— 新增厂商只加这一行
# ═══════════════════════════════════════════════════════════════
PROVIDER_REGISTRY: dict[str, ProviderMeta] = {
    # ── 模拟（无后端）──
    "mock": ProviderMeta(
        api_format="mock",
        name="mock",
        module_path="agent.llm.mock.MockProvider",
    ),

    # ── Anthropic 原生 ──
    "anthropic": ProviderMeta(
        api_format="anthropic",
        name="anthropic",
        module_path="agent.llm.anthropic_provider.AnthropicProvider",
        init_keys=("api_key", "base_url"),
    ),

    # ── OpenAI 及兼容（统一走 OpenAIProvider）──
    "openai": ProviderMeta(
        api_format="openai",
        name="openai",
        module_path="agent.llm.openai_provider.OpenAIProvider",
        url_patterns=("api.openai.com",),
        init_keys=("api_key", "base_url", "model", "enable_thinking", "thinking_budget", "model_type"),
    ),

    "dashscope": ProviderMeta(
        api_format="dashscope",
        name="dashscope",
        module_path="agent.llm.openai_provider.OpenAIProvider",
        url_patterns=("dashscope",),
        init_keys=("api_key", "base_url", "model", "enable_thinking", "thinking_budget", "model_type"),
        thinking_key="dashscope",
    ),

    "deepseek": ProviderMeta(
        api_format="deepseek",
        name="deepseek",
        module_path="agent.llm.openai_provider.OpenAIProvider",
        url_patterns=("deepseek",),
        init_keys=("api_key", "base_url", "model", "enable_thinking", "thinking_budget", "model_type"),
        thinking_key="deepseek",
    ),

    "zhipu": ProviderMeta(
        api_format="zhipu",
        name="zhipu",
        module_path="agent.llm.openai_provider.OpenAIProvider",
        url_patterns=("open.bigmodel.cn",),
        init_keys=("api_key", "base_url", "model", "enable_thinking", "thinking_budget", "model_type"),
        thinking_key="zhipu",
    ),

    "moonshot": ProviderMeta(
        api_format="moonshot",
        name="moonshot",
        module_path="agent.llm.openai_provider.OpenAIProvider",
        url_patterns=("moonshot",),
        init_keys=("api_key", "base_url", "model", "enable_thinking", "thinking_budget", "model_type"),
    ),

    "minimax": ProviderMeta(
        api_format="minimax",
        name="minimax",
        module_path="agent.llm.openai_provider.OpenAIProvider",
        url_patterns=("minimax",),
        init_keys=("api_key", "base_url", "model", "enable_thinking", "thinking_budget", "model_type"),
    ),

    "xiaomimimo": ProviderMeta(
        api_format="xiaomimimo",
        name="xiaomimimo",
        module_path="agent.llm.openai_provider.OpenAIProvider",
        url_patterns=("xiaomimimo",),
        init_keys=("api_key", "base_url", "model", "enable_thinking", "thinking_budget", "model_type"),
    ),

    "google": ProviderMeta(
        api_format="google",
        name="google",
        module_path="agent.llm.openai_provider.OpenAIProvider",
        url_patterns=("generativelanguage", "googleapis"),
        init_keys=("api_key", "base_url", "model", "enable_thinking", "thinking_budget", "model_type"),
    ),

    "siliconflow": ProviderMeta(
        api_format="siliconflow",
        name="siliconflow",
        module_path="agent.llm.openai_provider.OpenAIProvider",
        url_patterns=("siliconflow",),
        init_keys=("api_key", "base_url", "model", "enable_thinking", "thinking_budget", "model_type"),
    ),

    # ── DashScope 原生 SDK ──
    "dashscope_sdk": ProviderMeta(
        api_format="dashscope_sdk",
        name="dashscope_sdk",
        module_path="agent.llm.dashscope_provider.DashScopeProvider",
        init_keys=("api_key", "base_url", "model", "enable_thinking", "thinking_budget", "model_type"),
        thinking_key="dashscope_sdk",
    ),

    # ── 智谱原生 SDK ──
    "zai": ProviderMeta(
        api_format="zai",
        name="zai",
        module_path="agent.llm.zai_provider.ZaiProvider",
        init_keys=("api_key", "base_url", "model", "enable_thinking", "thinking_budget", "model_type"),
        thinking_key="zai_sdk",
    ),
}

# openai_compatible 兜底（不在此表中，_derive_name 未匹配时返回）
_FALLBACK_NAME = "openai_compatible"


def lookup_by_url(base_url: str) -> str:
    """根据 base_url 自动检测厂商名。

    遍历 PROVIDER_REGISTRY，检查 url_patterns 子串匹配。
    用于替换 _derive_name 的 if-else 链。

    Args:
        base_url: API 端点 URL

    Returns:
        厂商名（如 "deepseek"），未匹配返回 "openai_compatible"

    @author aceFelix
    """
    if not base_url:
        return "openai"
    url_lower = base_url.lower()
    for name, meta in PROVIDER_REGISTRY.items():
        if any(p in url_lower for p in meta.url_patterns):
            return name
    return _FALLBACK_NAME


def lookup_thinking_key(vendor_name: str) -> str | None:
    """根据厂商名查找对应的 ThinkingConfig key。

    Args:
        vendor_name: 厂商名

    Returns:
        THINKING_CONFIGS 的 key，不支持思考返回 None

    @author aceFelix
    """
    meta = PROVIDER_REGISTRY.get(vendor_name)
    if meta and meta.thinking_key:
        return meta.thinking_key
    return None
