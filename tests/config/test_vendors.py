"""厂商默认表（`agent/config/vendors.py`）单元测试。

核心契约：**同一份厂商默认值只能有一处**。历史上 `/init` 与 `/models` 各写一份，
漂移出「DeepSeek 少了 `/v1`、小米 MiMo 只在一边有」，于是「首启配的能连通、
`/models` 加的同厂商模型连不通」。

@author aceFelix
"""

from __future__ import annotations

from agent.config import vendors

_REQUIRED_KEYS = (
    "key", "name", "desc", "default_model",
    "default_base_url", "api_format", "api_format_desc",
)


class TestTable:
    def test_required_keys_and_unique_keys(self) -> None:
        keys = [v["key"] for v in vendors.VENDORS]
        assert len(keys) == len(set(keys))
        for v in vendors.VENDORS:
            for k in _REQUIRED_KEYS:
                assert k in v, (v.get("key"), k)

    def test_api_format_is_known(self) -> None:
        """api_format 决定用哪个 Provider 类，写错会直接抛「未知 provider」。"""
        from agent.llm.provider_registry import PROVIDER_REGISTRY

        for v in vendors.VENDORS:
            assert v["api_format"] in PROVIDER_REGISTRY, v["key"]

    def test_init_command_uses_the_same_table(self) -> None:
        """`/init` 不能再有自己那一份（同一份对象，不是复制）。"""
        from agent.commands.handlers import init_command

        assert init_command._VENDORS is vendors.VENDORS


class TestLookup:
    def test_find_known_and_unknown(self) -> None:
        assert vendors.find("deepseek")["name"] == "DeepSeek"
        assert vendors.find("no-such-vendor") is None

    def test_other_alias_maps_to_custom(self) -> None:
        """/models 表单里自建服务写 `other`，首启表里叫 `custom`。"""
        assert vendors.find("other") is vendors.find("custom")


class TestDefaultBaseUrl:
    def test_deepseek_includes_v1(self) -> None:
        """DeepSeek 的 OpenAI 兼容端点在 `/v1`，少了就 404/连不通。"""
        assert vendors.default_base_url("deepseek", "openai") == "https://api.deepseek.com/v1"

    def test_sdk_managed_formats_return_empty(self) -> None:
        """DashScope / 智谱原生 SDK 自己管端点，给地址反而是干扰。"""
        assert vendors.default_base_url("dashscope", "dashscope") == ""
        assert vendors.default_base_url("zhipu", "zai") == ""

    def test_unknown_and_custom_are_empty(self) -> None:
        """没默认值就必须问用户，不能编一个。"""
        assert vendors.default_base_url("no-such-vendor", "openai") == ""
        assert vendors.default_base_url("custom", "openai") == ""
        assert vendors.default_base_url("other", "openai") == ""

    def test_api_format_case_insensitive(self) -> None:
        assert vendors.default_base_url("zhipu", " ZAI ") == ""


class TestDefaults:
    def test_default_model_and_api_format(self) -> None:
        assert vendors.default_model("deepseek") == "deepseek-flash"
        assert vendors.default_api_format("anthropic") == "anthropic"

    def test_unknown_falls_back_to_openai_compatible(self) -> None:
        assert vendors.default_model("no-such-vendor") == ""
        assert vendors.default_api_format("no-such-vendor") == "openai"
