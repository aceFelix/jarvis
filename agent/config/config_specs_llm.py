"""第一批「模型与上下文」的可改配置项（12 项）。

为什么单独一个模块（2026-11）：

- `config_specs.py` 只剩 33 行余量就到 900 行门禁，12 项塞不进去；
- 这批字段**落盘位置不统一**：8 项在 `models.toml`（模型域），4 项在 `settings.toml`。
  把"哪个文件"写成表里的一列（`store`），比让调用方临时判断可靠——同一个口径问题
  在 `apply_mode` 上已经踩过一次（判错就是"以为改了其实没有"）。

**不要在这里写逻辑**：本模块只有数据。解析与校验在 `config_kinds.py`，落盘分派在
`config_store.py`。

@author aceFelix
"""

from __future__ import annotations

from agent.config.config_specs import (
    APPLY_LLM,
    APPLY_LIVE,
    STORE_MODELS,
    STORE_SETTINGS,
    TIER_CONFIRM,
    ConfigSpec,
)


def _provider_names() -> tuple[str, ...]:
    """提供商可选值：优先从注册表取，取不到回退已知集合。

    不在导入期强依赖注册表：配置层被 `--help`、文档生成等轻量入口导入时，
    不该因为 provider 模块的额外依赖（sdk 缺失）而整体起不来。
    """
    try:
        from agent.llm import provider_registry

        for attr in ("PROVIDERS", "PROVIDER_REGISTRY"):
            table = getattr(provider_registry, attr, None)
            if isinstance(table, dict) and table:
                return tuple(sorted(str(k) for k in table))
            if isinstance(table, (list, tuple)) and table:
                names = [getattr(x, "name", None) for x in table]
                if all(names):
                    return tuple(sorted(str(n) for n in names))
    except Exception:  # noqa: BLE001 - 见 docstring
        pass
    return ("anthropic", "dashscope", "deepseek", "mock", "openai_compatible", "zai", "zhipu")


_PROVIDERS = _provider_names()

#: 思考强度档位（与 thinking.py 的取值一致）
_THINKING_EFFORTS = ("low", "medium", "high")

LLM_SPECS: tuple[ConfigSpec, ...] = (
    # ---- 模型域（models.toml）----
    ConfigSpec(
        "provider", "llm", "provider", "provider", "enum",
        "当前模型提供商", "换一家就等于换一套模型与计费口径；换错会导致请求失败",
        tier=TIER_CONFIRM, apply_mode=APPLY_LLM, store=STORE_MODELS, choices=_PROVIDERS,
    ),
    ConfigSpec(
        "model", "llm", "model", "model", "str",
        "当前模型", "下一轮请求使用的模型名；写错会被端点拒绝",
        tier=TIER_CONFIRM, apply_mode=APPLY_LLM, store=STORE_MODELS,
    ),
    ConfigSpec(
        "max_tokens", "llm", "max_tokens", "max_tokens", "int",
        "单次回复上限", "一句话能写多长；调小会截断长回答，调大更贵",
        tier=TIER_CONFIRM, apply_mode=APPLY_LLM, store=STORE_MODELS,
        lo=256, hi=384000, default=4096,
    ),
    ConfigSpec(
        "temperature", "llm", "temperature", "temperature", "float",
        "采样温度", "越低越稳、越高越发散；0 附近适合改代码，1 附近适合头脑风暴",
        tier=TIER_CONFIRM, apply_mode=APPLY_LIVE, store=STORE_MODELS, lo=0.0, hi=2.0,
    ),
    ConfigSpec(
        "enable_thinking", "llm", "enable_thinking", "enable_thinking", "bool",
        "是否启用思考", "关掉后不再输出思考过程，复杂任务会明显变差但更快更便宜",
        tier=TIER_CONFIRM, apply_mode=APPLY_LLM, store=STORE_MODELS, default=True,
    ),
    ConfigSpec(
        "thinking_budget", "llm", "thinking_budget", "thinking_budget", "int",
        "思考 token 预算", "想得越久越贵；超预算会被截断，表现为思路突然断掉",
        tier=TIER_CONFIRM, apply_mode=APPLY_LLM, store=STORE_MODELS,
        lo=0, hi=64000, default=2000,
    ),
    ConfigSpec(
        "models", "llm.models", "models", "models", "alias_map",
        "模型别名表", "给模型起中文名，/model 里就能用中文挑；整表替换，空串表示清空",
        tier=TIER_CONFIRM, apply_mode=APPLY_LLM, store=STORE_MODELS,
    ),
    ConfigSpec(
        "custom_models", "llm", "custom_models", "custom_models", "model_table",
        "自定义模型", "接一个不在内置清单里的模型；一次只增删一项，别整表换",
        tier=TIER_CONFIRM, apply_mode=APPLY_LLM, store=STORE_MODELS,
    ),
    # ---- 设置域（settings.toml）----
    # 注：`thinking_effort` 与 `default_provider` **本批不开**。实测它们既不在
    # settings_domains 的映射里，也不在 settings.example.toml 里——写了没人读，
    # 属于"改完以为生效"的典型。要开得先给它们一个真源（映射 + 示例 + 读取点）。
    ConfigSpec(
        "profile_refine_model", "memory", "profile_refine_model", "profile_refine_model", "str",
        "画像提炼用的模型", "偏好提炼通常交给便宜模型；留空表示跟随主模型",
        tier=TIER_CONFIRM, apply_mode=APPLY_LLM, store=STORE_SETTINGS,
    ),
    ConfigSpec(
        "profile_refine_provider", "memory", "profile_refine_provider",
        "profile_refine_provider", "enum",
        "画像提炼用的提供商", "与上一个成对；留空表示跟随主模型",
        tier=TIER_CONFIRM, apply_mode=APPLY_LLM, store=STORE_SETTINGS,
        choices=("",) + _PROVIDERS,
    ),
)
