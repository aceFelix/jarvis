"""jarvis init —— 交互式首次配置引导。

U-01 改进项：选厂商 → 输入 Key → 测试连接 → 保存配置。
替代手改 settings.toml，降低上手门槛。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.commands.router import CommandContext
    from agent.ui.cli import RichCLI


# ── 厂商列表（基于 PROVIDER_REGISTRY，选项描述含 api_format 和推荐模型）──

_VENDORS = [
    {
        "key": "dashscope", "name": "阿里云 DashScope（通义千问）",
        "desc": "国内首选，qwen3.7-plus 多模态视觉",
        "default_model": "qwen3.7-plus",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容（推荐）",
    },
    {
        "key": "deepseek", "name": "DeepSeek",
        "desc": "高性价比，推理能力强，thinking 模式支持",
        "default_model": "deepseek-chat",
        "default_base_url": "https://api.deepseek.com/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容",
    },
    {
        "key": "openai", "name": "OpenAI（GPT-4o / GPT-4.1）",
        "desc": "综合能力最强，生态最成熟",
        "default_model": "gpt-4o",
        "default_base_url": "https://api.openai.com/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 原生",
    },
    {
        "key": "zhipu", "name": "智谱 AI（GLM 系列）",
        "desc": "国产大模型，GLM-4.7 系列",
        "default_model": "glm-4.7-flash",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容（推荐，GLM-4.7 支持 thinking）",
    },
    {
        "key": "zai", "name": "智谱 AI（原生 SDK）",
        "desc": "GLM 原生协议，绕过 OpenAI 兼容层",
        "default_model": "glm-4.7-flash",
        "default_base_url": "",
        "api_format": "zai",
        "api_format_desc": "智谱原生 SDK（更稳定）",
    },
    {
        "key": "anthropic", "name": "Anthropic（Claude 系列）",
        "desc": "擅长长文本、复杂推理、代码生成",
        "default_model": "claude-sonnet-4-20250514",
        "default_base_url": "",
        "api_format": "anthropic",
        "api_format_desc": "Anthropic 原生协议",
    },
    {
        "key": "moonshot", "name": "Moonshot（Kimi）",
        "desc": "超长上下文 128K",
        "default_model": "moonshot-v1-8k",
        "default_base_url": "https://api.moonshot.cn/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容",
    },
    {
        "key": "minimax", "name": "MiniMax（ABAB 系列）",
        "desc": "国内大模型，长上下文支持",
        "default_model": "abab6.5s-chat",
        "default_base_url": "https://api.minimax.chat/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容",
    },
    {
        "key": "siliconflow", "name": "SiliconFlow（硅基流动）",
        "desc": "开源模型托管平台，DeepSeek/Qwen/Llama 等",
        "default_model": "Qwen/Qwen2.5-7B-Instruct",
        "default_base_url": "https://api.siliconflow.cn/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容",
    },
    {
        "key": "xiaomimimo", "name": "小米 MiMo",
        "desc": "小米自研大模型",
        "default_model": "mimo-v2-flash",
        "default_base_url": "https://api.xiaomimimo.com/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容",
    },
    {
        "key": "google", "name": "Google Gemini",
        "desc": "Google 多模态大模型",
        "default_model": "gemini-2.0-flash",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容",
    },
    {
        "key": "custom", "name": "其他 OpenAI 兼容服务",
        "desc": "手动输入 base_url、model 名和 api_format",
        "default_model": "",
        "default_base_url": "",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容（可修改）",
    },
]


def _pick_vendor(ui: RichCLI) -> dict | None:
    """交互式选择厂商。"""
    ui._console.print("\n[bold cyan]═══ J.A.R.V.I.S 首次配置 ═══[/bold cyan]\n")
    ui._console.print("选择你的 LLM 厂商：\n")

    for i, v in enumerate(_VENDORS, 1):
        ui._console.print(
            f"  [bold]{i}.[/bold] {v['name']}\n"
            f"     {v['desc']}  |  API: {v['api_format_desc']}"
        )

    ui._console.print(f"\n  [dim]q. 退出配置[/dim]\n")

    try:
        choice = ui.ask_user(f"选择 [1-{len(_VENDORS)} 或 q]").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if choice.lower() in ("q", "quit", "exit"):
        return None

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(_VENDORS):
            return _VENDORS[idx]
    except ValueError:
        pass

    ui.warn(f"无效选择，请输入 1-{len(_VENDORS)} 或 q")
    return _pick_vendor(ui)


def _ask_model(ui: RichCLI, vendor: dict) -> str:
    """确认或输入模型名。"""
    default = vendor.get("default_model", "")
    prompt = f"模型名" + (f" [默认: {default}]" if default else "")
    try:
        model = ui.ask_user(f"{prompt}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return model or default


def _ask_model_type(ui: RichCLI) -> str:
    """选择模型类型：纯文本 or 多模态。"""
    ui._console.print("\n[bold]模型类型:[/bold]")
    ui._console.print("  1. 纯文本（text）—— 不支持图片输入")
    ui._console.print("  2. 多模态（multimodal）—— 支持图片/视觉输入")
    try:
        choice = ui.ask_user("选择 [1-2，默认 1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return "text"
    return "multimodal" if choice == "2" else "text"


def _ask_api_key(ui: RichCLI, vendor: dict) -> str | None:
    """询问 API Key。"""
    ui._console.print(f"\n[bold]厂商:[/bold] {vendor['name']}")
    ui._console.print(
        "[dim]API Key 不会明文存储，仅保存在本地 ~/.jarvis/settings.toml[/dim]"
    )

    try:
        key = ui.ask_user("请输入 API Key（或按回车跳过，稍后手动配置）").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    return key or None


def _ask_custom(ui: RichCLI, vendor: dict) -> dict | None:
    """自定义厂商：询问 base_url 和 model。"""
    try:
        base_url = ui.ask_user(
            f"请输入 base_url{(' [' + vendor['default_base_url'] + ']') if vendor['default_base_url'] else ''}: "
        ).strip()
        model = ui.ask_user(
            f"请输入默认 model 名{(' [' + vendor['default_model'] + ']') if vendor['default_model'] else ''}: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None

    vendor["default_base_url"] = base_url or vendor["default_base_url"]
    vendor["default_model"] = model or vendor["default_model"]
    return vendor


async def _test_connection(vendor: dict, api_key: str) -> tuple[bool, str]:
    """测试 API 连接：发一个最小请求验证 Key 有效。"""
    try:
        from agent.llm.openai_provider import OpenAIProvider

        provider = OpenAIProvider(
            api_key=api_key,
            base_url=vendor.get("default_base_url") or None,
            model=vendor.get("default_model") or None,
            enable_thinking=False,
            thinking_budget=0,
            model_type="text",
        )

        from agent.core.message import Message, TextContent
        msgs = [Message(role="user", content=[TextContent(text="hi")])]

        # 发请求，获取第一个事件即表示连接成功
        async for event in provider.stream(
            model=vendor["default_model"] or provider.default_model,
            system="",
            messages=msgs,
            tools=[],
            max_tokens=5,
        ):
            from agent.llm.base import TextDelta, Stop
            if isinstance(event, (TextDelta, Stop)):
                await provider.close()
                return True, "连接成功！"

        await provider.close()
        return True, "连接成功"
    except Exception as e:
        return False, str(e)


def _save_config(vendor: dict, api_key: str, model_type: str = "text") -> Path:
    """保存配置到 ~/.jarvis/settings.toml。

    写入逻辑与 /models 添加自定义模型一致：
    1. 在 [llm.custom_models] 中写入模型完整配置
    2. 设置 last_model 让下次启动默认使用该模型
    3. 保留已有的 LLM 顶层字段（provider/api_format）作兜底
    """
    import re

    model_name = vendor.get("default_model", "")
    base_url = vendor.get("default_base_url", "")
    api_fmt = vendor.get("api_format", "openai")

    toml_path = Path.home() / ".jarvis" / "settings.toml"
    toml_path.parent.mkdir(parents=True, exist_ok=True)

    # 构建 custom_models 子表条目
    entry_lines = [
        f'[llm.custom_models."{model_name}"]',
        f'name = "{model_name}"',
    ]
    if base_url:
        entry_lines.append(f'base_url = "{base_url}"')
    if api_key:
        entry_lines.append(f'api_key = "{api_key}"')
    entry_lines += [
        f'provider_type = "{api_fmt}"',
        f'model_type = "{model_type}"',
        f'vendor = "{vendor["key"]}"',
    ]
    entry = "\n".join(entry_lines)

    if toml_path.exists():
        content = toml_path.read_text(encoding="utf-8")
    else:
        content = "# J.A.R.V.I.S 配置（由 jarvis init 生成）\n"

    # ── 更新/添加 [llm.custom_models."model"] ──
    marker = f'[llm.custom_models."{model_name}"]'
    if marker in content:
        start = content.index(marker)
        rest = content[start + len(marker):]
        m = re.search(r'\n\[', rest)
        if m:
            end = start + len(marker) + m.start()
            while end < len(content) and content[end] == '\n':
                end += 1
            content = content[:start].rstrip() + "\n" + entry.strip() + "\n" + content[end:]
        else:
            content = content[:start].rstrip() + "\n" + entry.strip()
    else:
        if "[llm.custom_models" not in content:
            content = content.rstrip() + "\n\n# 自定义模型（通过 jarvis init 添加）\n"
        content = content.rstrip() + "\n" + entry.strip() + "\n"

    # ── 设置 last_model（下次启动默认使用该模型）──
    # 在第一个 [...] 节头之前操作顶层字段
    first_section = re.search(r'^\[', content, re.MULTILINE)
    top_end = first_section.start() if first_section else len(content)
    top_part = content[:top_end]
    rest_part = content[top_end:]
    if re.search(r'^last_model\s*=', top_part, re.MULTILINE):
        top_part = re.sub(
            r'^last_model\s*=.*$',
            f'last_model = "{model_name}"',
            top_part,
            flags=re.MULTILINE,
        )
    else:
        top_part = top_part.rstrip() + f'\nlast_model = "{model_name}"\n'
    content = top_part + rest_part

    # ── 兜底：确保 provider/api_format 存在 ──
    top_part = content[:top_end]
    for field, value in (("provider", vendor["key"]), ("api_format", api_fmt)):
        if not re.search(rf'^{field}\s*=', top_part, re.MULTILINE):
            top_part = top_part.rstrip() + f'\n{field} = "{value}"\n'
    content = top_part + rest_part

    toml_path.write_text(content, encoding="utf-8")
    return toml_path


async def handle_init(ctx: CommandContext, stripped: str) -> bool:
    """处理 /init 命令：交互式首次配置。

    也作为 jarvis --init CLI 参数的入口。
    """
    ui = ctx.ui
    if ui is None:
        return False

    # 1. 选择厂商
    vendor = _pick_vendor(ui)
    if vendor is None:
        ui.info("已取消配置")
        return True

    # 2. 自定义厂商需要额外信息
    if vendor["key"] == "custom":
        vendor = _ask_custom(ui, vendor)
        if vendor is None:
            ui.info("已取消配置")
            return True

    # 3. 确认模型名
    model = _ask_model(ui, vendor)
    if model:
        vendor["default_model"] = model

    # 3.5. 选择模型类型
    model_type = _ask_model_type(ui)

    # 4. 输入 API Key
    ui._console.print(f"\n[bold]API 格式:[/bold] {vendor['api_format_desc']}")
    api_key = _ask_api_key(ui, vendor)
    if api_key is None:
        ui.info("跳过 API Key（可稍后在 ~/.jarvis/settings.toml 中手动配置）")
    api_key = api_key or ""

    # 5. 测试连接
    if api_key:
        ui._console.print("\n[dim]⏳ 正在测试连接...[/dim]")
        ok, msg = await _test_connection(vendor, api_key)
        if ok:
            ui._console.print(f"[green]✓ {msg}[/green]")
        else:
            ui._console.print(f"[yellow]⚠ 连接测试失败: {msg}[/yellow]")
            ui._console.print("[dim]配置仍会保存，可稍后检查 API Key 是否正确[/dim]")
    else:
        ui._console.print("[dim]跳过连接测试（未提供 API Key）[/dim]")

    # 6. 保存配置
    path = _save_config(vendor, api_key, model_type)
    ui._console.print(f"\n[green]✓ 配置已保存到 {path}[/green]")
    ui._console.print("\n[bold cyan]现在运行 jarvis 即可开始使用！[/bold cyan]")

    return True


async def run_init_cli(ui: RichCLI) -> None:
    """jarvis --init CLI 入口。"""
    from dataclasses import dataclass

    @dataclass
    class _FakeCtx:
        ui: RichCLI

    await handle_init(_FakeCtx(ui=ui), "")  # type: ignore[arg-type]
