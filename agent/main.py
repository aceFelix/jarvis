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
from agent.core.images import (
    _auto_attach_clipboard_image,
    _hash_image,
    _load_image_from_clipboard,
    _pending_images,
)
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


async def repl(settings: Settings, with_tray: bool = False) -> int:
    """REPL 主循环。返回退出码。

    with_tray=True 时启动一个系统托盘图标（daemon 线程），托盘"退出"
    会直接终止整个进程。这样前台 REPL 和托盘共存，任一退出=整体退出。

    @author aceFelix
    """
    ui = RichCLI(verbose=settings.verbose, boot_animation=settings.boot_animation)
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

    # ToolSearch 工具注册（延迟加载机制的核心，参考 Claude Code deferred tool loading）
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
        compaction_threshold=settings.compaction_threshold,
        keep_recent_messages=settings.keep_recent_messages,
        vendor_fallback=settings.vendor_fallback,
        custom_models=settings.custom_models,
        deferred_loading=settings.tools_deferred_loading,
        chat_detection=settings.tools_chat_detection,
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
                voice_active_getter=lambda: True,  # 前台 REPL 无语音开关概念，默认开启
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
                # 跳过恢复 = 全新会话：启动时 auto_resume_session 已把上次会话
                # 消息加载进 messages，必须一并清空。否则第一轮标题生成会取
                # 旧会话首条消息（如「jarvis在干嘛」），新会话上下文也被旧对话
                # 污染。ctx.messages 与 messages 共享同一列表，clear() 即可同步。
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
        tray=tray,
        should_exit=False,
    )

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
            pending = _auto_attach_clipboard_image(ctx, ui)

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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。

    @author aceFelix
    """
    from agent import __version__

    p = argparse.ArgumentParser(
        prog="jarvis",
        description="个人电脑 AI Agent（借鉴 Claude Code 架构）",
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
        "--with-tray",
        action="store_true",
        help="前台 REPL 同时启动托盘图标（托盘退出=整体退出）",
    )
    p.add_argument(
        "--init",
        action="store_true",
        help="交互式首次配置引导：选厂商→输Key→测试连接→保存",
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
        "--talk",
        action="store_true",
        help="直接启动实时双工语音对话（/talk），需要配置 DashScope API Key",
    )
    return p.parse_args(argv)


def _run_with_watchdog(daemon) -> int:
    """看门狗：daemon 崩溃后自动重启。10 分钟内最多 5 次。

    @author aceFelix
    """
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


def main(argv: list[str] | None = None) -> int:
    """程序主入口。

    @author aceFelix
    """
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
    if args.quick:
        overrides["boot_animation"] = False
        overrides["enable_mcp"] = False
        overrides["enable_lsp"] = False
        overrides["quick_start"] = True
    settings = settings.with_overrides(**overrides)

    # 切到工作目录（不存在则自动创建）
    try:
        os.makedirs(settings.workdir, exist_ok=True)
        os.chdir(settings.workdir)
    except OSError as e:
        print(f"无法进入工作目录 {settings.workdir}: {e}", file=sys.stderr)
        return 1

    # 交互式首次配置
    if args.init:
        from agent.ui.cli import RichCLI
        ui = RichCLI(verbose=False, boot_animation=False)
        from agent.commands.handlers.init_command import run_init_cli
        try:
            asyncio.run(run_init_cli(ui))
        except KeyboardInterrupt:
            pass
        return 0

    # 查看当前生效配置
    if args.config_show:
        from agent.ui.cli import RichCLI
        ui = RichCLI(verbose=False, boot_animation=False)
        provider = _build_provider(settings, model_type="text")
        from agent.commands.handlers.config_commands import _show_config
        _show_config(ui, settings, provider, mcp_client=None)
        return 0

    # 直接启动实时双工语音对话
    if args.talk:
        ui = RichCLI(verbose=settings.verbose, boot_animation=not args.no_boot)
        try:
            # 延迟导入语音模块，避免未安装时主入口异常
            from agent.commands.handlers.voice_commands import _realtime_talk
            return asyncio.run(_realtime_talk(ui, settings))
        except KeyboardInterrupt:
            return 130

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
                import platform as _pf
                if _pf.system() == "Linux":
                    print("ℹ Linux 不支持后台分离模式，以前台模式运行 daemon", file=sys.stderr)
                    print("  提示: Linux 用户可直接用 `python -m agent.main` 进入 REPL 模式", file=sys.stderr)
                else:
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
