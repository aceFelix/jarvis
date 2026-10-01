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

from agent.llm.thinking import (
    THINKING_CONFIGS,
    ThinkingConfig,
    apply_thinking,
    supported_efforts,
)


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


class TestEffortLevels:
    """统一强度档位（low/medium/high）→ 厂商原生参数注入测试。"""

    def test_dashscope_budget_map_low(self) -> None:
        """DashScope effort=low → thinking_budget=512。"""
        kwargs: dict = {}
        apply_thinking(kwargs, THINKING_CONFIGS["dashscope"], thinking_on=True, effort="low")
        assert kwargs["extra_body"]["thinking_budget"] == 512

    def test_dashscope_budget_map_high_overrides_param(self) -> None:
        """有 effort 档位时 budget_map 优先于 thinking_budget 入参。"""
        kwargs: dict = {}
        apply_thinking(
            kwargs,
            THINKING_CONFIGS["dashscope"],
            thinking_on=True,
            thinking_budget=2000,
            effort="high",
        )
        assert kwargs["extra_body"]["thinking_budget"] == 8000

    def test_dashscope_no_effort_falls_back_to_budget_param(self) -> None:
        """无 effort 档位时回退旧 thinking_budget 入参（向后兼容）。"""
        kwargs: dict = {}
        apply_thinking(kwargs, THINKING_CONFIGS["dashscope"], thinking_on=True, thinking_budget=3000)
        assert kwargs["extra_body"]["thinking_budget"] == 3000

    def test_deepseek_effort_map(self) -> None:
        """DeepSeek effort 档位 → reasoning_effort(low/high/max)，medium 就近取 high。"""
        cases = {"low": "low", "medium": "high", "high": "max"}
        for effort, expected in cases.items():
            kwargs: dict = {}
            apply_thinking(kwargs, THINKING_CONFIGS["deepseek"], thinking_on=True, effort=effort)
            assert kwargs["reasoning_effort"] == expected, effort

    def test_zhipu_effort_map_three_levels(self) -> None:
        """智谱 effort 档位为原生 low/medium/high 三档。"""
        kwargs: dict = {}
        apply_thinking(kwargs, THINKING_CONFIGS["zhipu"], thinking_on=True, effort="medium")
        assert kwargs["reasoning_effort"] == "medium"

    def test_effort_off_still_writes_switch_only(self) -> None:
        """thinking_on=False 时短路：不注入 budget/reasoning_effort。"""
        kwargs: dict = {}
        apply_thinking(kwargs, THINKING_CONFIGS["deepseek"], thinking_on=False, effort="high")
        assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in kwargs


class TestNewVendorConfigs:
    """新增国产厂商（MiMo / Kimi）配置测试。"""

    def test_xiaomimimo_switch_only(self) -> None:
        """MiMo 仅开关（无强度档位）：has_levels=False，注入 thinking.type。"""
        cfg = THINKING_CONFIGS["xiaomimimo"]
        assert cfg.supported is True
        assert cfg.has_levels is False
        kwargs: dict = {}
        apply_thinking(kwargs, cfg, thinking_on=True, effort="high")
        assert kwargs["extra_body"]["thinking"] == {"type": "enabled"}
        # 无 effort_map/budget_map → 不应注入 reasoning_effort
        assert "reasoning_effort" not in kwargs

    def test_moonshot_effort_map(self) -> None:
        """Kimi/Moonshot effort 档位映射 low/high/max。"""
        kwargs: dict = {}
        apply_thinking(kwargs, THINKING_CONFIGS["moonshot"], thinking_on=True, effort="high")
        assert kwargs["reasoning_effort"] == "max"


class TestSupportedEfforts:
    """supported_efforts 档位枚举（供桌面选择器渲染）。"""

    def test_unsupported_vendor_empty(self) -> None:
        assert supported_efforts("openai") == []
        assert supported_efforts("not_a_vendor") == []

    def test_level_vendor(self) -> None:
        assert supported_efforts("dashscope") == ["off", "low", "medium", "high"]
        assert supported_efforts("deepseek") == ["off", "low", "medium", "high"]

    def test_switch_only_vendor(self) -> None:
        assert supported_efforts("xiaomimimo") == ["off", "on"]
