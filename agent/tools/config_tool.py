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

from agent.config.catalog import (
    describe_missing,
    entries_of_section,
    render_entries,
    render_overview,
    search_catalog,
)
from agent.config.config_ops import (
    ENTRY_MODEL,
    apply_config_patch,
    render_spec_list,
)
from agent.config.config_specs import (
    TIER_AUTO,
    TIER_CONFIRM,
    TIER_FORBIDDEN,
    forbidden_fix,
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


class ConfigExplainTool(Tool):
    """回答"这项配置是干什么的、在哪改、能不能你帮我改"。

    与 ``ConfigList`` 的分工是**答案的广度**而不是深浅：``ConfigList`` 只列
    真源表里那三十项（都是可以代改的），而用户真正会问的常常是表外那一百七十
    多项——"CPU 阈值太高了怎么调""邮件要怎么配"——那些项既不在表里，模型也
    没有别的途径知道它们存在。

    所以本工具查到的东西分两种，返回里会直接标出来，不需要模型自己判断：

    - ``我可以代改``：在真源表内，接着用 ``ConfigSet`` 就行；
    - ``只能自己改``：给出配置文件的准确位置（文件名 + 段名），别去试 ``ConfigSet``
      ——那只会得到一个"不认识的配置项"。

    只读，不过权限管线（允许）：它只读进程内已有对象与包内模板，不碰文件。
    """

    name = "ConfigExplain"
    description = (
        "查某个配置项的说明、当前值、在哪改、能不能代改——覆盖全部两百多项，包括"
        "ConfigList 里没有的那些。用户问『这个有什么用』『在哪改』『怎么配邮件 / "
        "监控 / 自动清理』『有哪些配置』时用它。返回里标『我可以代改』的才用 "
        "ConfigSet；标『只能自己改』的把文件位置如实告诉用户，不要试着改。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "用户的功能说法或关键词（中文就行），如「CPU 阈值」「邮件」「自动清理」",
            },
            "section": {
                "type": "string",
                "description": "直接看某一整段，如 monitor / email / maintenance；与 query 二选一",
            },
            "overview": {
                "type": "boolean",
                "description": "true 时返回按段汇总的总览（用户问「都有哪些配置」时用）",
            },
        },
    }
    max_result_chars = 6_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读配置说明")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if args.get("overview"):
            return ToolResult.ok(render_overview())
        section = str(args.get("section") or "").strip()
        query = str(args.get("query") or "").strip()
        if section:
            # 段名给了但正好没这项（如用户把 percept 敲成 perception）→ 退回关键词检索，
            # 不让一个笔误直接变成"没找到"。
            entries = entries_of_section(section) or search_catalog(section, limit=10)
        elif query:
            entries = search_catalog(query, limit=10)
        else:
            # 两个都没给：退回总览，比报参数错误对用户有用得多
            return ToolResult.ok(render_overview())
        return ToolResult.ok(render_entries(entries, ctx.settings))

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        data = args or {}
        if data.get("overview"):
            return "看配置总览"
        target = str(data.get("section") or data.get("query") or "").strip()
        return f"查配置 {target}" if target else "看配置总览"


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
                # 两种情形要分开说：这项根本不存在，与这项存在但只能改文件。
                # 后者若也说“不认识”，模型会反复重试同一个名字。
                if describe_missing(str(key)):
                    return ValidationResult.fail(
                        f"{key} 这项存在但不在可代改范围内（多半只能改文件）；"
                        f"用 ConfigExplain 查它的位置与改法，不要把值猜着填"
                    )
                return ValidationResult.fail(
                    f"没有 {key} 这个配置项；用 ConfigList 看可代改的键，"
                    f"或 ConfigExplain 按关键词查全部配置"
                )
        return ValidationResult.pass_()

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """分级 × 模式（2026-11 口径）：

        | 模式 | auto / confirm | forbidden |
        |---|---|---|
        | yolo | **ALLOW** | DENY |
        | default / accept_edits | **ASK** | DENY |
        | plan | DENY（只读规划不写配置） | DENY |

        为什么 yolo 下不再看 tier：用户明确要求「yolo 下啥都能改」，只是仍有
        **锁死清单**。护栏本身（权限规则/凭据/沙箱/审计线）在 forbidden 那一步
        已经拦死，连确认都不收——那一层不属于"配置值"，而属于监督机制。
        @author aceFelix
        """
        patch = args.get("patch") or {}
        # 两道禁止来源都要查：名字字典（表外键靠它）+ 真源表里 tier=forbidden 的项。
        # 只查一道，扩表时就可能出现"写进了表、却是禁止项"的漏洞（2026-11 首刷）。
        blocked: list[tuple[str, str]] = []
        for k in patch:
            key = str(k)
            reason = forbidden_reason(key)
            if not reason and _tier_of(key) == TIER_FORBIDDEN:
                reason = "该配置项已标记为禁止模型修改（护栏本身）"
            if reason:
                blocked.append((key, reason))
        if blocked:
            key, reason = blocked[0]
            fix = forbidden_fix(key)
            tail = f"正确改法：{fix}" if fix else "请让用户自己用 /init 或直接改 settings.toml。"
            return PermissionResult.deny(f"{key} 不能由模型改：{reason}。{tail}")

        mode = str(getattr(ctx, "permission_mode", "") or "default").lower()
        if mode == "yolo":
            return PermissionResult.allow("yolo 模式：配置写入不设限（锁死清单除外）")
        if mode == "plan":
            return PermissionResult.deny("plan 模式只读，不写配置")

        labels = "、".join(_label_of(str(k)) for k in patch) or "配置"
        return PermissionResult.ask(f"要改 {labels}，确认？")

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
