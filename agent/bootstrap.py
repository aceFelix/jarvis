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


def _build_provider(
    settings: Settings,
    model_type: str = "multimodal",
    *,
    dedicated_client: bool = False,
):
    """根据 settings 构造 LLM provider —— 配置表驱动。

    A-04 改进：原 if-else 链已被 PROVIDER_REGISTRY 取代。
    新增厂商只需在 provider_registry.py 加一行 ProviderMeta，
    无需修改此函数。

    Args:
        settings: 模型域配置
        model_type: 多模态能力（"text" / "multimodal"）
        dedicated_client: True 时给 provider 建**独占 HTTP 连接池**。
            后台任务（记忆画像提炼等）应当传 True：它们跑在独立线程里与
            主对话并发，共用同一连接池时，后台任务的流量与关闭会直接影响
            前台可用连接。仅 OpenAI 系 provider 支持，其余自动忽略。
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

    # 从 settings 收集构造参数（model_type 与 dedicated_client 是调用方传入的
    # 非 settings 字段，单独处理）
    kwargs: dict[str, Any] = {}
    # 字符串字段：空字符串 → None（api_key/base_url/model）
    _STR_KEYS = frozenset({"api_key", "base_url", "model"})
    for key in meta.init_keys:
        if key == "model_type":
            kwargs[key] = model_type
        elif key == "dedicated_client":
            kwargs[key] = dedicated_client
        else:
            val = getattr(settings, key, None)
            kwargs[key] = val or None if key in _STR_KEYS else val

    provider = meta.create(**kwargs)
    # 应用运行时思考强度档位（off/low/medium/high/on）：仅在思考开启时下发，
    # 避免把已关闭的思考又打开；关闭态由 enable_thinking 决定。@author aceFelix
    if getattr(settings, "enable_thinking", True):
        effort = getattr(settings, "thinking_effort", None)
        if effort:
            provider.set_thinking_effort(effort)
    return provider


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


def init_actions(settings: Settings) -> list:
    """装配副作用账本并作废上一次进程遗留的待发动作（宿主启动时调一次）。

    为什么需要“作废”而不是“补发”：延时窗口的意义在于用户此刻还能喊停，
    重启后没人在场，自动把一个外发不可逆动作发出去是最不该发生的事。
    所以遗留项一律转 cancelled 并交给调用方播报，账本上留得下痕迹。

    Args:
        settings: 已加载的配置（读 ``[actions]`` 字段）。

    Returns:
        被作废的待发动作列表（空=无遗留）。
    @author aceFelix
    """
    from agent.core.actions import configure_journal, get_journal, get_window, resume_pending

    configure_journal(enabled=bool(getattr(settings, "actions_journal_enabled", True)))
    stale = resume_pending(get_journal(), get_window())
    if stale:
        try:
            from agent.core.diag import diag_log

            diag_log("actions", f"作废上次遗留的待发动作 {len(stale)} 项")
        except Exception:
            pass  # 诊断日志挂了不影响启动与记账
    return stale


def _build_context(settings: Settings, ui: RichCLI, messages: list[Message]) -> ToolContext:
    """构造工具执行上下文。

    顺带按配置装配副作用账本单例（W3.2）：账本是进程级全局状态，放这里是因为
    全部宿主（repl / serve / daemon / model_manager）都经本函数，装配一处不漏挂。
    重建单例不会丢数据（台账在磁盘上），也不影响已登记的延时窗口。
    @author aceFelix
    """
    from agent.core.actions import configure_journal

    configure_journal(enabled=bool(getattr(settings, "actions_journal_enabled", True)))
    return ToolContext(
        workdir=settings.workdir,
        messages=messages,
        permission_mode=settings.permission_mode.value,
        ui=ui,
        settings=settings,
    )
