"""模型管理工具单元测试。

覆盖 _infer_model_vendor / _infer_base_url 等纯函数。

@author aceFelix
"""

import pytest
from agent.model_manager import _infer_model_vendor, _infer_base_url


class TestInferModelVendor:
    """厂商推断测试。"""

    def test_qwen_prefix(self) -> None:
        assert _infer_model_vendor("qwen3.7-plus") == "dashscope"
        assert _infer_model_vendor("qwen-max") == "dashscope"

    def test_deepseek_prefix(self) -> None:
        assert _infer_model_vendor("deepseek-chat") == "deepseek"
        assert _infer_model_vendor("deepseek-v4-pro") == "deepseek"

    def test_glm_prefix(self) -> None:
        assert _infer_model_vendor("glm-4.7-flash") == "zhipu"
        assert _infer_model_vendor("glm-5.2") == "zhipu"

    def test_cfg_vendor_override(self) -> None:
        """自定义配置中的 vendor 字段优先。"""
        cfg = {"vendor": "custom_vendor"}
        assert _infer_model_vendor("qwen-unknown", cfg) == "custom_vendor"

    def test_cfg_empty_vendor_falls_back(self) -> None:
        """cfg.vendor 为空时回退到名前缀推断。"""
        assert _infer_model_vendor("qwen-test", {}) == "dashscope"
        assert _infer_model_vendor("qwen-test", {"vendor": ""}) == "dashscope"

    def test_unknown_model(self) -> None:
        assert _infer_model_vendor("unknown-model-xyz") == "other"

    def test_cfg_none(self) -> None:
        assert _infer_model_vendor("deepseek-chat", None) == "deepseek"


class TestInferBaseUrl:
    """Base URL 推断测试。"""

    def test_dashscope(self) -> None:
        url = _infer_base_url("dashscope", "openai")
        assert "dashscope" in url

    def test_deepseek(self) -> None:
        assert "deepseek.com" in _infer_base_url("deepseek", "openai")

    def test_zhipu(self) -> None:
        assert "bigmodel.cn" in _infer_base_url("zhipu", "openai")

    def test_zai_sdk_returns_empty(self) -> None:
        """智谱原生 SDK 不需要 base_url。"""
        assert _infer_base_url("zhipu", "zai") == ""
        assert _infer_base_url("dashscope", "dashscope") == ""

    def test_unknown_vendor(self) -> None:
        """未知厂商返回空字符串。"""
        assert _infer_base_url("unknown", "openai") == ""
