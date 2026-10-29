"""jarvis init —— 交互式首次配置引导。

三种粒度（阶段 12 P4 区分）：

- ``/init``（默认）= **首启三问**：厂商 / API Key / 模型，其余全默认。
  模型清单**实时向厂商拉取**（`agent/llm/model_catalog.py`），不由内置表替用户决定；
  拉不到才用内置兑底并明说，也可以手动输入型号。工作目录默认当前目录。
  另外还有一问是**条件出现**的：语音（阿里云百炼）API Key——LLM 选百炼时会自动
  复用它的 key、已经配过的也不问，只有“LLM 用别家且语音还缺”时才问。
- ``/init full`` = 完整引导（厂商列表 → 自定义 base_url → 模型名 → 模型类型 →
  API Key → 连接测试）。换厂商、接自建兼容服务、改多模态时用它。
- ``/init voice`` = 只配语音（阿里云百炼）API Key。因为那一问在“已配置”时会被首启
  引导跳过（正确的），否则用户想**更换**时就没有任何交互入口了。

三者共用同一套写盘（`_save_config` → `models_config.save_init_model`），
不各写一份。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.commands.router import CommandContext
    from agent.ui.cli import RichCLI


# ── 厂商表 ──
# 单一来源在 agent/config/vendors.py：`/init` 与 `/models` 的 base_url 推断
# 原先各写一份，已经漂移过（DeepSeek 少了 `/v1`、小米 MiMo 只在一边有）。
# 这里只做别名，不再复制内容——否则下一次又是两张表。
from agent.config.vendors import VENDORS as _VENDORS  # noqa: E402


def _pick_vendor(ui: RichCLI) -> dict | None:
    """交互式选择厂商（↑↓ 箭头导航 + 输入字符实时筛选）。"""
    from agent.ui.terminal_picker import pick_from_list

    items = [
        (v["key"], v["name"], f"{v['desc']}  |  {v['api_format_desc']}")
        for v in _VENDORS
    ]

    ui._console.print("\n[bold cyan]═══ J.A.R.V.I.S 首次配置 ═══[/bold cyan]\n")
    ui._console.print("选择你的 LLM 厂商（↑↓ 移动，输入字符筛选，Enter 确认，Esc 取消）:\n")

    picked = pick_from_list(items, title="厂商选择")
    if picked is None:
        return None

    for v in _VENDORS:
        if v["key"] == picked:
            return v
    return None


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
    """选择模型类型：纯文本 or 多模态（↑↓ 箭头导航）。"""
    from agent.ui.terminal_picker import pick_from_list

    ui._console.print("\n[bold]模型类型:[/bold]")
    items = [
        ("text", "纯文本（text）", "不支持图片输入，适合对话和代码"),
        ("multimodal", "多模态（multimodal）", "支持图片/视觉输入"),
    ]
    picked = pick_from_list(items, title="模型类型", current="text")
    return picked if picked else "text"


def _ask_api_key(ui: RichCLI, vendor: dict) -> str | None:
    """询问 API Key。"""
    ui._console.print(f"\n[bold]厂商:[/bold] {vendor['name']}")
    ui._console.print(
        "[dim]API Key 仅保存在本地 ~/.jarvis/models.toml（并同步写入系统凭据管理器）[/dim]"
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
    """测试 API 连接：根据厂商 api_format 选择正确的 Provider 发请求。"""
    try:
        from agent.llm.provider_registry import PROVIDER_REGISTRY

        api_fmt = vendor.get("api_format", "openai")
        meta = PROVIDER_REGISTRY.get(api_fmt)
        if meta is None:
            return False, f"未知的 api_format: {api_fmt}"

        # 构造参数：只传 api_key 和 base_url（有则传）
        kwargs: dict = {"api_key": api_key}
        base_url = vendor.get("default_base_url")
        if base_url:
            kwargs["base_url"] = base_url
        # 其余参数用默认值
        for key in ("model", "enable_thinking", "thinking_budget", "model_type"):
            kwargs.setdefault(key, None)
        kwargs["model_type"] = "text"
        kwargs["enable_thinking"] = False

        # 连通性测试用**独占连接池**：这里只是临时探一下（拿到第一个文本块就
        # return），用完立刻 close。若与主对话共用共享池，一次 /init 就可能把
        # 正在使用的连接池一并关掉（与 2026-10 画像提炼事故同源，见
        # docs/fixlogs/shared-http-pool-close-kills-chat.md）。
        # 仅声明了该构造开关的 provider 才传（DashScope 原生 SDK / Zai 不接）。
        # @author aceFelix
        if "dedicated_client" in getattr(meta, "init_keys", ()):
            kwargs["dedicated_client"] = True

        provider = meta.create(**kwargs)

        from agent.core.message import Message, TextContent
        msgs = [Message(role="user", content=[TextContent(text="hi")])]

        model_name = vendor["default_model"] or getattr(provider, "default_model", "")
        async for event in provider.stream(
            model=model_name,
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
    """保存配置到 ~/.jarvis/models.toml（模型域专属文件）。

    2026-09 模型域拆分后，写入实现统一在 agent/config/models_config.py
    （save_init_model），与 /models 添加自定义模型同口径：
    1. 在 [llm.custom_models] 中写入模型完整配置
    2. 设置 last_model 让下次启动默认使用该模型
    3. 兜底 provider/api_format 顶层键（仅缺失时补写）

    @author aceFelix
    """
    from agent.config.models_config import save_init_model

    return save_init_model(
        vendor_key=vendor["key"],
        model_name=vendor.get("default_model", ""),
        api_key=api_key,
        base_url=vendor.get("default_base_url", ""),
        api_format=vendor.get("api_format", "openai"),
        model_type=model_type,
    )


def _apply_to_session(ctx: object, saved_model: str) -> str:
    """把刚写进 models.toml 的模型配置**热生效到当前会话**。

    为什么必须有这一步（真机实测暴露的 bug）：`/init` 只把配置写盘，而当前 REPL
    的 `ctx.provider` 还是启动时按旧配置建的。于是用户看到“✓ 连接成功”，紧接着
    说一句话却报 `Connection error`——因为那一句走的还是旧 provider / 旧 key。

    做法与 `/model` 完全一致（不另开一条路）：重读配置 → 把新值搬进现有 settings
    对象（其它地方持有同一引用，不能换成新对象）→ 复用 `_switch_model` 重建
    provider/loop。

    Args:
        ctx: 命令上下文；缺 provider/registry 等字段时退化为“重启后生效”。
        saved_model: 刚保存的模型名。

    Returns:
        给用户看的生效状态（已生效 / 需重启的原因）。

    @author aceFelix
    """
    settings = getattr(ctx, "settings", None)
    if settings is None or getattr(ctx, "provider", None) is None:
        return "重启 jarvis 后生效"

    from agent.config.settings import load_settings

    try:
        fresh = load_settings(workdir=getattr(settings, "workdir", "") or "")
    except Exception as e:  # noqa: BLE001 - 重读失败不该把“已保存”变成失败
        return f"配置已保存，但重新加载失败（{e}）；重启后生效"

    # 运行中 provider 用的那份端点快照：必须在原地更新**之前**留一份。
    # `_switch_model` 拿它判断「新配置和正在跑的 provider 是否有差异」；
    # 若直接拿更新后的 settings 去比，差异已经被自己抹平 → 永远判定无需重建 →
    # 复用启动时那个旧 provider（旧 base_url / 旧 key），下一句话仍会报
    # Connection error——正是真机那次「✓ 连接成功」紧跟失败的原因。
    from dataclasses import replace

    baseline = replace(settings)

    # 原地更新：ctx.settings 被多处共享，换对象会让别处继续用旧值
    for name in ("provider", "api_format", "model", "last_model", "api_key",
                 "base_url", "custom_models", "models"):
        if hasattr(fresh, name):
            try:
                setattr(settings, name, getattr(fresh, name))
            except Exception:  # noqa: BLE001 - 个别字段只读就跳过
                pass

    try:
        from agent.model_manager import _switch_model

        result = _switch_model(
            ctx.ui, settings, ctx.provider, ctx.registry, ctx.orchestrator,
            getattr(ctx, "system_prompt", ""), saved_model, baseline=baseline,
        )
    except Exception as e:  # noqa: BLE001
        return f"配置已保存，但切到新模型失败（{e}）；重启后生效"

    if result:
        ctx.provider, ctx.loop, ctx.model = result
        return f"已在本会话生效：{settings.provider} / {saved_model}"
    return "配置已保存；重启 jarvis 后生效"


async def _handle_voice_init(ui: Any, settings: Any = None) -> bool:
    """/init voice —— 只配语音（阿里云百炼）API Key。

    为什么需要单独一个入口（2026-10 实测后补）：语音 Key “已配置”时会被首启引导
    跳过——这个跳过本身是对的（不该每次启动都问）。但跳过之后，用户想**更换**就
    没有任何交互入口了；而“已经配了”往往只是环境变量被 ``env.py`` 灌进了配置
    字段，用户自己可能都不知道有这回事，于是更找不到改的地方。

    Args:
        ui: 终端 UI。
        settings: 当前配置；为 ``None``（``jarvis --init voice`` 独立进程）时现读。

    Returns:
        恒为 True（与其它命令处理器一致，表示已消费该输入）。

    @author aceFelix
    """
    from agent.config import voice_key as vk
    from agent.config.models_config import save_dashscope_api_key, user_models_path

    if settings is None:
        try:
            from agent.config.settings import load_settings

            settings = load_settings()
        except Exception:  # noqa: BLE001 - 读不到就按“未配置”展示
            settings = None

    ui._console.print("\n[bold cyan]═══ 语音（阿里云百炼）API Key ═══[/bold cyan]\n")
    ui.info(f"当前：{vk.describe_detail(settings)}")

    if vk.llm_is_dashscope(settings):
        # 这时语音本来就能用（复用 LLM 的 key）。说清楚“为什么还要单独配”，
        # 否则用户会以为这是在解决一个不存在的问题。
        ui.info(
            "当前 LLM 就是阿里云百炼，语音会直接复用它的 key，不用配也能说话；"
            "只有想让语音走**另一个账号**时才需要填这里"
        )

    try:
        key = ui.ask_user("新语音 API Key（阿里云百炼；回车保留现状）").strip()
    except (EOFError, KeyboardInterrupt):
        ui.info("已取消，现状未改动")
        return True

    if not key:
        ui.info("未改动")
        return True

    path = save_dashscope_api_key(key)
    if path is None:
        ui.error(f"写入失败，请手改 {user_models_path()} 的 dashscope_api_key")
        return True

    ui._console.print(f"[green]✓ 已写入 {path}[/green]")
    ui._console.print(
        "[dim]它优先于环境变量 DASHSCOPE_API_KEY：配置文件写了就以配置为准。\n"
        "重开语音模式（/voice 或 /talk）后生效。[/dim]"
    )
    return True


async def handle_init(ctx: CommandContext, stripped: str) -> bool:
    """处理 /init：默认走首启三问；``/init full`` 走完整引导。

    也作为 jarvis --init / --init full CLI 入口。
    """
    tokens = stripped.split()[1:]
    if tokens and tokens[0].lower() in ("full", "-f", "--full"):
        return await _handle_full_init(ctx)

    if ctx.ui is None:
        return False
    if tokens and tokens[0].lower() in ("voice", "-v", "--voice"):
        return await _handle_voice_init(ctx.ui, getattr(ctx, "settings", None))

    from agent.config.onboarding import run_quick_setup
    # 传 ctx 而不只是 settings：写完盘要能把新配置热生效到当前会话
    await run_quick_setup(ctx.ui, getattr(ctx, "settings", None), ctx=ctx)
    return True


async def _handle_full_init(ctx: CommandContext) -> bool:
    """完整引导（原 U-01 流程，一句未改）。

    Args:
        ctx: 命令上下文（CLI 入口会用一个只带 ui 的假上下文）。

    Returns:
        恒为 True（与其它命令处理器一致，表示已消费该输入）。

    @author aceFelix
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
        ui.info("跳过 API Key（可稍后在 ~/.jarvis/models.toml 中手动配置）")
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
    ui._console.print(f"[dim]{_apply_to_session(ctx, vendor.get('default_model', ''))}[/dim]")
    ui._console.print("\n[bold cyan]现在运行 jarvis 即可开始使用！[/bold cyan]")

    return True


def run_init_cli_from_args(mode: str = "") -> int:
    """``jarvis --init [full|voice]`` 的 CLI 入口（main 只负责传参）。

    为什么搬到这里：``agent/main.py`` 已接近（实际是已超过）800 行上限，而 --init
    的三种粒度本来就是本模块的事——参数解析与 UI 构造没理由继续堆在入口文件里。

    Args:
        mode: ``--init`` 跟的值。空串 = 裸 ``--init``（默认三问）；认不出的值也
            回落到默认三问，不报错（用户写错一个词不该把启动拦住）。

    Returns:
        进程退出码（恒为 0：交互式引导被 Ctrl+C 中断也不算失败）。

    @author aceFelix
    """
    from agent.ui.cli import RichCLI

    ui = RichCLI(verbose=False, boot_animation=False)
    name = (mode or "").strip().lower()
    try:
        asyncio.run(run_init_cli(
            ui,
            full=name in ("full", "-f", "--full"),
            voice=name in ("voice", "-v", "--voice"),
        ))
    except KeyboardInterrupt:
        pass
    return 0


async def run_init_cli(ui: RichCLI, *, full: bool = False,
                       voice: bool = False) -> None:
    """jarvis --init CLI 入口（默认三问，full=True 完整引导，voice=True 只配语音）。"""
    from dataclasses import dataclass

    @dataclass
    class _FakeCtx:
        ui: RichCLI

    if voice:
        await _handle_voice_init(ui)
    elif full:
        await _handle_full_init(_FakeCtx(ui=ui))  # type: ignore[arg-type]
    else:
        from agent.config.onboarding import run_quick_setup

        await run_quick_setup(ui)
