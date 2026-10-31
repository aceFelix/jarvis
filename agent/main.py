"""主入口 —— 装配所有零件，跑通 REPL。

大量import 和大量启动优化，
v0.1 聚焦"能跑起来": 解析 CLI 参数 -> 加载配置 -> 构建 provider/registry/checker/
loop -> 进入 REPL 循环。

REPL 循环:
    while True:
        user_input = ui.read()
        if /exit: break
        if /help, /mode, /reset: 通过 agent.commands.router 分发处理
        else: query_loop.run(user_input, ctx)

@author aceFelix
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading

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

from agent.bootstrap import (
    _build_checker,
    _build_context,
    _build_provider,
    _build_recovery_executor,
    _model_type_for,
)
from agent.bridge import get_bridge_server
from agent.commands.router import CommandContext, dispatch_command
from agent.config.settings import Settings, load_settings
from agent.core.checkpoint import manager_from_settings
from agent.core.images import paste_clipboard_image
from agent.core.message import Message
from agent.core.orchestrator import ToolOrchestrator
from agent.core.query_loop import QueryLoop
from agent.core.tool import ToolRegistry, build_default_registry
from agent.permissions import parse_mode
from agent.prompts.system import build_system_prompt
from agent.session_manager import (
    _auto_save,
    _generate_session_title,
    _generate_title_from_first_user,
)
from agent.ui.cli import RichCLI


def _install_quiet_loop_exception_handler() -> None:
    """过滤 asyncio 关闭 async generator 时对 MCP stdio 的报错刷屏。

    ``asyncio.run`` 退出时 ``loop.shutdown_asyncgens()`` 会逐个 ``aclose()``
    所有 async generator。MCP ``stdio_client`` 的 anyio task group 跨 task
    关闭会抛 ``BaseExceptionGroup``（内含 ``RuntimeError: Attempted to exit
    cancel scope in a different task``），该异常经 ``loop.call_exception_handler``
    打印成 "an error occurred during closing of asynchronous generator ..."。

    注意：这条路径走的是 ``call_exception_handler``，**不是** asyncgen 的 GC
    finalizer，所以 main.py / mcp_client.py 里替换 finalizer 的做法拦不住它，
    必须在 loop 的异常处理器上过滤。报错发生在进程即将退出时，纯属噪音。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    def _handler(loop_: asyncio.AbstractEventLoop, context: dict) -> None:
        msg = context.get("message", "") or ""
        exc = context.get("exception")
        if "closing of asynchronous generator" in msg:
            return
        if "cancel scope" in msg or "cancel scope" in str(exc):
            return
        loop_.default_exception_handler(context)

    loop.set_exception_handler(_handler)


async def repl(settings: Settings) -> int:
    """REPL 主循环。返回退出码。

    @author aceFelix
    """
    _install_quiet_loop_exception_handler()
    ui = RichCLI(verbose=settings.verbose, boot_animation=settings.boot_animation)

    # 版本自检（Phase 1）：后台线程查 PyPI 最新版，非阻塞、24h 节流。
    # 检出更新时仅打印一行提示（/update 一键升级），不影响启动速度。
    # @author aceFelix
    if settings.update_check:
        from agent import update as _update
        _update.start_background_check(ui)

    provider = _build_provider(settings, model_type=_model_type_for(settings))
    registry: ToolRegistry = build_default_registry()

    # 动态工具：CLI-Anything harness（~/.jarvis/cli_anything/ 与项目级 .jarvis/cli_anything/）
    # quick_start 模式下后台异步加载，不阻塞 REPL prompt 出现
    from agent.core.tool import register_dynamic_tools

    def _register_harness_in_background() -> None:
        try:
            count = register_dynamic_tools(registry, workdir=settings.workdir)
            if count > 0 and settings.verbose:
                ui.info(f"✓ CLI-Anything harness 已注册（{count} 个）")
        except Exception as e:
            if settings.verbose:
                ui.warn(f"harness 异步加载失败: {e}")

    if settings.quick_start:
        if settings.verbose:
            ui.info("⚡ 快速启动模式：harness 工具后台异步加载")
        threading.Thread(target=_register_harness_in_background, daemon=True).start()
    else:
        harness_count = register_dynamic_tools(registry, workdir=settings.workdir)
        if harness_count > 0 and settings.verbose:
            ui.info(f"✓ CLI-Anything harness 已注册（{harness_count} 个）")

    checker = _build_checker(settings)
    recovery = _build_recovery_executor(settings)
    orchestrator = ToolOrchestrator(
        registry=registry,
        permission_checker=checker,
        recovery_executor=recovery,
    )

    # MCP 接入：连接配置的 server 并注册工具
    mcp_client = None
    if settings.enable_mcp:
        try:
            from agent.core.extensions.mcp_client import MCPClient, load_mcp_config
            mcp_client = MCPClient()
            if mcp_client.available:
                config = load_mcp_config()
                if config:
                    # console.status: 显示带 spinner 的临时状态行，退出时自动清除
                    with ui._console.status(f"正在连接 {len(config)} 个 MCP server..."):
                        results = await mcp_client.connect_all(config)
                    connected = sum(1 for v in results.values() if v)
                    failed_names = [name for name, ok in results.items() if not ok]
                    if connected:
                        count = register_dynamic_tools(registry, mcp_client)
                        msg = f"MCP: {connected}/{len(config)} server 已连接，注册 {count} 个工具"
                        if failed_names:
                            msg += f"（{', '.join(failed_names)} 连接失败，对应工具不可用）"
                        ui.info(msg)
                        # P2 画像双向同步：图谱 → jarvis，启动时预加载知识图谱画像，
                        # 缓存到 profile_bridge，后续 build_system_prompt 的画像段合并注入；
                        # 未开启 [profile_bridge] 或拉取失败时静默跳过，不影响启动
                        try:
                            from agent.core.extensions.profile_bridge import preload_kg_profile
                            if await preload_kg_profile(mcp_client, settings):
                                ui.info("✓ 知识图谱画像已注入（图谱 → jarvis 同步）")
                        except Exception:
                            pass
                    else:
                        ui.warn(f"MCP: 所有 server 连接失败（{', '.join(config.keys())}）")
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

    # 定时提醒宿主：把桌面已有的 ScheduleReminder 能力对齐到 CLI/语音端。
    # 此前这批工具只在 agent/serve/app.py 里注册，纯 CLI 说「提醒我」时
    # ToolSearch 根本找不到工具，模型会转去手搜 Register-ScheduledTask 绕道
    # 实现，思考撞穿 max_tokens 后空转（详 docs/fixlogs/agent-loop-stall-fix.md）。
    # 局限：仅在进程存活期间触发，CLI 关闭后不会提醒（常驻提醒请用桌面版）。
    # @author aceFelix
    from agent.core.daemon.cli_reminder import CliReminderHost

    # 顺手把自动清理的调度也挂上去：本宿主是 REPL 里唯一有 Scheduler 的地方。
    # 两个开关（maintenance_enabled / auto_clean_enabled）任一为 false 就安静跳过。
    # @author aceFelix
    reminder_host = CliReminderHost(
        notify=getattr(ui, "notify_reminder", None) or ui.info,
        settings=settings,
    )
    try:
        reminder_host.start()
        reminder_tool_count = reminder_host.register_tools(registry)
        if reminder_tool_count > 0 and settings.verbose:
            ui.info(f"✓ 日程提醒工具已注册（{reminder_tool_count} 个）")
    except Exception as e:
        if settings.verbose:
            ui.warn(f"日程提醒初始化失败（本轮不提供定时提醒）: {e}")

    # LSP 集成
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

    system_prompt = build_system_prompt(settings.workdir, registry, enable_thinking=settings.enable_thinking, settings=settings)
    if settings.system_prompt_append:
        system_prompt = system_prompt + "\n\n" + settings.system_prompt_append

    # ToolSearch 工具注册（延迟加载机制的核心）
    if settings.tools_deferred_loading:
        from agent.tools.tool_search import ToolSearchTool
        if "ToolSearch" not in registry:
            registry.register(ToolSearchTool(registry))

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
        context_window=settings.context_window,
        compact_ratio=settings.compact_ratio,
        compact_refreeze_growth=settings.compact_refreeze_growth,
        compact_max_output_tokens=settings.compact_max_output_tokens,
        tool_result_keep_recent=settings.tool_result_keep_recent,
        vendor_fallback=settings.vendor_fallback,
        custom_models=settings.custom_models,
        deferred_loading=settings.tools_deferred_loading,
        chat_detection=settings.tools_chat_detection,
    )

    messages: list[Message] = []
    # 启动即全新会话：历史会话由 /load（桌面左栏点选）手动恢复。
    # 曾有 auto_resume_session 启动自动恢复，但恢复指针 auto-latest 是
    # 全局单文件、不区分项目目录，多项目并行时互相覆盖串台，已下线。
    # @author aceFelix

    ctx = _build_context(settings, ui, messages)

    # Ctrl+V 贴图：把读剪贴板回调注入终端 UI 的按键绑定（仅显式触发，
    # 回车/发消息不再自动读剪贴板）。@author aceFelix
    ui.set_paste_image_handler(lambda: paste_clipboard_image(ctx, ui))

    # 生成本次会话的唯一名称（时间戳），自动保存时写入独立文件
    from datetime import datetime as _dt
    _session_name = f"session-{_dt.now().strftime('%Y%m%d-%H%M%S')}"
    _title_generated = False   # 2轮后 LLM 自动生成标题
    _dialog_count = 0

    # 消息级回溯检查点：每轮对话前用 shadow git 对工作目录打检查点，/rewind 时可
    # 连带回滚文件。存储按 workdir 定位，/load 旧会话仍可命中；未装 git / 关闭
    # 开关时 available()=False，自动退化为仅对话回退。字段口径见工厂。
    # @author aceFelix
    ckpt_mgr = manager_from_settings(settings, session_name=_session_name)
    ctx.extra["checkpoint_mgr"] = ckpt_mgr
    # 副作用账本（W3.2）要靠会话名把记录归人可读的台账；本轮的检查点 cid 则在
    # 轮前补写进 extra，因为 cid 要到轮结束才挂到用户消息上，工具调用时还查不到。
    # @author aceFelix
    ctx.extra["session_name"] = _session_name
    ctx.extra.setdefault("checkpoint_cid", "")

    # W3 兑现层宿主装配（副作用账本 + 任务脊柱）：重启后没人能在延时窗口里喊停，
    # 上个进程排队但未发出的不可逆外发动作一律按取消处理并如实告知；
    # `tasks_enabled` 打开时同时起后台驱动线程，退出时收。细节全在 cli_host。
    # @author aceFelix
    from agent.core.tasks import CliSpineHost
    spine_host = CliSpineHost(settings, ui=ui, provider=provider, session_name=_session_name)
    spine_host.start()

    # 启用诊断日志（settings.debug=True 时同时输出到 stderr）
    from agent.core.diag import set_debug as _set_diag_debug
    _set_diag_debug(settings.debug)

    ui.banner(provider.name, model, settings.workdir)

    # 阶段 12 P4：用几天后主动提一次「你好像反复在同一个场合改同一项」。
    # 门槛（启动次数 + 天数）与"只提一次"都由 onboarding 掌控；候选来自 P3 的
    # 证据式提炼，**不生效**，要用户自己 /prefs accept。@author aceFelix
    try:
        from agent.config import onboarding as _onboarding

        _onboarding.maybe_suggest_adjustment(ui)
    except Exception:  # noqa: BLE001 - 提议失败不能拦住启动
        pass

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
                # 跳过恢复 = 全新会话：清空消息列表并同步 ctx（共享同一列表），
                # 防标题生成等后续逻辑误读残留上下文。@author aceFelix
                messages.clear()
                ui.info("已跳过恢复，恢复点已清除，开始全新会话")
    except Exception as e:
        # 恢复检测失败不阻塞启动
        from agent.core.diag import diag_warn
        diag_warn("recovery", f"恢复检测异常: {e}")

    # 组装命令路由共享上下文
    cmd_ctx = CommandContext(
        ui=ui,
        settings=settings,
        provider=provider,
        registry=registry,
        checker=checker,
        recovery=recovery,
        orchestrator=orchestrator,
        loop=loop,
        ctx=ctx,
        model=model,
        messages=messages,
        dialog_count=_dialog_count,
        team_mgr=team_mgr,
        task_list=task_list,
        mcp_client=mcp_client,
        session_name=_session_name,
        title_generated=_title_generated,
        system_prompt=system_prompt,
        should_exit=False,
    )

    while True:
        try:
            user_input = await ui.read_user_input_async("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        stripped = user_input.strip() if user_input else ""

        # 空行提交：忽略。剪贴板图片不再自动检测（残留图片会被误带进
        # 普通提问），改用 Ctrl+V / /paste 显式添加。@author aceFelix
        if not stripped:
            continue

        # ---- 斜杠命令 ----
        if stripped.startswith("/"):
            handled = await dispatch_command(cmd_ctx, stripped)
            if handled:
                # 同步可能被命令处理器修改的状态到本地变量
                provider = cmd_ctx.provider
                loop = cmd_ctx.loop
                model = cmd_ctx.model
                checker = cmd_ctx.checker
                recovery = cmd_ctx.recovery
                orchestrator = cmd_ctx.orchestrator
                _dialog_count = cmd_ctx.dialog_count

                # /load 加载会话后恢复轮数/标题状态：
                # 继续加载前的轮数计数（如加载 3 轮 → 下一轮是第 4 轮），
                # 并保留原标题不再重新生成。
                if cmd_ctx.last_load_info:
                    info = cmd_ctx.last_load_info
                    cmd_ctx.last_load_info = None  # 只消费一次
                    _session_name = info.get("session_name") or _session_name
                    _dialog_count = info.get("dialog_count") or 0
                    _title_generated = bool(info.get("title_generated"))
                    cmd_ctx.dialog_count = _dialog_count
                    cmd_ctx.session_name = _session_name
                    cmd_ctx.title_generated = _title_generated

                if cmd_ctx.should_exit:
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
                continue

            # 未知命令：dispatch_command 未识别（理论上已被内部处理并 warn）
            continue

        # ---- 普通对话 ----
        try:
            # 轮前检查点：记录工作区"发消息前"状态，绑定到本轮用户消息，
            # 供 /rewind / 桌面撤回连带回滚文件。@author aceFelix
            ckpt_id = ckpt_mgr.create(reason=stripped) if ckpt_mgr.available() else None
            # 给本轮工具调用（副作用账本的本地写回滚口径）留一份 cid，轮末
            # 再按既有逻辑挂到用户消息上。@author aceFelix
            ctx.extra["checkpoint_cid"] = ckpt_id or ""
            _pre_msg_len = len(messages)

            # 只取显式添加（Ctrl+V / /paste / /image）的待发送图片，
            # 不再发消息前自动读剪贴板。@author aceFelix
            pending = ctx.extra.pop("pending_images", None) or []

            # P3-1 跨设备协同：电脑端发消息时同步到手机端
            bridge_server = get_bridge_server()
            original_ui = ctx.ui
            broadcast_ui = None
            if bridge_server is not None:
                from agent.bridge.ui import BroadcastUI

                broadcast_ui = BroadcastUI(desktop_ui=ui, bridge=bridge_server)
                ctx.ui = broadcast_ui
                bridge_server.broadcast("user_message", {"text": stripped, "from": "desktop"})

            try:
                if bridge_server is not None:
                    # 在主线程 loop 加锁执行 query，与手机端 query 串行化
                    # run_query 内部用 threading.Lock 保证共享 messages 不被并发修改
                    stats = await bridge_server.run_query(stripped, ctx, images=pending)
                else:
                    stats = await loop.run(stripped, ctx, images=pending)
            finally:
                # 恢复原始 UI，并通知手机端本轮结束
                if broadcast_ui is not None:
                    ctx.ui = original_ui
                    broadcast_ui.finish()
                # 无论本轮成败都绑定检查点（loop.run 已把用户消息 append
                # 进 messages），出错轮次同样可撤回回滚。@author aceFelix
                if ckpt_id:
                    for _m in messages[_pre_msg_len:]:
                        if _m.role == "user":
                            _m.extra["checkpoint_id"] = ckpt_id
                            break

            if settings.verbose:
                _cache_hint = f" cache={stats.usage.cache_read_tokens}" if stats.usage.cache_read_tokens else ""
                ui.info(
                    f"[iterations={stats.iterations} tool_calls={stats.tool_calls} "
                    f"reason={stats.stopped_reason} "
                    f"tokens={stats.usage.input_tokens}+{stats.usage.output_tokens}{_cache_hint}]"
                )
            # 每轮对话后增量保存（防窗口被强杀丢失记忆）
            _dialog_count += 1
            cmd_ctx.dialog_count = _dialog_count
            _auto_save(ui, messages, workdir=settings.workdir, model=model, provider=settings.provider,
                       session_name=_session_name, verbose=False,
                       dialog_count=_dialog_count, title_generated=_title_generated,
                       settings=settings)
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
            # 注意：加载的会话已带标题（_title_generated=True），任何分支都不再生成
            if _dialog_count == 1 and not _title_generated:
                _session_name = await _generate_title_from_first_user(
                    ui, messages, _session_name
                )
                cmd_ctx.session_name = _session_name
            elif _dialog_count == 2 and len(messages) >= 4 and not _title_generated:
                _title_generated = True
                cmd_ctx.title_generated = True
                _session_name = await _generate_session_title(
                    ui, provider, model, messages, _session_name
                )
                cmd_ctx.session_name = _session_name
        except (KeyboardInterrupt, asyncio.CancelledError):
            ctx.abort_event.set()
            ui.warn("已中断（按回车继续）")
            ctx = _build_context(settings, ui, messages)  # 重置 abort
            cmd_ctx.ctx = ctx
        except Exception as e:
            ui.error(f"运行出错: {type(e).__name__}: {e}")
            if settings.debug:
                import traceback

                traceback.print_exc()

    # 退出前最终保存
    _auto_save(ui, messages, workdir=settings.workdir, model=model, provider=settings.provider,
               session_name=_session_name, dialog_count=_dialog_count, title_generated=_title_generated,
               settings=settings)

    # 先收后台线程再告别：避免 goodbye 之后还有提醒/目标播报插进来，
    # 也避免后台线程拖住进程退出（脊柱与提醒同口径）
    for _bg_host in (spine_host, reminder_host):
        try:
            _bg_host.stop()
        except Exception:
            pass

    ui.goodbye()
    # 清理 MCP 连接（静默处理 anyio cancel scope 错误）
    if mcp_client is not None:
        try:
            await mcp_client.disconnect_all()
        except BaseException:
            pass
    await provider.close()
    # 关闭进程级共享 HTTP 连接池。
    # provider.close() 刻意不碰它（多个 provider 共用同一个池，见
    # openai_provider.close() 的说明），所以进程退出时必须在这里显式关。
    # @author aceFelix
    try:
        from agent.llm.openai_provider import close_shared_http_client
        await close_shared_http_client()
    except Exception:
        pass
    # 清理 LSP server
    try:
        from agent.lsp.manager import get_lsp_manager
        mgr = get_lsp_manager()
        if mgr:
            await mgr.shutdown_all()
    except Exception:
        pass
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。

    @author aceFelix
    """
    from agent import __version__

    p = argparse.ArgumentParser(
        prog="jarvis",
        description="个人电脑 AI Agent",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"jarvis {__version__}",
        help="显示版本号并退出",
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
        "--config-show",
        action="store_true",
        dest="config_show",
        help="展示当前生效的完整配置（含多层合并结果 + MCP 状态）",
    )
    p.add_argument(
        "--quick",
        action="store_true",
        help="快速启动：跳过 boot animation / MCP / LSP，延迟加载 harness（热键唤起用）",
    )
    p.add_argument(
        "--init",
        # nargs="?"+const=""：裸 --init 等价于原 store_true；跟一个词才选流程
        # （"-" 开头的 token 不会被当成值，所以 `jarvis --init --workdir x` 安全）
        nargs="?",
        const="",
        default=None,
        metavar="[full|voice]",
        help="交互式配置引导（选厂商→输Key→测连接→保存）；可跟 full 或 voice",
    )
    # 已退役入口（2026-10，aceFelix）：--gui / --talk 原用于拉起 pywebview 三栏
    # 工作台窗口，窗口壳已从本仓库删除（图形化桌面由独立仓库 jarvis-desktop
    # 承担）。参数本身保留是为了给老用户一个可读的落地提示：历史版本写入的
    # 开机自启快捷方式指向 `jarvis --gui`，若直接报 unknown argument，在
    # wscript 静默运行下用户根本看不到原因。help=SUPPRESS 使其不出现在
    # --help 清单里，避免被当成可用功能。
    p.add_argument("--talk", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--gui", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--serve",
        action="store_true",
        help="headless API 服务（供 jarvis-desktop 等外部前端经 WebSocket 接入）",
    )
    p.add_argument(
        "--doctor",
        action="store_true",
        help="检查依赖安装状态（Python 包 / 系统级依赖 / 配置文件）",
    )
    p.add_argument(
        "--no-update-check",
        action="store_true",
        dest="no_update_check",
        help="跳过启动时的版本更新检查（等价 settings.toml [update] check = false）",
    )
    return p.parse_args(argv)


def announce_workbench_retired() -> int:
    """告知 --gui / --talk 已退役，并给出存量开机自启的手动清理路径。

    只做提示与指引，不删任何用户文件：自启项落在系统 Startup 目录，自动
    清理需重建旧版的注册表路径解析与文件删除逻辑，得不偿失。返回 0 而非
    非零码，避开旧版 VBS 把失败当异常弹框。

    文案只用 GBK 可编码字符（不用 ℹ 等符号）：该分支在 Rich 控制台建立
    之前，裸 print 直接写 Windows ANSI 代码页的控制台，遇不可编码字符会
    抛 UnicodeEncodeError。

    @author aceFelix
    """
    print("提示：三栏工作台窗口（--gui / --talk）已于 2026-10 退役。")
    print("  图形化桌面请改用独立仓库 jarvis-desktop（自身带开机自启设置）。")
    print("  终端用法不受影响：`jarvis` 进 REPL，`jarvis --serve` 开后端。")
    print("  若历史版本装过开机自启，需手动清理：")
    print("    Windows: 删 Startup 目录下的 JARVIS.lnk（一般在")
    print("             %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup）")
    print("             与 ~/.jarvis/start_workbench.vbs、~/.jarvis/jarvis_window.ico")
    print("    macOS:   删 ~/Library/LaunchAgents/ 下 JARVIS 的 .plist")
    return 0


def main(argv: list[str] | None = None) -> int:
    """程序主入口。

    @author aceFelix
    """
    args = parse_args(argv)

    # 健康检查：放在最前面，避免 settings 加载失败时无法诊断
    # 检查 Python 包 / 系统级依赖 / 配置文件是否就绪，不进入 REPL
    if args.doctor:
        from agent.doctor import run_doctor
        return run_doctor()

    # 三栏工作台窗口已退役（2026-10）：不再拉起任何 GUI，只打印下线说明与
    # 自启清理指引。放在加载配置之前：提示本身不依赖 settings，且避免旧
    # 自启项每次开机都白白跑一遍初始化（背景：docs/fixlogs/workbench-gui-retired.md）。
    if args.talk or args.gui:
        return announce_workbench_retired()

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
    if args.quick:
        overrides["boot_animation"] = False
        overrides["enable_mcp"] = False
        overrides["enable_lsp"] = False
        overrides["quick_start"] = True
    if args.no_update_check:
        overrides["update_check"] = False
    settings = settings.with_overrides(**overrides)

    # serve 模式（桌面壳）未显式指定 --workdir 时，回到上次活跃项目：
    # 桌面「选文件夹作为项目」把当前项目写入 projects.toml 的 last_active，
    # 重启 serve 即据此恢复 workdir（实现"重开续用最近项目"）。仅在记录的
    # 目录仍存在时启用，目录被删/移动则回退到默认 workdir（进程 cwd）。
    # @author aceFelix
    if args.serve and not args.workdir:
        from agent.config.projects_registry import get_last_active_existing
        _last = get_last_active_existing()
        if _last:
            settings = settings.with_overrides(workdir=_last)

    # W1 判断层启动接线：最终 settings 快照注入 policy 模块（CLI 与 serve
    # 都经 main() 进入，daemon 子进程同样受益）；未注入时 policy_gate
    # 用安全缺省，行为全回落既有管线。@author aceFelix
    from agent.core.policy import init_policy
    init_policy(settings)
    # 技能包 / 插件系统总开关：同为启动期快照（改完要重启），口径与
    # enable_mcp / enable_lsp 一致。@author aceFelix
    from agent.bootstrap import init_extension_switches
    init_extension_switches(settings)

    # 切到工作目录（不存在则自动创建）
    try:
        os.makedirs(settings.workdir, exist_ok=True)
        os.chdir(settings.workdir)
    except OSError as e:
        print(f"无法进入工作目录 {settings.workdir}: {e}", file=sys.stderr)
        return 1

    # 交互式首次配置（--init [full|voice]）
    if args.init is not None:
        from agent.commands.handlers.init_command import run_init_cli_from_args

        return run_init_cli_from_args(args.init)

    # 阶段 12 P4：全新机器上直接问三件，不让用户先去读文档跑 /init。
    # 判定与提问都放在 onboarding 里：非交互（管道/CI）不问，取消过一次就不再问。
    # 写完盘要重新 load 一次——provider 构建读的是 settings，内存里的还是旧值。
    # @author aceFelix
    if not args.serve:
        from agent.config import onboarding

        onboarding.note_start()
        if onboarding.should_onboard(settings):
            from agent.ui.cli import RichCLI as _CLI
            _ui = _CLI(verbose=False, boot_animation=False)
            try:
                asyncio.run(onboarding.run_quick_setup(_ui, settings))
            except KeyboardInterrupt:
                pass
            settings = load_settings(workdir=args.workdir or settings.workdir)
            settings = settings.with_overrides(**overrides)

    # 查看当前生效配置
    if args.config_show:
        from agent.ui.cli import RichCLI
        ui = RichCLI(verbose=False, boot_animation=False)
        provider = _build_provider(settings, model_type="text")
        from agent.commands.handlers.config_commands import _show_config
        _show_config(ui, settings, provider, mcp_client=None)
        return 0

    # headless API 服务：不渲染本地 UI，桌面壳（jarvis-desktop）经 WS 接入
    if args.serve:
        from agent.serve import run_serve
        return run_serve(settings)

    try:
        return asyncio.run(repl(settings))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
