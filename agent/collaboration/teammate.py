"""In-Process 队友执行引擎 —— 异步隔离的子 Agent 运行时。

致敬 Claude Code 的 InProcessTeammateTask 架构。

核心设计：
1. **TeammateRunner**: 创建一个 asyncio.Task 来运行独立的 QueryLoop。
2. **上下文隔离**: 子 agent 拥有独立的 messages 列表 + ToolRegistry + ToolContext。
3. **邮箱通信**: 通过文件邮箱与 leader/其他队友通信。
4. **状态管理**: pending → running → idle（每轮自动 idle）。
5. **关机协议**: leader 发 shutdown_request → teammate 回复 shutdown_response。
6. **空闲通知**: 每轮结束后自动发送 idle_notification 给 leader。
7. **自主任务领取**: 空闲时自动从共享 TaskList 领取可执行任务。
8. **计划/权限审批**: 执行写操作前可向 leader 发送审批请求并等待回复。
9. **心跳保活**: 按固定间隔发送 heartbeat，便于 leader 检测存活。

与现有 Subagent 的区别：
- Subagent: 同步阻塞，返回结果即死。一对一调用。
- Teammate: 持久运行，通过邮箱持续通信。多对多协作。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.core.context import ToolContext
from .mailbox import (
    TeammateMessage,
    clear_mailbox,
    has_unread,
    make_heartbeat,
    make_idle_notification,
    make_permission_request,
    make_plan_approval_request,
    make_task_claimed,
    make_task_completed,
    read_mailbox,
    write_mailbox,
)
from agent.core.message import Message


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class TeammateIdentity:
    """队友身份标识。

    Attributes:
        agent_id: 格式 "name@teamName"。
        agent_name: 显示名。
        team_name: 所属团队。
        color: UI 颜色。
        plan_mode_required: 是否需要 leader 审批计划。
    """
    agent_id: str
    agent_name: str
    team_name: str
    color: Optional[str] = None
    plan_mode_required: bool = False


@dataclass
class TeammateState:
    """队友运行时状态。

    Attributes:
        identity: 身份。
        prompt: 初始任务描述。
        status: 运行状态 (pending/running/idle/shutting_down/terminated)。
        model: 使用的模型。
        permission_mode: 权限模式。
        abort_event: 终止信号。
        task: asyncio.Task 引用。
        messages: 子 agent 的对话历史。
        last_report_tool_count: 上次汇报的工具调用数。
        last_report_token_count: 上次汇报的 token 数。
        shutdown_requested: 是否收到关机请求。
        pending_user_messages: 待处理的用户消息（leader 发来的文本消息）。
        started_at: 启动时间戳。
        current_task_id: 当前执行的任务 ID。
        plan_approved_event: 计划审批通过信号。
        plan_rejected_event: 计划审批驳回信号。
        permission_approved_event: 权限审批通过信号。
        permission_rejected_event: 权限审批驳回信号。
        pending_request_id: 当前等待审批的请求 ID。
        last_heartbeat_at: 上次发送心跳的时间戳。
    """
    identity: TeammateIdentity
    prompt: str
    status: str = "pending"  # pending | running | idle | shutting_down | terminated
    model: Optional[str] = None
    permission_mode: str = "default"
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None
    messages: list[Message] = field(default_factory=list)
    last_report_tool_count: int = 0
    last_report_token_count: int = 0
    shutdown_requested: bool = False
    pending_user_messages: list[str] = field(default_factory=list)
    started_at: float = 0.0
    current_task_id: str = ""
    plan_approved_event: asyncio.Event = field(default_factory=asyncio.Event)
    plan_rejected_event: asyncio.Event = field(default_factory=asyncio.Event)
    permission_approved_event: asyncio.Event = field(default_factory=asyncio.Event)
    permission_rejected_event: asyncio.Event = field(default_factory=asyncio.Event)
    pending_request_id: str = ""
    last_heartbeat_at: float = 0.0


# 心跳间隔（秒）
HEARTBEAT_INTERVAL_SECONDS = 30.0


# ---------------------------------------------------------------------------
# 执行引擎
# ---------------------------------------------------------------------------


class TeammateRunner:
    """In-process 队友执行器。

    用法::

        runner = TeammateRunner(
            identity=...,
            team_manager=mgr,
            task_list=tl,
            provider=...,
            workdir="...",
        )
        # 后台启动
        task = await runner.start("探索认证模块代码", ctx)
        # ... leader 继续其他工作 ...
        # 检查队友状态
        state = runner.state
        # 关机
        await runner.shutdown()
    """

    def __init__(
        self,
        identity: TeammateIdentity,
        *,
        team_manager: Any,  # TeamManager
        task_list: Any,  # TaskList
        provider: Any,  # LLMProvider
        sub_registry: Any,  # ToolRegistry（受限工具集）
        workdir: str,
        permission_mode: str = "default",
        model: Optional[str] = None,
        color: Optional[str] = None,
    ) -> None:
        self._identity = identity
        self._team_mgr = team_manager
        self._task_list = task_list
        self._provider = provider
        self._sub_registry = sub_registry
        self._workdir = workdir
        self._permission_mode = permission_mode
        self._model = model
        self._color = color

        self._state = TeammateState(
            identity=identity,
            prompt="",
            model=model,
            permission_mode=permission_mode,
        )

    # ---- 属性 ----

    @property
    def state(self) -> TeammateState:
        return self._state

    @property
    def identity(self) -> TeammateIdentity:
        return self._identity

    @property
    def is_idle(self) -> bool:
        return self._state.status == "idle"

    @property
    def is_active(self) -> bool:
        return self._state.status in ("pending", "running")

    # ---- 启动 ----

    async def start(self, prompt: str, parent_ui: Any = None) -> asyncio.Task:
        """后台启动队友执行。

        Args:
            prompt: 初始任务描述。
            parent_ui: 主 UI 对象（用于日志输出）。

        Returns:
            asyncio.Task（后台运行），可通过 task.cancel() 终止。
        """
        self._state.prompt = prompt
        self._state.status = "running"
        self._state.started_at = time.time()

        # 清空邮箱（上一个会话的残留）
        clear_mailbox(self._identity.agent_name, self._identity.team_name)

        # 后台 asyncio 任务
        self._state.task = asyncio.create_task(
            self._run(prompt, parent_ui),
            name=f"teammate-{self._identity.agent_name}",
        )
        return self._state.task

    async def _run(self, prompt: str, parent_ui: Any) -> None:
        """队友主循环：监控邮箱 + 执行任务。"""
        from agent.core.orchestrator import ToolOrchestrator
        from agent.core.query_loop import QueryLoop
        from agent.permissions import PermissionChecker
        from agent.permissions.modes import PermissionMode
        from agent.permissions.rules import RuleSet

        try:
            # 1. 构建子 Agent 的编排器
            perm = PermissionMode(self._permission_mode) if self._permission_mode else PermissionMode.YOLO
            checker = PermissionChecker(rules=RuleSet(), mode=perm)
            from agent.core.error_recovery import ToolRecoveryExecutor
            recovery = ToolRecoveryExecutor(global_enabled=True)
            orchestrator = ToolOrchestrator(
                registry=self._sub_registry,
                permission_checker=checker,
                max_concurrency=5,
                recovery_executor=recovery,
            )

            # 2. 构建 QueryLoop
            system_prompt = self._build_system_prompt()
            model = self._model or self._provider.default_model

            loop = QueryLoop(
                provider=self._provider,
                registry=self._sub_registry,
                orchestrator=orchestrator,
                system=system_prompt,
                model=model,
                max_iterations=20,
                max_tokens=4096,
                enable_compaction=True,
            )

            # 3. 构建 ToolContext
            ctx = ToolContext(
                workdir=self._workdir,
                messages=self._state.messages,
                permission_mode=self._permission_mode,
                ui=parent_ui,
            )

            # 4. 邮件轮询 + 任务执行循环
            waiting_for_more_work = False
            self._state.last_heartbeat_at = time.time()

            while not self._state.abort_event.is_set() and not self._state.shutdown_requested:
                # 4a. 检查邮箱（leader 的消息）
                messages = read_mailbox(
                    self._identity.agent_name,
                    self._identity.team_name,
                    unread_only=True,
                )
                for msg in messages:
                    handled = await self._handle_message(msg, loop, ctx, parent_ui)
                    if handled == "shutdown":
                        self._state.shutdown_requested = True
                        break
                    elif handled == "resume":
                        waiting_for_more_work = False

                if self._state.shutdown_requested:
                    break

                # 4b. 心跳保活
                await self._maybe_send_heartbeat()

                # 4c. 检查是否有待处理的用户消息
                if self._state.pending_user_messages:
                    user_text = self._state.pending_user_messages.pop(0)
                    await self._execute_turn(loop, user_text, ctx, parent_ui)
                    await self._maybe_complete_current_task()
                    waiting_for_more_work = False
                    continue

                # 4d. 如果有初始 prompt 还没执行
                if prompt and not waiting_for_more_work:
                    await self._execute_turn(loop, prompt, ctx, parent_ui)
                    await self._maybe_complete_current_task()
                    prompt = ""  # 只执行一次
                    waiting_for_more_work = True
                    continue

                # 4e. 空闲等待：自动从 TaskList 领取可执行任务
                if waiting_for_more_work:
                    self._state.status = "idle"
                    self._update_team_member_active(False)

                    # 发送 idle 通知
                    try:
                        write_mailbox(
                            "team-lead",
                            make_idle_notification(
                                from_name=self._identity.agent_name,
                                summary=f"{self._identity.agent_name} 空闲，等待新任务",
                                color=self._color,
                            ),
                            self._identity.team_name,
                        )
                    except Exception:
                        pass

                    # 等待新消息（轮询间隔 500ms），期间尝试领取任务
                    for _ in range(40):  # 最多等 20 秒
                        if self._state.abort_event.is_set():
                            break
                        if self._state.shutdown_requested:
                            break
                        if has_unread(self._identity.agent_name, self._identity.team_name):
                            break
                        if self._state.pending_user_messages:
                            break

                        # 每 5 秒尝试领取一次任务（避免频繁读盘）
                        if _ % 10 == 0:
                            claimed = self._claim_available_task(parent_ui)
                            if claimed:
                                waiting_for_more_work = False
                                break

                        await asyncio.sleep(0.5)

                    self._state.status = "running"
                    self._update_team_member_active(True)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            if parent_ui:
                parent_ui.error(
                    f"Teammate {self._identity.agent_name} 异常: {e}"
                )
        finally:
            self._state.status = "terminated"
            self._update_team_member_active(False)

            # 从进程内注册表注销
            try:
                from .teammate_registry import get_teammate_registry
                get_teammate_registry().unregister(
                    self._identity.team_name, self._identity.agent_name
                )
            except Exception:
                pass

            # 发送最后一条 idle 通知（terminated）
            try:
                write_mailbox(
                    "team-lead",
                    make_idle_notification(
                        from_name=self._identity.agent_name,
                        summary=f"{self._identity.agent_name} 已终止",
                        color=self._color,
                    ),
                    self._identity.team_name,
                )
            except Exception:
                pass

    async def _execute_turn(
        self,
        loop: Any,
        user_text: str,
        ctx: ToolContext,
        parent_ui: Any,
    ) -> None:
        """执行一轮对话（用户消息 → LLM → 工具 → 回灌）。"""
        if parent_ui:
            parent_ui.info(f"  [{self._identity.agent_name}] 开始处理: {user_text[:60]}...")

        try:
            stats = await loop.run(user_text, ctx)
            self._state.last_report_tool_count = stats.tool_calls
        except Exception as e:
            if parent_ui:
                parent_ui.error(f"  [{self._identity.agent_name}] 执行异常: {e}")

    async def _handle_message(
        self,
        msg: TeammateMessage,
        loop: Any,
        ctx: ToolContext,
        parent_ui: Any,
    ) -> Optional[str]:
        """处理一条邮箱消息。返回 "shutdown" / "resume" / None。"""
        msg_type = msg.type

        if msg_type == "shutdown_request":
            # 回复 shutdown_response (approve=true)
            try:
                from .mailbox import make_shutdown_response
                write_mailbox(
                    msg.from_name,
                    make_shutdown_response(
                        from_name=self._identity.agent_name,
                        request_id=msg.request_id,
                        approve=True,
                    ),
                    self._identity.team_name,
                )
            except Exception:
                pass
            return "shutdown"

        elif msg_type == "plain" and msg.from_name == "team-lead":
            # Leader 发来的新任务
            if msg.text:
                self._state.pending_user_messages.append(msg.text)
            return "resume"

        elif msg_type == "task_assignment":
            # 任务分配
            if parent_ui:
                parent_ui.info(
                    f"  [{self._identity.agent_name}] 收到任务: {msg.task_subject}"
                )
            if msg.text:
                self._state.pending_user_messages.append(
                    f"你被分配了任务 #{msg.task_id}: {msg.task_subject}\n\n{msg.text}"
                )
            self._state.current_task_id = msg.task_id
            return "resume"

        elif msg_type == "plan_approval_response":
            # Leader 对计划审批请求的回复
            if msg.request_id and self._state.pending_request_id != msg.request_id:
                # 不是当前等待的请求，忽略
                return None
            if msg.approve:
                self._state.plan_approved_event.set()
            else:
                self._state.plan_rejected_event.set()
            return "resume"

        elif msg_type == "permission_response":
            # Leader 对权限请求的回复
            if msg.request_id and self._state.pending_request_id != msg.request_id:
                return None
            if msg.approve:
                self._state.permission_approved_event.set()
            else:
                self._state.permission_rejected_event.set()
            return "resume"

        return None

    # ---- 任务与审批辅助 ----

    def _claim_available_task(self, parent_ui: Any) -> bool:
        """从 TaskList 领取一个可执行的 pending 任务。

        使用 TaskList.update 的原子性保证并发安全；返回 True 表示领取成功。
        """
        if self._task_list is None:
            return False
        try:
            available = self._task_list.get_available_tasks()
            for task in available:
                if task.owner:
                    continue
                updated = self._task_list.update(
                    task.id,
                    status="in_progress",
                    owner=self._identity.agent_name,
                )
                if updated:
                    self._state.current_task_id = task.id
                    self._state.pending_user_messages.append(
                        f"你领取了任务 #{task.id}: {task.subject}\n\n"
                        f"{task.description or '请按任务描述自主完成，完成后汇报结果。'}"
                    )
                    try:
                        write_mailbox(
                            "team-lead",
                            make_task_claimed(
                                from_name=self._identity.agent_name,
                                task_id=task.id,
                                task_subject=task.subject,
                                color=self._color,
                            ),
                            self._identity.team_name,
                        )
                    except Exception:
                        pass
                    if parent_ui:
                        parent_ui.info(
                            f"  [{self._identity.agent_name}] 自动领取任务 #{task.id}: {task.subject}"
                        )
                    return True
        except Exception as e:
            if parent_ui:
                parent_ui.error(f"  [{self._identity.agent_name}] 领取任务失败: {e}")
        return False

    async def _maybe_complete_current_task(self) -> None:
        """如果当前有执行中的任务，将其标记为 completed 并通知 leader。"""
        task_id = self._state.current_task_id
        if not task_id or self._task_list is None:
            return
        try:
            task = self._task_list.read(task_id)
            if task is not None and task.status == "in_progress":
                self._task_list.update(task_id, status="completed")
                try:
                    write_mailbox(
                        "team-lead",
                        make_task_completed(
                            from_name=self._identity.agent_name,
                            task_id=task_id,
                            status="completed",
                            summary=f"{self._identity.agent_name} 完成任务 #{task_id}",
                        ),
                        self._identity.team_name,
                    )
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self._state.current_task_id = ""

    async def _maybe_send_heartbeat(self) -> None:
        """按间隔向 leader 发送心跳消息。"""
        now = time.time()
        if now - self._state.last_heartbeat_at < HEARTBEAT_INTERVAL_SECONDS:
            return
        try:
            write_mailbox(
                "team-lead",
                make_heartbeat(
                    from_name=self._identity.agent_name,
                    status=self._state.status,
                    task_id=self._state.current_task_id,
                ),
                self._identity.team_name,
            )
            self._state.last_heartbeat_at = now
        except Exception:
            pass

    async def request_plan_approval(
        self,
        plan_text: str,
        timeout: float = 60.0,
    ) -> tuple[bool, str]:
        """向 leader 请求计划审批。

        Args:
            plan_text: 计划内容。
            timeout: 等待超时（秒）。

        Returns:
            (是否批准, 反馈文本)。超时视为驳回。
        """
        request_id = self._reset_approval_events()
        self._state.pending_request_id = request_id
        try:
            write_mailbox(
                "team-lead",
                make_plan_approval_request(
                    from_name=self._identity.agent_name,
                    plan_text=plan_text,
                    request_id=request_id,
                ),
                self._identity.team_name,
            )
            return await self._wait_for_approval(
                request_id,
                self._state.plan_approved_event,
                self._state.plan_rejected_event,
                timeout,
            )
        finally:
            self._state.pending_request_id = ""

    async def request_permission(
        self,
        action: str,
        tool: str,
        args: Optional[dict] = None,
        timeout: float = 60.0,
    ) -> tuple[bool, str]:
        """向 leader 请求执行某操作的权限。

        Args:
            action: 操作描述。
            tool: 涉及工具名。
            args: 工具参数快照。
            timeout: 等待超时（秒）。

        Returns:
            (是否批准, 反馈文本)。超时视为驳回。
        """
        request_id = self._reset_approval_events()
        self._state.pending_request_id = request_id
        try:
            write_mailbox(
                "team-lead",
                make_permission_request(
                    from_name=self._identity.agent_name,
                    action=action,
                    tool=tool,
                    args=args,
                    request_id=request_id,
                ),
                self._identity.team_name,
            )
            return await self._wait_for_approval(
                request_id,
                self._state.permission_approved_event,
                self._state.permission_rejected_event,
                timeout,
            )
        finally:
            self._state.pending_request_id = ""

    def _reset_approval_events(self) -> str:
        """重置审批事件并生成新的请求 ID。"""
        import uuid

        self._state.plan_approved_event.clear()
        self._state.plan_rejected_event.clear()
        self._state.permission_approved_event.clear()
        self._state.permission_rejected_event.clear()
        return uuid.uuid4().hex[:12]

    async def _wait_for_approval(
        self,
        request_id: str,
        approved_event: asyncio.Event,
        rejected_event: asyncio.Event,
        timeout: float,
    ) -> tuple[bool, str]:
        """等待审批事件或超时。"""
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    self._wait_event(approved_event),
                    self._wait_event(rejected_event),
                    return_exceptions=True,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return False, f"请求 {request_id} 等待审批超时"

        if approved_event.is_set():
            return True, "审批通过"
        if rejected_event.is_set():
            return False, "审批被驳回"
        return False, "等待审批异常结束"

    async def _wait_event(self, event: asyncio.Event) -> None:
        """等待事件触发（可被 abort_event 中断）。"""
        while not event.is_set() and not self._state.abort_event.is_set():
            try:
                await asyncio.wait_for(event.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

    # ---- 控制 ----

    async def shutdown(self, reason: str = "") -> None:
        """请求关机。"""
        self._state.shutdown_requested = True
        self._state.abort_event.set()
        if self._state.task and not self._state.task.done():
            self._state.task.cancel()
            try:
                await self._state.task
            except asyncio.CancelledError:
                pass

    async def send_message(self, text: str) -> None:
        """向队友发送文本消息（加入待处理队列）。"""
        self._state.pending_user_messages.append(text)

    # ---- 辅助 ----

    def _build_system_prompt(self) -> str:
        """构建队友的系统提示。"""
        import platform
        from datetime import datetime

        tool_lines = []
        for tool in self._sub_registry.all():
            desc_first = tool.description.split("\n", 1)[0]
            tool_lines.append(f"- **{tool.name}**: {desc_first}")

        return f"""你是贾维斯（JARVIS）多代理团队中的一名队友。

## 你的身份
- 名称: {self._identity.agent_name}
- 团队: {self._identity.team_name}
- 角色: 独立执行者，为团队领袖（team-lead）完成分配的任务

## 工作规范
1. **专注任务**: 只做被分配的事，不扩展范围。
2. **自主完成**: 你有独立工具集，自行调用工具收集信息、执行操作。
3. **结果导向**: 任务完成后，用简洁文字汇报结果——做了什么、发现了什么、有无异常。
4. **处理消息**: 当你收到 leader 的后续消息时，理解上下文后继续执行。
5. **不啰嗦**: 汇报只讲关键信息，不复述过程。
6. **计划审批**: 如果当前权限模式要求审批（PLAN/ASK），在执行任何会修改文件/系统的写操作前，你必须先用 SendMessage 向 team-lead 发送 plan_approval_request，收到 plan_approval_response 批准后才能执行。未获批准不得执行写操作。
7. **自动领任务**: 当你空闲且没有待处理消息时，会自动从团队 TaskList 领取 pending 且无人负责的任务并执行。

## 环境
- 操作系统: {platform.system()} {platform.release()} ({platform.machine()})
- 工作目录: {Path(self._workdir).resolve()}
- 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 可用工具
{chr(10).join(tool_lines)}
"""

    def _update_team_member_active(self, active: bool) -> None:
        """更新团队成员的活跃状态。"""
        try:
            self._team_mgr.mark_member_active(
                self._identity.team_name,
                self._identity.agent_name,
                active,
            )
        except Exception:
            pass
