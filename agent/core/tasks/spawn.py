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
from dataclasses import dataclass
from typing import Any


@dataclass
class StepOutcome:
    """一步执行的归一结果（注入的 spawn 与真实子代理都返回这个形状）。

    归一是必要的：驱动器只看这三个字段就能决定"继续推进 / 退避重试 / 定局失败"，
    不必知道执行体是子代理、插件还是测试桩。

    @author aceFelix
    """

    success: bool
    report: str = ""
    error: str = ""


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


def default_spawn(
    definition: Any,
    prompt: str,
    *,
    provider: Any,
    workdir: str,
    task_id: str = "",
    session_name: str = "",
    effect_ids: list[str] | None = None,
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
    @author aceFelix
    """
    from agent.collaboration.subagent import run_subagent
    from agent.permissions.modes import PermissionMode

    extra: dict[str, Any] = {"task_id": task_id, "session_name": session_name}
    if effect_ids is not None:
        extra["effect_ids"] = effect_ids

    async def _run() -> Any:
        return await run_subagent(
            definition,
            prompt,
            provider=provider,
            workdir=workdir,
            permission_mode=PermissionMode.DEFAULT,
            parent_ui=None,
            extra=extra,
        )

    # 每条后台线程自带一个新循环：驱动器线程没有运行中的循环，
    # asyncio.run 负责建/关，异常也一定收尾（不留悬空 task）。
    result = asyncio.run(_run())
    return StepOutcome(
        success=bool(getattr(result, "success", False)),
        report=str(getattr(result, "report", "") or ""),
        error=str(getattr(result, "error", "") or ""),
    )
