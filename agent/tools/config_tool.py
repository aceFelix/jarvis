"""ConfigList / ConfigSet 两个工具 —— 让模型自己看改配置的口子（阶段 12 P1）。

为什么需要这两个工具（aceFelix，2026-10-26）：配置域早就有"外科式落盘 + 白名单校验"
这一整套（`toml_writer` / `percept_settings` / `desktop_settings`），但那些口子都通向
**面板**——模型一个都用不上。于是用户想让 jarvis"把音量调小点"时，只能自己去改
`settings.toml`，这与管家该有的样子相反。

三条设计底线（都写进代码而不是只写在文档里）：

1. **分级即权限**：``tier=confirm`` 直接映射成权限管线的 ASK（改前问一句），
   ``tier=forbidden`` 直接 DENY。不在这里自造一套确认流程——W1 那套管线已经
   处理了「无人值守/沙箱/租约」各种边界，另起一套必然两处口径不一致。
2. **先校全部，再落盘**：交给 `config_ops.apply_config_patch`（与终端命令同一份）。
3. **不假装生效**：``apply_mode`` 需要重装配/重注册时，调宿主登记的钩子；
   没登记钩子（如终端 REPL 场景）就如实说"已保存，下次启动生效"。

本模块只负责**工具契约**（schema / 权限 / 结果包装），真正的读写六步在
`agent/config/config_ops.py`——那里同时被 `/config set` 调用（阶段 12 P2）。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.config.config_ops import (
    ENTRY_MODEL,
    apply_config_patch,
    render_spec_list,
)
from agent.config.config_specs import (
    TIER_AUTO,
    TIER_CONFIRM,
    forbidden_reason,
    get_spec,
)
from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool


class ConfigListTool(Tool):
    """列出"可以改哪些配置、现在是什么值、为什么改它"。

    ``forbidden`` 项**连名字都不返回**：给了名字模型就会反复试探，而它得到的
    只会是一句拒绝，不如一开始就不让它看见。
    """

    name = "ConfigList"
    description = (
        "列出可以调整的配置项：协议键、中文名、一句话说明、当前值、是否需要确认。"
        "用户说『现在是什么设置』『能调哪些』时用它；要改配置请用 ConfigSet。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "tier": {
                "type": "string",
                "enum": ["auto", "confirm", "all"],
                "description": "只看某一类：auto=可直接改，confirm=需你确认，all=两类都列（默认 all）",
            }
        },
    }
    max_result_chars = 6_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读配置清单")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        want = str(args.get("tier") or "all").strip().lower()
        tiers: tuple[str, ...] = (
            (TIER_AUTO,) if want == "auto"
            else (TIER_CONFIRM,) if want == "confirm"
            else (TIER_AUTO, TIER_CONFIRM)
        )
        return ToolResult.ok(render_spec_list(ctx.settings, tiers=tiers))

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        return "查看可改配置"


class ConfigSetTool(Tool):
    """按协议键改一个或多个配置项，并把"原值 → 新值"记进副作用账本。"""

    name = "ConfigSet"
    description = (
        "修改 jarvis 的配置项（键名先用 ConfigList 查）。只用于用户**明确要求**改设置，"
        "不要顺手优化。改完要告诉用户改了什么、原值多少、怎么回退（/undo）。"
        "标了 need_confirm 的项会先问用户一句；安全类配置（沙箱/凭据）一律不许改。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "patch": {
                "type": "object",
                "description": "要改的项：{协议键: 新值}，如 {\"tts_volume\": 30}",
            },
            "reason": {
                "type": "string",
                "description": "为什么改（一句话，会写进账本，便于日后回溯）",
            },
        },
        "required": ["patch"],
    }
    max_result_chars = 4_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        patch = args.get("patch")
        if not isinstance(patch, dict) or not patch:
            return ValidationResult.fail("patch 必须是非空对象，如 {\"tts_volume\": 30}")
        for key in patch:
            if get_spec(str(key)) is None and not forbidden_reason(str(key)):
                return ValidationResult.fail(
                    f"不认识的配置项 {key}；先用 ConfigList 查可用键"
                )
        return ValidationResult.pass_()

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """分级直通权限管线：禁止→DENY，需确认→ASK，其余 ALLOW。"""
        patch = args.get("patch") or {}
        forbidden = [(k, forbidden_reason(str(k))) for k in patch if forbidden_reason(str(k))]
        if forbidden:
            key, reason = forbidden[0]
            return PermissionResult.deny(
                f"{key} 不能由模型改：{reason}。请让用户自己用 /init 或直接改 settings.toml。"
            )
        confirm = [k for k in patch if _tier_of(str(k)) == TIER_CONFIRM]
        if confirm:
            labels = "、".join(_label_of(str(k)) for k in confirm)
            return PermissionResult.ask(
                f"要改 {labels}——这类设置会影响何时能打断你或能干什么活，确认？"
            )
        return PermissionResult.allow("表达层配置，可直接调整")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        outcome = apply_config_patch(
            ctx.settings,
            dict(args.get("patch") or {}),
            workdir=str(getattr(ctx, "workdir", "") or ""),
            permission_mode=str(getattr(ctx, "permission_mode", "default") or "default"),
            reason=str(args.get("reason") or "").strip(),
            entry=ENTRY_MODEL,
        )
        return ToolResult.ok(outcome.text) if outcome.ok else ToolResult.error(outcome.text)

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        patch = (args or {}).get("patch") or {}
        if not isinstance(patch, dict) or not patch:
            return "改配置"
        first = next(iter(patch.items()))
        more = f" 等 {len(patch)} 项" if len(patch) > 1 else ""
        return f"改配置 {first[0]} → {first[1]}{more}"


# ---- 内部 helpers ----


def _tier_of(key: str) -> str:
    """取某个协议键的分级（不认识的返回空串，当作普通项）。"""
    spec = get_spec(key)
    return spec.tier if spec is not None else ""


def _label_of(key: str) -> str:
    """取某个协议键的中文名（不认识的就用键名本身）。"""
    spec = get_spec(key)
    return spec.label if spec is not None and spec.label else key
