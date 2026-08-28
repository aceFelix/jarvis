"""配置查看命令处理器。

U-05 改进项：`jarvis config show` / `/config show` ——
展示当前生效的完整配置（含多层合并结果），包含 LLM、语音、权限、
工具、MCP 状态、自定义模型、环境变量覆盖。

@author aceFelix
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

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
        ["auto_resume_session", _bool_icon(settings.auto_resume_session)],
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


def handle_config_show(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /config show：展示当前生效的完整配置。

    @author aceFelix
    """
    _show_config(ctx.ui, ctx.settings, ctx.provider, ctx.mcp_client)
    return True
