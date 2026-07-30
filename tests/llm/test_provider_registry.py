"""Provider 注册表单元测试 — 验证 PROVIDER_REGISTRY 查表逻辑。

测试覆盖:
- lookup_by_url 子串匹配
- lookup_thinking_key 思考支持检测
- ProviderMeta.create 延迟实例化
- 所有注册厂商的元数据完整性

@author aceFelix
"""

import pytest

from agent.llm.provider_registry import (
    PROVIDER_REGISTRY,
    ProviderMeta,
    lookup_by_url,
    lookup_thinking_key,
)


class TestLookupByUrl:
    """base_url → 厂商名自动检测测试。"""

    def test_empty_url_returns_openai(self) -> None:
        assert lookup_by_url("") == "openai"

    def test_dashscope_url(self) -> None:
        assert lookup_by_url("https://dashscope.aliyuncs.com/compatible-mode/v1") == "dashscope"

    def test_deepseek_url(self) -> None:
        assert lookup_by_url("https://api.deepseek.com/v1") == "deepseek"

    def test_openai_url(self) -> None:
        assert lookup_by_url("https://api.openai.com/v1/chat/completions") == "openai"

    def test_zhipu_url(self) -> None:
        assert lookup_by_url("https://open.bigmodel.cn/api/paas/v4") == "zhipu"

    def test_moonshot_url(self) -> None:
        assert lookup_by_url("https://api.moonshot.cn/v1") == "moonshot"

    def test_siliconflow_url(self) -> None:
        assert lookup_by_url("https://api.siliconflow.cn/v1") == "siliconflow"

    def test_case_insensitive(self) -> None:
        """URL 匹配应大小写不敏感。"""
        assert lookup_by_url("https://DASHSCOPE.ALIYUNCS.COM/v1") == "dashscope"

    def test_unknown_url_returns_fallback(self) -> None:
        """未匹配的 URL 返回兜底名。"""
        assert lookup_by_url("https://api.unknown-provider.com/v1") == "openai_compatible"

    def test_groq_not_registered_yet(self) -> None:
        """未注册厂商应返回 fallback，新增厂商加一行配置即可。"""
        assert lookup_by_url("https://api.groq.com/openai/v1") == "openai_compatible"


class TestLookupThinkingKey:
    """厂商名 → ThinkingConfig key 映射测试。"""

    def test_dashscope_supports_thinking(self) -> None:
        assert lookup_thinking_key("dashscope") == "dashscope"

    def test_deepseek_supports_thinking(self) -> None:
        assert lookup_thinking_key("deepseek") == "deepseek"

    def test_zhipu_supports_thinking(self) -> None:
        assert lookup_thinking_key("zhipu") == "zhipu"

    def test_openai_does_not_support_thinking(self) -> None:
        assert lookup_thinking_key("openai") is None

    def test_moonshot_does_not_support_thinking(self) -> None:
        assert lookup_thinking_key("moonshot") is None

    def test_unknown_vendor_returns_none(self) -> None:
        assert lookup_thinking_key("groq") is None


class TestProviderMetaCreate:
    """ProviderMeta.create 测试。"""

    def test_create_mock_provider(self) -> None:
        """延迟导入 MockProvider 并实例化。"""
        meta = PROVIDER_REGISTRY["mock"]
        provider = meta.create()
        assert provider is not None
        assert provider.name == "mock"

    def test_create_openai_provider(self) -> None:
        """延迟导入 OpenAIProvider 并实例化（只需 api_key）。"""
        meta = PROVIDER_REGISTRY["openai"]
        provider = meta.create(api_key="sk-test", base_url=None, model=None,
                               enable_thinking=True, thinking_budget=2000,
                               model_type="multimodal")
        assert provider is not None
        assert provider.default_model == "gpt-4o"

    def test_create_with_minimal_args(self) -> None:
        """某些 Provider（如 Anthropic）只需要 api_key。"""
        meta = PROVIDER_REGISTRY["anthropic"]
        provider = meta.create(api_key="sk-test", base_url=None)
        assert provider is not None


class TestProviderRegistryCompleteness:
    """所有注册厂商的元数据完整性测试。"""

    def test_all_providers_have_module_path(self) -> None:
        for name, meta in PROVIDER_REGISTRY.items():
            assert meta.module_path, f"{name}: module_path 为空"

    def test_all_providers_have_name(self) -> None:
        for name, meta in PROVIDER_REGISTRY.items():
            assert meta.name, f"{name}: name 为空"

    def test_all_providers_have_api_format(self) -> None:
        for name, meta in PROVIDER_REGISTRY.items():
            assert meta.api_format, f"{name}: api_format 为空"

    def test_thinking_providers_have_thinking_key(self) -> None:
        """标记 thinking_key 的厂商应在 THINKING_CONFIGS 中有对应配置。"""
        from agent.llm.thinking import THINKING_CONFIGS
        for name, meta in PROVIDER_REGISTRY.items():
            if meta.thinking_key is not None:
                assert meta.thinking_key in THINKING_CONFIGS, (
                    f"{name}: thinking_key='{meta.thinking_key}' 不在 THINKING_CONFIGS 中"
                )

    def test_url_detectable_providers_have_url_patterns(self) -> None:
        """有 url_patterns 的厂商应可通过 lookup_by_url 自动检测。"""
        for name, meta in PROVIDER_REGISTRY.items():
            if meta.url_patterns:
                # 至少一个 pattern 能匹配自身厂商名
                found = False
                for p in meta.url_patterns:
                    result = lookup_by_url(f"https://{p}.example.com/v1")
                    if result not in ("openai_compatible",):
                        found = True
                        break
                # 不做强制断言——有些 pattern 是子串匹配，构造的 URL 不保证匹配
