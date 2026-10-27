"""配置命令处理器。

两类能力：

1. `jarvis config show` / `/config show`（U-05）——查看当前生效的完整配置
   （含多层合并结果）：LLM、语音、权限、工具、MCP 状态、自定义模型、环境变量覆盖。
2. `/config list` / `/config set`（阶段 12 P2）——与模型侧的 `ConfigList` /
   `ConfigSet` 走**同一条写入路径**（`agent/config/config_ops.py`），只是入口不同：
   人手敲的命令不需要经过权限管线审批，但需要自己拥有最终决定权。

两个入口的边界分工：

| 入口 | 谁在改 | 禁改项 | `confirm` 项怎么过 |
|---|---|---|---|
| `ConfigSet`（模型） | 模型提议 | 权限层 DENY | W1 管线的 ASK 弹框 |
| `/config set`（人） | 人自己 | 同样拒绝（护栏是配置本身的属性）| **两段式**：先看预览，再加 `--yes` |

为什么终端用两段式而不是交互 prompt：REPL 的主循环不接受嵌套输入，而且
“先看一眼原值 → 新值再决定”比一个 y/N 弹框更有信息量。这与 `/memory sync`
预览 + `yes` 确认同一体例。

@author aceFelix
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from agent.config.config_ops import (
    ENTRY_TERMINAL,
    apply_config_patch,
    parse_cli_value,
    render_cli_value,
    render_spec_list,
)
from agent.config.config_specs import (
    TIER_CONFIRM,
    forbidden_reason,
    get_spec,
    human_value,
    read_value,
    validate_value,
)
from agent.ui.markdown_renderer import render_table, render_panel
from agent.utils.mask import mask_key as _mask_key

if TYPE_CHECKING:
    from agent.commands.router import CommandContext
    from agent.ui.cli import RichCLI


# ── 辅助 ──

# _mask_key 已从 agent.utils.mask 导入（共享脱敏工具）

def _bool_icon(val: bool) -> str:
    """布尔值图标。"""
    return "✓ 开启" if val else "✗ 关闭"


# ── 各配置分组展示 ──

def _show_llm(ui: RichCLI, settings: Any, provider: Any) -> None:
    """LLM 配置面板。"""
    rows = [
        ["provider", settings.provider or "(自动检测)"],
        ["api_format", settings.api_format],
        ["model", settings.model or "(默认)"],
        ["base_url", settings.base_url or "(自动检测)"],
        ["api_key", _mask_key(settings.api_key)],
        ["dashscope_api_key", _mask_key(settings.dashscope_api_key)],
        ["max_tokens", str(settings.max_tokens)],
        ["temperature", str(settings.temperature) if settings.temperature is not None else "(默认)"],
        ["enable_thinking", _bool_icon(getattr(provider, '_enable_thinking', settings.enable_thinking))],
        ["thinking_budget", str(settings.thinking_budget)],
        ["vendor_fallback", settings.vendor_fallback or "(关闭)"],
    ]
    render_table(rows, headers=["配置项", "值"], title="LLM")


def _show_voice(ui: RichCLI, settings: Any) -> None:
    """TTS / STT / 实时语音 配置面板。

    @author aceFelix
    """
    rows = [
        # TTS
        ["tts_model", settings.tts_model],
        ["tts_voice", settings.tts_voice],
        ["tts_volume", str(settings.tts_volume)],
        ["tts_speech_rate", str(settings.tts_speech_rate)],
        ["tts_pitch_rate", str(settings.tts_pitch_rate)],
        # STT
        ["stt_model", settings.stt_model],
        ["stt_max_seconds", f"{settings.stt_max_seconds}s"],
        ["stt_silence_seconds", f"{settings.stt_silence_seconds}s"],
        # 实时双工
        ["realtime_model", settings.realtime_model],
        ["realtime_voice", settings.realtime_voice],
        ["realtime_ws_url", settings.realtime_ws_url or "(默认 DashScope 公共域名)"],
        # 语音打断
        ["voice_barge_in", _bool_icon(settings.voice_barge_in)],
        ["voice_barge_in_key", _bool_icon(settings.voice_barge_in_key)],
    ]
    render_table(rows, headers=["配置项", "值"], title="语音 (TTS / STT / Realtime)")


def _show_permissions(ui: RichCLI, settings: Any) -> None:
    """权限与沙箱配置面板。

    @author aceFelix
    """
    rows = [
        ["permission_mode", str(settings.permission_mode)],
        ["sandbox_enabled", _bool_icon(settings.sandbox_enabled)],
    ]
    if settings.sandbox_enabled:
        rows += [
            ["sandbox_max_memory_mb", str(settings.sandbox_max_memory_mb)],
            ["sandbox_timeout", f"{settings.sandbox_timeout}s"],
            ["sandbox_block_network", _bool_icon(settings.sandbox_block_network)],
        ]
    # 消息级回溯检查点（shadow git）@author aceFelix
    rows += [
        ["checkpoint_enabled", _bool_icon(getattr(settings, "checkpoint_enabled", True))],
        ["checkpoint_max_per_session", str(getattr(settings, "checkpoint_max_per_session", 20))],
    ]
    render_table(rows, headers=["配置项", "值"], title="权限与沙箱")


def _show_tools(ui: RichCLI, settings: Any) -> None:
    """工具系统配置面板。

    @author aceFelix
    """
    rows = [
        ["tools_deferred_loading", _bool_icon(settings.tools_deferred_loading)],
        ["tools_chat_detection", _bool_icon(settings.tools_chat_detection)],
        ["enable_tool_self_healing", _bool_icon(settings.enable_tool_self_healing)],
        ["tool_retry_max", str(settings.tool_retry_max)],
        ["tool_retry_backoff_base", f"{settings.tool_retry_backoff_base}s"],
        ["tool_retry_backoff_max", f"{settings.tool_retry_backoff_max}s"],
    ]
    render_table(rows, headers=["配置项", "值"], title="工具系统")


def _show_context(ui: RichCLI, settings: Any) -> None:
    """上下文压缩配置面板。

    @author aceFelix
    """
    rows = [
        ["context_compaction", _bool_icon(settings.context_compaction)],
        ["context_window", f"{settings.context_window} tokens"],
        ["compact_ratio", f"{settings.compact_ratio:.0%}"],
        ["compact_refreeze_growth", f"{settings.compact_refreeze_growth}x"],
        ["compact_max_output_tokens", f"{settings.compact_max_output_tokens} tokens"],
        ["long_term_memory", _bool_icon(settings.long_term_memory)],
    ]
    render_table(rows, headers=["配置项", "值"], title="上下文管理")


def _show_mcp(ui: RichCLI, mcp_client: Any, settings: Any) -> None:
    """MCP server 连接状态面板。

    @author aceFelix
    """
    if not mcp_client or not mcp_client.available:
        ui.info("MCP: SDK 未安装或已禁用")
        return

    connections = mcp_client.list_connections()
    if not connections:
        ui.info("MCP: 未配置 server")
        return

    rows = []
    for conn in connections:
        status = "✓ 已连接" if conn.connected else "✗ 未连接"
        tool_count = len(conn.tools) if conn.tools else 0
        rows.append([conn.name, status, f"{tool_count} 个工具"])

    render_table(rows, headers=["Server", "状态", "工具数"], title=f"MCP Servers ({len(connections)} 个)")


def _show_custom_models(ui: RichCLI, settings: Any) -> None:
    """自定义模型列表面板。

    @author aceFelix
    """
    custom = settings.custom_models
    if not custom:
        ui.info("自定义模型: (无)")
        return

    rows = []
    for name, cfg in custom.items():
        if not isinstance(cfg, dict):
            continue
        vendor = cfg.get("vendor", "?")
        api_fmt = cfg.get("provider_type", "?")
        mtype = cfg.get("model_type", "?")
        mtype_label = "多模态" if mtype == "multimodal" else "纯文本"
        api_key_val = _mask_key(cfg.get("api_key", "")) if cfg.get("api_key") else "(使用环境变量)"
        rows.append([name, vendor, api_fmt, mtype_label, api_key_val])

    render_table(rows, headers=["模型名", "厂商", "API格式", "类型", "Key"], title=f"自定义模型 ({len(rows)} 个)")


def _show_env_overrides(ui: RichCLI) -> None:
    """展示影响 Jarvis 的环境变量。

    @author aceFelix
    """
    env_vars = [
        "JARVIS_PROVIDER", "JARVIS_MODEL", "JARVIS_API_KEY",
        "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY", "ZAI_API_KEY",
        "ANTHROPIC_API_KEY", "KIMI_API_KEY", "MINIMAX_API_KEY", "MIMO_API_KEY",
        "OPENAI_API_KEY",
        "JARVIS_BASE_URL", "JARVIS_PERMISSION_MODE", "JARVIS_DEBUG",
    ]
    rows = []
    for var in env_vars:
        val = os.environ.get(var)
        if val:
            # API Key 类环境变量脱敏
            if "API_KEY" in var:
                display = _mask_key(val)
            else:
                display = val
            rows.append([var, display])

    if rows:
        render_table(rows, headers=["环境变量", "值"], title=f"生效的环境变量 ({len(rows)} 个)")
    else:
        ui.info("环境变量: 无 JARVIS_* 相关变量")


# ── 主函数 ──

def _show_config(
    ui: RichCLI,
    settings: Any,
    provider: Any,
    mcp_client: Any,
) -> None:
    """展示当前生效的完整配置。

    按分组依次输出：LLM → 语音 → 权限 → 工具 → 上下文 → MCP → 自定义模型 → 环境变量。

    @author aceFelix
    """
    ui._console.print("\n[bold cyan]═══ J.A.R.V.I.S 当前生效配置 ═══[/bold cyan]\n")

    _show_llm(ui, settings, provider)
    _show_voice(ui, settings)
    _show_permissions(ui, settings)
    _show_tools(ui, settings)
    _show_context(ui, settings)
    _show_mcp(ui, mcp_client, settings)
    _show_custom_models(ui, settings)
    _show_env_overrides(ui)


def _esc(text: str) -> str:
    """Rich 标记转义。

    配置值可能带方括号（路径里的 ``[x]``、名单项），而 `ui.info` 走 Rich、markup
    默认开启——不转义会把方括号当样式标签吞掉，形成"终端少字、桌面正常"的假象
    （同 ask_user 方括号吞字案）。rich 缺失时原样返回。

    @author aceFelix
    """
    try:
        from rich.markup import escape

        return escape(str(text))
    except Exception:  # noqa: BLE001 - 无 rich 就退化成纯文本
        return str(text)


_CONFIG_USAGE = (
    "用法：\n"
    "  /config                     查看当前生效的完整配置\n"
    "  /config show                同上\n"
    "  /config list                列出可以改哪些项（协议键 / 当前值 / 要不要确认）\n"
    "  /config set <键> <值>        改一项（需确认的项会先给预览）\n"
    "  /config set <键> <值> --yes  确认执行\n"
    "  清空名单类：/config set <键> \"\""
)


def _handle_list(ctx: "CommandContext") -> None:
    """/config list：与模型侧 ConfigList 同一份渲染（含当前值与否需要确认）。"""
    if ctx.settings is None:
        ctx.ui.warn("当前没有配置实例")
        return
    ctx.ui.info(_esc(render_spec_list(ctx.settings)))
    ctx.ui.info("用法：/config set <键> <值>（需确认的项会先给预览，再加 --yes 执行）")


def _handle_set(ctx: "CommandContext", body: str) -> None:
    """/config set <键> <值> [--yes]：两段式改动，与模型侧同一条写入路径。"""
    if ctx.settings is None:
        ctx.ui.warn("当前没有配置实例")
        return

    # --yes 先从整串里拿掉：这样名单类的值里带空格也不影响解析
    confirmed = False
    for flag in ("--yes", "-y"):
        if flag in body.split():
            confirmed = True
            body = " ".join(tok for tok in body.split() if tok != flag)

    key, _, raw = body.strip().partition(" ")
    key = key.strip()
    raw = raw.strip()
    if not key:
        ctx.ui.info(_CONFIG_USAGE)
        return

    why = forbidden_reason(key)
    if why:
        # 护栏是配置本身的属性：人能改的边界与模型一样（改这里等于改护栏的实现）
        ctx.ui.warn(f"{key} 是禁改项：{why}")
        ctx.ui.info("请直接编辑 ~/.jarvis/settings.toml。")
        return

    spec = get_spec(key)
    if spec is None:
        ctx.ui.warn(f"不认识的配置项 {key}")
        ctx.ui.info("用 /config list 看可用键。")
        return

    # 名单类允许清空，但必须显式写一对引号——裸写一个键就清空太容易误伤
    if not raw:
        if spec.kind == "list":
            ctx.ui.info(f"要清空清单请写：/config set {key} \"\"")
        else:
            ctx.ui.info(_CONFIG_USAGE)
        return

    # 先按类型转换再校验：预览里显示的必须是规范化之后的值（而不是原始串）。
    # `""` → 清空名单这层判断在 parse_cli_value 里（与回显保持逆运算关系）。
    value = parse_cli_value(spec, raw)
    try:
        value = validate_value(spec, value)
    except ValueError as e:
        ctx.ui.warn(str(e))
        return

    old = read_value(ctx.settings, spec)
    if spec.tier == TIER_CONFIRM and not confirmed:
        ctx.ui.info(
            f"这是一项需要确认的配置：{_esc(spec.label)}\n"
            f"  {_esc(human_value(spec, old))} → {_esc(human_value(spec, value))}\n"
            f"  为什么要问：{_esc(spec.hint)}\n"
            f"确认请再执行：/config set {key} {_esc(render_cli_value(spec, value))} --yes"
        )
        return

    outcome = apply_config_patch(
        ctx.settings,
        {key: value},
        workdir=str(getattr(getattr(ctx, "ctx", None), "workdir", "") or ""),
        permission_mode=str(getattr(ctx.settings, "permission_mode", "default") or "default"),
        reason="terminal /config set",
        entry=ENTRY_TERMINAL,
    )
    if outcome.ok:
        ctx.ui.info(_esc(outcome.text))
    else:
        ctx.ui.warn(_esc(outcome.text))


def handle_config_show(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /config [show|list|set …]：一个入口三种能力。

    ``/config`` 与 ``/config show`` 保持原行为（全量自查面板）；其余子命令分派到
    上面两个处理函数；不认识的子命令给用法而不是默默打印一屏配置。

    @author aceFelix
    """
    parts = stripped.split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else ""
    if sub == "list":
        _handle_list(ctx)
    elif sub == "set":
        _handle_set(ctx, parts[2] if len(parts) > 2 else "")
    elif sub in ("", "show"):
        _show_config(ctx.ui, ctx.settings, ctx.provider, ctx.mcp_client)
    else:
        ctx.ui.warn(f"不认识的子命令 {sub}")
        ctx.ui.info(_CONFIG_USAGE)
    return True
