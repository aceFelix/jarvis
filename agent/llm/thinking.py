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

    描述如何将 ``enable_thinking`` + ``thinking_budget`` / 统一强度档位翻译为
    具体 API 参数。``field`` 为空字符串表示该厂商不支持思考模式。

    ``effort_map`` / ``budget_map`` 把桌面/终端统一的强度档位（low/medium/high）
    翻译成各厂商原生取值：前者映射顶层 ``reasoning_effort``（DeepSeek/Kimi/GLM），
    后者映射 ``thinking_budget``（Qwen/DashScope）。二者皆空 = 该厂商只支持开/关。

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
    # 统一档位 low/medium/high → 该厂商 reasoning_effort 取值（无则回退 reasoning_effort 默认）
    effort_map: dict[str, str] | None = None
    # 统一档位 low/medium/high → 该厂商 thinking_budget 数值（无则回退入参 thinking_budget）
    budget_map: dict[str, int] | None = None

    @property
    def supported(self) -> bool:
        """该厂商是否支持思考模式参数注入。"""
        return bool(self.field)

    @property
    def has_levels(self) -> bool:
        """该厂商是否支持强度档位（而非仅开/关）。"""
        return bool(self.effort_map or self.budget_map)


# ── 配置表：厂商名 → 思考参数构造规则 ──
# 厂商名对应 OpenAIProvider._derive_name() 的返回值。
# 新增厂商只需在此表加一行，无需修改 stream() 等调用代码。
THINKING_CONFIGS: dict[str, ThinkingConfig] = {
    # 阿里云 DashScope —— OpenAI 兼容接口路径（Qwen：enable_thinking + thinking_budget）
    "dashscope": ThinkingConfig(
        placement="extra_body",
        field="enable_thinking",
        on_value=True,
        off_value=False,
        budget_field="thinking_budget",
        budget_map={"low": 512, "medium": 2000, "high": 8000},
    ),
    # DeepSeek 官方 API（thinking.type + reasoning_effort: low/high/max，无 medium）
    "deepseek": ThinkingConfig(
        placement="extra_body",
        field="thinking",
        on_value={"type": "enabled"},
        off_value={"type": "disabled"},
        reasoning_effort="high",
        effort_map={"low": "low", "medium": "high", "high": "max"},
    ),
    # 智谱 BigModel —— OpenAI 兼容接口路径（thinking.type + reasoning_effort: low/medium/high）
    "zhipu": ThinkingConfig(
        placement="extra_body",
        field="thinking",
        on_value={"type": "enabled"},
        off_value={"type": "disabled"},
        reasoning_effort="high",
        effort_map={"low": "low", "medium": "medium", "high": "high"},
    ),
    # DashScope 原生 SDK 路径（top_level，供 DashScopeProvider 复用）
    "dashscope_sdk": ThinkingConfig(
        placement="top_level",
        field="enable_thinking",
        on_value=True,
        off_value=False,
        budget_field="thinking_budget",
        budget_map={"low": 512, "medium": 2000, "high": 8000},
    ),
    # 智谱原生 SDK 路径（top_level，供 ZaiProvider 复用）
    "zai_sdk": ThinkingConfig(
        placement="top_level",
        field="thinking",
        on_value={"type": "enabled"},
        off_value={"type": "disabled"},
        reasoning_effort="high",
        effort_map={"low": "low", "medium": "medium", "high": "high"},
    ),
    # 小米 MiMo —— OpenAI 兼容（thinking.type 开/关，无强度档位）
    "xiaomimimo": ThinkingConfig(
        placement="extra_body",
        field="thinking",
        on_value={"type": "enabled"},
        off_value={"type": "disabled"},
    ),
    # Kimi / Moonshot —— k3 顶层 reasoning_effort(low/high/max) + thinking.type(k2.5/2.6)
    "moonshot": ThinkingConfig(
        placement="extra_body",
        field="thinking",
        on_value={"type": "enabled"},
        off_value={"type": "disabled"},
        effort_map={"low": "low", "medium": "high", "high": "max"},
    ),
}


def apply_thinking(
    request_kwargs: dict[str, Any],
    config: ThinkingConfig,
    thinking_on: bool,
    thinking_budget: int = 0,
    effort: str | None = None,
) -> None:
    """根据 ThinkingConfig 将思考参数注入请求 kwargs。

    统一入口：传入 thinking_on 状态、thinking_budget 与可选强度档位 effort，
    函数根据 config.placement 决定字段去向，并把统一档位翻译成厂商原生取值。

    Args:
        request_kwargs: LLM API 调用的 kwargs dict（原地修改）
        config: 对应厂商的 ThinkingConfig
        thinking_on: 思考是否开启
        thinking_budget: 思考 token 预算（向后兼容入参，无 effort 档位时回退用）
        effort: 统一强度档位 low/medium/high（None/on 表示用厂商默认）；
                有 budget_map 时翻译成 thinking_budget，有 effort_map 时翻译成 reasoning_effort

    @author aceFelix
    """
    if not config.supported:
        return

    value = config.on_value if thinking_on else config.off_value

    # 开关字段按 placement 落位：extra_body 域写进 extra_body，否则写顶层
    if config.placement == "extra_body":
        target: dict[str, Any] = request_kwargs.setdefault("extra_body", {})
    else:
        target = request_kwargs
    target[config.field] = value

    if not thinking_on:
        return

    # thinking_budget：优先按档位映射，档位缺失时回退旧入参（向后兼容）
    if config.budget_field:
        budget: int | None = None
        if effort and config.budget_map:
            budget = config.budget_map.get(effort)
        if budget is None and thinking_budget and thinking_budget > 0:
            budget = thinking_budget
        if budget:
            target[config.budget_field] = budget

    # reasoning_effort：优先按档位映射，否则回退 config 默认（如 DeepSeek 恒 high）。
    # 无论 placement 如何，reasoning_effort 恒为顶层参数（OpenAI/DeepSeek/Kimi 兼容协议）。
    effort_val: str | None = None
    if effort and config.effort_map:
        effort_val = config.effort_map.get(effort)
    if effort_val is None:
        effort_val = config.reasoning_effort
    if effort_val:
        request_kwargs.setdefault("reasoning_effort", effort_val)


def supported_efforts(vendor_key: str) -> list[str]:
    """返回该厂商可选的思考档位（含 "off"）。

    - 未注册/不支持思考 → 空列表（前端选择器置灰）；
    - 仅开/关 → ["off", "on"]；
    - 支持强度 → ["off", "low", "medium", "high"]。

    供 serve state.get 告知桌面壳当前模型能选哪些档，避免展示无效选项。

    @author aceFelix
    """
    cfg = THINKING_CONFIGS.get(vendor_key)
    if not cfg or not cfg.supported:
        return []
    if cfg.has_levels:
        return ["off", "low", "medium", "high"]
    return ["off", "on"]
