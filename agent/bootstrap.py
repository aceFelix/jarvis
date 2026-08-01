"""装配工厂 —— 从 settings 构建 provider / checker / recovery / context。

从 main 拆出，供 main、daemon、query_loop、model_manager 等模块共用，
避免"只想用 _build_provider 却要 import 整个 main"的重型依赖。
"""

from __future__ import annotations

from typing import Any

from agent.config.settings import Settings
from agent.core.context import ToolContext
from agent.core.message import Message
from agent.permissions import PermissionChecker
from agent.permissions.rules import RuleSet, load_rules
from agent.ui.cli import RichCLI


def _model_type_for(settings: Settings, model_name: str | None = None) -> str:
    """根据模型名推断 model_type（multimodal / text）。

    自定义模型优先读取其 model_type 配置；内置模型默认 multimodal。

    @author aceFelix
    """
    name = model_name or settings.model or ""
    if name in settings.custom_models:
        return settings.custom_models[name].get("model_type", "multimodal")
    return "multimodal"


def _build_provider(settings: Settings, model_type: str = "multimodal"):
    """根据 settings 构造 LLM provider —— 配置表驱动。

    A-04 改进：原 if-else 链已被 PROVIDER_REGISTRY 取代。
    新增厂商只需在 provider_registry.py 加一行 ProviderMeta，
    无需修改此函数。

    @author aceFelix
    """
    from agent.llm.provider_registry import PROVIDER_REGISTRY

    fmt = settings.api_format.lower()
    meta = PROVIDER_REGISTRY.get(fmt)
    if not meta:
        raise ValueError(
            f"未知 provider: {fmt}"
            f"（可选: {', '.join(PROVIDER_REGISTRY.keys())}）"
        )

    # 从 settings 收集构造参数（model_type 是调用方传入的非 settings 字段）
    kwargs: dict[str, Any] = {}
    # 字符串字段：空字符串 → None（api_key/base_url/model）
    _STR_KEYS = frozenset({"api_key", "base_url", "model"})
    for key in meta.init_keys:
        if key == "model_type":
            kwargs[key] = model_type
        else:
            val = getattr(settings, key, None)
            kwargs[key] = val or None if key in _STR_KEYS else val

    return meta.create(**kwargs)


def _build_checker(settings: Settings) -> PermissionChecker:
    """构造权限校验器。

    @author aceFelix
    """
    rules = RuleSet()
    if settings.permissions_file:
        loaded = load_rules(settings.permissions_file)
        rules = rules.merge(loaded)
    return PermissionChecker(rules=rules, mode=settings.permission_mode)


def _build_recovery_executor(settings: Settings) -> "ToolRecoveryExecutor":
    """构造工具错误自愈执行器。

    @author aceFelix
    """
    from agent.core.error_recovery import ToolRecoveryExecutor

    return ToolRecoveryExecutor(global_enabled=settings.enable_tool_self_healing)


def _build_context(settings: Settings, ui: RichCLI, messages: list[Message]) -> ToolContext:
    """构造工具执行上下文。

    @author aceFelix
    """
    return ToolContext(
        workdir=settings.workdir,
        messages=messages,
        permission_mode=settings.permission_mode.value,
        ui=ui,
        settings=settings,
    )
