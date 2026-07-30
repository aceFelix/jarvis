"""多 Agent 协作命令处理器。

包含 /agents, /tasks, /plan 命令。

@author aceFelix
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


def _show_agents(ui, team_mgr, task_list) -> None:
    """/agents —— 查看多 Agent 团队状态。"""
    from agent.collaboration.team import TeamManager

    mgr: TeamManager = team_mgr
    team_name = mgr.active_team

    if team_name is None:
        ui.info("当前没有活跃的多 Agent 团队。")
        ui.info("创建团队: TeamCreate  或直接对我说「建个团队」")
        return

    team = mgr.load(team_name)
    if team is None:
        ui.info(f"团队 '{team_name}' 配置文件丢失")
        return

    ui.info(f"")
    ui.info(f"团队: [bold cyan]{team.name}[/bold cyan]")
    ui.info(f"创建时间: {time.strftime('%Y-%m-%d %H:%M', time.localtime(team.created_at))}")
    ui.info(f"Leader:  {team.lead_agent_id}")
    ui.info(f"成员数: {len(team.members)}")

    ui.info("")
    ui.info("成员:")
    for m in team.members:
        status = "● 活跃" if m.is_active is not False else "○ 空闲"
        role = f"({m.agent_type})" if m.agent_type else ""
        leader_flag = "  [bold cyan]← leader[/bold cyan]" if m.name == "team-lead" else ""
        ui.info(f"  {status}  {m.name} {role}{leader_flag}")

    if task_list is not None:
        tasks = task_list.list_all()
        if tasks:
            ui.info("")
            ui.info("共享任务:")
            for t in tasks:
                status_icon = {"pending": "○", "in_progress": "●", "completed": "✓"}.get(t.status, "?")
                owner_str = f" [{t.owner}]" if t.owner else ""
                blocked = f" (阻塞于: {', '.join(f'#{b}' for b in t.blocked_by)})" if t.blocked_by else ""
                ui.info(f"  #[bold]{t.id}[/bold] {status_icon} {t.subject}{owner_str}{blocked}")


def _show_tasks(ui, task_list) -> None:
    """/tasks —— 查看共享任务列表。"""
    if task_list is None:
        ui.info("任务列表未初始化（当前无活跃团队）")
        return

    tasks = task_list.list_all()
    if not tasks:
        ui.info("任务列表为空。")
        ui.info("创建团队后，用 TaskCreate 添加任务。")
        return

    ui.info("")
    ui.info("[bold]共享任务列表[/bold]")
    ui.info("")
    for t in tasks:
        status_icon = {"pending": "○", "in_progress": "●", "completed": "✓"}.get(t.status, "?")
        owner_str = f" [dim]{t.owner}[/dim]" if t.owner else ""
        blocked = ""
        if t.blocked_by:
            blocked = f" [dim yellow](阻塞于: {', '.join(f'#{b}' for b in t.blocked_by)})[/dim yellow]"
        blocks = ""
        if t.blocks:
            blocks = f" [dim](阻塞: {', '.join(f'#{b}' for b in t.blocks)})[/dim]"
        ui.info(f"  #[bold]{t.id}[/bold] {status_icon} {t.subject}{owner_str}{blocked}{blocks}")
        if t.description:
            ui.info(f"    {t.description[:120]}")
    ui.info("")
    pending = sum(1 for t in tasks if t.status == "pending")
    active = sum(1 for t in tasks if t.status == "in_progress")
    done = sum(1 for t in tasks if t.status == "completed")
    ui.info(f"任务统计: {done} 完成  |  {active} 进行中  |  {pending} 待开始")


async def _toggle_plan(ui, settings, ctx) -> None:
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


async def handle_agents(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /agents。"""
    _show_agents(ctx.ui, ctx.team_mgr, ctx.task_list)
    return True


async def handle_tasks(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /tasks。"""
    _show_tasks(ctx.ui, ctx.task_list)
    return True


async def handle_plan(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /plan。"""
    await _toggle_plan(ctx.ui, ctx.settings, ctx.ctx)
    return True
