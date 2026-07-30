"""思考模式配置表单元测试 — 验证 apply_thinking 参数注入。

测试覆盖:
- extra_body 注入（OpenAI 兼容接口路径）
- top_level 注入（DashScope SDK / 智谱 SDK 路径）
- 不支持思考的厂商不注入参数
- thinking_budget 注入（DashScope）
- reasoning_effort 注入（DeepSeek / 智谱）
- thinking_on/off 切换

@author aceFelix
"""

import pytest

from agent.llm.thinking import THINKING_CONFIGS, ThinkingConfig, apply_thinking


class TestApplyThinkingExtraBody:
    """extra_body 路径（OpenAI 兼容接口）测试。"""

    def test_dashscope_extra_body_thinking_on(self) -> None:
        """DashScope extra_body 路径：开启时注入 enable_thinking=True + thinking_budget。"""
        kwargs: dict = {}
        cfg = THINKING_CONFIGS["dashscope"]
        apply_thinking(kwargs, cfg, thinking_on=True, thinking_budget=2000)
        assert kwargs["extra_body"]["enable_thinking"] is True
        assert kwargs["extra_body"]["thinking_budget"] == 2000

    def test_dashscope_extra_body_thinking_off(self) -> None:
        """DashScope off 时应注入 enable_thinking=False。"""
        kwargs: dict = {}
        cfg = THINKING_CONFIGS["dashscope"]
        apply_thinking(kwargs, cfg, thinking_on=False, thinking_budget=2000)
        assert kwargs["extra_body"]["enable_thinking"] is False
        assert "thinking_budget" not in kwargs["extra_body"]

    def test_deepseek_extra_body_thinking_on(self) -> None:
        """DeepSeek extra_body：开启时注入 thinking.type + reasoning_effort。"""
        kwargs: dict = {}
        cfg = THINKING_CONFIGS["deepseek"]
        apply_thinking(kwargs, cfg, thinking_on=True)
        assert kwargs["extra_body"]["thinking"] == {"type": "enabled"}
        assert kwargs["reasoning_effort"] == "high"

    def test_deepseek_extra_body_thinking_off(self) -> None:
        kwargs: dict = {}
        cfg = THINKING_CONFIGS["deepseek"]
        apply_thinking(kwargs, cfg, thinking_on=False)
        assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in kwargs

    def test_zhipu_extra_body_thinking_on(self) -> None:
        """智谱 BigModel extra_body：开启时注入 thinking.type + reasoning_effort。"""
        kwargs: dict = {}
        cfg = THINKING_CONFIGS["zhipu"]
        apply_thinking(kwargs, cfg, thinking_on=True)
        assert kwargs["extra_body"]["thinking"] == {"type": "enabled"}
        assert kwargs["reasoning_effort"] == "high"


class TestApplyThinkingTopLevel:
    """top_level 路径（原生 SDK）测试。"""

    def test_dashscope_sdk_top_level_thinking_on(self) -> None:
        kwargs: dict = {}
        cfg = THINKING_CONFIGS["dashscope_sdk"]
        apply_thinking(kwargs, cfg, thinking_on=True, thinking_budget=1000)
        assert kwargs["enable_thinking"] is True
        assert kwargs["thinking_budget"] == 1000

    def test_dashscope_sdk_top_level_thinking_off(self) -> None:
        kwargs: dict = {}
        cfg = THINKING_CONFIGS["dashscope_sdk"]
        apply_thinking(kwargs, cfg, thinking_on=False)
        assert kwargs["enable_thinking"] is False
        assert "thinking_budget" not in kwargs

    def test_zai_sdk_top_level_thinking_on(self) -> None:
        kwargs: dict = {}
        cfg = THINKING_CONFIGS["zai_sdk"]
        apply_thinking(kwargs, cfg, thinking_on=True)
        assert kwargs["thinking"] == {"type": "enabled"}
        assert kwargs["reasoning_effort"] == "high"

    def test_zai_sdk_top_level_thinking_off(self) -> None:
        kwargs: dict = {}
        cfg = THINKING_CONFIGS["zai_sdk"]
        apply_thinking(kwargs, cfg, thinking_on=False)
        assert kwargs["thinking"] == {"type": "disabled"}


class TestNoThinkingSupport:
    """不支持思考的厂商测试。"""

    def test_unsupported_config_does_not_modify_kwargs(self) -> None:
        """field 为空的 Config 不应修改 kwargs。"""
        kwargs: dict = {"model": "test"}
        cfg = ThinkingConfig(placement="extra_body", field="")
        apply_thinking(kwargs, cfg, thinking_on=True)
        assert kwargs == {"model": "test"}  # 原样不变


class TestThinkingConfigsCompleteness:
    """配置表完整性测试。"""

    def test_all_configs_have_valid_placement(self) -> None:
        for name, cfg in THINKING_CONFIGS.items():
            assert cfg.placement in ("extra_body", "top_level"), (
                f"{name}: placement='{cfg.placement}' 无效"
            )

    def test_supported_configs_have_field(self) -> None:
        for name, cfg in THINKING_CONFIGS.items():
            if cfg.supported:
                assert cfg.field, f"{name}: supported=True 但 field 为空"

    def test_deepseek_and_zhipu_have_reasoning_effort(self) -> None:
        for key in ("deepseek", "zhipu", "zai_sdk"):
            assert THINKING_CONFIGS[key].reasoning_effort == "high"

    def test_dashscope_configs_have_budget_field(self) -> None:
        for key in ("dashscope", "dashscope_sdk"):
            assert THINKING_CONFIGS[key].budget_field == "thinking_budget"
