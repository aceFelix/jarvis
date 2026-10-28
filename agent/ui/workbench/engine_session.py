"""ChatEngine 的会话装配与会话管理（从 engine.py 拆出）。

两组职责，都围绕「一个会话怎么被建起来、怎么被换掉」：

- 装配：``_ensure_session`` 惰性建会话（provider / QueryLoop / 系统提示词 / harness /
  MCP 连接），``_handle_switch_model`` 热切模型，``_handle_set_workdir`` 换工作目录，
  ``_register_harness``、``_connect_mcp`` 是装配过程中的两个子步骤。
- 管理：``_handle_load`` 载入历史会话、``_handle_new_session`` 开新会话、
  ``_handle_rename`` 重命名、``_handle_delete`` 删除。

拆分动机：engine.py 原 1252 行、单类 46 个方法。方法体逐行原样搬迁，行为与拆分前
一致；以 mixin 形式挂回 ChatEngine，``_dispatch`` 仍按原名调用这些 ``_handle_*``。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from typing import Any
from agent.core.message import Message
from agent.ui.workbench.render import (  # noqa: F401
    _MAX_ATTACH_FILE_CHARS,
    _compose_with_files,
    _messages_to_render,
    _parse_images,
)
from agent.ui.workbench import (
    mcp_runtime,
    model_switch,
    project_switch,
)


class EngineSessionMixin:
    """会话装配（ensure/切模型/换目录/MCP）与会话管理（载入/新建/重命名/删除）的方法组。"""

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
        from agent.core.tool import ToolRegistry, build_default_registry
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
        # ToolSearch 注册（延迟加载机制的钥匙工具，对齐终端 main.py）：
        # deferred_loading=True 时 MCP/harness/GUI 等工具被标为延迟态，初始不发给
        # 模型，只在其需要时由 ToolSearch 搜关键词加载。桌面引擎此前漏注册它，
        # 导致模型被告知「用 ToolSearch 加载 MCP 工具」却在工具列表里找不到该工具，
        # 只能退回 Bash+curl 抓网页（桌面表现比终端"笨"、不用高德天气 MCP 的根因）。
        # 必须在 build_system_prompt 之前注册，才能进核心工具清单发给模型。
        # @author aceFelix
        if s.tools_deferred_loading:
            from agent.tools.tool_search import ToolSearchTool

            if "ToolSearch" not in registry:
                registry.register(ToolSearchTool(registry))
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
        # 装配后的活跃 registry 快照：权限模式热切换重建 orchestrator 需复用
        # 同一份工具表（含已连 MCP/harness/ToolSearch），不能另 build 一份。@author aceFelix
        self._active_registry = registry
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

    async def _handle_set_workdir(self, path: str) -> None:
        """运行时切换工作目录（桌面壳 project.set；实现见 project_switch）。

        与 _handle_switch_model 同构：切换在引擎指令队列里串行执行，正有一轮
        回复在跑时在回复结束后落地；会话未装配时只记账（_workdir_override +
        直接写 settings.workdir）延迟到 _ensure_session。切项目 = 开新会话。
        @author aceFelix
        """
        await project_switch.handle_set_workdir(self, path)

    def _register_harness(self, registry: Any, workdir: str) -> None:
        """后台注册 CLI-Anything harness 工具（实现见 mcp_runtime）。"""
        mcp_runtime.register_harness(self, registry, workdir)

    async def _connect_mcp(self, registry: Any) -> None:
        """连接配置的 MCP server 并把工具注册进 registry（实现见 mcp_runtime）。

        任何异常静默降级（仅 info 提示），不阻断文本对话装配。@author aceFelix
        """
        await mcp_runtime.connect_mcp(self, registry)

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
