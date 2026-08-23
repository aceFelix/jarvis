"""核心 REPL 命令处理器。

包含 /exit, /help, /mode, /reset, /clear, /compact, /cost, /context,
/rewind, /diff, /doctor, /think 等核心命令。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import platform
import sys
from typing import TYPE_CHECKING, Any

from agent.core.message import Message, TextContent
from agent.core.orchestrator import ToolOrchestrator
from agent.core.query_loop import QueryLoop
from agent.permissions import parse_mode
from agent.permissions.modes import PermissionMode
from agent.prompts.system import build_system_prompt
from agent.ui.cli import RichCLI
from agent.ui.markdown_renderer import render_diff, render_table, render_tree, render_panel
from agent.utils.mask import mask_key

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


async def handle_exit(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /exit /quit /q：标记退出信号，由 repl 执行清理。"""
    ctx.should_exit = True
    return True


def _print_help(ui: RichCLI) -> None:
    """打印可用命令帮助。"""
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
        "  /config show 查看当前生效的完整配置（含 MCP 状态）\n"
        "  /save [name] 保存当前会话\n"
        "  /load [前缀]  前缀匹配加载会话（支持模糊输入）\n"
        "  /loads       列出并选择已保存会话\n"
        "  /memory      查看长期记忆文件\n"
        "  /skills      列出已加载的技能包\n"
        "  /mcp         查看 MCP server 连接状态\n"
        "  /tools       列出可用工具\n"
        "  /server [目录] [--port N] [--command \"cmd\"] 启动开发服务器（Vite/Next/...）\n"
        "  /plugin                 列出已安装插件（Plugin 系统）\n"
        "  /plugin search [关键词]  搜索 Plugin 系统市场\n"
        "  /plugin install <名称>   安装 Plugin 系统的插件\n"
        "  /plugin uninstall <名称> 卸载 Plugin 系统的插件\n"
        "  /plugin info <名称>      查看 Plugin 插件详情\n"
        "  /plugin update           检查 Plugin 插件更新\n"
        "  /plugin enable <名称>    启用被禁用的插件（通用）\n"
        "  /plugin disable <名称>   禁用插件，不卸载（通用）\n"
        "  /plugin create <名称>    创建新插件脚手架（--type harness|plugin）\n"
        "  /plugin validate <路径>  校验 plugin.json / SKILL.md（通用）\n"
        "  /cli_anything list    列出已安装 CLI-Anything harness\n"
        "  /cli_anything market  列出市场可用 harness\n"
        "  /cli_anything install <id>  安装 harness\n"
        "  /cli_anything uninstall <id> 卸载 harness\n"
        "  /image <path> 添加本地图片到待发送列表（下条消息附带）\n"
        "  /img <path>   添加本地图片（/image 别名）\n"
        "  /paste       添加剪贴板图片到待发送列表（下条消息附带）\n"
        "  /p           添加剪贴板图片（/paste 别名）\n"
        "  /say <text>  用语音朗读一段文字\n"
        "  /listen      录音并识别成文字（麦克风→文字）\n"
        "  /voice       进入语音对话模式（连续听→想→说，说「退出」结束）\n"
        "  /connect-phone  手机扫码连接 JARVIS（共享当前会话，手机端 PLAN 模式）\n"
        "  /connect-wechat 微信扫码连接 JARVIS（通过微信 ClawBot 对话）\n"
        "  /disconnect-wechat 断开微信 ClawBot 连接\n"
    )
    if ui._console:
        ui._console.print(help_text)
    else:
        print(help_text)


def handle_help(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /help /h /?。"""
    _print_help(ctx.ui)
    return True


def handle_reset(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /reset /clear：清空对话历史与工具上下文。"""
    ctx.messages.clear()
    ctx.ctx.extra.clear()
    ctx.ui.info("对话已重置")
    return True


def handle_clear(ctx: "CommandContext", stripped: str) -> bool:
    """/clear 是 /reset 的别名。"""
    return handle_reset(ctx, stripped)


def handle_mode(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /mode [mode]：切换权限模式并重建相关组件。"""
    cmd = stripped.lower()
    settings = ctx.settings
    ui = ctx.ui

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
            return True
        mode_str = choice
    elif cmd.startswith("/mode "):
        # /mode xxx — 带参数切换
        mode_str = stripped.split(None, 1)[1].strip().lower()
        valid_modes = ["default", "plan", "accept_edits", "yolo"]
        matches = [m for m in valid_modes if m.startswith(mode_str)]
        if not matches:
            ui.warn(f"未知模式: {mode_str}（可选: default/plan/accept_edits/yolo）")
            return True
        if len(matches) > 1:
            from agent.ui.terminal_picker import pick_from_list
            items = [(m, m, "") for m in matches]
            choice = pick_from_list(items, title="多个匹配，请选择")
            if choice is None:
                return True
            mode_str = choice
        else:
            mode_str = matches[0]
    else:
        return False

    new_mode = parse_mode(mode_str)
    settings.permission_mode = new_mode

    # 延迟导入 bootstrap 中的构建函数，避免循环引用
    from agent.bootstrap import _build_checker, _build_recovery_executor

    checker = _build_checker(settings)
    recovery = _build_recovery_executor(settings)
    orchestrator = ToolOrchestrator(
        registry=ctx.registry,
        permission_checker=checker,
        recovery_executor=recovery,
    )
    loop = QueryLoop(
        provider=ctx.provider,
        registry=ctx.registry,
        orchestrator=orchestrator,
        system=ctx.system_prompt,
        model=ctx.model,
        max_iterations=settings.max_iterations,
        max_tokens=settings.max_tokens,
        temperature=settings.temperature,
        enable_compaction=settings.context_compaction,
        compaction_threshold=settings.compaction_threshold,
        keep_recent_messages=settings.keep_recent_messages,
        vendor_fallback=settings.vendor_fallback,
        custom_models=settings.custom_models,
    )

    ctx.checker = checker
    ctx.recovery = recovery
    ctx.orchestrator = orchestrator
    ctx.loop = loop
    ctx.ctx.permission_mode = new_mode.value
    ui.info(f"权限模式切换为: {new_mode.value}")
    return True


async def _compact(ui: RichCLI, loop: QueryLoop, ctx: Any) -> None:
    """手动触发上下文压缩。/compact 命令的执行体。

    必须是 async：dispatch_command 已运行在事件循环里，
    内部调用 loop.compact_now()（async）需直接 await，
    不能用 asyncio.run() 嵌套（会抛 RuntimeError）。

    token 统计仅含对话历史（不含 system prompt），因为压缩
    只作用于对话历史，system prompt 不参与压缩。

    @author aceFelix
    """
    from agent.core.memory.compactor import estimate_tokens

    pre_tokens = estimate_tokens(ctx.messages)
    keep_recent = loop.keep_recent_messages

    # 提前判断：消息数 ≤ 保留阈值时，没有可摘要的内容，直接提示
    if not loop.enable_compaction:
        ui.warn(f"压缩功能已禁用（enable_compaction=False）")
        return
    if len(ctx.messages) <= keep_recent:
        ui.info(
            f"当前 {len(ctx.messages)} 条消息，约 {pre_tokens} tokens（对话历史，不含 system）\n"
            f"消息数 ≤ 保留阈值（{keep_recent}），全部保留，无需压缩"
        )
        return

    ui.info(f"开始压缩（{len(ctx.messages)} 条消息，约 {pre_tokens} tokens）...")
    # 直接 await，不能用 asyncio.run()——会与已运行的事件循环冲突导致崩溃
    ok = await loop.compact_now(ctx)
    if ok:
        post_tokens = estimate_tokens(ctx.messages)
        ui.info(
            f"压缩完成：{pre_tokens} → {post_tokens} tokens"
            f"（节省 {pre_tokens - post_tokens}，现在 {len(ctx.messages)} 条消息）"
        )
    else:
        ui.warn("压缩未执行（摘要为空或 LLM 调用失败）")


async def handle_compact(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /compact。"""
    await _compact(ctx.ui, ctx.loop, ctx.ctx)
    return True


def _print_cost(ui: RichCLI, messages: list[Message], dialog_count: int, model: str, provider_name: str, ctx: "CommandContext") -> None:
    """/cost: 显示本会话累计 token 用量与估算成本。

    token 估算拆分为三部分，让用户清晰看到各组成占比：
    - System Prompt token：系统提示词（含工具说明/技能包/平台规范）
    - 对话历史 token：用户与助手交互的消息（不含 system）
    - 完整上下文 token：上述两者之和，即每轮请求的实际上下文规模

    @author aceFelix
    """
    from agent.core.memory.compactor import estimate_tokens, estimate_text_tokens
    from agent.llm.base import Usage

    # System Prompt token 估算（独立计算，原 estimate_tokens 不含 system）
    system_tokens = estimate_text_tokens(ctx.system_prompt) if ctx.system_prompt else 0
    dialog_tokens = estimate_tokens(messages)
    total_tokens = system_tokens + dialog_tokens
    rows = [
        ["对话轮数", str(dialog_count)],
        ["消息数", str(len(messages))],
        ["System Prompt token（估算）", f"{system_tokens:,}"],
        ["对话历史 token（估算）", f"{dialog_tokens:,}"],
        ["完整上下文 token（含 system）", f"{total_tokens:,}"],
        ["模型", model],
        ["Provider", provider_name],
    ]

    # 会话级真实用量统计（含缓存命中，来自各 Provider 归一化后的 usage）
    session = ctx.loop.session_usage if ctx.loop is not None else Usage()
    if session.input_tokens:
        rows.append(["累计输入 token（API 实计）", f"{session.input_tokens:,}"])
        rows.append(["累计输出 token（API 实计）", f"{session.output_tokens:,}"])
        rows.append(["缓存命中 token", f"{session.cache_read_tokens:,}"])
        rows.append(["缓存创建 token", f"{session.cache_creation_tokens:,}"])
        # 命中率分母按协议口径区分（而非厂商名）：
        # - OpenAI/DashScope 兼容协议：input_tokens 已含缓存命中 → 分母=input_tokens
        # - Anthropic 协议（含 DeepSeek Anthropic 兼容端点）：input_tokens 不含缓存
        #   → 真实总输入 = input_tokens + cache_read_tokens
        # 判断依据：Anthropic 协议下 cache_read 是 input_tokens 之外的独立值，
        # 命中数可能远大于未命中的 input_tokens（如 system prompt 全命中）。
        # 用 cache_read > input_tokens 作为"input_tokens 不含缓存"的信号。
        if session.cache_read_tokens > session.input_tokens:
            # input_tokens 不含缓存（Anthropic 协议），分母需加上缓存命中
            denom = session.input_tokens + session.cache_read_tokens
        else:
            # input_tokens 已含缓存（OpenAI/DashScope 协议）
            denom = session.input_tokens
        hit_ratio = session.cache_read_tokens / denom * 100 if denom else 0
        rows.append(["缓存命中率", f"{hit_ratio:.1f}%"])

    ui.info("本会话成本统计（部分基于估算）")
    render_table(rows, headers=["指标", "值"])
    ui.info("提示: 实际 API 计费以厂商账单为准；历史累计请查看 ~/.jarvis/logs/diag.log")


def handle_cost(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /cost。

    Provider 显示运行时实际检测名（如 DeepSeek Anthropic 兼容端点检测为
    deepseek），而非 settings.provider 静态配置。
    """
    provider_name = getattr(ctx.provider, "name", None) or ctx.settings.provider
    _print_cost(ctx.ui, ctx.messages, ctx.dialog_count, ctx.model, provider_name, ctx)
    return True


def _print_context(ui: RichCLI, messages: list[Message], model: str, system_prompt: str = "") -> None:
    """/context: 显示上下文窗口使用情况，按角色分组统计消息数。

    system_prompt 单独传入并单独统计 token，因为 system prompt 不在
    messages 列表内，但实际占上下文窗口。窗口占比必须含 system 才准确。

    @author aceFelix
    """
    from agent.core.memory.compactor import estimate_tokens, estimate_text_tokens

    role_counts: dict[str, int] = {}
    role_tokens: dict[str, int] = {}
    for m in messages:
        role_counts[m.role] = role_counts.get(m.role, 0) + 1
        role_tokens[m.role] = role_tokens.get(m.role, 0) + estimate_tokens([m])
    # System Prompt 独立统计（不在 messages 里，但占上下文窗口）
    system_tokens = estimate_text_tokens(system_prompt) if system_prompt else 0
    if system_tokens:
        role_tokens["system"] = role_tokens.get("system", 0) + system_tokens
        role_counts["system"] = role_counts.get("system", 0) + (1 if system_tokens else 0)
    dialog_total = estimate_tokens(messages)
    total = dialog_total + system_tokens
    # 窗口默认值取主流大模型的保守值（128k）。
    # JARVIS 暂无模型→窗口映射表，此处仅用于估算占比，非精确值。
    window = 128000
    pct = (total / window * 100) if window else 0
    ui.info(f"上下文使用情况（模型: {model}，假设窗口 {window:,} tokens）")
    rows = [
        [role, str(role_counts.get(role, 0)), f"{role_tokens.get(role, 0):,}"]
        for role in ["system", "user", "assistant", "tool"]
        if role in role_counts
    ]
    render_table(rows, headers=["角色", "消息数", "tokens"])
    ui.info(f"合计: {len(messages)} 条消息 + system prompt / {total:,} tokens / 窗口占比 {pct:.1f}%")
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


def handle_context(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /context。"""
    _print_context(ctx.ui, ctx.messages, ctx.model, ctx.system_prompt)
    return True


def _rewind(ui: RichCLI, messages: list[Message], cmd: str) -> None:
    """/rewind [n]: 回退最近 n 条消息（默认 1 条）。"""
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
    for _ in range(n):
        messages.pop()
    ui.info(f"已回退 {n} 条消息，当前 {len(messages)} 条")


def handle_rewind(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /rewind [n]。"""
    _rewind(ctx.ui, ctx.messages, stripped)
    return True


async def _show_diff(ui: RichCLI, settings: Any, cmd: str) -> None:
    """/diff [path]: 显示工作目录的 git diff。"""
    parts = cmd.split(maxsplit=1)
    path_arg = parts[1].strip() if len(parts) > 1 else ""
    try:
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
            if "not a git repository" in err.lower():
                ui.info("（提示: 当前目录不是 git 仓库，/diff 仅支持 git）")
            return
        render_diff(diff_text)
    except FileNotFoundError:
        ui.warn("找不到 git 命令，请确认 git 已安装并加入 PATH")
    except Exception as e:
        ui.error(f"/diff 执行失败: {type(e).__name__}: {e}")


async def handle_diff(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /diff [path]。"""
    await _show_diff(ctx.ui, ctx.settings, stripped)
    return True


def _doctor(ui: RichCLI, settings: Any, provider, model: str, messages: list[Message]) -> None:
    """/doctor: 系统诊断。检查配置、依赖、日志、迁移状态等。"""
    from agent.core.diag import get_log_path, read_recent_logs
    from agent.config.migrations import list_all_migrations

    ui.info("JARVIS 系统诊断")
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

    prov_rows = [
        ["Provider 类", type(provider).__name__],
        ["思考模式", "on" if provider.is_thinking_enabled() else "off"],
        ["模型", model],
    ]
    render_table(prov_rows, headers=["项", "值"], title="Provider 状态")

    user_cfg = Path.home() / ".jarvis" / "settings.toml"
    cfg_rows = [
        ["用户配置", "存在" if user_cfg.exists() else "不存在（用默认值）"],
        ["API key", mask_key(settings.api_key)],
        ["Base URL", settings.base_url or "(默认)"],
    ]
    render_table(cfg_rows, headers=["项", "状态"], title="配置")

    migrations = list_all_migrations()
    if migrations:
        mig_rows = [
            [mid, desc, "已执行" if done else "待执行"]
            for mid, desc, done in migrations
        ]
        render_table(mig_rows, headers=["ID", "描述", "状态"], title="配置迁移")
    else:
        ui.info("迁移: 无待执行迁移")

    log_path = get_log_path()
    if log_path and log_path.exists():
        recent = read_recent_logs(max_lines=5)
        if recent:
            render_panel("\n".join(recent), title=f"最近 5 条诊断日志 ({log_path.name})")
        else:
            ui.info(f"诊断日志: 空 ({log_path})")
    else:
        ui.info("诊断日志: 尚未生成")

    sess_rows = [
        ["消息数", str(len(messages))],
        ["对话轮数", "(请用 /cost 查看)"],
    ]
    render_table(sess_rows, headers=["项", "值"], title="当前会话")

    _doctor_recovery(ui, settings)


def _doctor_recovery(ui: RichCLI, settings: Any) -> None:
    """/doctor 子面板：展示工具错误自愈统计与建议。"""
    from agent.core.error_recovery import RecoveryTelemetry, _CATEGORY_REASONS

    telemetry = RecoveryTelemetry()
    summary = telemetry.get_summary()
    ui.info("工具错误自愈")
    state_rows = [
        ["总开关", "开启" if settings.enable_tool_self_healing else "关闭"],
        ["最大重试", str(settings.tool_retry_max)],
        ["退避基数", f"{settings.tool_retry_backoff_base}s"],
        ["最大退避", f"{settings.tool_retry_backoff_max}s"],
        ["历史事件", str(summary["total_incidents"])],
        ["已自愈", str(summary["resolved"])],
        ["未恢复", str(summary["unresolved"])],
    ]
    render_table(state_rows, headers=["项", "值"], title="自愈配置")

    if summary["by_category"]:
        cat_rows = [
            [_CATEGORY_REASONS.get(cat, cat), str(cnt)]
            for cat, cnt in sorted(summary["by_category"].items(), key=lambda x: -x[1])
        ]
        render_table(cat_rows, headers=["错误类型", "次数"], title="错误分布")
        top = telemetry.top_category()
        if top:
            ui.warn(f"高频错误类型: {_CATEGORY_REASONS.get(top, top)}，建议检查相关依赖/网络/配置")

    recent = telemetry.get_recent(5)
    if recent:
        recent_rows = [
            [
                i.tool_name,
                _CATEGORY_REASONS.get(i.category.value, i.category.value),
                "成功" if i.resolved else "失败",
                str(i.attempts),
                i.message,
            ]
            for i in recent
        ]
        render_table(recent_rows, headers=["工具", "错误", "结果", "重试", "说明"], title="最近 5 次自愈")


def handle_doctor(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /doctor。"""
    _doctor(ctx.ui, ctx.settings, ctx.provider, ctx.model, ctx.messages)
    return True


async def _toggle_plan(ui: RichCLI, settings: Any, ctx: Any) -> None:
    """/plan —— 切换规划模式。"""
    current = ctx.permission_mode
    if current == "plan":
        prev = ctx.extra.pop("_plan_mode_previous", "default")
        ctx.permission_mode = prev
        ctx.extra.pop("_plan_mode_entered", None)
        plan_content = ctx.extra.pop("_plan_content", None)
        ui.info(f"已退出规划模式，权限恢复为: {prev}")
        if plan_content:
            ui.info(f"方案内容已保留在上下文中（{len(plan_content)} 字符）")
    else:
        ctx.extra["_plan_mode_entered"] = True
        ctx.extra["_plan_mode_previous"] = current
        ctx.permission_mode = "plan"
        ui.info("已进入规划模式（只读）。")
        ui.info("调研完整后，用 ExitPlanMode 提交方案，或用 /plan 切回。")


async def handle_plan(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /plan。"""
    await _toggle_plan(ctx.ui, ctx.settings, ctx.ctx)
    return True


def _toggle_thinking(
    ui: RichCLI,
    settings: Any,
    provider,
    loop: QueryLoop,
    registry: Any,
    raw: str,
) -> None:
    """/think [on|off] —— 开关深度思考模式。"""
    current = getattr(provider, '_enable_thinking', True)

    parts = raw.split(maxsplit=1)
    if len(parts) == 1:
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

    provider.set_thinking_enabled(new_state)
    settings.enable_thinking = new_state

    new_system = build_system_prompt(settings.workdir, registry, enable_thinking=new_state, settings=settings)
    if settings.system_prompt_append:
        new_system = new_system + "\n\n" + settings.system_prompt_append
    loop._system = new_system

    ui.info(f"深度思考: {'✅ 开' if new_state else '❌ 关'}")


def handle_think(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /think [on|off]。"""
    _toggle_thinking(ctx.ui, ctx.settings, ctx.provider, ctx.loop, ctx.registry, stripped)
    return True
