"""多 Agent 协作命令处理器。

包含 /agents, /tasks, /plan 命令。其中 ``/tasks`` 从 W3.1 起是两节的：
协作内的共享任务（一次查询内）+ 跨会话任务脊柱（目标台账）；阶段 10
收尾切片再给它加了写入子命令（``add``/``show``/``pause``/``resume``/``cancel``/
``advance``/``delete``），语义与 ``TrackGoal`` 工具、``tasks.exec`` RPC 同源于
:mod:`agent.core.tasks.spine_ops`。

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


def _esc(text: str) -> str:
    """Rich 标记转义：目标/步骤文本里的 ``[y]`` 这类方括号会被当样式标签吞掉
    （同 ask_user 的方括号吞字案）。rich 缺失（非交互/降级环境）时原样返回。
    @author aceFelix
    """
    try:
        from rich.markup import escape

        return escape(str(text))
    except Exception:  # noqa: BLE001 - 无 rich 就退化成纯文本，不得因此报错
        return str(text)


#: 目标状态 → 台账图标（不用方括号包裹，避开 Rich 解析）
_SPINE_ICON = {"active": "▶", "paused": "⏸", "done": "✓", "failed": "✗", "cancelled": "⏹"}


def _show_goal_spine(ui) -> None:
    """``/tasks`` 的第二条腿：跨会话目标台账（W3.1 spine），只读不推进。

    与上一节的分工：``TaskList`` 只在一次查询内活着；脊柱是“替我盯着”的台账，
    可以等时间、等文件、等感知事件。本函数**不建目标、不调 LLM**：命令处理器
    跑在输入线程上，推进是后台驱动器的事；读不到台账就当空处理，不报错。

    @author aceFelix
    """
    from agent.core.tasks.spine import STEP_BLOCKED, get_spine_store, iso_of

    try:
        goals = get_spine_store().open_goals(limit=20)
    except Exception as e:  # noqa: BLE001 - 台账读取失败不影响协作任务那一节
        ui.info(f"  （任务脊柱台账读取失败：{type(e).__name__}）")
        return

    ui.info("")
    ui.info("[bold]跨会话任务脊柱[/bold]")
    if not goals:
        ui.info("  （没有进行中的目标）")
    for g in goals:
        done, total = g.progress()
        ui.info(f"  {_SPINE_ICON.get(g.status, '○')} {_esc(g.goal[:48])}  〔{g.id}〕{done}/{total}")
        step = g.current_step()
        if step is None:
            continue
        idx = g.steps.index(step) + 1
        if step.state == STEP_BLOCKED:
            # 阻塞行里要带上“在等什么、最迟什么时候”：这两样才是用户决定
            # “继续等 / 换做法 / 先搁下”的依据，只报一个 waiting 什么也定不了
            tail = ""
            if step.wait is not None:
                tail = (
                    f"，等 {step.wait.kind}"
                    f"（最迟 {iso_of(step.wait.deadline_at) or '未设限'}）"
                )
            ui.info(f"    ◇ 卡在第 {idx} 步：{_esc(step.desc[:40])}（{step.block_reason}）{tail}")
        elif step.wait is not None:
            ui.info(
                f"    ○ 第 {idx} 步在等：{step.wait.kind}"
                f"（最迟 {iso_of(step.wait.deadline_at) or '未设限'}）"
            )
        else:
            ui.info(f"    ● 第 {idx} 步待推进：{_esc(step.desc[:40])}")

    try:
        from agent.core.tasks.driver import get_driver

        driver = get_driver()
        running = bool(driver is not None and driver.running)
    except Exception:  # noqa: BLE001 - 驱动器句柄读不到就当未起
        running = False
    ui.info(
        f"  后台驱动线程：{'在跑' if running else '未起'}"
        "（开关 settings.tasks_enabled；台账在 ~/.jarvis/goals/）"
    )


#: ``/tasks`` 子命令用法（单一处维护：help 与误用提示都读它，不另写一份）
_TASKS_USAGE = (
    "/tasks                       两节：协作内共享任务 + 跨会话目标台账",
    "/tasks add <目标>[;步骤2 …] [--wait <条件>] [--deadline <小时>]",
    "        等待条件：time:+90s · time:2026-10-21T18:30 · file:<路径> · gone:<路径> · event:<source>/<kind>",
    "/tasks show <目标id>          逐步展开（含在等什么、上次失败原因、副作用 id）",
    "/tasks pause|resume|cancel <目标id>",
    "/tasks advance <目标id>        跳过等待条件立刻推进（测试用，不改步骤内容）",
    "/tasks delete <目标id>         从台账删除（本期无软删归档）",
)


def _parse_tasks_args(rest: list[str]) -> tuple[str, str, float]:
    """从 ``/tasks add`` 的参数里剥出 ``(目标文本, --wait 条件, --deadline 小时)``。

    目标文本里带空格是常态，不能拿 ``split()`` 的结果直接当参数：只认 ``--wait``
    与 ``--deadline`` 两个带值开关（也支持 ``--wait=`` 连写），其余 token 原样拼回
    文本。开关的值不能含空格（Windows 路径带空格请先用 ``~`` 或短路径）。
    @author aceFelix
    """
    text_parts: list[str] = []
    wait_spec = ""
    deadline_hours = 0.0
    i = 0
    while i < len(rest):
        token = rest[i]
        if token.startswith("--wait="):
            wait_spec = token[len("--wait="):]
            i += 1
            continue
        if token.startswith("--deadline="):
            deadline_hours = _to_hours(token[len("--deadline="):])
            i += 1
            continue
        if token == "--wait" and i + 1 < len(rest):
            wait_spec = rest[i + 1]
            i += 2
            continue
        if token == "--deadline" and i + 1 < len(rest):
            deadline_hours = _to_hours(rest[i + 1])
            i += 2
            continue
        text_parts.append(token)
        i += 1
    return " ".join(text_parts).strip(), wait_spec, deadline_hours


def _to_hours(raw: str) -> float:
    """``--deadline`` 取值：解不出来就当没给（照配置默认）。"""
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _tasks_add(ui, ctx, rest: list[str]) -> None:
    """``/tasks add``——新建一个目标台账。

    建完必须把“能不能本身往下跑”说清：``[tasks] enabled=false`` 时目标只是躺在
    台账里，不说一句用户会以为“建好了怎么不动”是缺陷。驱动器在不在跑从
    :func:`spine_ops.snapshot` 取，不在命令里靠配置自己推（配置开了但线程未起
    是两种不同情形）。
    @author aceFelix
    """
    from agent.core.tasks.spine_ops import create_goal, parse_wait, snapshot

    text, wait_raw, deadline_hours = _parse_tasks_args(rest)
    if not text:
        ui.warn(_TASKS_USAGE[1])
        return
    hours = deadline_hours or float(getattr(ctx.settings, "tasks_wait_deadline_hours", 72) or 72)
    wait, err = parse_wait(wait_raw, deadline_hours=hours)
    if err:
        ui.warn(f"等待条件写错了：{err}")
        return

    workdir = str(getattr(getattr(ctx, "ctx", None), "workdir", "") or "")
    res = create_goal(
        text,
        wait=wait,
        workdir=workdir,
        session_id=getattr(ctx, "session_name", ""),
    )
    if not res.ok:
        ui.warn(res.message)
        return
    ui.info(res.message)
    running = bool(snapshot().get("driver_running"))
    if running:
        ui.info("  后台驱动线程在跑：条件满足会自己往下推，随时 /tasks 看进度")
    else:
        ui.info(
            "  驱动线程未跑（[tasks] enabled=false 或本轮未装配）：目标已落台账，但不会自动推进；"
            "打开开关重启，或用 /tasks advance <id> 手动推一步"
        )


def _tasks_show(ui, goal_id: str) -> None:
    """``/tasks show <id>``：逐步展开一个目标（id 支持前缀）。"""
    from agent.core.tasks.spine_ops import describe_steps
    from agent.core.tasks.spine import get_spine_store

    goal = _find_goal(get_spine_store(), goal_id)
    if goal is None:
        ui.warn(f"没找到目标：{goal_id or '（没给 id）'}")
        return
    done, total = goal.progress()
    ui.info(f"目标〔{goal.id}〕{_esc(goal.goal)}")
    ui.info(f"  状态：{goal.status}  进度：{done}/{total}  工作目录：{goal.workdir or '（未记）'}")
    for line in describe_steps(goal):
        ui.info(_esc(line))
    ids = [j for s in goal.steps for j in s.journal_ids]
    if ids:
        ui.info(f"  本目标产生的副作用：{len(ids)} 项（/undo --task {goal.id} 可批量回收）")


def _tasks_control(ui, goal_id: str, action: str) -> None:
    """``/tasks pause|resume|cancel|advance|delete <id>``。"""
    from agent.core.tasks.spine_ops import control_goal

    if not goal_id:
        ui.warn(f"用法: /tasks {action} <目标id>")
        return
    res = control_goal(goal_id, action)
    (ui.info if res.ok else ui.warn)(res.message)


def _find_goal(store, goal_id: str):
    """按完整 id 或前缀取一个目标（前缀规则与管控共用 :func:`spine_ops.find_goal`）。"""
    from agent.core.tasks.spine_ops import find_goal

    return find_goal(store, goal_id)


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
    """处理 ``/tasks``——只读两节，外加目标台账的写入与管控子命令。

    子命令形态（阶段 10 收尾切片，2026-10-21）：``add`` / ``show`` /
    ``pause`` / ``resume`` / ``cancel`` / ``advance`` / ``delete``，无参时照旧只列两节。
    业务规则全在 :mod:`agent.core.tasks.spine_ops`（与 ``TrackGoal`` 工具、
    ``tasks.exec`` RPC 共用），本函数只做参数搬运与呈现。

    不新建同义命令是因为 slash 表只有一份（``ui/slash_completer.py``），
    再添一个“看目标”的入口会让模型与用户都要先猜哪个是活的。
    @author aceFelix
    """
    parts = stripped.split()
    sub = parts[1].lower() if len(parts) > 1 else ""

    if not sub:
        _show_tasks(ctx.ui, ctx.task_list)
        _show_goal_spine(ctx.ui)
        return True

    if sub == "add":
        _tasks_add(ctx.ui, ctx, parts[2:])
        _show_goal_spine(ctx.ui)
    elif sub == "show":
        _tasks_show(ctx.ui, parts[2] if len(parts) > 2 else "")
    elif sub in ("pause", "resume", "cancel", "advance", "delete"):
        _tasks_control(ctx.ui, parts[2] if len(parts) > 2 else "", sub)
    elif sub in ("help", "-h", "--help"):
        for line in _TASKS_USAGE:
            ctx.ui.info(line)
    else:
        ctx.ui.warn(f"未知的 /tasks 子命令：{sub}")
        for line in _TASKS_USAGE:
            ctx.ui.info(line)
    return True


async def handle_plan(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /plan。"""
    await _toggle_plan(ctx.ui, ctx.settings, ctx.ctx)
    return True
