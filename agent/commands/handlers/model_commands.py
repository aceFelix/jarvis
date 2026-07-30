"""模型相关命令处理器。

包含 /models 交互式选择器与 /model <prefix> 前缀切换。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.model_manager import (
    _MODEL_VENDOR_OPTIONS,
    _VENDOR_LABELS,
    _add_custom_model_flow,
    _edit_builtin_model,
    _edit_custom_model,
    _delete_custom_model,
    _infer_model_vendor,
    _pick_model_action,
    _switch_model,
)
from agent.ui.terminal_picker import pick_from_grouped_list, pick_from_list

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


_ADD_MARKER = "__jarvis_add_model__"


async def handle_models(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /models：按厂商分组显示内置+自定义模型，支持添加/编辑/删除/切换。"""
    ui = ctx.ui
    settings = ctx.settings

    while True:
        custom_names: set[str] = set()
        builtin_names: set[str] = set(settings.models.keys())
        vendor_to_items: dict[str, list[tuple[str, str, str]]] = {
            v: [] for v, _ in _MODEL_VENDOR_OPTIONS
        }

        for k, v in settings.models.items():
            if k in settings.custom_models and isinstance(settings.custom_models[k], dict):
                cfg = settings.custom_models[k]
                mtype = cfg.get("model_type", "multimodal")
                prefix = "[文本]" if mtype == "text" else "[多模态]"
                desc = f"{prefix} {v}  ✎ 已自定义配置"
                custom_names.add(k)
            else:
                cfg = None
                desc = v
            vendor = _infer_model_vendor(k, cfg)
            vendor_to_items[vendor].append((k, k, desc))

        for cname, cfg in settings.custom_models.items():
            if not isinstance(cfg, dict):
                continue
            if cname in builtin_names:
                continue
            desc = cfg.get("name", cname)
            mtype = cfg.get("model_type", "multimodal")
            prefix = "[文本]" if mtype == "text" else "[多模态]"
            vendor = _infer_model_vendor(cname, cfg)
            vendor_to_items[vendor].append((cname, cname, f"{prefix} {desc}"))
            custom_names.add(cname)

        vendor_to_items["other"].append(
            (_ADD_MARKER, "+ 添加其他模型", "添加新的自定义模型")
        )

        grouped_items = [
            (_VENDOR_LABELS[v], vendor_to_items[v])
            for v, _ in _MODEL_VENDOR_OPTIONS
            if vendor_to_items[v]
        ]

        all_space_tags = builtin_names | custom_names

        picked = pick_from_grouped_list(
            grouped_items, title="选择模型", current=ctx.model,
            space_tags=all_space_tags,
        )

        if picked is None:
            break

        if picked.startswith("__SPACE__"):
            cname = picked[len("__SPACE__"):]
            is_custom = cname in custom_names
            is_builtin = cname in builtin_names
            action = _pick_model_action(ui, cname, allow_delete=is_custom)
            if action == "edit":
                if is_builtin:
                    _edit_builtin_model(ui, settings, cname)
                else:
                    _edit_custom_model(ui, settings, cname)
            elif action == "delete":
                _delete_custom_model(ui, settings, cname)
            continue

        if picked == _ADD_MARKER:
            added = _add_custom_model_flow(ui, settings)
            if added:
                continue
            else:
                break
        elif picked != ctx.model:
            result = _switch_model(
                ui, settings, ctx.provider, ctx.registry, ctx.orchestrator,
                ctx.system_prompt, picked,
            )
            if result:
                ctx.provider, ctx.loop, ctx.model = result
        break

    return True


async def handle_model(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /model <prefix>：前缀匹配切换模型。"""
    ui = ctx.ui
    settings = ctx.settings
    want = stripped.split(None, 1)[1].strip().lower()
    all_models = list(settings.models.keys()) + list(settings.custom_models.keys())

    exact = next((m for m in all_models if m.lower() == want), None)
    if exact:
        result = _switch_model(
            ui, settings, ctx.provider, ctx.registry, ctx.orchestrator,
            ctx.system_prompt, exact,
        )
        if result:
            ctx.provider, ctx.loop, ctx.model = result
        return True

    matches = [m for m in all_models if m.lower().startswith(want)]
    if not matches:
        ui.warn(f"无匹配模型: {want}（用 /models 查看可用列表）")
    elif len(matches) == 1:
        result = _switch_model(
            ui, settings, ctx.provider, ctx.registry, ctx.orchestrator,
            ctx.system_prompt, matches[0],
        )
        if result:
            ctx.provider, ctx.loop, ctx.model = result
    else:
        match_items = []
        for m in matches:
            if m in settings.models:
                match_items.append((m, m, settings.models[m]))
            elif m in settings.custom_models:
                cfg = settings.custom_models[m]
                mtype = cfg.get("model_type", "multimodal") if isinstance(cfg, dict) else "multimodal"
                prefix_label = "[文本]" if mtype == "text" else "[多模态]"
                match_items.append((m, m, f"{prefix_label} {cfg.get('name', m)}"))
        picked = pick_from_list(match_items, title=f"「{want}」匹配 {len(matches)} 个模型", current=ctx.model)
        if picked:
            result = _switch_model(
                ui, settings, ctx.provider, ctx.registry, ctx.orchestrator,
                ctx.system_prompt, picked,
            )
            if result:
                ctx.provider, ctx.loop, ctx.model = result

    return True
