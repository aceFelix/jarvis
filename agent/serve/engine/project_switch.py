"""工作台引擎的项目（workdir）运行时切换（自 engine.py 拆出，控引擎文件行数）。

桌面壳左栏「打开文件夹」选一个已存在的绝对目录作为项目 → WS ``project.set``
→ serve 校验后入队 ``{"cmd": "set_workdir", "path": ...}``，由引擎线程调用本
模块：把 ``settings.workdir`` 换成目标目录，重建系统提示词（环境段含 workdir、
项目级技能/记忆随之变化）、对新 workdir 重挂 harness、开一个新会话。

复刻 ``model_switch.handle_switch_model`` 的引擎线程内串行范式：切换在指令
队列里排队，若正有一轮回复在跑，则在该轮结束后落地——不打断流式、无竞态。
会话尚未装配时只记账（``engine._workdir_override`` + 直接写 settings.workdir），
等 ``_ensure_session`` 用新 workdir 生成提示词/挂 harness，避免为一次切换
提前触发重型装配（MCP 连接 + 提示词生成）。

provider 不重建（模型不变）：切项目只换工作目录及其派生的提示词/工具，与
切模型正交。落地/失败分别经 ``project_switched`` / ``warn`` 事件回执。

@author aceFelix
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型标注：运行时不导入，避免 engine ↔ project_switch 循环
    from agent.serve.engine.engine import ChatEngine


async def handle_set_workdir(
    engine: ChatEngine,
    path: str,
    *,
    start_new_session: bool = True,
    announce: bool = True,
) -> bool:
    """运行时切换工作目录（serve project.set → 引擎线程内串行执行）。

    Args:
        engine: 工作台对话引擎实例。
        path: 目标项目目录（须为已存在的绝对路径；serve 侧已做一次校验，
            这里二次校验兜底——引擎可能收到非 serve 来源的指令）。
        start_new_session: True（默认）= 手动切项目的语义「切项目就开新会话」；
            False = 只换目录、保住当前对话。后者专供 ``_handle_load`` 恢复
            历史会话使用：那条对话是用户点名要打开的对象，不能因换目录被清掉。
        announce: True（默认）= 推面向用户的气泡（``info`` / ``warn`` / ``_ui.info``）；
            False = 只推 ``project_switched`` 让宿主更新项目区，不弹任何提示。
            恢复会话时用户的意图是“打开会话”而非“切项目”，弹切换提示是噪音；
            失败文案也由调用方按自己的场景给（比“项目路径非法”贴切）。

    Returns:
        True 表示目录已生效（含“本来就同目录”的幂等分支）；False 表示未切换
        （路径非法 / 提示词重建失败），此时 settings.workdir 保持原值，调用方
        可据此给出更贴场景的提示。

    @author aceFelix
    """
    path = (path or "").strip()
    if not path:
        return False
    # 1. 二次校验：绝对路径 + 目录存在。非法只 warn，不推 project_switched
    #    （前端「待生效」标记保留，用户可重选），也不落 projects.toml。
    try:
        resolved = Path(path).expanduser()
    except (OSError, ValueError) as e:
        if announce:
            engine._emitter.emit("warn", f"项目路径非法: {type(e).__name__}: {e}")
        return False
    if not resolved.is_absolute() or not resolved.is_dir():
        if announce:
            engine._emitter.emit("warn", f"项目路径非法（需为已存在的绝对目录）：{path}")
        return False
    path = str(resolved)
    name = resolved.name
    s = engine._settings

    # 2. 与当前 workdir 相同（重复点选/竞态）：不重建，只回执让前端收敛
    if str(s.workdir) == path:
        engine._emitter.emit("project_switched", {"workdir": path, "name": name})
        return True

    from agent.config import projects_registry

    loop = getattr(engine, "_query_loop", None)

    # 3a. 会话未装配：写 settings.workdir（供 _ensure_session 直接采用）+ 记账
    #     _workdir_override + 登记最近项目，不触发重型装配。此路径必成，先落库。
    if loop is None:
        s.workdir = path
        engine._workdir_override = path
        _persist(projects_registry, path, engine)
        engine._emitter.emit("project_switched", {"workdir": path, "name": name})
        if announce:
            engine._emitter.emit("info", f"已切换到项目：{name}")
        return True

    # 3b. 已装配：先用新 workdir 重建提示词（未提交 settings），成功才就地
    #     换 workdir → 更新 loop → 重挂 harness → 开新会话 → 落库。任一步失败
    #     都不提交切换（settings.workdir 保持旧值），避免"提示词旧、目录新"分裂。
    registry = getattr(loop, "_registry", None) or getattr(engine, "_registry", None)
    from agent.prompts.system import compose_system_prompt

    try:
        # workdir 显式覆盖为新路径：此刻 settings.workdir 仍是旧值，切换尚未提交；
        # append 段由 compose_system_prompt 统一拼上。
        system_prompt = compose_system_prompt(s, registry, workdir=path)
    except Exception as e:  # noqa: BLE001 - 提示词重建失败：不提交切换
        if announce:
            engine._emitter.emit(
                "warn", f"项目切换失败（提示词重建）: {type(e).__name__}: {e}"
            )
        return False

    s.workdir = path
    engine._workdir_override = ""
    loop.update_system_prompt(system_prompt)

    # 对新 workdir 重挂 harness 工具（后台线程，不阻塞切换落地；与
    # _ensure_session 的 harness 加载同口径，register_dynamic_tools 按名去重，
    # 旧项目已挂的同名工具会被跳过、新项目的增量工具补挂）。
    if registry is not None:
        threading.Thread(
            target=lambda: engine._register_harness(registry, path), daemon=True
        ).start()

    # 切项目 = 开新会话：清空上下文、换 session 名（旧会话仍按 SessionMeta.workdir
    # 归在旧项目下可回看）。恢复历史会话时由调用方传 start_new_session=False 跳过
    # —— 否则刚载入的那条对话会被当场清掉。
    if start_new_session:
        engine._handle_new_session()
    _persist(projects_registry, path, engine)
    engine._emitter.emit("project_switched", {"workdir": path, "name": name})
    if announce:
        engine._ui.info(f"已切换到项目：{name}")
    return True


def _persist(projects_registry, path: str, engine: ChatEngine) -> None:
    """登记最近项目 + 设为最后活跃；失败仅 info 告知，不阻断已生效的本次切换。"""
    try:
        projects_registry.touch_project(path)
    except Exception as e:  # noqa: BLE001 - 持久化失败不影响本次切换生效
        engine._emitter.emit("info", f"⚠ 最近项目登记失败: {type(e).__name__}: {e}")
