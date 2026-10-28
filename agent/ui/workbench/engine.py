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
from typing import Any, Callable

from agent.config.settings import Settings
from agent.core.message import Message, TextContent
from agent.utils.query_lock import acquire_lock_cancellable
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
from agent.ui.workbench import (
    checkpoint_ops,
    mcp_runtime,
    remote_bridge,
    slash_bridge,
)

from .engine_status import EngineStatusMixin  # noqa: E402
from .engine_session import EngineSessionMixin  # noqa: E402
from .engine_talk import EngineTalkMixin  # noqa: E402
from .engine_voice import EngineVoiceMixin  # noqa: E402


class ChatEngine(EngineStatusMixin, EngineSessionMixin, EngineTalkMixin, EngineVoiceMixin):
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
        # owner=self：实时语音转写经 WorkbenchRealtimeUI 配对后回调
        # commit_voice_turn 落入会话历史（不传则只透传事件不落库）
        self._realtime_ui = WorkbenchRealtimeUI(self._emitter, owner=self)
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
        # 待生效的工作目录切换：project.set 可能在会话装配前到达（首条消息前
        # 就选了项目），此时写 settings.workdir + 记账，_ensure_session 用新
        # workdir 生成提示词/挂 harness；空串 = 无（镜像 _model_override）。
        # @author aceFelix
        self._workdir_override = ""
        # 当前 provider 的端点配置快照：热切换时作为「要不要重建 provider」的
        # 比较基准。不能直接用 _settings —— 它始终是进程启动时的快照。
        # @author aceFelix
        self._provider_settings = settings
        # 跨设备协同（手机 / 微信）与桌面文本共用的唯一串行锁：三通道都调度到
        # 本引擎 loop（self._loop）执行 query_loop.run，抢同一把 threading.Lock
        # 保证「同一时刻只有一个 query 在跑」，防共享 _messages 被并发写坏。
        # 语义与终端 BridgeServer/WeChatBridge 各自的 _query_lock 一致，只是这里
        # 提升为跨通道共享的唯一锁，由本引擎持有并下发给桥接。
        # @author aceFelix
        self._query_lock = threading.Lock()
        # 微信登录配对码回喂队列：connect_wechat 时创建，login 线程的
        # verify_callback 阻塞在此 get()，桌面 wechat.pairing 指令 put() 解锁。
        # @author aceFelix
        self._wechat_pairing_q: queue.Queue[str] | None = None












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
        elif action == "set_workdir":
            # 项目（workdir）运行时切换：与会话未装配时只记账延迟（见 project_switch），
            # 装配后则引擎线程内串行重建提示词/重挂 harness/开新会话。刻意不调
            # _ensure_session，交由处理函数判定是否需延迟，不为一次切换提前触发重型装配。
            # @author aceFelix
            await self._handle_set_workdir(cmd.get("path", ""))
        elif action == "set_mode":
            # 权限模式热切换（mode.set / 终端 /mode）：写 settings 后重建
            # checker/orchestrator 换进现有 loop（保留会话）。未装配时只写
            # settings，装配时 _build_checker 自然读到新模式。@author aceFelix
            await self._handle_set_mode(cmd.get("mode", ""))
        elif action == "set_thinking":
            # 思考强度切换（think.set / 终端 /think）：写 settings 后同步到 loop/provider，
            # 开关变化时重建系统提示词。未装配时只写 settings。@author aceFelix
            await self._handle_set_thinking(cmd.get("effort", ""))
        elif action == "slash_exec":
            # 桌面 slash.exec 透传：复用终端命令路由（白名单/交互禁令/输出捕获
            # 全在 slash_bridge，engine 已超大体量只挂路由），结果走 slash_result
            # 事件。@author aceFelix
            await slash_bridge.run_slash(self, cmd.get("command", ""))
        elif action == "start_talk":
            await self._handle_start_talk(cmd)
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
        elif action == "checkpoint_rewind":
            # 桌面气泡撤回：截断对话 + 可选回滚工作区文件（shadow git
            # 检查点，见 checkpoint_ops）。结果走 rewound 事件。
            # 未装配会话时无消息可撤回，直接回执失败。@author aceFelix
            await self._handle_checkpoint_rewind(
                int(cmd.get("user_tail_count", 1) or 1),
                bool(cmd.get("restore_files")),
            )
        elif action == "connect_phone":
            # 跨设备协同（桌面 phone.connect）：实现见 remote_bridge，在本引擎
            # loop 上先 ensure_session 再以共享锁/本 loop 起绑 0.0.0.0 的手机桥接。
            # @author aceFelix
            await remote_bridge.connect_phone(self)
        elif action == "disconnect_phone":
            await remote_bridge.disconnect_phone(self)
        elif action == "connect_wechat":
            await remote_bridge.connect_wechat(self)
        elif action == "disconnect_wechat":
            await remote_bridge.disconnect_wechat(self)
        elif action == "wechat_pairing":
            # 桌面回填微信数字配对码：放进 login 线程阻塞等待的队列。
            # @author aceFelix
            q = getattr(self, "_wechat_pairing_q", None)
            if q is not None:
                try:
                    q.put_nowait(str(cmd.get("code", "")))
                except Exception:
                    pass

    # ---- 权限模式 / 思考强度运行时切换 ----

    async def _handle_set_mode(self, mode: str) -> None:
        """热切换权限模式（default/plan/accept_edits/yolo），保留当前会话。

        先写 settings.permission_mode（未装配时 _ensure_session 的 _build_checker 会读到），
        已装配则用新 mode 重建 checker/orchestrator 并 set_orchestrator 换进 loop。
        @author aceFelix
        """
        from agent.permissions.modes import parse_mode

        new_mode = parse_mode(mode)
        self._settings.permission_mode = new_mode
        if not getattr(self, "_session_ready", False):
            return
        from agent.bootstrap import _build_checker, _build_recovery_executor
        from agent.core.orchestrator import ToolOrchestrator

        checker = _build_checker(self._settings)
        recovery = _build_recovery_executor(self._settings)
        orchestrator = ToolOrchestrator(
            registry=self._active_registry,
            permission_checker=checker,
            recovery_executor=recovery,
        )
        self._query_loop.set_orchestrator(orchestrator)
        self._emitter.emit("status", f"工作模式：{getattr(new_mode, 'value', new_mode)}")

    async def _handle_set_thinking(self, effort: str) -> None:
        """切换思考强度（off/low/medium/high/on），同步 settings 与运行中的 loop/provider。

        off 等价关闭思考；开关态变化时重建系统提示词（enable_thinking 影响工具说明，
        与终端 /think 同口径）。未装配时只写 settings，装配时 _build_provider 会应用档位。
        @author aceFelix
        """
        s = self._settings
        prev_on = bool(getattr(s, "enable_thinking", True))
        s.thinking_effort = effort
        s.enable_thinking = effort != "off"
        if not getattr(self, "_session_ready", False):
            return
        self._query_loop.set_thinking_effort(effort)
        if bool(s.enable_thinking) != prev_on:
            self._rebuild_system_prompt()
        self._emitter.emit("status", f"思考：{effort}")

    def _rebuild_system_prompt(self) -> None:
        """按当前 settings（含 enable_thinking/workdir）重建系统提示词并热替换进 loop。

        思考开关、切项目等改变提示词口径的场景共用；不重建 QueryLoop，只换 _system。
        @author aceFelix
        """
        from agent.prompts.system import build_system_prompt

        s = self._settings
        registry = getattr(self, "_active_registry", None)
        if registry is None:
            return
        prompt = build_system_prompt(
            s.workdir, registry, enable_thinking=s.enable_thinking, settings=s
        )
        if s.system_prompt_append:
            prompt = prompt + "\n\n" + s.system_prompt_append
        loop = getattr(self, "_query_loop", None)
        if loop is not None:
            loop.update_system_prompt(prompt)






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
        # 轮前检查点：记录工作区"发消息前"状态，绑定到本轮用户消息，
        # 供桌面撤回（checkpoint.rewind）连带回滚文件。创建是同步 git
        # 操作（超时保护在 CheckpointManager 内部），失败静默降级。
        # @author aceFelix
        ckpt_mgr = checkpoint_ops.make_manager(self)
        ckpt_id = ckpt_mgr.create(reason=composed) if ckpt_mgr.available() else None
        # 把本轮 cid 与会话名交给副作用账本（W3.2）：cid 到轮末才挂上用户消息，
        # 工具调用时只能经 extra 拿到；本轮没检查点时必须写空串而不是留着上轮的
        # 值，否则 /undo 会把"回到本轮前"做成"多退一轮"。@author aceFelix
        if self._ctx is not None:
            self._ctx.extra["checkpoint_cid"] = ckpt_id or ""
            self._ctx.extra["session_name"] = getattr(self, "_session_name", "") or ""
        _pre_msg_len = len(self._messages)
        # 记录当前任务句柄：abort_current_reply 从其他线程取消它实现"停止回复"
        self._send_task = asyncio.current_task()
        # 与手机 / 微信桥接共用唯一 query 锁：抢锁期间跑 run，锁把三通道串行化。
        # run_in_executor 把阻塞式 acquire 丢线程池，等待时不冻结本 loop（其余
        # 协程仍可推进），拿到锁再 await run —— 与桥接侧 _exec_query 同构。
        # 用当前运行 loop（生产环境即 self._loop；单测直接 await 本协程时也可用），
        # 避免依赖 engine.start() 才赋值的 self._loop。
        # acquire 必须在 try/finally 内：reply.abort 的取消若落在「等锁」窗口，
        # 线程池事后仍会把锁拿走，acquire_lock_cancellable 负责把它归还；而 finally
        # 仍需照常收尾（清句柄 + assistant_done），否则气泡永不落地、锁被永久占用。
        # @author aceFelix
        locked = False
        try:
            locked = await acquire_lock_cancellable(self._query_lock)
            await self._query_loop.run(composed, self._ctx, images=img_blocks or None)
        except Exception as e:
            self._emitter.emit("error", f"运行出错: {type(e).__name__}: {e}")
        finally:
            if locked:
                self._query_lock.release()
            self._send_task = None
            # 无论本轮成败都绑定检查点（query_loop.run 已把用户消息
            # append 进 _messages），出错轮次同样可撤回。@author aceFelix
            if ckpt_id:
                for _m in self._messages[_pre_msg_len:]:
                    if _m.role == "user":
                        _m.extra["checkpoint_id"] = ckpt_id
                        break
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

    async def _handle_checkpoint_rewind(self, user_tail_count: int, restore_files: bool) -> None:
        """桌面消息撤回：截断对话并可选回滚工作区文件（实现见 checkpoint_ops）。

        结果以 rewound 事件推给前端（裁气泡/回滚提示）；会话未装配时
        无消息可撤，直接回执失败。@author aceFelix
        """
        if not getattr(self, "_session_ready", False):
            self._emitter.emit("rewound", {
                "ok": False, "removed": 0, "files_restored": False,
                "reason": "会话尚未装配，无消息可撤回",
            })
            return
        result = await checkpoint_ops.rewind(self, user_tail_count, restore_files)
        self._emitter.emit("rewound", result)

    def _remote_query_begin(self) -> None:
        """手机 / 微信桥接在引擎 loop 上开跑一轮 query 前登记当前任务句柄。

        _send_task 此前只在本地 _handle_send 里置位，远端轮次走桥接自己的
        run_query / _exec_query 不经它，导致 reply.abort 只能取消桌面本地输入、
        对手机/微信在跑的轮次无效。本方法由桥接在拿到 query 锁、即将跑
        query_loop.run 时回调（已在引擎 loop 上），把当前任务登记为 _send_task，
        使“停止”按钮能取消任意来源的在跑轮次。三通道由 query 锁串行化，同一
        时刻只有一个在跑，登记不会互相覆盖。

        @author aceFelix
        """
        self._send_task = asyncio.current_task()

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

    def commit_voice_turn(self, user_text: str | None, ai_text: str | None) -> None:
        """实时语音一轮转写落库：按 [问, 答] 顺序追加消息并触发持久化。

        实时语音对话发生在 DashScope 实时通道内，不经 QueryLoop →
        ``self._messages``，此前语音内容从不进会话历史（恢复会话只剩文本轮，
        2026-10 修复）。由 WorkbenchRealtimeUI 在转写配对完整后调用（配对规则
        见该类注释：正常顺序 / 转写滞后 / 开场白 / 回声轮过滤 / 收尾 flush）；
        user/ai 至少一项非空。落库后复用 ``_after_turn`` 走与文本对话同一套
        自动保存 + 标题生成（语音问句同样可作标题来源）。

        Args:
            user_text: 用户转写文本，None 表示本轮无用户问句（开场白/主动播报）。
            ai_text: AI 回复转写文本，None 表示用户问句未获回复（被打断）。

        @author aceFelix
        """
        if not getattr(self, "_session_ready", False):
            return
        messages = getattr(self, "_messages", None)
        if messages is None or (not user_text and not ai_text):
            return
        if user_text:
            messages.append(Message(role="user", content=[TextContent(text=user_text)]))
        if ai_text:
            messages.append(Message(role="assistant", content=[TextContent(text=ai_text)]))
        self._after_turn()













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
