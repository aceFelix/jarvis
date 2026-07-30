"""思考模式配置表 —— 策略化取代 if-else。

每个 LLM 厂商对"深度思考/思维链"的 API 参数格式各不相同：
- DashScope: ``enable_thinking=True/False`` + ``thinking_budget``
- DeepSeek: ``thinking={"type": "enabled"/"disabled"}`` + ``reasoning_effort``
- 智谱: ``thinking={"type": "enabled"/"disabled"}`` + ``reasoning_effort``
- OpenAI / Moonshot / MiniMax 等: 不支持，不发送任何参数

此前这些差异通过 if-else 分支硬编码在 stream() 中，
新增厂商需要改代码。现在用配置表驱动：新增厂商只需加一行配置。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ThinkingConfig:
    """单个厂商的思考模式参数构造规则。

    描述如何将 ``enable_thinking`` + ``thinking_budget`` 翻译为具体 API 参数。
    ``field`` 为空字符串表示该厂商不支持思考模式。

    @author aceFelix
    """

    # 参数域: "extra_body"（OpenAI extra_body）/ "top_level"（直接放请求顶层 kwargs）
    placement: str = "extra_body"
    # 参数字段名（如 "thinking", "enable_thinking"），空字符串 = 不支持
    field: str = ""
    # 开启时的值（True 或 {"type": "enabled"} 等）
    on_value: Any = True
    # 关闭时的值（False 或 {"type": "disabled"} 等）
    off_value: Any = False
    # 开启时额外在顶层注入的 reasoning_effort 值（如 "high"），None = 不发
    reasoning_effort: str | None = None
    # 开启时额外在 extra_body/top_level 注入的 thinking_budget 字段名
    budget_field: str | None = None

    @property
    def supported(self) -> bool:
        """该厂商是否支持思考模式参数注入。"""
        return bool(self.field)


# ── 配置表：厂商名 → 思考参数构造规则 ──
# 厂商名对应 OpenAIProvider._derive_name() 的返回值。
# 新增厂商只需在此表加一行，无需修改 stream() 等调用代码。
THINKING_CONFIGS: dict[str, ThinkingConfig] = {
    # 阿里云 DashScope —— OpenAI 兼容接口路径
    "dashscope": ThinkingConfig(
        placement="extra_body",
        field="enable_thinking",
        on_value=True,
        off_value=False,
        budget_field="thinking_budget",
    ),
    # DeepSeek 官方 API
    "deepseek": ThinkingConfig(
        placement="extra_body",
        field="thinking",
        on_value={"type": "enabled"},
        off_value={"type": "disabled"},
        reasoning_effort="high",
    ),
    # 智谱 BigModel —— OpenAI 兼容接口路径
    "zhipu": ThinkingConfig(
        placement="extra_body",
        field="thinking",
        on_value={"type": "enabled"},
        off_value={"type": "disabled"},
        reasoning_effort="high",
    ),
    # DashScope 原生 SDK 路径（top_level，供 DashScopeProvider 复用）
    "dashscope_sdk": ThinkingConfig(
        placement="top_level",
        field="enable_thinking",
        on_value=True,
        off_value=False,
        budget_field="thinking_budget",
    ),
    # 智谱原生 SDK 路径（top_level，供 ZaiProvider 复用）
    "zai_sdk": ThinkingConfig(
        placement="top_level",
        field="thinking",
        on_value={"type": "enabled"},
        off_value={"type": "disabled"},
        reasoning_effort="high",
    ),
}


def apply_thinking(
    request_kwargs: dict[str, Any],
    config: ThinkingConfig,
    thinking_on: bool,
    thinking_budget: int = 0,
) -> None:
    """根据 ThinkingConfig 将思考参数注入请求 kwargs。

    统一入口：传入 thinking_on 状态和 thinking_budget，
    函数根据 config.placement 决定参数去向。

    Args:
        request_kwargs: LLM API 调用的 kwargs dict（原地修改）
        config: 对应厂商的 ThinkingConfig
        thinking_on: 思考是否开启
        thinking_budget: 思考 token 预算（仅 DashScope 用）

    @author aceFelix
    """
    if not config.supported:
        return

    value = config.on_value if thinking_on else config.off_value

    if config.placement == "extra_body":
        extra = request_kwargs.setdefault("extra_body", {})
        extra[config.field] = value
        if thinking_on and config.budget_field and thinking_budget > 0:
            extra[config.budget_field] = thinking_budget
    elif config.placement == "top_level":
        request_kwargs[config.field] = value
        if thinking_on and config.budget_field and thinking_budget > 0:
            request_kwargs[config.budget_field] = thinking_budget

    if thinking_on and config.reasoning_effort:
        request_kwargs.setdefault("reasoning_effort", config.reasoning_effort)
