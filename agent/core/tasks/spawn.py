"""任务脊柱的默认执行通路（从 ``driver.py`` 拆出）。

职责边界：本文件只管"**一步怎么被执行**"——子代理定义、真实 spawn、执行结果的
归一形状；什么时候唤醒、怎么推进、失败怎么定局全在 ``driver.py``。拆开的原因是
驱动器本身已经把调度、等待判定、播报闸门、断点续跑四件事装在一个类里，再往里塞
执行通路会超过单文件行数上限，而且这两块的变化原因本来就不一样：换执行体（换成
插件、换成远程 RPC）不该动调度，改退避策略也不该动子代理的规范。

实现沿用拆出前的 driver.py 口径，仅把内部私有名改成公开符号
（``StepOutcome`` / ``default_step_definition`` / ``default_spawn``），便于宿主与测试直接引用。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("jarvis.tasks.spawn")


@dataclass
class StepOutcome:
    """一步执行的归一结果（注入的 spawn 与真实子代理都返回这个形状）。

    归一是必要的：驱动器只看这几个字段就能决定"继续推进 / 退避重试 / 定局失败"，
    不必知道执行体是子代理、插件还是测试桩。

    ``denials`` 是本轮被权限拦下的调用清单（由 :func:`default_spawn` 从子 ctx.extra
    里取回）。驱动器把它与“本步到底有没有产出”合起来判：有 denial 且一个副作用都
    没记上，才是真卡住（去问用户）；已有产出却夹着被拒，则照实把被拒项附在结论里
    落 done。两种都不能把被拦下报成“已完成”（2026-10-22 两轮实机各踩过一头，
    见 docs/fixlogs/w3-driver-approval-blocked-report.md）。注入通路（测试/自定义
    编排）不填即表示未受阻。

    @author aceFelix
    """

    success: bool
    report: str = ""
    error: str = ""
    denials: list[dict[str, Any]] = field(default_factory=list)


def default_step_definition() -> Any:
    """脊柱步骤专用的子代理定义（惰性 import，避免装配期拉起整个协作包）。

    与内置 ``general`` 的区别必须写进 system prompt：它的对话对象是驱动器不是用户，
    不能追问、不能扩展范围，且遇到需要确认的操作要**如实汇报受阻**而不是硬闯。
    @author aceFelix
    """
    from agent.collaboration.subagent import AgentDefinition

    return AgentDefinition(
        agent_type="butler-step",
        description="任务脊柱的后台值守步骤执行体（不面向用户，只向驱动器汇报）。",
        system_prompt=(
            "你是贾维斯（JARVIS）的后台值守代理，正在按既定目标推进**一个**步骤。\n"
            "\n"
            "# 硬性规范\n"
            "1. **只做这一步**：不扩展范围、不替用户决定下一步做什么。\n"
            "2. **不能追问**：你的汇报对象是驱动器不是用户，信息不足就按最合理理解做，"
            "并把假设写进汇报。\n"
            "3. **受阻就直说**：需要用户确认的操作被拒时，明确写出「需要授权：…」，"
            "不要绕道硬闯（绕道是最坏结果——用户以为被拦下了，实际改动已经发生）。\n"
            "4. **汇报简洁**：一段话说清做了什么、结果是什么、有无异常；"
            "路径与命令用反引号。\n"
        ),
        allowed_tools=None,   # 继承全部工具；权限判定仍由管线与 W1 闸门把关
        max_iterations=12,
    )


def _host_rules(settings: Any) -> Any:
    """取宿主配好的声明式权限规则（供子代理继承），缺失/读失败一律给空集。

    为什么每次现取而不缓存：``permissions_file`` 指向的 YAML 用户随时可改，而后台
    任务是长周期运行的，缓存会把“改了规则”变成静默失效。读一次本地 YAML 的代价
    远低于这个理解成本。

    @author aceFelix
    """
    from agent.permissions.rules import RuleSet, load_rules

    path = str(getattr(settings, "permissions_file", "") or "").strip()
    if not path:
        return RuleSet()
    try:
        return load_rules(path)
    except Exception as e:  # noqa: BLE001 - 规则读不到就只能回到默认判定
        logger.warning("加载权限规则失败（本子代理按默认判定跑）: %s", e)
        return RuleSet()


def _step_checkpoint(settings: Any, workdir: str, task_id: str, step_desc: str) -> str:
    """为本步打一个任务级 shadow git 检查点，返回 cid（不可用时给空串）。

    没有它，后台步骤的文件改动在副作用账本里一条都不会记——编排器的规则是
    “拿不到本轮检查点就不记”，于是 ``/undo --task`` 永远列不出东西（2026-10-22
    实机走查确认过这条断链）。

    配额走 ``kind="task"`` 与消息级分开计（见 checkpoint.py），所以不会把
    ``/rewind`` 的回溯挤没；没装 git / 开关关闭时照实返回空串，不假装能回退。

    @author aceFelix
    """
    if settings is None or not workdir:
        return ""
    try:
        from agent.core.checkpoint import manager_from_settings

        mgr = manager_from_settings(
            settings, workdir=workdir, session_name="tasks", fallback_session="tasks",
        )
        if not mgr.available():
            return ""
        return mgr.create(f"任务 {task_id} 步骤: {step_desc[:40]}", kind="task") or ""
    except Exception as e:  # noqa: BLE001 - 打不成检查点只意味着这步不可回退
        logger.warning("任务步骤检查点创建失败: %s", e)
        return ""


def default_spawn(
    definition: Any,
    prompt: str,
    *,
    provider: Any,
    workdir: str,
    task_id: str = "",
    session_name: str = "",
    effect_ids: list[str] | None = None,
    settings: Any = None,
) -> StepOutcome:
    """真实执行通路：在本线程的事件循环里跑一个子代理。

    权限模式固定 ``default``：无人值守的后台步骤不该默认放行任何需确认的操作
    （子代理自身默认是 yolo，这里必须显式收紧）。无 UI 时 ASK 会落成 DENY，
    由驱动器把它翻译成"需要授权"来问用户。

    ``task_id``/``session_name``/``effect_ids`` 是副作用归属的接线：前两个写进子
    ctx.extra，编排器记账时据此填 ``EffectRecord.task_id``（``/undo --task`` 的查询
    键）；effect_ids 是同一个列表对象，编排器往里追加本步产生的账本 id，驱动器
    跑完回填到 ``TaskStep.journal_ids``。没这一段，台账里那列永远为空（这条链路
    2026-10-21 实机暴露过断点，见 docs/fixlogs/w3-delay-window-bypass-fix.md）。

    受阻清单 ``denials`` 的容器由本函数建好写进子代理的 ctx.extra，编排器在
    被拦下的第一现场往里追加，跑完由本函数取回装进 ``StepOutcome.denials``——
    驱动器据此判断“这步到底是被拒还是做完了”（2026-10-22 实机暴露的谎报缺陷，
    见 docs/fixlogs/w3-driver-approval-blocked-report.md）。两个容器不同的归宿各有原因：
    effect_ids 要落到台账字段所以由驱动器持有，denials 只用于本轮判定所以由通路回报。
    ``settings`` 则同时供两件事：把用户配好的声明式规则递给子代理（否则
    “前台已放行、后台仍被拒”两个入口口径不一致），以及为本步打一个
    任务级检查点并把 cid 交给编排器（没 cid 时本地写不入账）。

    @author aceFelix
    """
    from agent.collaboration.subagent import run_subagent
    from agent.core.orchestrator import EXTRA_DENIALS
    from agent.permissions.modes import PermissionMode

    extra: dict[str, Any] = {
        "task_id": task_id,
        "session_name": session_name,
        # 容器必须在这里建、而不是等编排器自己 setdefault：异步子代理往
        # ctx.extra 里塞新键后再取回同一对象容易断链，显式传一个列表最直白
        EXTRA_DENIALS: [],
    }
    if effect_ids is not None:
        extra["effect_ids"] = effect_ids
    # 编排器只认这个键：有它才肯为一次本地写记可回滚条目
    cid = _step_checkpoint(settings, workdir, task_id, prompt)
    if cid:
        extra["checkpoint_cid"] = cid

    async def _run() -> Any:
        return await run_subagent(
            definition,
            prompt,
            provider=provider,
            workdir=workdir,
            permission_mode=PermissionMode.DEFAULT,
            parent_ui=None,
            extra=extra,
            permission_rules=_host_rules(settings),
            settings=settings,
        )

    # 每条后台线程自带一个新循环：驱动器线程没有运行中的循环，
    # asyncio.run 负责建/关，异常也一定收尾（不留悬空 task）。
    result = asyncio.run(_run())
    denials = extra.get(EXTRA_DENIALS)
    return StepOutcome(
        success=bool(getattr(result, "success", False)),
        report=str(getattr(result, "report", "") or ""),
        error=str(getattr(result, "error", "") or ""),
        denials=list(denials) if isinstance(denials, list) else [],
    )
