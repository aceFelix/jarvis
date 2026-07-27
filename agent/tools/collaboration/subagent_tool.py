"""Subagent/Agent 工具 —— 派生子代理或背景队友执行任务。

支持两种模式：
1. **同步子代理**（默认）：派生 → 独立执行 → 返回报告。用于一次性任务。
2. **背景队友**（run_in_background=true）：派生持久队友，加入团队，通过邮箱持续通信。
   用于需要多轮协作的复杂任务。

用法:
    # 同步子代理（快速搜索）
    Subagent(prompt="找所有 TODO", agent_type="explorer")

    # 背景队友（持久协作）
    Subagent(prompt="改造登录模块", agent_type="coder",
             run_in_background=true, name="coder-1", team_name="my-project")

设计要点：
- provider 通过构造函数注入（ToolContext 没有 provider 字段）。
- 同步模式：返回子代理汇报文本。
- 背景模式：返回 agent_id，后续用 SendMessage 通信。
- 子代理不能再调 Subagent/TeamCreate（防无限递归）。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.collaboration.mailbox import make_task_completed, write_mailbox
from agent.collaboration.subagent import (
    BUILTIN_AGENTS,
    AgentDefinition,
    SubagentResult,
    _build_sub_registry,
    _build_sub_system_prompt,
    run_subagent,
    run_subagents_parallel,
)
from agent.collaboration.teammate_registry import get_teammate_registry
from agent.core.tool import JSONSchema, Tool
from agent.permissions.modes import PermissionMode


class SubagentTool(Tool):
    """派生子代理或背景队友执行任务。"""

    name = "Agent"
    description = (
        "启动一个子代理执行任务。支持两种模式:\n"
        "1. 同步模式（默认）：派生独立子代理 → 返回结果。用于快速搜索、分析等一次性任务。\n"
        "2. 背景队友模式（run_in_background=true）：派生持久队友加入团队，通过 SendMessage 持续通信。"
        "用于需要多轮协作的复杂编程任务。\n\n"
        "agent_type 可选: explorer(只读搜索)、researcher(研究分析)、coder(写代码)、general(通用)。\n"
        "同步模式子代理独立运行，有自己的工具和对话历史，不污染主对话。\n"
        "背景模式队友在团队内持续运行，有自己的任务列表和邮箱。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "交给子代理的任务描述。要具体明确，包含足够上下文。",
            },
            "agent_type": {
                "type": "string",
                "description": "子代理类型: explorer/researcher/coder/general。默认 general。",
                "enum": ["explorer", "researcher", "coder", "general"],
                "default": "general",
            },
            "description": {
                "type": "string",
                "description": "任务简短描述(3-5词)。用于日志和任务标题。",
            },
            "run_in_background": {
                "type": "boolean",
                "description": (
                    "设为 true 创建持久队友（加入团队持续运行）。需配合 team_name 和 name 使用。"
                    "设为 false(默认) 创建一次性同步子代理。"
                ),
                "default": False,
            },
            "name": {
                "type": "string",
                "description": (
                    "队友名（run_in_background=true 时必需）。用于 SendMessage 寻址。"
                    "如 'researcher', 'coder-1'。"
                ),
            },
            "team_name": {
                "type": "string",
                "description": (
                    "团队名（run_in_background=true 时必需）。队友加入此团队。"
                    "默认用当前活跃团队。"
                ),
            },
            "tasks": {
                "type": "array",
                "description": "批量同步子代理任务列表。提供时忽略顶层 prompt/agent_type，每个元素独立执行。",
                "items": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "子任务描述。",
                        },
                        "agent_type": {
                            "type": "string",
                            "description": "子代理类型。默认 general。",
                            "enum": ["explorer", "researcher", "coder", "general"],
                            "default": "general",
                        },
                        "description": {
                            "type": "string",
                            "description": "任务简短描述。可选。",
                        },
                    },
                    "required": ["prompt"],
                },
            },
        },
        "required": ["prompt"],
    }
    max_result_chars = 15_000

    def __init__(
        self,
        provider: Any = None,
        permission_mode: PermissionMode = PermissionMode.YOLO,
        team_mgr: Any = None,
        task_list: Any = None,
    ) -> None:
        self._provider = provider
        self._permission_mode = permission_mode
        self._team_mgr = team_mgr
        self._task_list = task_list

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("派生子代理/队友")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args is None:
            return "派生子代理"
        tasks = args.get("tasks")
        if isinstance(tasks, list) and tasks:
            return f"批量派生 {len(tasks)} 个子代理"
        agent_type = args.get("agent_type", "general")
        bg = args.get("run_in_background", False)
        prefix = "派生队友" if bg else "派生子代理"
        prompt = args.get("prompt", "")
        preview = prompt[:40] + ("..." if len(prompt) > 40 else "")
        return f"{prefix} {agent_type}: {preview}"

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if self._provider is None:
            return ToolResult.error("Agent 工具未注入 provider，无法派生")

        # 批量同步子代理模式
        tasks = args.get("tasks")
        if isinstance(tasks, list) and tasks:
            return await self._run_batch(tasks, ctx)

        prompt = args.get("prompt", "").strip()
        if not prompt:
            return ToolResult.error("prompt 不能为空")

        agent_type = args.get("agent_type", "general")
        agent_def = BUILTIN_AGENTS.get(agent_type)
        if agent_def is None:
            available = ", ".join(BUILTIN_AGENTS.keys())
            return ToolResult.error(f"未知 agent_type: {agent_type}（可选: {available}）")

        run_bg = args.get("run_in_background", False)

        if run_bg:
            return await self._spawn_teammate(prompt, agent_def, agent_type, args, ctx)
        else:
            return await self._run_sync(prompt, agent_def, agent_type, args, ctx)

    # ---- 同步子代理 ----

    async def _run_sync(
        self, prompt: str, agent_def: AgentDefinition, agent_type: str,
        args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        """同步执行，返回汇报文本。"""
        mode_str = ctx.permission_mode or "yolo"
        try:
            permission_mode = PermissionMode(mode_str)
        except ValueError:
            permission_mode = self._permission_mode

        result = await run_subagent(
            agent_def,
            prompt,
            provider=self._provider,
            workdir=ctx.workdir,
            permission_mode=permission_mode,
            parent_ui=ctx.ui,
        )

        if not result.success:
            return ToolResult.error(f"子代理执行失败: {result.error}")

        report = result.report
        if result.iterations > 0 or result.tool_calls > 0:
            report = (
                f"{report}\n\n"
                f"---\n"
                f"[子代理 {agent_type}: {result.iterations} 轮迭代, "
                f"{result.tool_calls} 次工具调用]"
            )

        return ToolResult(data=report)

    async def _run_batch(
        self, tasks: list[dict[str, Any]], ctx: ToolContext,
    ) -> ToolResult:
        """批量并行执行多个同步子代理任务。"""
        mode_str = ctx.permission_mode or "yolo"
        try:
            permission_mode = PermissionMode(mode_str)
        except ValueError:
            permission_mode = self._permission_mode

        job_list: list[tuple[AgentDefinition, str]] = []
        for idx, task in enumerate(tasks):
            prompt = task.get("prompt", "").strip()
            if not prompt:
                return ToolResult.error(f"tasks[{idx}].prompt 不能为空")
            agent_type = task.get("agent_type", "general")
            agent_def = BUILTIN_AGENTS.get(agent_type)
            if agent_def is None:
                available = ", ".join(BUILTIN_AGENTS.keys())
                return ToolResult.error(
                    f"tasks[{idx}] 未知 agent_type: {agent_type}（可选: {available}）"
                )
            job_list.append((agent_def, prompt))

        results = await run_subagents_parallel(
            job_list,
            provider=self._provider,
            workdir=ctx.workdir,
            permission_mode=permission_mode,
            parent_ui=ctx.ui,
        )

        lines = [f"批量子代理结果（共 {len(results)} 个）:"]
        for idx, result in enumerate(results):
            if result.success:
                lines.append(
                    f"\n--- 子代理 {idx + 1} ---\n"
                    f"{result.report}\n"
                    f"[迭代 {result.iterations} 轮, 工具调用 {result.tool_calls} 次]"
                )
            else:
                lines.append(
                    f"\n--- 子代理 {idx + 1} ---\n"
                    f"执行失败: {result.error}"
                )

        return ToolResult(data="\n".join(lines))

    # ---- 背景队友 ----

    async def _spawn_teammate(
        self, prompt: str, agent_def: AgentDefinition, agent_type: str,
        args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        """后台派生持久队友。"""
        # 校验必需参数
        name = args.get("name", "").strip()
        if not name:
            return ToolResult.error("run_in_background 模式需要指定 name（队友名）")

        # 获取团队
        from agent.collaboration.team import (
            TEAM_LEAD_NAME,
            TeamManager,
            TeamMember,
            format_agent_id,
            get_team_manager,
        )

        mgr: TeamManager = self._team_mgr or get_team_manager()
        team_name = args.get("team_name", "").strip() or mgr.active_team
        if not team_name:
            return ToolResult.error(
                "run_in_background 模式需要指定 team_name，或先创建团队。"
            )

        team = mgr.load(team_name)
        if team is None:
            return ToolResult.error(f"团队 '{team_name}' 不存在。请先用 TeamCreate 创建。")

        # 检查是否已有同名成员
        existing = team.get_member(name)
        if existing is not None and existing.is_active is not False:
            return ToolResult.error(f"队友 '{name}' 已在团队 '{team_name}' 中运行")

        # 构建队友身份
        import time

        agent_id = format_agent_id(name, team_name)

        member = TeamMember(
            agent_id=agent_id,
            name=name,
            agent_type=agent_type,
            joined_at=time.time(),
            cwd=ctx.workdir,
            is_active=True,
            permission_mode=ctx.permission_mode or "yolo",
        )
        mgr.add_member(team_name, member)

        # 构建 TeammateRunner
        from agent.collaboration.teammate import TeammateIdentity, TeammateRunner

        identity = TeammateIdentity(
            agent_id=agent_id,
            agent_name=name,
            team_name=team_name,
        )

        sub_registry = _build_sub_registry(agent_def.allowed_tools)
        # 移除队友不能用的顶级工具
        for forbidden in ("Agent", "TeamCreate", "TeamDelete"):
            for t in list(sub_registry.all()):
                if t.name == forbidden:
                    sub_registry._tools.pop(t.name, None)

        runner = TeammateRunner(
            identity=identity,
            team_manager=mgr,
            task_list=self._task_list,
            provider=self._provider,
            sub_registry=sub_registry,
            workdir=ctx.workdir,
            permission_mode=ctx.permission_mode or "yolo",
            model=agent_def.model,
        )

        # 设置 TaskList 生命周期回调：任务完成时通知 leader 和相关队友
        if self._task_list is not None:
            def _on_task_completed(task: Any) -> None:
                try:
                    write_mailbox(
                        "team-lead",
                        make_task_completed(
                            from_name=name,
                            task_id=task.id,
                            status=task.status,
                            summary=f"{name} 完成任务 #{task.id}: {task.subject}",
                        ),
                        team_name,
                    )
                except Exception:
                    pass

            self._task_list.set_hooks(on_completed=_on_task_completed)

        # 注册到进程内注册表，供 TaskStop 查找
        registry = get_teammate_registry()
        registry.register(team_name, name, runner)

        # 后台启动
        bg_task = await runner.start(prompt, parent_ui=ctx.ui)

        return ToolResult(data=(
            f"队友 '{name}' 已在团队 '{team_name}' 中启动（后台运行）。\n"
            f"- agent_id: {agent_id}\n"
            f"- 状态: 正在执行初始任务...\n"
            f"- 用 SendMessage 向 {name} 发送后续指令\n"
            f"- 用 TaskList 查看团队任务\n"
            f"- 用 TaskStop 终止该队友\n"
            f"- 用 SendMessage shutdown_request 让队友退出"
        ))
