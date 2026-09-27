"""工作台对话引擎：工作线程中的 QueryLoop 宿主。

pywebview 要求主线程跑窗口，因此把对话引擎放进守护线程：

- 独立 asyncio 事件循环，消费前端指令队列（send/load/new/stop 等）
- 组装方式对齐 ``main.repl()``：provider → registry → orchestrator → loop，
  但去掉终端专属环节（启动动画/剪贴板/桥接广播），MCP 保持接入
- 会话持久化复用 ``session_manager._auto_save``（与 REPL 同一套存盘格式），
  历史会话可被左栏列表读取并恢复
- 旁路能力按功能拆同包模块（控本文件行数）：voice_adapter（语音事件适配）、
  mcp_runtime（MCP 接入与启动预热）、model_switch（模型热切换）、
  render（附件解析与渲染）

@author aceFelix
"""

from __future__ import annotations

import asyncio
import queue
import threading
from datetime import datetime
from typing import Any, Callable

from agent.config.settings import Settings
from agent.core.message import Message
from agent.ui.workbench.bridge import WorkbenchRealtimeUI, WorkbenchUI, _EventEmitter
# 附件/渲染纯函数拆到 render.py（控本文件行数）；此处再导出保持
# tests/ui 既有导入路径（from engine import _messages_to_render）不变。
# @author aceFelix
from agent.ui.workbench.render import (  # noqa: F401
    _MAX_ATTACH_FILE_CHARS,
    _compose_with_files,
    _messages_to_render,
    _parse_images,
)
# 语音适配器与语音 WS 事件名拆到 voice_adapter.py（控本文件行数）；此处导入
# 保持 ``engine.ServeVoiceAdapter`` 的既有引用路径（tests/voice 直接导入）。
# @author aceFelix
from agent.ui.workbench.voice_adapter import (  # noqa: F401
    _EVT_VOICE_STARTED,
    _EVT_VOICE_STOPPED,
    ServeVoiceAdapter,
)
# 引擎旁路能力（MCP 预热 / 模型热切换）实现在同包模块，见各自模块头注释。
# 方法名与行为保持不变（测试与调用方直接调 engine._prewarm 等）。
# @author aceFelix
from agent.ui.workbench import mcp_runtime, model_switch


class ChatEngine:
    """工作台后端引擎：文本对话 + /talk 实时语音的统一宿主。

    前端通过 JSBridge 把指令放入 ``command_queue``，引擎线程消费执行；
    引擎通过 ``event_queue`` 向前端推事件。双向完全异步、互不阻塞。

    @author aceFelix
    """

    def __init__(
        self,
        settings: Settings,
        event_queue: queue.Queue[dict[str, Any]],
        command_queue: queue.Queue[dict[str, Any]],
        registry_hook: Callable[[Any], None] | None = None,
    ) -> None:
        """
        Args:
            settings: 全局配置。
            event_queue: 引擎 → 前端事件队列。
            command_queue: 前端 → 引擎指令队列。
            registry_hook: 可选工具注册表钩子。在 build_default_registry()
                之后、系统提示词生成之前调用，宿主可据此挂载额外工具
                （如 serve 宿主的提醒/截止日期工具）。workbench 宿主不传，
                行为零变化。钩子异常静默降级（info 事件告知）。
        """
        self._settings = settings
        self._event_queue = event_queue
        self._command_queue = command_queue
        self._registry_hook = registry_hook
        # MCP client 引用（_connect_mcp 保留防 GC 断连，对齐 realtime_talk 的做法）
        # @author aceFelix
        self._mcp_client: Any = None
        # MCP 连接结果快照（state.get 供右栏运行健康展示；None=MCP 未启用/未装配）
        # 结构：{"connected": [server 名...], "failed": [...], "tools": int}
        # @author aceFelix
        self._mcp_status: dict[str, Any] | None = None
        self._emitter = _EventEmitter(event_queue)
        self._ui = WorkbenchUI(self._emitter)
        self._realtime_ui = WorkbenchRealtimeUI(self._emitter)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()
        # 当前 send 轮次的任务句柄（reply.abort 从其他线程线程安全地取消它）
        self._send_task: asyncio.Task | None = None
        # 启动后台预热：registry（+宿主钩子）与 MCP 连接提前到启动空窗完成，
        # 首条消息不再同步等 MCP（实测 7 server 并发约 9s）；_ensure_session
        # 等 _registry_ready 后复用 _registry，预热失败降级回同步装配。
        # @author aceFelix
        self._registry: Any = None
        self._registry_ready = asyncio.Event()
        self._prewarm_task: asyncio.Task | None = None
        # 自动标题任务句柄：用户改名时取消未落地任务，防自动标题覆盖自定义名。
        # @author aceFelix
        self._title_task: asyncio.Task | None = None
        # 待生效的模型切换：models.select 可能在会话装配前到达（首条消息前的
        # 选模型），此时先记账，_ensure_session 装配完成后落地；空串 = 无。
        # @author aceFelix
        self._model_override = ""
        # 当前 provider 的端点配置快照：热切换时作为「要不要重建 provider」的
        # 比较基准。不能直接用 _settings —— 它始终是进程启动时的快照。
        # @author aceFelix
        self._provider_settings = settings

    # ---- 只读状态快照（serve 右栏数据源：cost.get / state.get 经 WorkbenchAPI 读取） ----

    @property
    def session_usage(self) -> dict[str, int]:
        """会话级累计 token 用量（cost.get 数据源；引擎未装配/假 loop 时全 0）。

        @author aceFelix
        """
        usage = getattr(getattr(self, "_query_loop", None), "session_usage", None)
        if usage is None:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            }
        return {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read_tokens": int(getattr(usage, "cache_read_tokens", 0) or 0),
            "cache_creation_tokens": int(getattr(usage, "cache_creation_tokens", 0) or 0),
        }

    @property
    def dialog_count(self) -> int:
        """已完成对话轮数（cost.get 数据源；未装配时为 0）。"""
        return int(getattr(self, "_dialog_count", 0) or 0)

    @property
    def message_count(self) -> int:
        """当前会话消息条数（cost.get 数据源；未装配时为 0）。"""
        return len(getattr(self, "_messages", None) or [])

    @property
    def session_name(self) -> str:
        """当前会话名（sessions.list 的 current 标记数据源；未装配时为空串）。

        会话名在新建/恢复/标题改名时都会变（_session_name），
        列表每次刷新现读现比，前端无需跟踪事件序列。@author aceFelix
        """
        return str(getattr(self, "_session_name", "") or "")

    @property
    def current_model(self) -> str:
        """当前生效的模型名（models.list 的 current / cost.get 数据源）。

        会话已装配 → QueryLoop 正在跑的模型（热切换即时更新）；未装配 →
        待生效的切换目标（models.select 早于首条消息时先记账）；
        都没有 → 启动配置快照。@author aceFelix
        """
        if getattr(self, "_session_ready", False):
            current = getattr(self, "_model", "")
        else:
            current = self._model_override
        return str(current or self._settings.model or "")

    @property
    def current_vendor(self) -> str:
        """当前生效模型的厂商（state.get 的 provider 字段数据源）。

        provider 端点快照随热切换更新（自定义模型可能换厂商），
        故不能一律读启动 settings。@author aceFelix
        """
        vendor = getattr(self._provider_settings, "provider", "")
        return str(vendor or self._settings.provider or "")

    @property
    def mcp_status(self) -> dict[str, Any] | None:
        """MCP 连接结果快照（state.get 数据源；None=MCP 未启用或未装配）。"""
        return self._mcp_status

    @property
    def is_busy(self) -> bool:
        """是否处于对话轮次或语音会话（主动播报 TTS「忙时跳过」探针）。

        三路任一未结束即忙：文本 send 轮次（_send_task）、半双工语音
        （_voice_task）、实时双工（_talk_task）。忙时主动播报只推事件、
        不做 TTS 朗读，避免打断正在进行的对话或抢占语音通道。

        @author aceFelix
        """
        task = self._send_task
        if task is not None and not task.done():
            return True
        for attr in ("_voice_task", "_talk_task"):
            t = getattr(self, attr, None)
            if t is not None and not t.done():
                return True
        return False

    # ---- 生命周期 ----

    def start(self) -> None:
        """启动引擎线程（幂等）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="workbench-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止引擎：投递 stop 指令，等待线程退出。"""
        self._stop_event.set()
        try:
            self._command_queue.put_nowait({"cmd": "stop"})
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        """线程入口：建独立事件循环并消费指令。"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        # 启动预热任务：registry + MCP 连接在启动空窗后台完成（首条消息秒进）
        self._prewarm_task = self._loop.create_task(self._prewarm())
        try:
            self._loop.run_until_complete(self._command_loop())
        except Exception as e:
            self._emitter.emit("error", f"引擎异常: {type(e).__name__}: {e}")
        finally:
            try:
                self._loop.run_until_complete(self._shutdown())
            except Exception:
                pass
            self._loop.close()

    # ---- 指令消费主循环 ----

    async def _prewarm(self) -> None:
        """启动后台预装配：registry（+宿主钩子）→ MCP 连接（实现见 mcp_runtime）。

        MCP 多 server 连接实测约 9s，前移到启动空窗后台后首条消息秒进 LLM；
        异常时仍置位 _registry_ready，由 _ensure_session 降级同步装配。
        @author aceFelix
        """
        await mcp_runtime.prewarm(self)

    async def _command_loop(self) -> None:
        """消费前端指令队列；空闲时让出事件循环。"""
        while not self._stop_event.is_set():
            try:
                cmd = self._command_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            if not isinstance(cmd, dict):
                continue
            action = cmd.get("cmd")
            if action == "stop":
                break
            try:
                await self._dispatch(cmd)
            except asyncio.CancelledError:
                # reply.abort 取消了当前 send 轮次：_handle_send 的 finally
                # 已发 assistant_done 收尾，指令循环继续消费后续指令。
                # @author aceFelix
                self._emitter.emit("info", "已停止回复")
            except Exception as e:
                self._emitter.emit("error", f"指令处理失败({action}): {type(e).__name__}: {e}")

    async def _dispatch(self, cmd: dict[str, Any]) -> None:
        """指令分发：懒装配对话会话，首次 send 时才初始化重型零件。"""
        action = cmd.get("cmd")
        if action == "send":
            await self._ensure_session()
            await self._handle_send(
                cmd.get("text", ""), cmd.get("images"), cmd.get("files")
            )
        elif action == "load_session":
            await self._ensure_session()
            await self._handle_load(cmd.get("name", ""))
        elif action == "new_session":
            await self._ensure_session()
            self._handle_new_session()
        elif action == "rename_session":
            await self._ensure_session()
            self._handle_rename(cmd.get("name", ""), cmd.get("new_name", ""))
        elif action == "delete_session":
            await self._ensure_session()
            self._handle_delete(cmd.get("name", ""))
        elif action == "switch_model":
            # 模型热切换：刻意不调 _ensure_session（会话未装配时由处理函数
            # 记账延迟，不因一次切换提前触发重型装配）。force 由 models.edit
            # 带上（改了当前模型配置 → 同名也必须重建 provider）。@author aceFelix
            await self._handle_switch_model(cmd.get("name", ""), bool(cmd.get("force")))
        elif action == "start_talk":
            await self._handle_start_talk()
        elif action == "stop_talk":
            await self._handle_stop_talk()
        elif action == "start_voice":
            await self._handle_start_voice()
        elif action == "stop_voice":
            await self._handle_stop_voice()
        elif action == "interrupt_voice":
            await self._handle_interrupt_voice()
        elif action == "answer_user":
            self._ui.answer_user(cmd.get("text", ""))

    # ---- 文本对话 ----

    async def _ensure_session(self) -> None:
        """懒初始化对话会话（对齐 repl 的装配，去掉终端专属环节）。"""
        if getattr(self, "_session_ready", False):
            return
        from agent.bootstrap import (
            _build_checker,
            _build_context,
            _build_provider,
            _build_recovery_executor,
            _model_type_for,
        )
        from agent.core.orchestrator import ToolOrchestrator
        from agent.core.query_loop import QueryLoop
        from agent.core.tool import ToolRegistry, build_default_registry, register_dynamic_tools
        from agent.prompts.system import build_system_prompt

        s = self._settings
        self._emitter.emit("status", "正在初始化对话引擎...")
        provider = _build_provider(s, model_type=_model_type_for(s))
        # 复用启动预热的 registry（MCP 已在后台连接/连完）；预热未产出
        # （超时/失败）才同步装配兜底，含 MCP 连接，保证能力不缺失。
        # @author aceFelix
        registry: ToolRegistry | None = None
        try:
            await asyncio.wait_for(self._registry_ready.wait(), timeout=30)
            registry = self._registry
        except asyncio.TimeoutError:
            registry = None
        if registry is None:
            registry = build_default_registry()
            # 宿主钩子：挂载额外工具（serve 宿主在此接提醒/截止日期工具），
            # 在系统提示词生成前执行，使 LLM 能感知新工具；失败不阻断装配
            if self._registry_hook is not None:
                try:
                    self._registry_hook(registry)
                except Exception as e:
                    self._emitter.emit("info", f"⚠ 工具注册钩子失败: {type(e).__name__}: {e}")
            if s.enable_mcp:
                await self._connect_mcp(registry)
        # harness 动态工具后台加载，避免阻塞首轮对话
        threading.Thread(
            target=lambda: self._register_harness(registry, s.workdir), daemon=True
        ).start()
        checker = _build_checker(s)
        recovery = _build_recovery_executor(s)
        orchestrator = ToolOrchestrator(
            registry=registry, permission_checker=checker, recovery_executor=recovery
        )
        system_prompt = build_system_prompt(
            s.workdir, registry, enable_thinking=s.enable_thinking, settings=s
        )
        if s.system_prompt_append:
            system_prompt = system_prompt + "\n\n" + s.system_prompt_append
        model = s.model or provider.default_model
        loop = QueryLoop(
            provider=provider,
            registry=registry,
            orchestrator=orchestrator,
            system=system_prompt,
            model=model,
            max_iterations=s.max_iterations,
            max_tokens=s.max_tokens,
            temperature=s.temperature,
            enable_compaction=s.context_compaction,
            context_window=s.context_window,
            compact_ratio=s.compact_ratio,
            compact_refreeze_growth=s.compact_refreeze_growth,
            compact_max_output_tokens=s.compact_max_output_tokens,
            tool_result_keep_recent=s.tool_result_keep_recent,
            vendor_fallback=s.vendor_fallback,
            custom_models=s.custom_models,
            deferred_loading=s.tools_deferred_loading,
            chat_detection=s.tools_chat_detection,
        )
        self._provider = provider
        self._provider_settings = s
        self._query_loop = loop
        self._model = model
        self._messages: list[Message] = []
        self._ctx = _build_context(s, self._ui, self._messages)
        self._session_name = f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self._dialog_count = 0
        self._title_generated = False
        self._session_ready = True
        self._emitter.emit("status", "就绪")
        self._emitter.emit("session_ready", {"name": self._session_name})
        # 装配期间到达的模型切换（models.select 早于首条消息）：此时补落地，
        # 让首个 send 就用上用户刚选的模型。@author aceFelix
        if self._model_override:
            override = self._model_override
            self._model_override = ""
            await self._handle_switch_model(override)

    async def _handle_switch_model(self, name: str, force: bool = False) -> None:
        """热切换运行中的对话模型（models.select / models.edit；实现见 model_switch）。

        切换在引擎指令队列里串行执行：正有一轮回复在跑时，它在回复结束后
        落地；会话未装配时只记账（_model_override）延迟到 _ensure_session。
        force=True（models.edit 改了当前模型配置）时同名也重建 provider。
        @author aceFelix
        """
        await model_switch.handle_switch_model(self, name, force)

    def _register_harness(self, registry: Any, workdir: str) -> None:
        """后台注册 CLI-Anything harness 工具（实现见 mcp_runtime）。"""
        mcp_runtime.register_harness(self, registry, workdir)

    async def _connect_mcp(self, registry: Any) -> None:
        """连接配置的 MCP server 并把工具注册进 registry（实现见 mcp_runtime）。

        任何异常静默降级（仅 info 提示），不阻断文本对话装配。@author aceFelix
        """
        await mcp_runtime.connect_mcp(self, registry)

    async def _handle_send(
        self,
        text: str,
        images: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
    ) -> None:
        """执行一轮对话：loop.run → 事件流已在 UI 适配器中推给前端。

        附件（桌面壳 📎 按钮 / 粘贴，见 docs/architecture/07-UI层.md）：
        - images：base64 图片块列表，转 ImageContent 走 loop.run 的 vision 参数；
        - files：文本文件内容列表，拼进消息正文的「附带文件」代码块（超长截断），
          模型直接读到文件内容，无需额外工具。

        @author aceFelix
        """
        text = (text or "").strip()
        img_blocks = _parse_images(images)
        if not text and not img_blocks and not files:
            return
        composed = _compose_with_files(text, files)
        if not composed.strip():
            # 纯图片消息：补一句最小指令文本，让模型有回应落点
            composed = "请结合附带的图片回答。"
        self._emitter.emit("user_message", text)
        # 记录当前任务句柄：abort_current_reply 从其他线程取消它实现"停止回复"
        self._send_task = asyncio.current_task()
        try:
            await self._query_loop.run(composed, self._ctx, images=img_blocks or None)
        except Exception as e:
            self._emitter.emit("error", f"运行出错: {type(e).__name__}: {e}")
        finally:
            self._send_task = None
            self._ui.assistant_done()
            self._after_turn()

    def abort_current_reply(self) -> bool:
        """停止当前回复（桌面壳发送按钮二次点击）：线程安全取消 send 任务。

        不走指令队列：_command_loop 串行 await 当前 _handle_send，队列型
        abort 会像 answer_user 一样自死锁。CancelledError 沿 _stream_once /
        工具层优雅退出（bash 子进程被回收），_handle_send 的 finally 仍发
        assistant_done 让前端收尾，_command_loop 捕获后继续服务。

        @author aceFelix
        """
        task = self._send_task
        loop = self._loop
        if task is None or task.done() or loop is None:
            return False
        loop.call_soon_threadsafe(task.cancel)
        return True

    def _after_turn(self) -> None:
        """一轮对话后的持久化：增量保存 + 标题生成（与 REPL 同规则）。"""
        from agent.session_manager import (
            _auto_save,
            _generate_session_title,
            _generate_title_from_first_user,
        )

        self._dialog_count += 1
        try:
            _auto_save(
                self._ui,
                self._messages,
                workdir=self._settings.workdir,
                model=self._model,
                provider=self._settings.provider,
                session_name=self._session_name,
                verbose=False,
                dialog_count=self._dialog_count,
                title_generated=self._title_generated,
                settings=self._settings,
            )
        except Exception:
            pass
        # 标题生成放到后台任务：不阻塞下一轮输入
        if self._dialog_count == 1 and not self._title_generated:

            async def _gen_first() -> None:
                self._session_name = await _generate_title_from_first_user(
                    self._ui, self._messages, self._session_name
                )
                # 标题改名专用事件：session_ready 带"清空气泡"的初始化语义，
                # 复用会把刚渲染的回复清掉（桌面端/工作台均踩过），改名只刷列表
                self._emitter.emit("session_renamed", {"name": self._session_name})

            # 句柄留存：用户改名时取消未落地任务，防自动标题覆盖自定义名
            self._title_task = asyncio.get_event_loop().create_task(_gen_first())
        elif self._dialog_count == 2 and len(self._messages) >= 4 and not self._title_generated:
            self._title_generated = True

            async def _gen_llm() -> None:
                self._session_name = await _generate_session_title(
                    self._ui, self._provider, self._model, self._messages, self._session_name
                )
                # 同上：改名推送 session_renamed，前端只刷新会话列表不清屏
                self._emitter.emit("session_renamed", {"name": self._session_name})

            # 同上：LLM 标题任务句柄留存供改名取消
            self._title_task = asyncio.get_event_loop().create_task(_gen_llm())

    async def _handle_load(self, name: str) -> None:
        """恢复历史会话：载入消息并把历史渲染给前端。"""
        from agent.core.memory.store import load_session

        session = load_session(name)
        if session is None or not session.messages:
            self._emitter.emit("error", f"会话不存在或为空: {name}")
            return
        self._messages.clear()
        self._messages.extend(session.messages)
        self._session_name = session.meta.name
        self._dialog_count = session.meta.dialog_count
        self._title_generated = bool(session.meta.title_generated)
        # 重建 ctx（messages 列表对象未变，只需刷新引用）
        self._emitter.emit("session_loaded", {
            "name": self._session_name,
            "messages": _messages_to_render(self._messages),
        })

    def _handle_new_session(self) -> None:
        """新建会话：清空消息与轮数，前端同步清空气泡。"""
        self._messages.clear()
        self._session_name = f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self._dialog_count = 0
        self._title_generated = False
        self._emitter.emit("session_new", {"name": self._session_name})

    def _handle_rename(self, name: str, new_name: str) -> None:
        """会话改名：同步存盘文件与当前会话名，成功推 session_renamed 刷列表。

        用户改名优先于自动标题：改名当前会话后置 _title_generated 并取消
        未落地的标题任务，防自动标题覆盖用户自定义名；目标名已占用
        （存盘文件存在）一律拒绝。@author aceFelix
        """
        from agent.core.memory.store import rename_session as _store_rename
        from agent.core.memory.store import session_exists

        name = (name or "").strip()
        new_name = (new_name or "").strip()
        if not name or not new_name or new_name == name:
            self._emitter.emit("warn", "改名已忽略：会话名为空或未变化")
            return
        if session_exists(new_name):
            self._emitter.emit("warn", f"改名失败：目标会话名已存在: {new_name}")
            return
        if session_exists(name):
            _store_rename(name, new_name)
        elif name != self._session_name:
            self._emitter.emit("warn", f"改名失败：会话不存在: {name}")
            return
        # 盘上完成（或当前会话尚未落盘）：同步引擎态
        if name == self._session_name:
            task = self._title_task
            if task is not None and not task.done():
                task.cancel()
            self._session_name = new_name
            self._title_generated = True
        self._emitter.emit("session_renamed", {"name": new_name})

    def _handle_delete(self, name: str) -> None:
        """会话删除：移除存盘文件并推 session_deleted 刷列表。

        删除当前会话时复用 _handle_new_session 语义（清消息开新会话），
        前端聊天区与存盘列表一致归空。@author aceFelix
        """
        from agent.core.memory.store import delete_session as _store_delete

        name = (name or "").strip()
        if not name:
            return
        existed = _store_delete(name)
        if not existed and name != self._session_name:
            self._emitter.emit("warn", f"删除失败：会话不存在: {name}")
            return
        self._emitter.emit("session_deleted", {"name": name})
        if name == self._session_name:
            self._handle_new_session()

    # ---- /talk 实时语音 ----

    async def _handle_start_talk(self) -> None:
        """启动实时双工语音：独立线程跑 RealtimeTalk（配置提取对齐 /talk 命令）。"""
        import os

        if getattr(self, "_talk_task", None) is not None:
            self._emitter.emit("info", "实时对话已在运行")
            return
        # 与 /voice 互斥：进入实时语音前先停半双工语音，避免麦克风/扬声器冲突
        if getattr(self, "_voice_task", None) is not None:
            await self._handle_stop_voice()
        s = self._settings
        api_key = (
            s.dashscope_api_key
            or os.environ.get("DASHSCOPE_API_KEY", "")
            or s.api_key
            or os.environ.get("OPENAI_API_KEY", "")
        )
        if not api_key:
            self._emitter.emit("error", "未配置 DashScope API Key，无法启动实时语音")
            return
        try:
            from agent.voice.realtime_talk import DEFAULT_WS_URL, RealtimeTalk
        except ImportError as e:
            self._emitter.emit("error", f"实时语音模块不可用: {e}")
            return

        rt = RealtimeTalk(
            api_key=api_key,
            model=getattr(s, "realtime_model", "qwen-audio-3.0-realtime-flash"),
            voice=getattr(s, "realtime_voice", "longanqian"),
            ws_url=getattr(s, "realtime_ws_url", "") or DEFAULT_WS_URL,
            workdir=getattr(s, "workdir", "") or os.getcwd(),
        )
        self._talk_instance = rt

        async def _talk_main() -> None:
            try:
                await rt.run(self._realtime_ui)
            except Exception as e:
                self._emitter.emit("error", f"实时对话异常: {type(e).__name__}: {e}")
            finally:
                self._talk_task = None
                self._emitter.emit("talk_stopped", "")

        self._talk_task = asyncio.get_event_loop().create_task(_talk_main())
        self._emitter.emit("talk_started", "")

    async def _handle_stop_talk(self) -> None:
        """停止实时语音会话（窗口保持打开）。"""
        rt = getattr(self, "_talk_instance", None)
        if rt is not None:
            try:
                rt._running = False
            except Exception:
                pass
        task = getattr(self, "_talk_task", None)
        if task is not None:
            task.cancel()
            self._talk_task = None
        self._emitter.emit("talk_stopped", "")

    # ---- /voice 半双工语音 ----

    async def _handle_start_voice(self) -> None:
        """启动 /voice 半双工：独立 asyncio task 跑解耦 voice_loop。

        与 /talk 互斥（先停实时语音）；麦克风 barge-in watcher 挂接
        interrupt_event（播报中开口自动打断），桌面壳按钮另走
        interrupt_voice 指令置位同一 event（双通道）。

        @author aceFelix
        """
        if getattr(self, "_voice_task", None) is not None:
            self._emitter.emit("info", "半双工语音已在运行")
            return
        # 与 /talk 互斥：进入半双工前先停实时语音，避免麦克风/扬声器冲突
        if getattr(self, "_talk_task", None) is not None:
            await self._handle_stop_talk()
        await self._ensure_session()
        s = self._settings
        adapter = ServeVoiceAdapter(self._emitter)
        self._voice_interrupt = threading.Event()
        self._voice_stop = threading.Event()
        # 麦克风 barge-in（双通道之一）：TTS 播报中检测用户开口自动打断
        self._voice_mic_watcher = None
        if getattr(s, "voice_barge_in", True):
            try:
                from agent.voice.barge_in import _BargeInWatcher
                w = _BargeInWatcher(lambda: self._voice_interrupt.set())
                if getattr(w, "available", True):
                    w.start()
                    self._voice_mic_watcher = w
            except Exception:
                self._voice_mic_watcher = None

        async def _voice_main() -> None:
            try:
                from agent.voice.voice_loop import voice_loop
                await voice_loop(
                    adapter, s, self._query_loop, self._ctx,
                    stop_event=self._voice_stop,
                    interrupt_event=self._voice_interrupt,
                )
            except Exception as e:
                self._emitter.emit("error", f"半双工语音异常: {type(e).__name__}: {e}")
            finally:
                self._voice_task = None
                self._stop_voice_watchers()
                self._emitter.emit(_EVT_VOICE_STOPPED, "")

        self._voice_task = asyncio.get_event_loop().create_task(_voice_main())
        self._emitter.emit(_EVT_VOICE_STARTED, "")

    async def _handle_stop_voice(self) -> None:
        """停止半双工语音：置 stop_event + 中断阻塞录音，让 voice_loop 干净退出。"""
        stop_ev = getattr(self, "_voice_stop", None)
        if stop_ev is not None:
            stop_ev.set()
        # 中断可能阻塞在 pyaudio 的 stt.listen()（C 扩展不吃 asyncio 取消）
        try:
            from agent.voice import stt as stt_module
            stt_module._request_stop()
        except Exception:
            pass
        task = getattr(self, "_voice_task", None)
        if task is not None:
            # 给 voice_loop 一点时间走干净退出（释放语音锁 / 音频）
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
            except Exception:
                task.cancel()
            self._voice_task = None
        self._stop_voice_watchers()
        self._emitter.emit(_EVT_VOICE_STOPPED, "")

    async def _handle_interrupt_voice(self) -> bool:
        """打断当前播报 / 推理（桌面壳按钮通道）：置位 interrupt_event。"""
        intr = getattr(self, "_voice_interrupt", None)
        if intr is None:
            return False
        intr.set()
        # 聆听阶段打断：同时中断阻塞的 stt.listen()
        try:
            from agent.voice import stt as stt_module
            stt_module._request_stop()
        except Exception:
            pass
        return True

    def _stop_voice_watchers(self) -> None:
        """停止麦克风 barge-in watcher（voice 退出时回收）。"""
        w = getattr(self, "_voice_mic_watcher", None)
        if w is not None:
            try:
                w.stop()
            except Exception:
                pass
            self._voice_mic_watcher = None

    # ---- 收尾 ----

    async def _shutdown(self) -> None:
        """引擎退出：停预热、停实时语音、停半双工语音、关 provider。"""
        task = self._prewarm_task
        if task is not None and not task.done():
            task.cancel()
            # 必须等取消落地再关 loop：否则 loop.close() 时预热任务仍 pending，
            # 报 "Task was destroyed but it is pending" 且 MCP 子进程回收不全。
            # @author aceFelix
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._handle_stop_talk()
        await self._handle_stop_voice()
        provider = getattr(self, "_provider", None)
        if provider is not None:
            try:
                await provider.close()
            except Exception:
                pass
