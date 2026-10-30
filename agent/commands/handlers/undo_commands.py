"""/undo 命令 —— W3.2 副作用账本的用户面入口（阶段 10）。

四种形态：
    /undo                撤销上一步可撤销的副作用
    /undo <id 前缀>       撤销指定记录
    /undo list           最近记录与各自可否撤销（不可撤销的也要列出来）
    /undo cancel [id]    撤回还在延时窗口里、尚未执行的动作
    /undo --task <id>    按任务脊柱目标批量补偿

两条如实性要求（比"看起来能撤"更重要）：
1. 撤不动的就明确说撤不动，并给原因（邮件已离开本机 / 检查点已被配额修剪 /
   留底已被清理），绝不返回一句模糊的"失败"，更不假装撤销成功；
2. 落回前必须展示影响面并问过用户。影响面按**粒度**分开说，不能笼统写
   "可撤销"（2026-10 加文件级撤销后）：
   - 文件级（``kind=files``）：只还原这条改动碰过的几个文件，同轮其它改动
     不受影响 —— 从写前留底恢复，不需要 git 检查点；
   - 整轮（``kind=checkpoint``）：恢复到本轮开始前的 shadow git 检查点，
     本轮其余文件改动会一并回退。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


def _journal_or_none(ctx: "CommandContext"):
    """取账本；配置关闭时返回 None 并给出解释（不是静默什么也不做）。"""
    from agent.core.actions import get_journal

    journal = get_journal()
    if not journal.enabled:
        ctx.ui.warn("副作用账本未启用（[actions].journal_enabled=false），无从撤销")
        return None
    return journal


def _undo_kind_label(rec: Any) -> str:
    """这条记录撤下去**会动多少东西**（清单里给用户看的粒度提示）。

    为什么不能笼统写“可撤销”：文件级恢复只碰这一条改过的几个文件，整轮回滚
    会把同一轮里其它正确的改动一起退掉。两者在用户眼里代价完全不同，用同一句
    “可撤销”会把风险差说没了（2026-10 加文件级撤销时补上）。
    @author aceFelix
    """
    kind = str((rec.undo_spec or {}).get("kind") or "")
    return {
        "files": "可按文件恢复",
        "checkpoint": "可整轮回滚",
        "config": "可回退配置",
    }.get(kind, "可撤销")


def _state_mark(rec: Any) -> str:
    """状态的中文读法。

    注：拼接给 ``ui.info`` 时一律用 〈〉 而不是方括号——Rich 会把
    `[xxx]` 当样式标签，warn/error 那边已经 escape()，但 info 没有，
    带方括号的控制标签会让整行文本少字（见 cli-ask-user-bracket-eaten-fix.md）。
    @author aceFelix
    """
    return {
        "held": "待发",
        "applied": "已执行",
        "undone": "已撤销",
        "failed": "失败",
        "cancelled": "已取消",
    }.get(rec.state, rec.state)


async def handle_undo(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /undo：分发到列表 / 撤回窗口 / 按任务批量 / 撤单条四条路径。"""
    journal = _journal_or_none(ctx)
    if journal is None:
        return True

    parts = stripped.split()[1:]
    head = parts[0].lower() if parts else ""

    if head == "list":
        _show_list(ctx, journal)
    elif head == "cancel":
        await _cancel_hold(ctx, journal, parts[1:])
    elif head == "--task":
        if len(parts) < 2:
            ctx.ui.warn("用法: /undo --task <任务id>")
            return True
        await _undo_by_task(ctx, journal, parts[1])
    else:
        await _undo_one(ctx, journal, parts[0] if parts else "")
    return True


def _show_list(ctx: "CommandContext", journal) -> None:
    """/undo list：列最近记录，含"撤不动"的项与其原因。"""
    records = journal.recent(20)
    if not records:
        ctx.ui.info("副作用账本为空（尚未记录任何已执行或待发的动作）")
        return
    lines = [f"最近副作用 {len(records)} 条（新→旧）："]
    for rec in records:
        can = _undo_kind_label(rec) if rec.undoable else (
            "窗口内可撤回" if rec.held else "不可撤销"
        )
        lines.append(f"  {rec.id}  〈{_state_mark(rec)}〉  {rec.label}  — {can}")
        if not rec.undoable and not rec.held:
            from agent.core.actions import describe_undo

            lines.append(f"        原因: {describe_undo(rec)}")
    counts = journal.counts()
    lines.append(
        f"统计: 已执行 {counts.get('applied', 0)}  待发 {counts.get('held', 0)}  "
        f"已撤销 {counts.get('undone', 0)}  失败 {counts.get('failed', 0)}"
    )
    kinds = {str((r.undo_spec or {}).get("kind") or "") for r in records}
    if "files" in kinds:
        lines.append("（“可按文件恢复”的条目只还原这条改动碰过的文件，同轮其它改动不受影响）")
    if "checkpoint" in kinds:
        lines.append("（“可整轮回滚”的条目会恢复到该轮开始前，本轮其余改动会一并回退）")
    ctx.ui.info("\n".join(lines))


def _pick(ctx: "CommandContext", journal, arg: str, states: tuple[str, ...]) -> Any:
    """按参数取一条记录：给了 id 就精确取，没给就取最近一条符合状态的。"""
    if arg:
        rec = journal.get(arg)
        if rec is None:
            ctx.ui.warn(f"账本里没有 id 以 {arg} 开头的记录（/undo list 可查全部）")
        return rec
    return journal.last(*states)


async def _undo_one(ctx: "CommandContext", journal, arg: str) -> None:
    """撤销一条已执行的副作用：先展示影响面问过用户，再真回滚。"""
    from agent.core.actions import STATE_APPLIED, UNDO_FILES, describe_undo, manager_for, undo_record

    rec = _pick(ctx, journal, arg, (STATE_APPLIED,))
    if rec is None:
        ctx.ui.info("没有可撤销的已执行副作用（待发项请用 /undo cancel）")
        return
    if not rec.undoable:
        # 如实报告：这一条撤不回来，用户有权知道"我到底留下了什么"
        ctx.ui.warn(f"{rec.label} 无法撤销 — {describe_undo(rec)}")
        return

    # 文件级撤销走另一条路：它不需要 git 检查点，也不该展示“整轮影响面”
    # （那条路径会去问 manager_for，而文件级条目根本没引用检查点，
    # 照旧路径走会在“本轮检查点已不可用”那里被误判成撤不动）。
    if str((rec.undo_spec or {}).get("kind") or "") == UNDO_FILES:
        await _undo_files_ui(ctx, rec)
        return

    # 整轮回滚会影响本轮其他文件，必须先给用户看清单再落手（与 /rewind 同一口径）
    mgr = manager_for(rec.workdir, ctx.settings, ctx.session_name)
    cid = str(rec.undo_spec.get("cid", ""))
    desc = mgr.describe(cid) if mgr.available() else None
    if desc is None:
        ctx.ui.warn(f"{rec.label} 的本轮检查点已不可用（缺 git 或已被配额修剪）")
        return
    files = desc.get("files") or []
    untracked = desc.get("untracked") or []
    if not files and not untracked:
        ctx.ui.info(f"{rec.label} 与工作区当前状态无差异，无需回滚")
        return
    ctx.ui.info(f"撤销 {rec.label} 会把工作区回滚到本轮开始前，影响：")
    marks = {"M": "修改", "A": "新增", "D": "删除"}
    for f in files[:20]:
        mark = marks.get(str(f.get("status", "")), str(f.get("status", "")))
        ctx.ui.info(f"  〈{mark}〉{f.get('path', '')}")
    if len(files) > 20:
        ctx.ui.info(f"  …… 共 {len(files)} 项改动")
    if untracked:
        ctx.ui.info(f"  另有 {len(untracked)} 个新增未跟踪文件将被删除")
    try:
        answer = await ctx.ui.read_user_input_async("确认回滚工作区？〈y/N〉 ")
    except Exception:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        ctx.ui.info("已放弃撤销，工作区未改动")
        return

    ok, note = undo_record(rec, settings=ctx.settings, session_name=ctx.session_name)
    (ctx.ui.info if ok else ctx.ui.warn)(f"撤销结果：{note}")


async def _undo_files_ui(ctx: "CommandContext", rec: Any) -> None:
    """文件级撤销：只还原这次碰过的那几个文件，不动同轮其它改动。

    与整轮回滚分两套 UI 是有意的：整轮那边必须把几十个文件的影响面摆出来；
    文件级这边就该明说“只动这几个”——否则用户会以为又要连带退掉别的改动，
    于是不敢撤，这个功能等于白做。

    @author aceFelix
    """
    from agent.core.actions import undo_record
    from agent.core.actions.file_effects import list_entries

    entries = list_entries(str((rec.undo_spec or {}).get("snap_id") or ""), ctx.settings)
    if not entries:
        ctx.ui.warn(f"{rec.label} 的留底已不在了（可能被配额清理），无法恢复文件")
        return
    ctx.ui.info(f"撤销 {rec.label} 只恢复下列文件，同轮其它改动不受影响：")
    for e in entries[:20]:
        mark = "恢复原内容" if e.get("existed") else "删除（原本不存在）"
        ctx.ui.info(f"  〈{mark}〉{e.get('path', '')}")
    if len(entries) > 20:
        ctx.ui.info(f"  …… 共 {len(entries)} 项")
    try:
        answer = await ctx.ui.read_user_input_async("确认恢复这些文件？〈y/N〉 ")
    except Exception:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        ctx.ui.info("已放弃撤销，文件未改动")
        return

    ok, note = undo_record(rec, settings=ctx.settings, session_name=ctx.session_name)
    (ctx.ui.info if ok else ctx.ui.warn)(f"恢复结果：{note}")


async def _cancel_hold(ctx: "CommandContext", journal, rest: list[str]) -> None:
    """撤回延时窗口里尚未执行的动作（它还没真的发生，所以不需要回滚）。"""
    from agent.core.actions import STATE_CANCELLED, STATE_HELD, get_window

    arg = rest[0] if rest else ""
    rec = _pick(ctx, journal, arg, (STATE_HELD,))
    if rec is None:
        ctx.ui.info("当前没有还在延时窗口里待发的动作")
        return
    if not rec.held:
        ctx.ui.warn(f"{rec.label} 状态是「{_state_mark(rec)}」，不是待发动作")
        return
    # 先摘定时器再改状态：顺序反了也不出错（到点回调会重读账本状态并放弃执行），
    # 但这个顺序能避免"已改状态却仍被触发"的窗口出现在日志里
    get_window().cancel(rec.id)
    journal.set_state(rec.id, STATE_CANCELLED, error="用户以 /undo cancel 撤回")
    ctx.ui.info(f"已撤回，未执行：{rec.label}")


async def _undo_by_task(ctx: "CommandContext", journal, task_id: str) -> None:
    """按任务目标批量补偿，并把撤不动的逐项说明。"""
    from agent.core.actions import undo_task

    results = undo_task(task_id, settings=ctx.settings, session_name=ctx.session_name)
    if not results:
        ctx.ui.warn(f"任务 {task_id} 名下没有已执行的副作用记录")
        return
    ok = sum(1 for _r, o, _n in results if o)
    lines = [f"任务 {task_id} 的副作用 {len(results)} 项，撤销成功 {ok} 项："]
    for rec, o, note in results:
        lines.append(f"  {'✓' if o else '✗'} {rec.label} — {note}")
    ctx.ui.info("\n".join(lines))
