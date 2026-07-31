"""模型管理模块。

负责自定义模型表单、模型切换、模型列表展示等与 LLM 模型相关的交互逻辑。

@author aceFelix
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent.config.settings import Settings
from agent.core.query_loop import QueryLoop
from agent.ui.cli import RichCLI


# 模型厂商选项，用于 /models 添加/编辑模型表单。
# value 会作为 provider/vendor 写入配置；label 为界面显示文本。
_MODEL_VENDOR_OPTIONS: list[tuple[str, str]] = [
    ("deepseek", "DeepSeek"),
    ("dashscope", "阿里云 DashScope"),
    ("zhipu", "智谱 BigModel"),
    ("moonshot", "Moonshot AI"),
    ("minimax", "MiniMax"),
    ("xiaomimimo", "Xiaomi MIMO"),
    ("google", "Google AI"),
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("other", "其他"),
]

# 厂商 value → 显示名，用于 /models 列表分组标题。
_VENDOR_LABELS: dict[str, str] = {value: label for value, label in _MODEL_VENDOR_OPTIONS}


__all__ = [
    "_MODEL_VENDOR_OPTIONS",
    "_VENDOR_LABELS",
    "_infer_model_vendor",
    "_infer_base_url",
    "_add_custom_model_flow",
    "_pick_model_action",
    "_edit_custom_model",
    "_edit_builtin_model",
    "_delete_custom_model",
    "_remove_custom_model_from_toml",
    "_switch_model",
    "_list_models",
]


def _infer_model_vendor(model_name: str, cfg: dict[str, Any] | None = None) -> str:
    """推断模型所属厂商。

    优先使用自定义模型配置中的 vendor 字段；
    否则根据模型名前缀推断；无法推断时返回 "other"。

    Args:
        model_name: 模型名。
        cfg: 自定义模型配置字典，可选。

    Returns:
        厂商 value（对应 _MODEL_VENDOR_OPTIONS 中的 value）。

    @author aceFelix
    """
    if cfg and isinstance(cfg, dict):
        vendor = cfg.get("vendor")
        if vendor:
            return vendor
    name_lower = model_name.lower()
    if name_lower.startswith("qwen"):
        return "dashscope"
    if name_lower.startswith("deepseek"):
        return "deepseek"
    if name_lower.startswith("glm"):
        return "zhipu"
    # MiniMax / Moonshot / OpenAI 等模型名通常与厂商名一致
    for vendor, _ in _MODEL_VENDOR_OPTIONS:
        if name_lower.startswith(vendor):
            return vendor
    return "other"


def _infer_base_url(vendor: str, api_format: str) -> str:
    """根据厂商和接口协议推断默认 Base URL。

    DashScope / 智谱 ZhipuAi SDK 模式由 SDK 内部管理 endpoint，无需 base_url。
    其他未知厂商返回空字符串，由用户手动填写。

    @author aceFelix
    """
    if api_format in ("dashscope", "zai"):
        return ""
    urls = {
        "deepseek": "https://api.deepseek.com",
        "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "moonshot": "https://api.moonshot.cn/v1",
        "minimax": "https://api.minimax.chat/v1",
        "google": "https://generativelanguage.googleapis.com/v1beta/openai",
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
    }
    return urls.get(vendor, "")


def _add_custom_model_flow(ui: RichCLI, settings: Settings) -> bool:
    """单屏表单添加自定义模型。

    用一个终端内联表单完成全部输入（不再切 5 次全屏）:
    模型名 → 接口类型 → Base URL → API Key → 模型类型
    持久化到 ~/.jarvis/settings.toml，同时更新 settings.custom_models。

    Returns:
        True 表示添加成功（settings.custom_models 已更新）。
    """
    from agent.ui.terminal_picker import form_input

    fields = [
        {
            "name": "模型厂商",
            "type": "select",
            "options": _MODEL_VENDOR_OPTIONS,
            "default": "deepseek",
        },
        {
            "name": "模型名",
            "type": "text",
            "placeholder": "例如: deepseek-v4-pro, gpt-4o, claude-sonnet-4",
            "default": "",
        },
        {
            "name": "API Key",
            "type": "password",
            "placeholder": "留空使用全局 Key（环境变量）",
            "default": "",
        },
        {
            "name": "接口类型",
            "type": "select",
            "options": [
                ("openai", "OpenAI 兼容"),
                ("anthropic", "Anthropic"),
                ("dashscope", "DashScope SDK（qwen 原生）"),
                ("zai", "智谱 ZhipuAi SDK"),
            ],
            "default": "openai",
        },
        {
            "name": "Base URL",
            "type": "text",
            "placeholder": "留空自动推断（例如: https://api.deepseek.com）",
            "default": "",
        },
        {
            "name": "模型类型",
            "type": "select",
            "options": [
                ("multimodal", "多模态 - 支持图片识别"),
                ("text", "纯文本 - 禁用视觉（省 token）"),
            ],
            "default": "text",
        },
    ]

    result = form_input(title="添加自定义模型", fields=fields)
    if result is None:
        ui.info("已取消")
        return False

    name = (result.get("模型名") or "").strip()
    if not name:
        ui.warn("模型名不能为空")
        return False

    provider_type = result["接口类型"]
    vendor = result.get("模型厂商", "deepseek") or "deepseek"
    base_url = (result.get("Base URL") or "").strip()
    # Base URL 留空时按厂商自动推断，减少用户手动填写。
    if not base_url:
        base_url = _infer_base_url(vendor, provider_type)
    api_key = (result.get("API Key") or "").strip()
    model_type = result["模型类型"]

    config = {
        "name": name,
        "provider": vendor,       # 模型提供商（vendor），如 deepseek
        "api_format": provider_type,  # API 协议，如 openai / anthropic
        "base_url": base_url,
        "api_key": api_key,
        "model_type": model_type,
        # 保留旧字段名以兼容旧代码读取
        "vendor": vendor,
        "provider_type": provider_type,
    }

    try:
        from agent.config.settings import save_custom_model
        if save_custom_model(name, config):
            settings.custom_models[name] = config
            mlabel = "多模态" if model_type == "multimodal" else "纯文本"
            ui.info(f"模型「{name}」已添加并保存（{mlabel}）")
            return True
        else:
            ui.warn("保存失败：找不到 ~/.jarvis/settings.toml")
            return False
    except Exception as e:
        ui.error(f"保存模型失败: {e}")
        return False


def _pick_model_action(ui: RichCLI, model_name: str, *, allow_delete: bool = True) -> str | None:
    """弹出操作菜单：修改配置 / 删除模型 / 取消。返回 "edit" / "delete" / None。"""
    from agent.ui.terminal_picker import pick_from_list

    items = [
        ("edit", "修改配置", f"修改「{model_name}」的接口/Key/类型等参数"),
    ]
    if allow_delete:
        items.append(("delete", "删除模型", f"从列表中移除「{model_name}」（不可恢复）"))
    items.append(("cancel", "取消", "返回模型列表"))
    return pick_from_list(items, title=f"管理「{model_name}」", current="cancel")


def _edit_custom_model(ui: RichCLI, settings: Settings, name: str) -> None:
    """弹出表单编辑自定义模型配置，保存到 TOML 和内存。"""
    from agent.ui.terminal_picker import form_input

    cfg = settings.custom_models.get(name)
    if not isinstance(cfg, dict):
        ui.warn(f"模型「{name}」配置无效")
        return

    fields = [
        {
            "name": "模型厂商", "type": "select",
            "options": _MODEL_VENDOR_OPTIONS,
            "default": cfg.get("provider") or cfg.get("vendor", "deepseek"),
        },
        {"name": "模型名", "type": "text", "default": name},
        {"name": "API Key", "type": "password", "default": cfg.get("api_key", "")},
        {
            "name": "接口类型", "type": "select",
            "options": [
                ("openai", "OpenAI 兼容"),
                ("anthropic", "Anthropic"),
                ("dashscope", "DashScope SDK（qwen 原生）"),
                ("zai", "智谱 ZhipuAi SDK"),
            ],
            "default": cfg.get("api_format") or cfg.get("provider_type", "openai"),
        },
        {"name": "Base URL", "type": "text", "default": cfg.get("base_url", "")},
        {
            "name": "模型类型", "type": "select",
            "options": [("multimodal", "多模态 - 支持图片识别"), ("text", "纯文本 - 禁用视觉（省 token）")],
            "default": cfg.get("model_type", "text"),
        },
    ]

    result = form_input(title=f"修改「{name}」", fields=fields)
    if result is None:
        ui.info("已取消")
        return

    new_name = (result.get("模型名") or "").strip()
    if not new_name:
        ui.warn("模型名不能为空")
        return

    new_vendor = result.get("模型厂商", "deepseek") or "deepseek"
    new_api_format = result["接口类型"]
    new_base_url = (result.get("Base URL") or "").strip()
    # Base URL 留空时按厂商自动推断。
    if not new_base_url:
        new_base_url = _infer_base_url(new_vendor, new_api_format)
    new_config = {
        "name": new_name,
        "provider": new_vendor,
        "api_format": new_api_format,
        "base_url": new_base_url,
        "api_key": (result.get("API Key") or "").strip(),
        "model_type": result["模型类型"],
        # 保留旧字段名以兼容
        "vendor": new_vendor,
        "provider_type": new_api_format,
    }

    from agent.config.settings import save_custom_model

    # 如果改名了，先删旧名
    if new_name != name:
        _remove_custom_model_from_toml(name)
        del settings.custom_models[name]

    if save_custom_model(new_name, new_config):
        settings.custom_models[new_name] = new_config
        ui.info(f"模型「{new_name}」配置已更新")
    else:
        ui.warn("保存失败")


def _edit_builtin_model(ui: RichCLI, settings: Settings, name: str) -> None:
    """编辑内置模型（qwen 系列等）的覆盖配置。

    内置模型名固定不可改（来自项目级 configs/settings.toml 的 [llm.models]），
    这里编辑的是"用户级覆盖配置"：保存到 ~/.jarvis/settings.toml 的
    [llm.custom_models."{name}"] 节，启动时和 /models 切换时会优先使用此配置
    而非内置默认（settings 顶层的 provider/base_url/api_key）。

    默认值取自 settings 顶层字段（即 settings.toml 原始的 provider/base_url/api_key），
    若 last_model 曾覆盖到自定义模型，则用 default_* 字段恢复原始值作为默认。
    """
    from agent.ui.terminal_picker import form_input

    # 默认值：优先用 default_*（last_model 覆盖前的原始值），否则用当前 settings 字段
    default_vendor = settings.default_provider or settings.provider or "dashscope"
    default_api_fmt = settings.default_api_format or settings.api_format or "openai"
    default_base_url = settings.default_base_url or settings.base_url or ""
    default_api_key = settings.default_api_key or settings.api_key or ""

    # 若该内置模型已有自定义覆盖配置，用其作为默认值
    existing = settings.custom_models.get(name)
    if isinstance(existing, dict):
        default_vendor = existing.get("provider") or existing.get("vendor", default_vendor)
        default_api_fmt = existing.get("api_format") or existing.get("provider_type", default_api_fmt)
        # DashScope / 智谱 ZhipuAi SDK 模式下 base_url 由 SDK 内部自管，
        # 用空字符串避免误填 OpenAI 兼容路径。
        if default_api_fmt in ("dashscope", "zai"):
            default_base_url = ""
        else:
            default_base_url = existing.get("base_url", default_base_url) or default_base_url
        default_api_key = existing.get("api_key", default_api_key) or default_api_key
        default_model_type = existing.get("model_type", "multimodal")
    else:
        # 未覆盖时，若顶层 api_format 是 dashscope 或 zai，base_url 也用空（避免误导）
        if default_api_fmt in ("dashscope", "zai"):
            default_base_url = ""
        default_model_type = "multimodal"

    fields = [
        {
            "name": "模型厂商", "type": "select",
            "options": _MODEL_VENDOR_OPTIONS,
            "default": default_vendor,
        },
        {"name": "API Key", "type": "password", "default": default_api_key},
        {
            "name": "接口类型", "type": "select",
            "options": [
                ("openai", "OpenAI 兼容"),
                ("anthropic", "Anthropic"),
                ("dashscope", "DashScope SDK（qwen 原生）"),
                ("zai", "智谱 ZhipuAi SDK"),
            ],
            "default": default_api_fmt,
        },
        {"name": "Base URL", "type": "text", "default": default_base_url},
        {
            "name": "模型类型", "type": "select",
            "options": [("multimodal", "多模态 - 支持图片识别"), ("text", "纯文本 - 禁用视觉（省 token）")],
            "default": default_model_type,
        },
    ]

    result = form_input(title=f"修改内置模型「{name}」", fields=fields)
    if result is None:
        ui.info("已取消")
        return

    vendor = result.get("模型厂商", "dashscope") or "dashscope"
    api_fmt = result["接口类型"]
    base_url = (result.get("Base URL") or "").strip()
    # Base URL 留空时按厂商自动推断。
    if not base_url:
        base_url = _infer_base_url(vendor, api_fmt)
    api_key = (result.get("API Key") or "").strip()
    model_type = result["模型类型"]

    config = {
        "name": name,
        "provider": vendor,
        "api_format": api_fmt,
        "base_url": base_url,
        "api_key": api_key,
        "model_type": model_type,
        # 保留旧字段名以兼容旧代码读取
        "vendor": vendor,
        "provider_type": api_fmt,
    }

    from agent.config.settings import save_custom_model
    if save_custom_model(name, config):
        settings.custom_models[name] = config
        ui.info(f"内置模型「{name}」已添加自定义覆盖配置（重启或切换后生效）")
    else:
        ui.warn("保存失败：找不到 ~/.jarvis/settings.toml")


def _delete_custom_model(ui: RichCLI, settings: Settings, name: str) -> None:
    """确认并删除自定义模型。"""
    from agent.ui.terminal_picker import pick_from_list

    confirm_items = [
        ("yes", "确认删除", f"永久移除「{name}」"),
        ("no", "取消", "保留模型"),
    ]
    choice = pick_from_list(confirm_items, title=f"⚠ 确认删除「{name}」？", current="no")
    if choice != "yes":
        ui.info("已取消")
        return

    _remove_custom_model_from_toml(name)
    settings.custom_models.pop(name, None)
    ui.info(f"模型「{name}」已删除")


def _remove_custom_model_from_toml(name: str) -> None:
    """从 ~/.jarvis/settings.toml 中移除指定自定义模型段。"""
    toml_path = Path.home() / ".jarvis" / "settings.toml"
    if not toml_path.exists():
        return
    content = toml_path.read_text(encoding="utf-8")
    marker = f'[llm.custom_models."{name}"]'
    if marker not in content:
        return
    start = content.index(marker)
    rest = content[start + len(marker):]
    m = re.search(r'\n\[', rest)
    if m:
        end = start + len(marker) + m.start()
    else:
        end = len(content)
    # 去掉前导空行
    while start > 0 and content[start - 1] == '\n':
        start -= 1
    content = content[:start] + content[end:]
    # 清理可能的空 custom_models 注释
    content = re.sub(r'\n# 自定义模型（通过 /models 添加）\n+$', '', content)
    toml_path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _switch_model(
    ui: RichCLI,
    settings: Settings,
    provider: object,
    registry: object,
    orchestrator: object,
    system_prompt: str,
    model_name: str,
) -> tuple[object, object, str] | None:
    """切换模型并返回新的 (provider, loop, model_name)。

    如果自定义模型指定了不同的 api_format/base_url/api_key，会重建 provider。
    Returns None 表示无需切换。
    """
    old_model = getattr(provider, '_model', '')
    if model_name == old_model:
        return None

    new_provider = provider
    new_is_custom = model_name in settings.custom_models

    # 延迟导入 _build_provider，避免 model_manager 与 main 模块循环引用
    from agent.main import _build_provider

    if new_is_custom:
        # 自定义模型 → 检查是否需要重建 provider（不同 base_url/api_key/api_format）
        cfg = settings.custom_models[model_name]
        # 兼容旧字段名 provider_type
        api_fmt = cfg.get("api_format") or cfg.get("provider_type", "openai")
        current_ptype = settings.api_format.lower()
        needs_new_provider = False

        if api_fmt.lower() != current_ptype:
            needs_new_provider = True
        elif cfg.get("base_url") and cfg["base_url"] != settings.base_url:
            needs_new_provider = True
        elif cfg.get("api_key") and cfg["api_key"] != settings.api_key:
            needs_new_provider = True

        mtype = cfg.get("model_type", "multimodal")
        if needs_new_provider:
            # 克隆 settings 并覆盖为自定义模型配置
            custom_settings = replace(
                settings,
                provider=cfg.get("provider", api_fmt),
                api_format=api_fmt,
                base_url=cfg.get("base_url") or settings.base_url,
                api_key=cfg.get("api_key") or settings.api_key,
            )
            new_provider = _build_provider(custom_settings, model_type=mtype)
        else:
            # 复用同一个 provider，但必须同步 model_type（同厂商不同模型可能是 text/multimodal）
            if hasattr(new_provider, "set_model_type"):
                new_provider.set_model_type(mtype)
    else:
        # 内置模型 → 用 settings.toml 原始 api_format/base_url 重建 provider
        # 若启动时 last_model 是自定义模型，settings 字段已被覆盖，
        # 需用 default_* 字段恢复原始值，否则内置模型会错误地连到自定义模型端点
        if settings.default_provider or settings.default_base_url or settings.default_api_key:
            clean_settings = replace(
                settings,
                provider=settings.default_provider or settings.provider,
                api_format=settings.default_api_format or settings.api_format,
                base_url=settings.default_base_url or settings.base_url,
                api_key=settings.default_api_key or settings.api_key,
            )
        else:
            clean_settings = settings
        new_provider = _build_provider(clean_settings, model_type="multimodal")

    # 记录当前模型名，供下次 _switch_model 判断是否需要重置
    new_provider._model = model_name

    new_loop = QueryLoop(
        provider=new_provider,
        registry=registry,
        orchestrator=orchestrator,
        system=system_prompt,
        model=model_name,
        max_iterations=settings.max_iterations,
        max_tokens=settings.max_tokens,
        temperature=settings.temperature,
        enable_compaction=settings.context_compaction,
        compaction_threshold=settings.compaction_threshold,
        keep_recent_messages=settings.keep_recent_messages,
        vendor_fallback=settings.vendor_fallback,
        custom_models=settings.custom_models,
    )

    # 文本模型添加视觉禁用提示
    model_desc = settings.models.get(model_name, "")
    if model_name in settings.custom_models:
        cfg = settings.custom_models[model_name]
        if cfg.get("model_type") == "text":
            model_desc = f"纯文本（{model_desc or cfg.get('name', model_name)}）"
        else:
            model_desc = f"多模态（{model_desc or cfg.get('name', model_name)}）"

    ui.info(f"模型已切换为: {model_name}（{model_desc}）")
    # 持久化当前模型，下次启动自动恢复
    try:
        from agent.config.settings import save_last_model
        save_last_model(model_name)
    except Exception:
        pass
    return new_provider, new_loop, model_name


def _list_models(ui: RichCLI, settings: Settings, current: str) -> None:
    """列出可选模型列表，标注当前模型。"""
    models = settings.models
    if not models:
        if ui._console:
            ui._console.print("[dim]暂无配置的可选模型（在 settings.toml [llm.models] 里添加）[/dim]")
        else:
            print("暂无配置的可选模型（在 settings.toml [llm.models] 里添加）")
        return

    if ui._console:
        from rich.table import Table
        table = Table(title="可选模型", show_header=True)
        table.add_column("模型", style="cyan", no_wrap=True)
        table.add_column("说明", style="dim")
        for name, desc in models.items():
            marker = " ★ 当前" if name == current else ""
            table.add_row(name + marker, str(desc))
        ui._console.print(table)
    else:
        print("[可选模型]")
        for name, desc in models.items():
            mark = " ★ 当前" if name == current else ""
            print(f"  {name}{mark}  — {desc}")
