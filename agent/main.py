"""主入口 —— 装配所有零件，跑通 REPL。

大量import 和大量启动优化，
v0.1 聚焦"能跑起来": 解析 CLI 参数 -> 加载配置 -> 构建 provider/registry/checker/
loop -> 进入 REPL 循环。

REPL 循环:
    while True:
        user_input = ui.read()
        if /exit: break
        if /help, /mode, /reset: 处理命令
        else: query_loop.run(user_input, ctx)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# Python 3.13 + anyio v4: MCP stdio 的 async gen 在进程退出时
# 抛 GeneratorExit + RuntimeError（cancel scope 跨 task），无法在代码层静默。
# 用 asyncgen hooks 替换默认的 aclose() 为 no-op，避免刷满屏。
_sys_agen_firstiter = getattr(sys, 'get_asyncgen_hooks', lambda: (None, None))()
if callable(_sys_agen_firstiter[0]):
    _orig_firstiter = _sys_agen_firstiter[0]
    _orig_finalizer = _sys_agen_firstiter[1]
else:
    _orig_firstiter = None
    _orig_finalizer = None

def _jarvis_agen_finalizer(agen):
    """放弃关闭 async gen，避免 MCP GeneratorExit 刷屏。"""
    pass

sys.set_asyncgen_hooks(firstiter=_orig_firstiter, finalizer=_jarvis_agen_finalizer)

from agent.config.settings import Settings, load_settings
from agent.core.context import ToolContext
from agent.core.message import ImageContent, Message, TextContent
from agent.core.orchestrator import ToolOrchestrator
from agent.core.query_loop import QueryLoop
from agent.core.tool import ToolRegistry, build_default_registry
from agent.permissions import PermissionChecker, parse_mode
from agent.permissions.modes import PermissionMode
from agent.permissions.rules import RuleSet, load_rules
from agent.prompts.system import build_system_prompt
from agent.ui.cli import RichCLI


def _build_provider(settings: Settings, model_type: str = "multimodal"):
    """根据 settings 构造 LLM provider。

    model_type: "multimodal"（默认，支持图片）或 "text"（纯文本，跳过图片）。
    使用 settings.api_format 决定用哪个 LLM Provider 类（openai/anthropic），
    而非 settings.provider（那是 vendor 名称，仅显示用途）。
    """
    name = settings.api_format.lower()
    if name == "mock":
        from agent.llm.mock import MockProvider
        return MockProvider()
    if name == "anthropic":
        from agent.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=settings.api_key or None, base_url=settings.base_url or None)
    if name in ("openai", "openai_compatible"):
        from agent.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(
            api_key=settings.api_key or None,
            base_url=settings.base_url or None,
            model=settings.model or None,
            enable_thinking=settings.enable_thinking,
            thinking_budget=settings.thinking_budget,
            model_type=model_type,
        )
    if name == "dashscope":
        from agent.llm.dashscope_provider import DashScopeProvider
        return DashScopeProvider(
            api_key=settings.api_key or None,
            base_url=settings.base_url or None,
            model=settings.model or None,
            enable_thinking=settings.enable_thinking,
            thinking_budget=settings.thinking_budget,
            model_type=model_type,
        )
    raise ValueError(f"未知 provider: {name}（可选: mock / anthropic / openai / dashscope）")


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
            "options": [
                ("deepseek", "DeepSeek"),
                ("dashscope", "阿里云 DashScope"),
                ("openai", "OpenAI"),
                ("anthropic", "Anthropic"),
                ("other", "其他"),
            ],
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
            "options": [
                ("deepseek", "DeepSeek"),
                ("dashscope", "阿里云 DashScope"),
                ("openai", "OpenAI"),
                ("anthropic", "Anthropic"),
                ("other", "其他"),
            ],
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

    new_config = {
        "name": new_name,
        "provider": result.get("模型厂商", "deepseek") or "deepseek",
        "api_format": result["接口类型"],
        "base_url": (result.get("Base URL") or "").strip(),
        "api_key": (result.get("API Key") or "").strip(),
        "model_type": result["模型类型"],
        # 保留旧字段名以兼容
        "vendor": result.get("模型厂商", "deepseek") or "deepseek",
        "provider_type": result["接口类型"],
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
        # DashScope SDK 模式下 base_url 无意义（SDK 内部自管），用空字符串避免误填 OpenAI 兼容路径
        if default_api_fmt == "dashscope":
            default_base_url = ""
        else:
            default_base_url = existing.get("base_url", default_base_url) or default_base_url
        default_api_key = existing.get("api_key", default_api_key) or default_api_key
        default_model_type = existing.get("model_type", "multimodal")
    else:
        # 未覆盖时，若顶层 api_format 是 dashscope，base_url 也用空（避免误导）
        if default_api_fmt == "dashscope":
            default_base_url = ""
        default_model_type = "multimodal"

    fields = [
        {
            "name": "模型厂商", "type": "select",
            "options": [
                ("dashscope", "阿里云 DashScope"),
                ("deepseek", "DeepSeek"),
                ("openai", "OpenAI"),
                ("anthropic", "Anthropic"),
                ("other", "其他"),
            ],
            "default": default_vendor,
        },
        {"name": "API Key", "type": "password", "default": default_api_key},
        {
            "name": "接口类型", "type": "select",
            "options": [
                ("openai", "OpenAI 兼容"),
                ("anthropic", "Anthropic"),
                ("dashscope", "DashScope SDK（qwen 原生）"),
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
    import re
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

        if needs_new_provider:
            # 克隆 settings 并覆盖为自定义模型配置
            from dataclasses import replace
            custom_settings = replace(
                settings,
                provider=cfg.get("provider", api_fmt),
                api_format=api_fmt,
                base_url=cfg.get("base_url", settings.base_url),
                api_key=cfg.get("api_key", settings.api_key),
            )
            new_provider = _build_provider(custom_settings, model_type=cfg.get("model_type", "multimodal"))
    else:
        # 内置模型 → 用 settings.toml 原始 api_format/base_url 重建 provider
        # 若启动时 last_model 是自定义模型，settings 字段已被覆盖，
        # 需用 default_* 字段恢复原始值，否则内置模型会错误地连到自定义模型端点
        from dataclasses import replace
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


def _build_checker(settings: Settings) -> PermissionChecker:
    """构造权限校验器。"""
    rules = RuleSet()
    if settings.permissions_file:
        loaded = load_rules(settings.permissions_file)
        rules = rules.merge(loaded)
    return PermissionChecker(rules=rules, mode=settings.permission_mode)


def _build_context(settings: Settings, ui: RichCLI, messages: list[Message]) -> ToolContext:
    return ToolContext(
        workdir=settings.workdir,
        messages=messages,
        permission_mode=settings.permission_mode.value,
        ui=ui,
    )


def _load_image_from_path(path: str) -> ImageContent | None:
    """从文件路径加载图片，缩放并编码为 ImageContent。"""
    try:
        from PIL import Image
        from agent.tools.system.screen import ScreenShotTool
    except ImportError:
        return None

    p = Path(path).expanduser().resolve()
    if not p.exists():
        return None
    try:
        img = Image.open(p)
        img.load()
        return ScreenShotTool._encode_image(img, "jpeg", 1280)
    except Exception:
        return None


def _load_image_from_clipboard() -> ImageContent | None:
    """从系统剪贴板读取图片（Windows/macOS 支持），编码为 ImageContent。"""
    try:
        from PIL import Image, ImageGrab
        from agent.tools.system.screen import ScreenShotTool
    except ImportError:
        return None

    data = ImageGrab.grabclipboard()
    if data is None:
        return None
    if isinstance(data, Image.Image):
        return ScreenShotTool._encode_image(data, "jpeg", 1280)
    # Windows 剪贴板有时是文件路径列表
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                ext = Path(item).suffix.lower()
                if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                    content = _load_image_from_path(item)
                    if content is not None:
                        return content
    return None


def _pending_images(ctx: ToolContext) -> list[ImageContent]:
    """获取当前待发送的图片列表。"""
    return ctx.extra.setdefault("pending_images", [])


def _hash_image(img: ImageContent) -> str:
    """为 ImageContent 生成稳定哈希，用于去重。"""
    import hashlib
    return hashlib.md5(f"{img.media_type}:{img.data}".encode()).hexdigest()


def _auto_attach_clipboard_image(ctx: ToolContext, ui: RichCLI) -> list[ImageContent]:
    """如果剪贴板有新图片，自动加入待发送列表并返回。"""
    pending = ctx.extra.pop("pending_images", None) or []
    if pending:
        return pending
    img = _load_image_from_clipboard()
    if img is None:
        return []
    h = _hash_image(img)
    if h == ctx.extra.get("_last_clipboard_image_hash"):
        return []
    pending.append(img)
    ctx.extra["_last_clipboard_image_hash"] = h
    ui.info("✅ 检测到剪贴板图片，已自动附加到当前消息")
    return pending


async def repl(settings: Settings, with_tray: bool = False) -> int:
    """REPL 主循环。返回退出码。

    with_tray=True 时启动一个系统托盘图标（daemon 线程），托盘"退出"
    会直接终止整个进程。这样前台 REPL 和托盘共存，任一退出=整体退出。
    """
    ui = RichCLI(verbose=settings.verbose, boot_animation=settings.boot_animation)
    provider = _build_provider(settings)
    registry: ToolRegistry = build_default_registry()
    checker = _build_checker(settings)
    orchestrator = ToolOrchestrator(registry=registry, permission_checker=checker)

    # MCP 接入：连接配置的 server 并注册工具
    mcp_client = None
    if settings.enable_mcp:
        try:
            from agent.core.extensions.mcp_client import MCPClient, load_mcp_config
            from agent.core.tool import register_dynamic_tools
            mcp_client = MCPClient()
            if mcp_client.available:
                config = load_mcp_config()
                if config:
                    # console.status: 显示带 spinner 的临时状态行，退出时自动清除
                    with ui._console.status(f"正在连接 {len(config)} 个 MCP server..."):
                        results = await mcp_client.connect_all(config)
                    connected = sum(1 for v in results.values() if v)
                    if connected:
                        count = register_dynamic_tools(registry, mcp_client)
                        ui.info(f"MCP: {connected}/{len(config)} server 已连接，注册 {count} 个工具")
                    else:
                        ui.warn(f"MCP: 所有 server 连接失败（{len(config)} 个配置）")
            else:
                ui.info("MCP SDK 未安装，跳过 MCP 接入（pip install mcp 启用）")
        except ImportError:
            ui.info("MCP 模块不可用，跳过 MCP 接入")
        except Exception as e:
            ui.warn(f"MCP 接入异常: {e}")

    # 子代理协作工具注入（阶段五第二刀）：Agent Tool 需要 provider 才能派生子 agent/队友
    from agent.collaboration.team import get_team_manager
    from agent.collaboration.task_list import TaskList

    team_mgr = get_team_manager()
    # 用会话 ID 作为默认任务列表 ID（独立使用时用；团队模式下会被 TeamCreate 覆盖）
    task_list = TaskList("default")

    from agent.core.tool import register_subagent_tool, register_team_tools, register_plan_tools
    if register_subagent_tool(registry, provider=provider, permission_mode=settings.permission_mode,
                               team_mgr=team_mgr, task_list=task_list):
        if settings.verbose:
            ui.info("✓ 子代理协作工具已注册（Agent/Subagent）")

    # 多 Agent 协作工具注入（Phase 1）：Team + Task + Message
    team_tool_count = register_team_tools(registry, task_list=task_list, team_mgr=team_mgr)
    if team_tool_count > 0 and settings.verbose:
        ui.info(f"✓ 多 Agent 协作工具已注册（{team_tool_count} 个）")

    # Plan Mode 工具注入（Phase 3）
    plan_tool_count = register_plan_tools(registry)
    if plan_tool_count > 0 and settings.verbose:
        ui.info(f"✓ 规划模式工具已注册（{plan_tool_count} 个）")

    # LSP 集成（对标 Claude Code）
    lsp_tool_count = 0
    if settings.enable_lsp and settings.lsp_servers:
        try:
            from agent.lsp.manager import init_lsp_manager, load_lsp_config
            configs = load_lsp_config(settings)
            if configs:
                init_lsp_manager(settings.workdir, configs)
                from agent.core.tool import register_lsp_tool
                lsp_tool_count = register_lsp_tool(registry)
                if lsp_tool_count > 0:
                    ui.info(f"✓ LSP 代码智能已注册（{len(configs)} 个 server）")
        except Exception as e:
            if settings.verbose:
                ui.warn(f"LSP 初始化失败: {e}")

    system_prompt = build_system_prompt(settings.workdir, registry, enable_thinking=settings.enable_thinking)
    if settings.system_prompt_append:
        system_prompt = system_prompt + "\n\n" + settings.system_prompt_append

    model = settings.model or provider.default_model
    loop = QueryLoop(
        provider=provider,
        registry=registry,
        orchestrator=orchestrator,
        system=system_prompt,
        model=model,
        max_iterations=settings.max_iterations,
        max_tokens=settings.max_tokens,
        temperature=settings.temperature,
        enable_compaction=settings.context_compaction,
        compaction_threshold=settings.compaction_threshold,
        keep_recent_messages=settings.keep_recent_messages,
        vendor_fallback=settings.vendor_fallback,
        custom_models=settings.custom_models,
    )

    messages: list[Message] = []

    # 自动恢复上次会话
    if settings.auto_resume_session:
        from agent.core.memory.store import latest_session_name, load_session
        latest_name = latest_session_name()
        if latest_name:
            session = load_session(latest_name)
            if session and session.messages:
                messages.extend(session.messages)
                ui.info(f"已自动恢复上次会话「{latest_name}」({len(session.messages)} 条消息)")

    ctx = _build_context(settings, ui, messages)

    # 托盘图标（可选）：前台 REPL + 托盘共存模式
    # 托盘"退出"直接 os._exit(0) 终止整个进程（input() 阻塞中无法优雅 break）
    tray = None
    if with_tray:
        try:
            from agent.daemon.daemon import TrayIcon
            import os as _os

            def _tray_quit() -> None:
                """托盘退出回调：直接终止进程。"""
                try:
                    if ui._console:
                        ui._console.print("\n[dim]托盘退出，贾维斯关闭中...[/dim]")
                except Exception:
                    pass
                _os._exit(0)

            tray = TrayIcon(
                on_voice=lambda: None,   # 前台 REPL 不需要托盘唤起
                on_text=lambda: None,    # 用户在终端直接打字
                on_quit=_tray_quit,
                voice_enabled_getter=lambda: True,  # 前台 REPL 无语音开关概念，默认开启
                voice_toggle=lambda: None,
                realtime_enabled_getter=lambda: False,  # 前台 REPL 不展示实时聊天开关
                realtime_toggle=lambda: None,
            )
            if tray.start():
                ui.info("✓ 托盘图标已启动（右键「退出贾维斯」可关闭）")
            else:
                ui.warn("托盘图标启动失败（不影响使用，直接 /exit 退出）")
                tray = None
        except ImportError:
            ui.info("托盘模块不可用（pip install pystray pillow 启用）")

    # 生成本次会话的唯一名称（时间戳），自动保存时写入独立文件
    from datetime import datetime as _dt
    _session_name = f"session-{_dt.now().strftime('%Y%m%d-%H%M%S')}"
    _title_generated = False   # 2轮后 LLM 自动生成标题
    _dialog_count = 0

    # 启用诊断日志（settings.debug=True 时同时输出到 stderr）
    from agent.core.diag import set_debug as _set_diag_debug
    _set_diag_debug(settings.debug)

    ui.banner(provider.name, model, settings.workdir)

    # ---- Hook: session_start ----
    try:
        from agent.core.hooks import get_hooks, HookEvent
        await get_hooks().trigger(HookEvent.SESSION_START, {
            "provider": provider.name,
            "model": model,
            "workdir": settings.workdir,
        })
    except Exception:
        pass

    # ---- 崩溃恢复检测 ----
    # 检查上次会话是否异常退出，是则提示用户是否恢复
    try:
        from agent.core.memory.recovery import load_recovery_point, format_recovery_summary, clear_recovery_point
        point = load_recovery_point()
        if point is not None and point.messages:
            ui.warn("检测到上次会话异常退出，是否恢复？")
            ui.info(format_recovery_summary(point))
            try:
                answer = await ui.read_user_input_async("[y/N] ")
            except Exception:
                answer = ""
            if answer.strip().lower() in ("y", "yes"):
                # 恢复 messages 和 workdir
                messages.clear()
                messages.extend(point.messages)
                if point.workdir and point.workdir != settings.workdir:
                    ui.info(f"恢复到工作目录: {point.workdir}")
                    # 注意: 不改 settings.workdir，避免影响 provider 配置；
                    # 工具执行的 workdir 通过 ctx 传递，这里只做提示
                _dialog_count = point.dialog_count
                ui.info(f"已恢复 {len(point.messages)} 条消息（{point.dialog_count} 轮对话）")
            else:
                clear_recovery_point()
                ui.info("已跳过恢复，恢复点已清除")
    except Exception as e:
        # 恢复检测失败不阻塞启动
        from agent.core.diag import diag_warn
        diag_warn("recovery", f"恢复检测异常: {e}")

    while True:
        try:
            user_input = await ui.read_user_input_async("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        stripped = user_input.strip() if user_input else ""

        # 空行提交：检测剪贴板图片，允许「复制图片 → 直接回车」的粘贴操作
        if not stripped:
            img = _load_image_from_clipboard()
            if img:
                h = _hash_image(img)
                if h != ctx.extra.get("_last_clipboard_image_hash"):
                    _pending_images(ctx).append(img)
                    ctx.extra["_last_clipboard_image_hash"] = h
                    ui.info("✅ 检测到剪贴板图片，已添加到待发送列表，请输入消息")
                    continue
            continue

        # ---- 斜杠命令 ----
        if stripped.startswith("/"):
            cmd = stripped.lower()
            if cmd in ("/exit", "/quit", "/q"):
                # ---- Hook: session_end ----
                try:
                    from agent.core.hooks import get_hooks, HookEvent
                    await get_hooks().trigger(HookEvent.SESSION_END, {
                        "dialog_count": _dialog_count,
                    })
                except Exception:
                    pass
                # 标记正常退出（清除恢复点）
                try:
                    from agent.core.memory.recovery import mark_clean_exit
                    mark_clean_exit()
                except Exception:
                    pass
                break
            if cmd in ("/help", "/h", "?"):
                _print_help(ui)
                continue
            if cmd == "/mode":
                # 无参数 → 终端内联选择器
                from agent.ui.terminal_picker import pick_from_list
                modes = ["default", "plan", "accept_edits", "yolo"]
                mode_descs = {
                    "default": "默认：写操作需确认，危险命令拒绝",
                    "plan": "规划：只读规划，拒绝所有写操作",
                    "accept_edits": "接受编辑：文件编辑自动放行，其他需确认",
                    "yolo": "全自动：自动放行所有操作（危险命令除外）",
                }
                items = [(m, m, mode_descs.get(m, "")) for m in modes]
                choice = pick_from_list(items, title="选择权限模式")
                if choice is None:
                    continue
                mode_str = choice
            elif cmd.startswith("/mode ") and not cmd.startswith("/modes"):
                # /mode xxx — 带参数切换
                mode_str = stripped.split(None, 1)[1].strip().lower()
                valid_modes = ["default", "plan", "accept_edits", "yolo"]
                matches = [m for m in valid_modes if m.startswith(mode_str)]
                if not matches:
                    ui.warn(f"未知模式: {mode_str}（可选: default/plan/accept_edits/yolo）")
                    continue
                if len(matches) > 1:
                    from agent.ui.terminal_picker import pick_from_list
                    items = [(m, m, "") for m in matches]
                    choice = pick_from_list(items, title="多个匹配，请选择")
                    if choice is None:
                        continue
                    mode_str = choice
                else:
                    mode_str = matches[0]

            if cmd == "/mode" or cmd.startswith("/mode "):
                new_mode = parse_mode(mode_str)
                settings.permission_mode = new_mode
                checker = _build_checker(settings)
                # 重新装配 orchestrator 和 loop（checker 变了）
                orchestrator = ToolOrchestrator(registry=registry, permission_checker=checker)
                loop = QueryLoop(
                    provider=provider, registry=registry, orchestrator=orchestrator,
                    system=system_prompt, model=model,
                    max_iterations=settings.max_iterations,
                    max_tokens=settings.max_tokens, temperature=settings.temperature,
                    enable_compaction=settings.context_compaction,
                    compaction_threshold=settings.compaction_threshold,
                    keep_recent_messages=settings.keep_recent_messages,
                    vendor_fallback=settings.vendor_fallback,
                    custom_models=settings.custom_models,
                )
                ctx.permission_mode = new_mode.value
                ui.info(f"权限模式切换为: {new_mode.value}")
                continue
            if cmd in ("/reset", "/clear"):
                messages.clear()
                ctx.extra.clear()
                ui.info("对话已重置")
                continue
            if cmd in ("/compact",):
                _compact(ui, loop, ctx)
                continue
            if cmd in ("/cost",):
                _print_cost(ui, messages, _dialog_count, model, settings.provider)
                continue
            if cmd in ("/context",):
                _print_context(ui, messages, model)
                continue
            if cmd in ("/rewind",) or cmd.startswith("/rewind "):
                _rewind(ui, messages, stripped)
                continue
            if cmd in ("/diff",) or cmd.startswith("/diff "):
                await _show_diff(ui, settings, stripped)
                continue
            if cmd in ("/doctor",):
                _doctor(ui, settings, provider, model, messages)
                continue
            if cmd.startswith("/save"):
                _save_session(ui, settings, stripped, messages)
                continue
            if cmd.startswith("/load") and not cmd.startswith("/loads"):
                # /load <prefix> — 前缀匹配加载会话
                parts = stripped.split(None, 1)
                if len(parts) > 1 and parts[1].strip():
                    want = parts[1].strip().lower()
                    from agent.core.memory.store import list_sessions, load_session

                    sessions = list_sessions()
                    # 精确匹配优先
                    exact = next((s for s in sessions if s.name.lower() == want), None)
                    if exact:
                        _load_by_name(ui, settings, exact.name, messages)
                    else:
                        # 前缀匹配
                        matches = [s for s in sessions if s.name.lower().startswith(want)]
                        if not matches:
                            ui.warn(f"无匹配会话: {want}（用 /sessions 查看保存列表）")
                        elif len(matches) == 1:
                            _load_by_name(ui, settings, matches[0].name, messages)
                        else:
                            from agent.ui.terminal_picker import pick_from_list
                            match_items = [
                                (s.name, s.name, f"{s.message_count} 条消息 | {s.workdir or '(无)'}")
                                for s in matches
                            ]
                            picked = pick_from_list(match_items, title=f"「{want}」匹配 {len(matches)} 个会话")
                            if picked:
                                _load_by_name(ui, settings, picked, messages)
                else:
                    ui.warn("用法: /load <会话名前缀>（用 /sessions 查看并选择会话）")
                continue
            if cmd in ("/loads", "/sessions", "/ls-sessions"):
                _load_by_picker(ui, messages)
                continue
            if cmd in ("/memory",):
                _show_memory(ui, settings)
                continue
            if cmd in ("/skills",):
                _list_skills(ui, settings)
                continue
            if cmd in ("/plugin", "/plugins"):
                _show_plugins(ui, settings)
                continue
            if cmd.startswith("/plugin install "):
                _plugin_install(ui, settings, stripped)
                continue
            if cmd.startswith("/plugin uninstall "):
                _plugin_uninstall(ui, settings, stripped)
                continue
            if cmd.startswith("/plugin search") or cmd.startswith("/plugin search "):
                _plugin_search(ui, settings, stripped)
                continue
            if cmd in ("/agents",):
                _show_agents(ui, team_mgr, task_list)
                continue
            if cmd in ("/tasks",):
                _show_tasks(ui, task_list)
                continue
            if cmd in ("/mcp",):
                _show_mcp(ui, mcp_client)
                continue
            if cmd in ("/tools",):
                _print_tools(ui, registry)
                continue
            if cmd in ("/plan",):
                await _toggle_plan(ui, settings, ctx)
                continue
            if cmd == "/think" or cmd.startswith("/think "):
                _toggle_thinking(ui, settings, provider, loop, registry, stripped)
                continue
            if cmd.startswith("/say "):
                _say(ui, settings, stripped.split(" ", 1)[1])
                continue
            if cmd in ("/paste", "/p", "/clipboard"):
                img = _load_image_from_clipboard()
                if img is None:
                    ui.warn("剪贴板中没有图片（或缺少 Pillow）")
                else:
                    _pending_images(ctx).append(img)
                    ui.info("✅ 已添加剪贴板图片，下一条消息会附带发送")
                continue
            if cmd.startswith(("/image", "/img")):
                parts = stripped.split(None, 1)
                if len(parts) < 2:
                    ui.warn("用法: /image <图片路径>")
                else:
                    img = _load_image_from_path(parts[1].strip())
                    if img is None:
                        ui.warn(f"无法加载图片: {parts[1].strip()}")
                    else:
                        _pending_images(ctx).append(img)
                        ui.info(f"✅ 已添加图片，下一条消息会附带发送: {parts[1].strip()}")
                continue
            if cmd in ("/listen", "/mic"):
                _listen(ui, settings, loop, ctx)
                continue
            if cmd in ("/voice",):
                await _voice_mode(ui, settings, loop, ctx)
                continue
            if cmd == "/talk":
                await _realtime_talk(ui, settings)
                continue
            if cmd == "/models":
                # 终端内联选择器：含内置模型 + 自定义模型 + 添加按钮
                # 空格键在自定义模型上触发修改/删除
                from agent.ui.terminal_picker import pick_from_list

                _ADD_MARKER = "__jarvis_add_model__"

                while True:
                    # 构建模型列表（内置 + 自定义）
                    # 内置模型若已有同名自定义覆盖配置，标记 ✎ 并用 custom 配置的描述
                    custom_names: set[str] = set()
                    builtin_names: set[str] = set(settings.models.keys())
                    model_items = []
                    for k, v in settings.models.items():
                        if k in settings.custom_models and isinstance(settings.custom_models[k], dict):
                            cfg = settings.custom_models[k]
                            mtype = cfg.get("model_type", "multimodal")
                            prefix = "[文本]" if mtype == "text" else "[多模态]"
                            desc = f"{prefix} {v}  ✎ 已自定义配置"
                            # 已被覆盖的内置模型同样支持空格键编辑/删除（允许_delete）
                            custom_names.add(k)
                        else:
                            desc = v
                        model_items.append((k, k, desc))
                    for cname, cfg in settings.custom_models.items():
                        if not isinstance(cfg, dict):
                            continue
                        # 同名内置模型已在上面显示，跳过
                        if cname in builtin_names:
                            continue
                        desc = cfg.get("name", cname)
                        mtype = cfg.get("model_type", "multimodal")
                        prefix = "[文本]" if mtype == "text" else "[多模态]"
                        model_items.append((cname, cname, f"{prefix} {desc}"))
                        custom_names.add(cname)
                    # 末尾追加「添加其他模型」
                    model_items.append((_ADD_MARKER, "+ 添加其他模型", "添加新的自定义模型"))

                    # 所有模型都支持空格键操作（内置+自定义）
                    all_space_tags = builtin_names | custom_names

                    picked = pick_from_list(
                        model_items, title="选择模型", current=model,
                        space_tags=all_space_tags,
                    )

                    if picked is None:
                        break  # Esc 取消

                    # 空格键 → 修改/删除模型
                    if picked.startswith("__SPACE__"):
                        cname = picked[len("__SPACE__"):]
                        is_custom = cname in custom_names
                        is_builtin = cname in builtin_names
                        # 删除权限：纯自定义模型可删；已被覆盖的内置模型也可删（删除的是覆盖配置，恢复内置默认）
                        action = _pick_model_action(ui, cname, allow_delete=is_custom)
                        if action == "edit":
                            # 内置模型（含已覆盖）走 _edit_builtin_model（模型名固定不可改）
                            # 纯自定义模型走 _edit_custom_model（允许改名）
                            if is_builtin:
                                _edit_builtin_model(ui, settings, cname)
                            else:
                                _edit_custom_model(ui, settings, cname)
                        elif action == "delete":
                            # 统一调用 _delete_custom_model：从 toml 删除 [llm.custom_models."name"] 段
                            # 并从 settings.custom_models 移除。对内置模型而言效果是"恢复内置默认配置"
                            _delete_custom_model(ui, settings, cname)
                        # 操作完成后回到模型列表
                        continue

                    if picked == _ADD_MARKER:
                        added = _add_custom_model_flow(ui, settings)
                        if added:
                            continue
                        else:
                            break
                        # 进入添加流程
                        added = _add_custom_model_flow(ui, settings)
                        if added:
                            # 回到模型列表（重新循环）
                            continue
                        else:
                            break  # 取消失了
                    elif picked != model:
                        result = _switch_model(
                            ui, settings, provider, registry, orchestrator,
                            system_prompt, picked,
                        )
                        if result:
                            provider, loop, model = result
                    break  # 已选择或取消
                continue
            if cmd.startswith("/model "):
                # /model <prefix> — 前缀匹配切换模型
                want = stripped.split(None, 1)[1].strip().lower()
                all_models = list(settings.models.keys()) + list(settings.custom_models.keys())

                # 精确匹配优先
                exact = next((m for m in all_models if m.lower() == want), None)
                if exact:
                    result = _switch_model(ui, settings, provider, registry, orchestrator, system_prompt, exact)
                    if result: provider, loop, model = result
                    continue

                # 前缀匹配
                matches = [m for m in all_models if m.lower().startswith(want)]
                if not matches:
                    ui.warn(f"无匹配模型: {want}（用 /models 查看可用列表）")
                elif len(matches) == 1:
                    result = _switch_model(ui, settings, provider, registry, orchestrator, system_prompt, matches[0])
                    if result: provider, loop, model = result
                else:
                    # 多匹配 → 内联选择器（仅显示匹配项）
                    from agent.ui.terminal_picker import pick_from_list
                    match_items = []
                    for m in matches:
                        if m in settings.models:
                            match_items.append((m, m, settings.models[m]))
                        elif m in settings.custom_models:
                            cfg = settings.custom_models[m]
                            mtype = cfg.get("model_type", "multimodal") if isinstance(cfg, dict) else "multimodal"
                            prefix_label = "[文本]" if mtype == "text" else "[多模态]"
                            match_items.append((m, m, f"{prefix_label} {cfg.get('name', m)}"))
                    picked = pick_from_list(match_items, title=f"「{want}」匹配 {len(matches)} 个模型", current=model)
                    if picked:
                        result = _switch_model(ui, settings, provider, registry, orchestrator, system_prompt, picked)
                        if result: provider, loop, model = result
                continue
            # ---- /<skill-name>: 用斜杠命令触发已安装的 skill ----
            _maybe_skill = await _dispatch_skill(ui, settings, stripped, loop, ctx)
            if _maybe_skill:
                continue

            ui.warn(f"未知命令: {stripped}（/help 查看可用命令）")
            continue

        # ---- 普通对话 ----
        try:
            pending = _auto_attach_clipboard_image(ctx, ui)
            stats = await loop.run(stripped, ctx, images=pending)
            if settings.verbose:
                ui.info(
                    f"[iterations={stats.iterations} tool_calls={stats.tool_calls} "
                    f"reason={stats.stopped_reason} "
                    f"tokens={stats.usage.input_tokens}+{stats.usage.output_tokens}]"
                )
            # 每轮对话后增量保存（防窗口被强杀丢失记忆）
            _dialog_count += 1
            _auto_save(ui, messages, workdir=settings.workdir, model=model, provider=settings.provider, session_name=_session_name, verbose=False)
            # 写恢复点（崩溃恢复用）
            try:
                from agent.core.memory.recovery import save_recovery_point
                save_recovery_point(
                    messages,
                    workdir=settings.workdir,
                    model=model,
                    provider=settings.provider,
                    dialog_count=_dialog_count,
                )
            except Exception:
                pass

            # 1轮对话后用用户首句生成标题；2轮对话后用 LLM 根据前两轮生成标题
            if _dialog_count == 1:
                _session_name = await _generate_title_from_first_user(
                    ui, messages, _session_name
                )
            elif _dialog_count == 2 and len(messages) >= 4 and not _title_generated:
                _title_generated = True
                _session_name = await _generate_session_title(
                    ui, provider, model, messages, _session_name
                )
        except KeyboardInterrupt:
            ctx.abort_event.set()
            ui.warn("已中断（按回车继续）")
            ctx = _build_context(settings, ui, messages)  # 重置 abort
        except Exception as e:
            ui.error(f"运行出错: {type(e).__name__}: {e}")
            if settings.debug:
                import traceback

                traceback.print_exc()

    # 退出前最终保存
    _auto_save(ui, messages, workdir=settings.workdir, model=model, provider=settings.provider, session_name=_session_name)

    ui.goodbye()
    # 清理托盘图标
    if tray is not None:
        tray.stop()
    # 清理 MCP 连接（静默处理 anyio cancel scope 错误）
    if mcp_client is not None:
        try:
            await mcp_client.disconnect_all()
        except BaseException:
            pass
    await provider.close()
    # 清理 LSP server
    try:
        from agent.lsp.manager import get_lsp_manager
        mgr = get_lsp_manager()
        if mgr:
            await mgr.shutdown_all()
    except Exception:
        pass
    return 0


def _sanitize_title(title: str) -> str:
    """标题文件名安全化：去标点、换空格为连字符，截断到 15 字。"""
    import re

    title = title.strip()[:15]
    title = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', title).strip()
    title = re.sub(r'\s+', '-', title)
    return title


def _rename_session_file(old_name: str, title: str) -> str:
    """重命名会话文件。返回最终可用的新名称。"""
    from agent.core.memory.store import sessions_dir

    title = _sanitize_title(title)
    if not title:
        return old_name

    old_path = sessions_dir() / f"{old_name}.json"
    new_path = sessions_dir() / f"{title}.json"
    if old_path.exists() and not new_path.exists():
        old_path.rename(new_path)
    elif new_path.exists():
        # 目标已存在：保留原文件名但把标题信息附在末尾
        return old_name
    return title


async def _generate_title_from_first_user(
    ui: RichCLI, messages: list[Message], old_name: str
) -> str:
    """第 1 轮对话结束后：取用户第一条消息的前 15 字作为标题。"""
    try:
        first_user_text = ""
        for m in messages:
            if getattr(m, "role", "") == "user":
                text = m.get_text() if hasattr(m, "get_text") else ""
                text = text.strip()
                if text:
                    first_user_text = text
                    break

        if not first_user_text:
            return old_name

        # 取前 15 个字符（中英文混排按字符计）
        title = first_user_text[:15]
        title = _rename_session_file(old_name, title)
        if title != old_name:
            ui.info(f"📝 会话标题已生成: {title}")
        return title
    except Exception:
        return old_name


async def _generate_session_title(
    ui: RichCLI, provider, model: str, messages: list[Message], old_name: str
) -> str:
    """第 2 轮对话结束后：用 LLM 根据前两轮对话生成标题。返回新名称（失败则返回旧名）。

    只取前两轮 user/assistant 消息（最多 4 条）喂给 LLM，不污染上下文。
    标题限制 15 字以内，去标点，作文件名时安全截断。
    """
    try:
        # 取前两轮 user/assistant 消息（最多 4 条），跳过 tool 消息
        dialog_lines: list[str] = []
        for m in messages:
            role = getattr(m, "role", "")
            if role in ("user", "assistant"):
                text = m.get_text() if hasattr(m, "get_text") else ""
                text = text.strip()[:200]
                if text:
                    who = "用户" if role == "user" else "贾维斯"
                    dialog_lines.append(f"{who}: {text}")
            if len(dialog_lines) >= 4:
                break

        if not dialog_lines:
            return old_name

        dialog_text = "\n".join(dialog_lines)
        prompt = (
            "请根据以下对话内容，用15个字以内生成一个会话标题。\n"
            "要求：只输出标题文本，不要输出任何解释、标点、引号，"
            "不要重复题目或用户原话。\n\n"
            f"对话：\n{dialog_text}\n\n"
            "标题："
        )

        msgs = [Message(role="user", content=[TextContent(text=prompt)])]

        # 标题生成不需要深度思考，临时关闭避免模型输出冗余 reasoning/echo
        old_thinking = provider.is_thinking_enabled()
        title_text = ""
        try:
            provider.set_thinking_enabled(False)
            events = provider.stream(
                model=model,
                system="",
                messages=msgs,
                tools=[],
                max_tokens=30,
                temperature=0.3,
            )
            async for event in events:
                if hasattr(event, "text") and event.text:
                    title_text += event.text
        finally:
            provider.set_thinking_enabled(old_thinking)

        # 去除模型可能带出的 "标题：" 前缀、引号等多余字符
        title_text = title_text.strip()
        for prefix in ("标题：", "标题:", "会话标题：", "会话标题:", "Title:", "Title："):
            if title_text.startswith(prefix):
                title_text = title_text[len(prefix):].strip()
        title_text = title_text.strip("'\"«»")

        title = _rename_session_file(old_name, title_text)
        if title != old_name:
            ui.info(f"📝 会话标题已生成: {title}")
        return title
    except Exception:
        return old_name


def _auto_save(
    ui: RichCLI,
    messages: list[Message],
    *,
    workdir: str = "",
    model: str = "",
    provider: str = "",
    session_name: str = "auto-latest",
    verbose: bool = True,
) -> None:
    """保存会话到指定名称（增量刷新，每次对话后都会调用）。

    同时写入 auto-latest.json 确保重启时自动恢复。
    session_name 为空时使用时间戳自动命名。
    """
    if not messages:
        return
    try:
        from agent.core.memory.store import save_session, user_jarvis_dir
        jarvis_dir = user_jarvis_dir()
        jarvis_dir.mkdir(parents=True, exist_ok=True)
        # 写入独立的会话文件
        save_session(
            session_name, messages,
            workdir=workdir,
            model=model,
            provider=provider,
        )
        # 同时写入 auto-latest 作为恢复指针
        save_session(
            "auto-latest", messages,
            workdir=workdir,
            model=model,
            provider=provider,
        )
        if verbose:
            ui.info(f"会话已自动保存（{session_name[:40]}）")
    except Exception:
        pass


def _print_help(ui: RichCLI) -> None:
    help_text = (
        "[bold]命令:[/bold]\n"
        "  /exit        退出\n"
        "  /help        查看帮助\n"
        "  /mode <m>    切换权限模式 (default/plan/accept_edits/yolo)\n"
        "  /model [前缀] 前缀匹配切换模型（支持模糊输入）\n"
        "  /reset       清空对话历史\n"
        "  /compact     手动压缩上下文（摘要旧消息）\n"
        "  /cost        显示本会话 token 用量与成本估算\n"
        "  /context     显示上下文窗口使用情况\n"
        "  /rewind [n]  回退最近 n 条消息（默认 1）\n"
        "  /diff [path] 显示 git diff（工作区改动）\n"
        "  /doctor      系统诊断（环境/配置/日志/迁移状态）\n"
        "  /save [name] 保存当前会话\n"
        "  /load [前缀]  前缀匹配加载会话（支持模糊输入）\n"
        "  /loads       列出并选择已保存会话\n"
        "  /memory      查看长期记忆文件\n"
        "  /skills      列出已加载的技能包\n"
        "  /mcp         查看 MCP server 连接状态\n"
        "  /tools       列出可用工具\n"
        "  /image <path> 添加本地图片到待发送列表（下条消息附带）\n"
        "  /img <path>   添加本地图片（/image 别名）\n"
        "  /paste       添加剪贴板图片到待发送列表（下条消息附带）\n"
        "  /p           添加剪贴板图片（/paste 别名）\n"
        "  /say <text>  用语音朗读一段文字\n"
        "  /listen      录音并识别成文字（麦克风→文字）\n"
        "  /voice       进入语音对话模式（连续听→想→说，说「退出」结束）\n"
    )
    if ui._console:
        ui._console.print(help_text)
    else:
        print(help_text)


def _print_cost(ui: RichCLI, messages: list[Message], dialog_count: int, model: str, provider_name: str) -> None:
    """/cost: 显示本会话累计 token 用量与估算成本。"""
    from agent.core.memory.compactor import estimate_tokens
    from agent.ui.markdown_renderer import render_table
    total_in = 0
    total_out = 0
    total_cache_read = 0
    total_cache_creation = 0
    # messages 不直接含 usage 信息（QueryStats 是临时的），这里用消息体积估算
    est_tokens = estimate_tokens(messages)
    # 累计 usage 来自每轮 stats；这里没有持久化，给出基于消息的估算
    rows = [
        ["对话轮数", str(dialog_count)],
        ["消息数", str(len(messages))],
        ["当前上下文 token（估算）", f"{est_tokens:,}"],
        ["模型", model],
        ["Provider", provider_name],
    ]
    ui.info("本会话成本统计（部分基于估算）")
    render_table(rows, headers=["指标", "值"])
    ui.info("提示: 实际 API 计费以厂商账单为准；历史累计请查看 ~/.jarvis/logs/diag.log")


def _print_context(ui: RichCLI, messages: list[Message], model: str) -> None:
    """/context: 显示上下文窗口使用情况，按角色分组统计消息数。"""
    from agent.core.memory.compactor import estimate_tokens
    from agent.ui.markdown_renderer import render_tree, render_table
    # 按角色统计
    role_counts: dict[str, int] = {}
    role_tokens: dict[str, int] = {}
    for m in messages:
        role_counts[m.role] = role_counts.get(m.role, 0) + 1
        role_tokens[m.role] = role_tokens.get(m.role, 0) + estimate_tokens([m])
    total = estimate_tokens(messages)
    # 模型上下文窗口默认 32k（OpenAI 兼容链路常见值），用户可参考实际模型
    window = 32768
    pct = (total / window * 100) if window else 0
    ui.info(f"上下文使用情况（模型: {model}，假设窗口 {window:,} tokens）")
    rows = [
        [role, str(role_counts.get(role, 0)), f"{role_tokens.get(role, 0):,}"]
        for role in ["system", "user", "assistant", "tool"]
        if role in role_counts
    ]
    render_table(rows, headers=["角色", "消息数", "tokens"])
    ui.info(f"合计: {len(messages)} 条消息 / {total:,} tokens / 窗口占比 {pct:.1f}%")
    # 显示最近 5 条消息树
    children = []
    for m in messages[-5:]:
        first_text = ""
        for b in m.content:
            if hasattr(b, "text") and b.text:
                first_text = b.text[:50].replace("\n", " ")
                break
            elif hasattr(b, "name"):  # ToolUseContent
                first_text = f"[工具调用: {b.name}]"
                break
        children.append((f"{m.role}: {first_text}...", None))
    render_tree("最近 5 条消息", children)


def _rewind(ui: RichCLI, messages: list[Message], cmd: str) -> None:
    """/rewind [n]: 回退最近 n 条消息（默认 1 条）。

    回退后无法恢复（消息已从列表移除），但已写入磁盘的会话文件不受影响。
    """
    parts = cmd.split()
    n = 1
    if len(parts) > 1:
        try:
            n = int(parts[1])
            if n < 1:
                ui.warn("参数必须 ≥ 1")
                return
        except ValueError:
            ui.warn(f"无效参数: {parts[1]}（应为正整数）")
            return
    if n > len(messages):
        ui.warn(f"消息数不足：当前 {len(messages)} 条，无法回退 {n} 条")
        return
    # 弹出最后 n 条
    for _ in range(n):
        messages.pop()
    ui.info(f"已回退 {n} 条消息，当前 {len(messages)} 条")


async def _show_diff(ui: RichCLI, settings: Settings, cmd: str) -> None:
    """/diff [path]: 显示工作目录的 git diff。

    无参数: 显示工作区所有改动
    带 path: 只显示指定文件的改动
    """
    import asyncio
    parts = cmd.split(maxsplit=1)
    path_arg = parts[1].strip() if len(parts) > 1 else ""
    try:
        # 优先 git diff
        cmd_args = ["git", "diff", "--color=never"]
        if path_arg:
            cmd_args.append("--")
            cmd_args.append(path_arg)
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            cwd=settings.workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        diff_text = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if not diff_text and not err:
            ui.info("工作区干净，无未提交改动")
            return
        if err and not diff_text:
            ui.warn(f"git diff 失败: {err.strip()}")
            # 非 git 仓库则提示
            if "not a git repository" in err.lower():
                ui.info("（提示: 当前目录不是 git 仓库，/diff 仅支持 git）")
            return
        from agent.ui.markdown_renderer import render_diff
        render_diff(diff_text)
    except FileNotFoundError:
        ui.warn("找不到 git 命令，请确认 git 已安装并加入 PATH")
    except Exception as e:
        ui.error(f"/diff 执行失败: {type(e).__name__}: {e}")


def _doctor(ui: RichCLI, settings: Settings, provider, model: str, messages: list[Message]) -> None:
    """/doctor: 系统诊断。检查配置、依赖、日志、迁移状态等。"""
    import sys
    import platform
    from pathlib import Path
    from agent.ui.markdown_renderer import render_table, render_panel
    from agent.core.diag import get_log_path, read_recent_logs
    from agent.config.migrations import list_all_migrations

    ui.info("JARVIS 系统诊断")
    # 1. 环境信息
    env_rows = [
        ["Python", sys.version.split()[0]],
        ["Platform", platform.platform()],
        ["Working dir", settings.workdir],
        ["Provider", settings.provider],
        ["Model", model],
        ["Debug", "on" if settings.debug else "off"],
        ["Permissions file", settings.permissions_file or "(未配置)"],
    ]
    render_table(env_rows, headers=["项", "值"], title="环境信息")

    # 2. Provider 状态
    prov_rows = [
        ["Provider 类", type(provider).__name__],
        ["思考模式", "on" if provider.is_thinking_enabled() else "off"],
        ["模型", model],
    ]
    render_table(prov_rows, headers=["项", "值"], title="Provider 状态")

    # 3. 配置文件检查
    user_cfg = Path.home() / ".jarvis" / "settings.toml"
    cfg_rows = [
        ["用户配置", "存在" if user_cfg.exists() else "不存在（用默认值）"],
        ["API key", "已配置" if settings.api_key else "未配置"],
        ["Base URL", settings.base_url or "(默认)"],
    ]
    render_table(cfg_rows, headers=["项", "状态"], title="配置")

    # 4. 迁移状态
    migrations = list_all_migrations()
    if migrations:
        mig_rows = [
            [mid, desc, "已执行" if done else "待执行"]
            for mid, desc, done in migrations
        ]
        render_table(mig_rows, headers=["ID", "描述", "状态"], title="配置迁移")
    else:
        ui.info("迁移: 无待执行迁移")

    # 5. 诊断日志
    log_path = get_log_path()
    if log_path and log_path.exists():
        recent = read_recent_logs(max_lines=5)
        if recent:
            render_panel("\n".join(recent), title=f"最近 5 条诊断日志 ({log_path.name})")
        else:
            ui.info(f"诊断日志: 空 ({log_path})")
    else:
        ui.info("诊断日志: 尚未生成")

    # 6. 当前会话
    sess_rows = [
        ["消息数", str(len(messages))],
        ["对话轮数", "(请用 /cost 查看)"],
    ]
    render_table(sess_rows, headers=["项", "值"], title="当前会话")


def _compact(ui: RichCLI, loop: QueryLoop, ctx: ToolContext) -> None:
    """手动触发上下文压缩。/compact 命令的执行体。"""
    from agent.core.memory.compactor import estimate_tokens, should_compact

    pre_tokens = estimate_tokens(ctx.messages)
    if not should_compact(ctx.messages, threshold=1):  # threshold=1 强制触发
        ui.info(f"当前 {len(ctx.messages)} 条消息，约 {pre_tokens} tokens")
        if len(ctx.messages) < 4:
            ui.warn("消息太少，无需压缩")
            return

    ui.info(f"开始压缩（{len(ctx.messages)} 条消息，约 {pre_tokens} tokens）...")
    import asyncio
    ok = asyncio.run(loop.compact_now(ctx))
    if ok:
        post_tokens = estimate_tokens(ctx.messages)
        ui.info(
            f"压缩完成：{pre_tokens} → {post_tokens} tokens"
            f"（节省 {pre_tokens - post_tokens}，现在 {len(ctx.messages)} 条消息）"
        )
    else:
        ui.warn("压缩未执行（可能消息太少或已禁用）")


def _print_tools(ui: RichCLI, registry: ToolRegistry) -> None:
    lines = [f"  - {t.name}: {t.description.splitlines()[0]}" for t in registry.all()]
    text = "[bold]可用工具:[/bold]\n" + "\n".join(lines)
    if ui._console:
        ui._console.print(text)
    else:
        print(text)


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


def _save_session(ui: RichCLI, settings: Settings, cmd: str, messages: list[Message]) -> None:
    """/save [name] — 保存当前会话。"""
    from agent.core.memory.store import save_session
    from datetime import datetime

    # 解析名字: /save myname → myname; /save → auto-<timestamp>
    parts = cmd.split(None, 1)
    if len(parts) > 1 and parts[1].strip():
        name = parts[1].strip()
    else:
        name = f"auto-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    if not messages:
        ui.warn("当前对话为空，无需保存")
        return

    path = save_session(
        name, messages,
        workdir=settings.workdir,
        model=settings.model or "",
        provider=settings.provider,
    )
    ui.info(f"会话已保存: {name}（{len(messages)} 条消息）→ {path}")


def _load_session(ui: RichCLI, settings: Settings, cmd: str, messages: list[Message]) -> None:
    pass  # deprecated, 改用 _load_by_name / _load_by_picker


def _load_by_name(ui: RichCLI, settings: Settings, name: str, messages: list[Message]) -> None:
    """/load <name> — 直接加载指定会话。"""
    from agent.core.memory.store import load_session

    session = load_session(name)
    if not session:
        ui.error(f"会话不存在: {name}（用 /sessions 查看保存列表）")
        return

    messages.clear()
    messages.extend(session.messages)
    ui.info(f"已加载会话: {name}（{len(session.messages)} 条消息，"
            f"保存于 {session.meta.workdir}）")
    _render_session(ui, session.messages)


def _load_by_picker(ui: RichCLI, messages: list[Message]) -> None:
    """/load（无参数）— 终端内联选择（↑↓ Enter Esc，不弹窗）。"""
    from agent.core.memory.store import list_sessions, load_session

    sessions = list_sessions()
    if not sessions:
        ui.info("没有已保存的会话。用 /save [name] 保存当前会话。")
        return

    # 转 pick_from_list 格式 [(value, label, description), ...]
    from datetime import datetime
    items = []
    for s in sessions:
        ts = ""
        try:
            ts = datetime.fromtimestamp(s.updated_at).strftime("%m-%d %H:%M")
        except Exception:
            pass
        items.append((
            s.name,
            s.name,
            f"{s.message_count}条消息  {ts}",
        ))

    from agent.ui.terminal_picker import pick_from_list
    picked = pick_from_list(items, title="加载会话")
    if picked is None:
        return

    session = load_session(picked)
    if not session:
        ui.error(f"会话加载失败: {picked}")
        return

    messages.clear()
    messages.extend(session.messages)
    ui.info(f"已加载会话: {picked}（{len(session.messages)} 条消息）")
    _render_session(ui, session.messages)


def _render_session(ui: RichCLI, msgs: list[Message]) -> None:
    """回放已加载会话的消息到终端。"""
    from agent.core.message import TextContent, ThinkingContent, ToolUseContent, ToolResultContent

    for msg in msgs:
        if msg.role == "user":
            # 用户消息可能是 TextContent（用户输入）或 ToolResultContent（工具结果）
            text = "".join(
                b.text for b in msg.content if isinstance(b, TextContent)
            )
            tool_results = [b for b in msg.content if isinstance(b, ToolResultContent)]
            if text.strip():
                ui.info(f"👤 {text}")
            for tr in tool_results:
                ui.tool_result(
                    f"工具({tr.tool_use_id[:8]})",
                    tr.tool_use_id,
                    tr.content,
                    is_error=tr.is_error,
                )
        elif msg.role == "assistant":
            # 思考过程
            thinking = "".join(
                b.text for b in msg.content if isinstance(b, ThinkingContent)
            )
            if thinking:
                ui.assistant_thinking(thinking)
                ui._end_thinking()
            # 正式文本
            texts = [b for b in msg.content if isinstance(b, TextContent)]
            for t in texts:
                if t.text.strip():
                    ui.info(f"🤖 {t.text}")
            # 工具调用
            for b in msg.content:
                if isinstance(b, ToolUseContent):
                    ui.tool_use(b.name, b.input, b.id)


def _list_sessions(ui: RichCLI) -> None:
    """/sessions — 列出所有已保存会话。"""
    from agent.core.memory.store import list_sessions
    from datetime import datetime

    sessions = list_sessions()
    if not sessions:
        ui.info("没有已保存的会话。用 /save [name] 保存当前会话。")
        return

    if ui._console:
        from rich.table import Table
        table = Table(title="已保存会话", show_lines=False)
        table.add_column("名称", style="cyan", no_wrap=True)
        table.add_column("消息数", justify="right", style="green")
        table.add_column("更新时间", style="dim")
        table.add_column("工作目录", style="dim")
        for s in sessions:
            ts = datetime.fromtimestamp(s.updated_at).strftime("%Y-%m-%d %H:%M")
            table.add_row(s.name, str(s.message_count), ts, s.workdir)
        ui._console.print(table)
    else:
        for s in sessions:
            ts = datetime.fromtimestamp(s.updated_at).strftime("%Y-%m-%d %H:%M")
            print(f"  {s.name}  ({s.message_count} 条, {ts})  {s.workdir}")


def _show_memory(ui: RichCLI, settings: Settings) -> None:
    """/memory — 查看长期记忆文件。"""
    from agent.core.memory.store import get_memory_files, load_long_term_memory

    files = get_memory_files(settings.workdir)
    ui.info("长期记忆文件:")
    for label, path in files.items():
        exists = "✓" if path and path.exists() else "✗"
        ui.info(f"  [{label}] {exists} {path}")

    mem = load_long_term_memory(settings.workdir)
    if mem:
        ui.info("当前加载的记忆内容:")
        if ui._console:
            ui._console.print(mem)
        else:
            print(mem)
    else:
        ui.info("（暂无长期记忆。可手动创建上述文件写入需要记住的信息。）")


# ---- plugin 命令辅助函数 ----

def _show_plugins(ui: RichCLI, settings: Settings) -> None:
    """/plugin — 列出已安装插件。"""
    from agent.core.extensions.plugins import PluginManager
    pm = PluginManager(settings.plugin_marketplace)
    installed = pm.list_installed()
    plugins = installed.get("plugins", {})
    if not plugins:
        ui.info("（暂无已安装插件。使用 /plugin search 搜索可安装的插件。）")
        return
    if ui._console:
        from rich.table import Table
        table = Table(title="已安装插件", show_lines=False)
        table.add_column("名称", style="cyan", no_wrap=True)
        table.add_column("版本", style="dim")
        table.add_column("来源", style="dim")
        table.add_column("安装时间", style="dim")
        for p in plugins.values():
            table.add_row(
                p.get("name", "?"),
                p.get("version", "?"),
                p.get("source", "?"),
                p.get("installed_at", "?")[:19],
            )
        ui._console.print(table)
    else:
        for p in plugins.values():
            ui.info(f"  {p['name']} v{p['version']} 来自 {p['source']}")


def _plugin_install(ui: RichCLI, settings: Settings, stripped: str) -> None:
    """/plugin install <name>"""
    parts = stripped.split(None, 2)
    if len(parts) < 3:
        ui.warn("用法: /plugin install <插件名>")
        return
    name = parts[2].strip()
    from agent.core.extensions.plugins import PluginManager
    pm = PluginManager(settings.plugin_marketplace)
    ui.info(f"正在安装插件: {name} ...")
    ok, msg = pm.install(name)
    if ok:
        ui.info(f"插件 '{name}' 安装成功！" + (f" ({msg})" if msg else ""))
        ui.info("技能已安装到 ~/.jarvis/skills/，请 /reset 或重启后生效。")
    else:
        ui.error(f"安装失败: {msg}")


def _plugin_uninstall(ui: RichCLI, settings: Settings, stripped: str) -> None:
    """/plugin uninstall <name>"""
    parts = stripped.split(None, 2)
    if len(parts) < 3:
        ui.warn("用法: /plugin uninstall <插件名>")
        return
    name = parts[2].strip()
    from agent.core.extensions.plugins import PluginManager
    pm = PluginManager(settings.plugin_marketplace)
    ok, msg = pm.uninstall(name)
    if ok:
        ui.info(f"插件 '{name}' 已卸载。")
    else:
        ui.error(f"卸载失败: {msg}")


def _plugin_search(ui: RichCLI, settings: Settings, stripped: str) -> None:
    """/plugin search [keyword]"""
    parts = stripped.split(None, 2)
    keyword = parts[2].strip() if len(parts) > 2 else ""
    from agent.core.extensions.plugins import PluginManager
    pm = PluginManager(settings.plugin_marketplace)
    label = keyword if keyword else "全部"
    ui.info(f"搜索插件: {label} ...")
    try:
        results = pm.search(keyword)
    except Exception as e:
        ui.error(f"搜索失败: {e}")
        return
    if not results:
        ui.info("无匹配插件。")
        return
    if ui._console:
        from rich.table import Table
        table = Table(title=f"插件市场搜索结果: {label}", show_lines=True)
        table.add_column("名称", style="cyan", no_wrap=True)
        table.add_column("版本", style="dim")
        table.add_column("描述")
        table.add_column("作者")
        for p in results:
            table.add_row(
                p.get("name", "?"),
                p.get("version", "?"),
                p.get("description", ""),
                p.get("author", ""),
            )
        ui._console.print(table)
    else:
        for p in results:
            ui.info(f"  {p['name']} v{p['version']} - {p.get('description', '')}")
    ui.info("使用 /plugin install <名称> 安装")


def _list_skills(ui: RichCLI, settings: Settings) -> None:
    """/skills — 列出已加载的技能包。"""
    from agent.core.extensions.skills import load_skills, list_skill_files

    files = list_skill_files(settings.workdir)
    ui.info("技能包目录:")
    for label, path in files.items():
        exists = "✓" if path and path.exists() else "✗"
        count = len(list(path.iterdir())) if path.exists() else 0
        ui.info(f"  [{label}] {exists} {path} ({count} 个子目录)")

    skills = load_skills(settings.workdir)
    if not skills:
        ui.info("（暂无技能包。在上述目录创建 <name>/SKILL.md 即可添加。）")
        return

    if ui._console:
        from rich.table import Table
        table = Table(title="已加载技能包", show_lines=False)
        table.add_column("名称", style="cyan", no_wrap=True)
        table.add_column("来源", style="dim")
        table.add_column("描述")
        table.add_column("使用时机", style="dim")
        for s in skills:
            table.add_row(s.name, s.source, s.description, s.when_to_use)
        ui._console.print(table)
    else:
        for s in skills:
            print(f"  {s.name} [{s.source}] {s.description} — {s.when_to_use}")


async def _dispatch_skill(
    ui: RichCLI,
    settings: Settings,
    stripped: str,
    loop: Any,
    ctx: ToolContext,
) -> bool:
    """如果 stripped = /<skill-name> [...args]，则把 skill 提示词 + 用户参数注入对话。

    Returns:
        True 表示已匹配并执行了 skill（调用方应 continue），False 表示不是 skill 命令。
    """
    from agent.core.extensions.skills import load_skills

    parts = stripped[1:].split(None, 1)  # 去掉 /
    if not parts:
        return False
    skill_name = parts[0].lower()
    user_arg = parts[1] if len(parts) > 1 else ""

    skills = load_skills(settings.workdir)
    matched = None
    for s in skills:
        if s.name.lower() == skill_name:
            matched = s
            break

    if matched is None:
        return False

    prompt = matched.content
    if user_arg:
        prompt = f"{user_arg}\n\n请运用以下技能来完成上述请求：\n\n{matched.content}"
    else:
        prompt = f"请运用以下技能来帮助我：\n\n{matched.content}"

    ui.info(f"调用技能: {matched.name}")
    try:
        stats = await loop.run(prompt, ctx)
        if settings.verbose:
            ui.info(
                f"[{matched.name}] iterations={stats.iterations} "
                f"tool_calls={stats.tool_calls} "
                f"tokens={stats.usage.input_tokens}+{stats.usage.output_tokens}]"
            )
    except Exception as e:
        ui.error(f"技能执行出错: {type(e).__name__}: {e}")
    return True


# ---- /agents /tasks /plan 命令处理器 (Phase 1-3) ----


def _show_agents(ui: RichCLI, team_mgr, task_list) -> None:
    """/agents —— 查看多 Agent 团队状态。"""
    from agent.collaboration.team import TeamManager

    mgr: TeamManager = team_mgr
    team_name = mgr.active_team

    if team_name is None:
        ui.info("当前没有活跃的多 Agent 团队。")
        ui.info("创建团队: TeamCreate  或直接对我说「建个团队」")
        return

    team = mgr.load(team_name)
    if team is None:
        ui.info(f"团队 '{team_name}' 配置文件丢失")
        return

    ui.info(f"")
    ui.info(f"团队: [bold cyan]{team.name}[/bold cyan]")
    ui.info(f"创建时间: {time.strftime('%Y-%m-%d %H:%M', time.localtime(team.created_at))}")
    ui.info(f"Leader:  {team.lead_agent_id}")
    ui.info(f"成员数: {len(team.members)}")

    ui.info("")
    ui.info("成员:")
    for m in team.members:
        status = "● 活跃" if m.is_active is not False else "○ 空闲"
        role = f"({m.agent_type})" if m.agent_type else ""
        leader_flag = "  [bold cyan]← leader[/bold cyan]" if m.name == "team-lead" else ""
        ui.info(f"  {status}  {m.name} {role}{leader_flag}")

    # 显示任务
    if task_list is not None:
        tasks = task_list.list_all()
        if tasks:
            ui.info("")
            ui.info("共享任务:")
            for t in tasks:
                status_icon = {"pending": "○", "in_progress": "●", "completed": "✓"}.get(t.status, "?")
                owner_str = f" [{t.owner}]" if t.owner else ""
                blocked = f" (阻塞于: {', '.join(f'#{b}' for b in t.blocked_by)})" if t.blocked_by else ""
                ui.info(f"  #[bold]{t.id}[/bold] {status_icon} {t.subject}{owner_str}{blocked}")


def _show_tasks(ui: RichCLI, task_list) -> None:
    """/tasks —— 查看共享任务列表。"""
    if task_list is None:
        ui.info("任务列表未初始化（当前无活跃团队）")
        return

    tasks = task_list.list_all()
    if not tasks:
        ui.info("任务列表为空。")
        ui.info("创建团队后，用 TaskCreate 添加任务。")
        return

    ui.info("")
    ui.info("[bold]共享任务列表[/bold]")
    ui.info("")
    for t in tasks:
        status_icon = {"pending": "○", "in_progress": "●", "completed": "✓"}.get(t.status, "?")
        owner_str = f" [dim]{t.owner}[/dim]" if t.owner else ""
        blocked = ""
        if t.blocked_by:
            blocked = f" [dim yellow](阻塞于: {', '.join(f'#{b}' for b in t.blocked_by)})[/dim yellow]"
        blocks = ""
        if t.blocks:
            blocks = f" [dim](阻塞: {', '.join(f'#{b}' for b in t.blocks)})[/dim]"
        ui.info(f"  #[bold]{t.id}[/bold] {status_icon} {t.subject}{owner_str}{blocked}{blocks}")
        if t.description:
            ui.info(f"    {t.description[:120]}")
    ui.info("")
    pending = sum(1 for t in tasks if t.status == "pending")
    active = sum(1 for t in tasks if t.status == "in_progress")
    done = sum(1 for t in tasks if t.status == "completed")
    ui.info(f"任务统计: {done} 完成  |  {active} 进行中  |  {pending} 待开始")


async def _toggle_plan(ui: RichCLI, settings, ctx) -> None:
    """/plan —— 切换规划模式。"""
    current = ctx.permission_mode
    if current == "plan":
        # 退出规划模式
        prev = ctx.extra.pop("_plan_mode_previous", "default")
        ctx.permission_mode = prev
        ctx.extra.pop("_plan_mode_entered", None)
        plan_content = ctx.extra.pop("_plan_content", None)
        ui.info(f"已退出规划模式，权限恢复为: {prev}")
        if plan_content:
            ui.info(f"方案内容已保留在上下文中（{len(plan_content)} 字符）")
    else:
        # 进入规划模式
        ctx.extra["_plan_mode_entered"] = True
        ctx.extra["_plan_mode_previous"] = current
        ctx.permission_mode = "plan"
        ui.info("已进入规划模式（只读）。")
        ui.info("调研完整后，用 ExitPlanMode 提交方案，或用 /plan 切回。")


def _toggle_thinking(
    ui: RichCLI,
    settings,
    provider,
    loop,
    registry,
    raw: str,
) -> None:
    """/think [on|off] —— 开关深度思考模式。"""
    current = getattr(provider, '_enable_thinking', True)

    parts = raw.split(maxsplit=1)
    if len(parts) == 1:
        # 无参数：显示状态并切换
        new_state = not current
    else:
        arg = parts[1].strip().lower()
        if arg in ("on", "1", "true", "enable"):
            new_state = True
        elif arg in ("off", "0", "false", "disable"):
            new_state = False
        else:
            ui.warn(f"用法: /think on|off（当前: {'开' if current else '关'}）")
            return

    # 更新 provider 和 settings
    provider.set_thinking_enabled(new_state)
    settings.enable_thinking = new_state

    # 重新生成系统提示，让模型知道当前是否需要输出 reasoning_content
    new_system = build_system_prompt(settings.workdir, registry, enable_thinking=new_state)
    if settings.system_prompt_append:
        new_system = new_system + "\n\n" + settings.system_prompt_append
    loop._system = new_system

    ui.info(f"深度思考: {'✅ 开' if new_state else '❌ 关'}")


def _show_mcp(ui: RichCLI, mcp_client) -> None:
    """/mcp — 查看 MCP server 连接状态。"""
    from agent.core.extensions.mcp_client import mcp_config_path, load_mcp_config

    config_path = mcp_config_path()
    ui.info(f"MCP 配置文件: {config_path}")
    exists = "✓" if config_path.exists() else "✗"
    ui.info(f"  配置文件存在: {exists}")

    if mcp_client is None:
        ui.info("MCP 未启用（settings.enable_mcp=false 或启动异常）")
        return

    if not mcp_client.available:
        ui.info("MCP SDK 未安装（pip install mcp 启用）")
        return

    config = load_mcp_config()
    if not config:
        ui.info("（无 MCP server 配置。编辑上述文件添加 mcpServers 配置。）")
        return

    ui.info(f"\n配置的 server（{len(config)} 个）:")
    connections = {c.name: c for c in mcp_client.list_connections()}
    for name in config:
        conn = connections.get(name)
        if conn and conn.connected:
            ui.info(f"  [{name}] ✓ 已连接（{len(conn.tools)} 个工具）")
            for t in conn.tools:
                ui.info(f"      - {t.name}: {t.description[:60]}")
        else:
            ui.info(f"  [{name}] ✗ 未连接")

    total_tools = sum(len(c.tools) for c in connections.values())
    ui.info(f"\n共 {len(connections)} 个已连接，{total_tools} 个工具")


def _say_old_placeholder(ui: RichCLI) -> None:
    pass


def _say(ui: RichCLI, settings: Settings, text: str) -> None:
    """用 TTS 朗读一段文字。/say 命令的执行体。"""
    text = text.strip()
    if not text:
        ui.warn("用法: /say <要朗读的文字>")
        return
    try:
        from agent.voice.tts import CosyVoiceTTS
    except ImportError as e:
        ui.error(f"语音模块不可用: {e}")
        return

    tts = CosyVoiceTTS(
        api_key=settings.api_key,
        model=settings.tts_model,
        voice=settings.tts_voice,
        volume=settings.tts_volume,
        speech_rate=settings.tts_speech_rate,
        pitch_rate=settings.tts_pitch_rate,
    )
    ui.info(f"朗读（音色 {settings.tts_voice}）: {text[:60]}{'...' if len(text) > 60 else ''}")
    import time
    t0 = time.time()
    result = tts.speak(text, on_first_data=lambda: ui.info("🔊 开始播放..."))
    elapsed = time.time() - t0
    if result.get("error"):
        ui.error(f"语音合成失败: {result['error']}")
        return
    ui.info(
        f"播放完成（{elapsed:.1f}s，"
        f"音频 {result.get('total_seconds', 0)}s，"
        f"首包延迟 {result.get('first_package_delay_ms', '?')}ms）"
    )


def _listen(ui: RichCLI, settings: Settings, loop: QueryLoop, ctx: ToolContext) -> None:
    """录音→识别成文字。/listen 命令的执行体。

    麦克风录音，Paraformer 实时识别，静音检测自动停止。
    识别出的文字会打印出来；用户可据此验证 STT 是否正常。
    （第三刀会把识别文字直接送给 LLM 做闭环回应。）
    """
    try:
        from agent.voice import create_stt
    except ImportError as e:
        ui.error(f"语音模块不可用: {e}")
        return

    stt = create_stt(
        api_key=settings.api_key,
        model=settings.stt_model,
    )
    ui.info(
        f"正在聆听...（模型 {settings.stt_model}，最多 {settings.stt_max_seconds}s，"
        f"停顿 {settings.stt_silence_seconds}s 自动结束）"
    )

    import sys

    def _on_partial(text: str) -> None:
        # 中间结果实时回显（同行刷新，不走 rich 避免冲突）
        sys.stdout.write(f"\r  识别中: {text}")
        sys.stdout.flush()

    def _on_open() -> None:
        ui.info("麦克风已就绪，请说话")

    import time
    t0 = time.time()
    result = stt.listen(
        max_seconds=settings.stt_max_seconds,
        silence_seconds=settings.stt_silence_seconds,
        silence_threshold=settings.stt_silence_threshold,
        on_partial=_on_partial,
        on_open=_on_open,
    )
    elapsed = time.time() - t0
    sys.stdout.write("\r" + " " * 80 + "\r")  # 清掉中间回显行
    sys.stdout.flush()

    if result.get("error"):
        ui.error(f"语音识别失败: {result['error']}")
        return

    text = result.get("text", "").strip()
    duration = result.get("duration", 0)
    ui.info(f"识别完成（{elapsed:.1f}s，录音 {duration}s）")
    if text:
        ui.info(f"识别结果: {text}")
    else:
        ui.warn("未识别到内容（可能环境噪音或未说话）")


async def _voice_mode(ui: RichCLI, settings: Settings, loop: QueryLoop, ctx: ToolContext) -> None:
    """进入语音对话模式。/voice 命令的执行体。

    连续多轮：听→想→说→听→...，直到说「退出」或 Ctrl+C。
    """
    try:
        from agent.voice.voice_loop import voice_loop
    except ImportError as e:
        ui.error(f"语音模块不可用: {e}")
        return
    await voice_loop(ui, settings, loop, ctx)


async def _realtime_talk(
    ui: RichCLI, settings: Settings, *, use_window: bool = True
) -> None:
    """/talk 命令 —— 实时双工语音对话。

    实时语音/多模态服务由 DashScope 提供，必须使用 DashScope API Key。
    如果当前 LLM 是 deepseek/openai 等其它厂商，settings.api_key 会是另一家 key，
    不能直接用于 DashScope WebSocket 鉴权，因此优先使用独立的 dashscope_api_key。

    Args:
        use_window: 为 True 时优先使用 pywebview 独立窗口 UI；
                    未安装 pywebview 或环境不支持时回退到 RichCLI。

    @author aceFelix
    """
    api_key = (
        settings.dashscope_api_key
        or os.environ.get("DASHSCOPE_API_KEY", "")
        or settings.api_key
        or os.environ.get("OPENAI_API_KEY", "")
    )
    if not api_key:
        ui.error(
            "未配置 DashScope API Key。实时双工语音对话依赖 DashScope 服务，"
            "请设置环境变量 DASHSCOPE_API_KEY，或在 settings.toml 中配置 dashscope_api_key。"
        )
        return

    try:
        from agent.voice.realtime_talk import RealtimeTalk, DEFAULT_WS_URL
    except ImportError as e:
        ui.error(f"实时语音模块不可用: {e}")
        return

    config = {
        "api_key": api_key,
        "model": getattr(settings, "realtime_model", "qwen-audio-3.0-realtime-flash"),
        "voice": getattr(settings, "realtime_voice", "longanqian"),
        "ws_url": getattr(settings, "realtime_ws_url", "") or DEFAULT_WS_URL,
    }

    has_window = False
    window = None
    if use_window:
        try:
            from agent.ui.realtime_window import RealtimeTalkWindow

            # standalone=True：子进程自己启动 RealtimeTalk，父进程只负责窗口。
            # 必须把配置传给窗口，否则子进程里的 RealtimeTalk 拿不到 api_key。
            window = RealtimeTalkWindow(on_close=lambda: None, standalone=True)
            window.set_config(config)
            window.show()
            has_window = window.is_open or True  # show 后立即认为有窗口（线程启动中）
        except ImportError:
            ui.warn("未安装 pywebview，实时聊天将回退到终端界面。")
        except Exception as e:
            ui.warn(f"启动实时聊天窗口失败: {e}，回退到终端界面。")

    if has_window and window is not None:
        # standalone=True 时 RealtimeTalk 在子进程中运行，父进程只需等待窗口关闭。
        while window.is_open:
            await asyncio.sleep(0.2)
    else:
        rt = RealtimeTalk(**config)
        await rt.run(ui)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="jarvis",
        description="个人电脑 AI Agent（借鉴 Claude Code 架构）",
    )
    p.add_argument(
        "--provider",
        choices=["mock", "anthropic", "openai"],
        help="LLM provider（默认 mock，无 key 也能跑）",
    )
    p.add_argument("--model", help="模型名（默认用 provider 默认）")
    p.add_argument("--api-key", help="API key（也可用环境变量）")
    p.add_argument("--base-url", help="API base URL（OpenAI 兼容服务用）")
    p.add_argument("--workdir", help="工作目录（默认当前目录）")
    p.add_argument(
        "--mode",
        choices=["default", "plan", "accept_edits", "yolo"],
        help="权限模式",
    )
    p.add_argument("--max-tokens", type=int, help="单轮最大输出 token")
    p.add_argument("--max-iterations", type=int, help="单次对话最大工具迭代数")
    p.add_argument("--verbose", action="store_true", help="详细输出（含统计）")
    p.add_argument("--debug", action="store_true", help="调试模式（打印异常栈）")
    p.add_argument(
        "--no-boot",
        action="store_true",
        help="跳过启动动画（直接显示横幅）",
    )
    p.add_argument(
        "--with-tray",
        action="store_true",
        help="前台 REPL 同时启动托盘图标（托盘退出=整体退出）",
    )
    p.add_argument(
        "--daemon",
        action="store_true",
        help="常驻模式：后台待命，热键/托盘唤起（阶段五）",
    )
    p.add_argument(
        "--detached",
        action="store_true",
        help=argparse.SUPPRESS,  # 内部参数：已是无窗口子进程
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="无界面模式：stdin 读消息，stdout 写回复（供 cc-connect 等外部桥接）",
    )
    p.add_argument(
        "--acp",
        action="store_true",
        help="ACP (Agent Client Protocol) 模式：JSON-RPC stdio 传输（供 cc-connect 桥接）",
    )
    p.add_argument(
        "--talk",
        action="store_true",
        help="直接启动实时双工语音对话（/talk），需要配置 DashScope API Key",
    )
    return p.parse_args(argv)


def _run_with_watchdog(daemon) -> int:
    """看门狗：daemon 崩溃后自动重启。10 分钟内最多 5 次。"""
    import time as _time
    import sys as _sys

    MAX_RESTARTS = 5
    WINDOW_SECONDS = 600
    FAST_CRASH_SECONDS = 5

    restart_times: list[float] = []
    last_start = _time.time()

    while True:
        try:
            return daemon.run()
        except KeyboardInterrupt:
            return 130
        except BaseException as e:
            now = _time.time()
            elapsed = now - last_start
            restart_times = [t for t in restart_times if now - t < WINDOW_SECONDS]
            if elapsed < FAST_CRASH_SECONDS:
                restart_times.append(now)
            restart_times.append(now)
            if len(restart_times) >= MAX_RESTARTS:
                print(f"看门狗：{WINDOW_SECONDS}s 内崩溃 {len(restart_times)} 次，放弃重启", file=_sys.stderr)
                return 1
            _time.sleep(2)
            print(f"看门狗：daemon 崩溃 ({type(e).__name__})，2s 后重启 ({len(restart_times)}/{MAX_RESTARTS})", file=_sys.stderr)
            daemon.__init__(daemon._settings)
            last_start = _time.time()


async def repl_headless(settings: Settings) -> int:
    """无界面模式：stdin 读消息，stdout 写回复。

    供 cc-connect 等外部桥接工具调用。输出格式：
    - 每条回复以 \\n---JARVIS-REPLY---\\n 分隔
    - 思考内容前缀 [THINK]
    - 工具进度前缀 [TOOL]
    """
    provider = _build_provider(settings)
    registry: ToolRegistry = build_default_registry()
    checker = _build_checker(settings)
    orchestrator = ToolOrchestrator(registry=registry, permission_checker=checker)

    # MCP
    mcp_client = None
    if settings.enable_mcp:
        try:
            from agent.core.extensions.mcp_client import MCPClient, load_mcp_config
            from agent.core.tool import register_dynamic_tools
            mcp_client = MCPClient()
            if mcp_client.available:
                config = load_mcp_config()
                if config:
                    results = await mcp_client.connect_all(config)
                    connected = sum(1 for v in results.values() if v)
                    if connected:
                        register_dynamic_tools(registry, mcp_client)
        except Exception:
            pass

    from agent.core.tool import register_subagent_tool
    register_subagent_tool(registry, provider=provider, permission_mode=settings.permission_mode)

    system_prompt = build_system_prompt(settings.workdir, registry, enable_thinking=settings.enable_thinking)
    if settings.system_prompt_append:
        system_prompt = system_prompt + "\n\n" + settings.system_prompt_append

    model = settings.model or provider.default_model
    loop = QueryLoop(
        provider=provider, registry=registry, orchestrator=orchestrator,
        system=system_prompt, model=model,
        max_iterations=settings.max_iterations, max_tokens=settings.max_tokens,
        temperature=settings.temperature,
        enable_compaction=settings.context_compaction,
        compaction_threshold=settings.compaction_threshold,
        keep_recent_messages=settings.keep_recent_messages,
        vendor_fallback=settings.vendor_fallback,
        custom_models=settings.custom_models,
    )

    messages: list[Message] = []
    from agent.core.context import ToolContext
    import asyncio as _aio

    ctx = ToolContext(
        messages=messages,
        workdir=settings.workdir,
        abort_event=_aio.Event(),
    )

    # 告诉 cc-connect 贾维斯已就绪
    sys.stdout.write("---JARVIS-READY---\n")
    sys.stdout.flush()

    while True:
        line = await _aio.to_thread(sys.stdin.readline)
        if not line:
            break
        text = line.strip()
        if not text:
            continue

        # 回收集回调
        reply_parts: list[str] = []

        class _HeadlessUI:
            def info(self, *a, **kw): pass
            def warn(self, *a, **kw): pass
            def error(self, *a, **kw): pass
            def assistant_text(self, t):
                reply_parts.append(t)
            def assistant_thinking(self, t):
                sys.stdout.write(f"[THINK]{t}")
                sys.stdout.flush()
            verbose = False

        ctx.ui = _HeadlessUI()
        ctx.on_assistant_text = None

        try:
            await loop.run(text, ctx)
        except Exception as e:
            reply_parts.append(f"[错误] {e}")

        sys.stdout.write("".join(reply_parts))
        sys.stdout.write("\n---JARVIS-REPLY---\n")
        sys.stdout.flush()

    await provider.close()
    return 0


def _run_acp(settings: Settings) -> int:
    """ACP 模式：JSON-RPC stdio 传输，供 cc-connect 桥接贾维斯到 IM 平台。

    cc-connect 通过 ACP (Agent Client Protocol) 框架调用贾维斯，
    本函数在 agent 生命周期内阻塞，处理 stdin JSON-RPC 消息。
    """
    import asyncio as _aio

    # 构建 agent 核心（和 repl() 相同的装配逻辑）
    provider = _build_provider(settings)
    registry: ToolRegistry = build_default_registry()
    checker = _build_checker(settings)
    orchestrator = ToolOrchestrator(registry=registry, permission_checker=checker)

    # MCP（ACP 模式跳过：MCP 子进程输出会污染 stdout JSON-RPC，且连接超时导致启动失败）
    # 如需要 MCP 工具，在 jarvis daemon 模式下连接，IM 通过 ACP 复用 daemon 内的 MCP 连接。

    from agent.core.tool import register_subagent_tool
    register_subagent_tool(registry, provider=provider, permission_mode=settings.permission_mode)

    system_prompt = build_system_prompt(settings.workdir, registry, enable_thinking=settings.enable_thinking)
    if settings.system_prompt_append:
        system_prompt = system_prompt + "\n\n" + settings.system_prompt_append

    model = settings.model or provider.default_model
    loop = QueryLoop(
        provider=provider, registry=registry, orchestrator=orchestrator,
        system=system_prompt, model=model,
        max_iterations=settings.max_iterations, max_tokens=settings.max_tokens,
        temperature=settings.temperature,
        enable_compaction=settings.context_compaction,
        compaction_threshold=settings.compaction_threshold,
        keep_recent_messages=settings.keep_recent_messages,
        vendor_fallback=settings.vendor_fallback,
        custom_models=settings.custom_models,
    )

    messages: list[Message] = []
    from agent.core.context import ToolContext

    ctx = ToolContext(
        messages=messages,
        workdir=settings.workdir,
        abort_event=_aio.Event(),
    )

    from agent.acp import JarvisACP
    acp = JarvisACP(settings, provider, loop, ctx, messages)

    # 阻塞式 stdin 读取循环，直到 stdin 关闭或进程退出
    acp.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings(workdir=args.workdir)

    # CLI 参数覆盖配置（最高优先级）
    overrides: dict[str, object] = {}
    if args.provider:
        overrides["provider"] = args.provider
    if args.model:
        overrides["model"] = args.model
    if args.api_key:
        overrides["api_key"] = args.api_key
    if args.base_url:
        overrides["base_url"] = args.base_url
    if args.workdir:
        overrides["workdir"] = args.workdir
    if args.mode:
        overrides["permission_mode"] = parse_mode(args.mode)
    if args.max_tokens:
        overrides["max_tokens"] = args.max_tokens
    if args.max_iterations:
        overrides["max_iterations"] = args.max_iterations
    if args.verbose:
        overrides["verbose"] = True
    if args.debug:
        overrides["debug"] = True
    if args.no_boot:
        overrides["boot_animation"] = False
    settings = settings.with_overrides(**overrides)

    # 切到工作目录（不存在则自动创建）
    try:
        os.makedirs(settings.workdir, exist_ok=True)
        os.chdir(settings.workdir)
    except OSError as e:
        print(f"无法进入工作目录 {settings.workdir}: {e}", file=sys.stderr)
        return 1

    # 直接启动实时双工语音对话
    if args.talk:
        ui = RichCLI(verbose=settings.verbose, boot_animation=not args.no_boot)
        try:
            return asyncio.run(_realtime_talk(ui, settings))
        except KeyboardInterrupt:
            return 130

    # ACP 模式：cc-connect 通过 JSON-RPC over stdio 桥接贾维斯到 IM 平台
    if args.headless or args.acp:
        return _run_acp(settings)

    # 常驻模式路由
    if args.daemon:
        # 跨平台后台启动：若当前不是 --detached 模式，先 fork 一个
        # detached 子进程（Windows: pythonw.exe / macOS: start_new_session），
        # 主进程立刻退出。--detached 由 launch_detached_daemon 注入，
        # 表示"我已经是后台子进程了，直接 run"。
        # Linux: launch_detached_daemon 返回 1，回退到前台运行 daemon。
        if not args.detached:
            from agent.daemon.daemon import launch_detached_daemon, _is_detached
            script = os.path.abspath(__file__)
            rc = launch_detached_daemon(script, settings.workdir)
            if rc == 0:
                # detached 子进程无 stdout，print 会抛异常，需保护
                if not _is_detached():
                    print("✓ 贾维斯已后台启动（无窗口模式）")
                    print("  托盘图标稍后出现，可关闭此窗口")
                    print("  日志: ~/.jarvis/daemon.log")
                return 0
            # fork 失败（Linux 或无 pythonw.exe），回退到前台运行
            if not _is_detached():
                print("⚠ 后台启动不可用，回退到前台模式", file=sys.stderr)
        try:
            from agent.daemon import JarvisDaemon
        except ImportError as e:
            print(f"常驻模块不可用: {e}", file=sys.stderr)
            return 1
        daemon = JarvisDaemon(settings)
        # 看门狗：daemon 崩溃后自动重启，10 分钟内最多重启 5 次
        return _run_with_watchdog(daemon)

    try:
        return asyncio.run(repl(settings, with_tray=args.with_tray))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
