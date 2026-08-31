"""工作台对话引擎：工作线程中的 QueryLoop 宿主。

pywebview 要求主线程跑窗口，因此把对话引擎放进守护线程：

- 独立 asyncio 事件循环，消费前端指令队列（send/load/new/stop 等）
- 组装方式对齐 ``main.repl()``：provider → registry → orchestrator → loop，
  但去掉终端专属环节（启动动画/剪贴板/桥接广播），MCP 保持接入
- 会话持久化复用 ``session_manager._auto_save``（与 REPL 同一套存盘格式），
  历史会话可被左栏列表读取并恢复

@author aceFelix
"""

from __future__ import annotations

import asyncio
import queue
import threading
from datetime import datetime
from typing import Any

from agent.config.settings import Settings
from agent.core.message import Message
from agent.ui.workbench.bridge import WorkbenchRealtimeUI, WorkbenchUI, _EventEmitter


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
    ) -> None:
        self._settings = settings
        self._event_queue = event_queue
        self._command_queue = command_queue
        self._emitter = _EventEmitter(event_queue)
        self._ui = WorkbenchUI(self._emitter)
        self._realtime_ui = WorkbenchRealtimeUI(self._emitter)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()

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
            except Exception as e:
                self._emitter.emit("error", f"指令处理失败({action}): {type(e).__name__}: {e}")

    async def _dispatch(self, cmd: dict[str, Any]) -> None:
        """指令分发：懒装配对话会话，首次 send 时才初始化重型零件。"""
        action = cmd.get("cmd")
        if action == "send":
            await self._ensure_session()
            await self._handle_send(cmd.get("text", ""))
        elif action == "load_session":
            await self._ensure_session()
            await self._handle_load(cmd.get("name", ""))
        elif action == "new_session":
            await self._ensure_session()
            self._handle_new_session()
        elif action == "start_talk":
            await self._handle_start_talk()
        elif action == "stop_talk":
            await self._handle_stop_talk()
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
        registry: ToolRegistry = build_default_registry()
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

    def _register_harness(self, registry: Any, workdir: str) -> None:
        """后台注册 CLI-Anything harness 工具（失败不影响主流程）。"""
        try:
            from agent.core.tool import register_dynamic_tools

            count = register_dynamic_tools(registry, workdir=workdir)
            if count > 0:
                self._emitter.emit("info", f"✓ harness 工具已加载（{count} 个）")
        except Exception:
            pass

    async def _handle_send(self, text: str) -> None:
        """执行一轮文本对话：loop.run → 事件流已在 UI 适配器中推给前端。"""
        text = (text or "").strip()
        if not text:
            return
        self._emitter.emit("user_message", text)
        try:
            await self._query_loop.run(text, self._ctx)
        except Exception as e:
            self._emitter.emit("error", f"运行出错: {type(e).__name__}: {e}")
        finally:
            self._ui.assistant_done()
            self._after_turn()

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
                self._emitter.emit("session_ready", {"name": self._session_name})

            asyncio.get_event_loop().create_task(_gen_first())
        elif self._dialog_count == 2 and len(self._messages) >= 4 and not self._title_generated:
            self._title_generated = True

            async def _gen_llm() -> None:
                self._session_name = await _generate_session_title(
                    self._ui, self._provider, self._model, self._messages, self._session_name
                )
                self._emitter.emit("session_ready", {"name": self._session_name})

            asyncio.get_event_loop().create_task(_gen_llm())

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

    # ---- /talk 实时语音 ----

    async def _handle_start_talk(self) -> None:
        """启动实时双工语音：独立线程跑 RealtimeTalk（配置提取对齐 /talk 命令）。"""
        import os

        if getattr(self, "_talk_task", None) is not None:
            self._emitter.emit("info", "实时对话已在运行")
            return
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

    # ---- 收尾 ----

    async def _shutdown(self) -> None:
        """引擎退出：停实时语音、关 provider。"""
        await self._handle_stop_talk()
        provider = getattr(self, "_provider", None)
        if provider is not None:
            try:
                await provider.close()
            except Exception:
                pass


def _messages_to_render(messages: list[Message]) -> list[dict[str, Any]]:
    """把历史消息转成前端渲染结构（只保留文本块，工具块折叠为提示）。

    @author aceFelix
    """
    from agent.core.message import TextContent, ToolResultContent, ToolUseContent

    out: list[dict[str, Any]] = []
    for m in messages:
        texts: list[str] = []
        tool_count = 0
        for b in m.content:
            if isinstance(b, TextContent) and b.text.strip():
                texts.append(b.text)
            elif isinstance(b, (ToolUseContent, ToolResultContent)):
                tool_count += 1
        if not texts and not tool_count:
            continue
        # 纯工具结果消息（role=user 的工具回填）不进历史回放；
        # assistant 纯工具消息保留为"工具调用 ×N"提示行。
        if not texts and m.role != "assistant":
            continue
        item: dict[str, Any] = {"role": m.role, "text": "\n\n".join(texts)}
        if tool_count:
            item["tool_count"] = tool_count
        out.append(item)
    return out
