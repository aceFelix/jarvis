"""W3.1 任务脊柱包：跨会话、跨天的目标台账与它的后台推进器。

门面只导出台账读写、目标写入与管控、默认执行通路与驱动器装配入口，五条口径的完整说明在各模块头部：
- ``spine`` 只回答"条件满足了吗、下一步是什么"，判定不花钱、不起进程；
- ``spine_ops`` 管"目标怎么被建出来与管控"（建/挂等待/暂停恢复取消/立刻推进），
  终端命令、``TrackGoal`` 工具与 ``tasks.exec`` RPC 三个入口共用同一套语义；
- ``spawn`` 只管"一步怎么被执行"（子代理定义与真实 spawn），从 driver 拆出；
- ``driver`` 负责"什么时候醒、醒来推一步、推完怎么播报"；
- 播报一律走宿主注入的 notifier（内部必须先过 W1 打扰闸门）。

与既有"任务"概念的分工见 ``spine`` 模块头部（闹钟 / 一次查询内的子任务 / 目标台账）。

用法::

    from agent.core.tasks import get_spine_store, build_driver, wake_driver
    from agent.core.tasks import create_goal, control_goal, snapshot

@author aceFelix
"""

from __future__ import annotations

from agent.core.tasks.cli_host import CliSpineHost
from agent.core.tasks.driver import (
    NOTIFY_SOURCE,
    TaskDriver,
    build_driver,
    get_driver,
    make_gate_notifier,
    set_driver,
    wake_driver,
)
from agent.core.tasks.spine_ops import (
    CONTROL_ACTIONS,
    OpResult,
    control_goal,
    create_goal,
    describe_steps,
    find_goal,
    goal_detail,
    parse_wait,
    snapshot,
    split_steps,
    wait_from_spec,
    wait_readable,
)
from agent.core.tasks.spawn import (
    StepOutcome,
    default_spawn,
    default_step_definition,
)
from agent.core.tasks.spine import (
    BLOCK_APPROVAL,
    BLOCK_ERROR,
    BLOCK_TIMEOUT,
    BLOCK_WAIT,
    GOAL_ACTIVE,
    GOAL_CANCELLED,
    GOAL_DONE,
    GOAL_FAILED,
    GOAL_PAUSED,
    STEP_BLOCKED,
    STEP_DONE,
    STEP_FAILED,
    STEP_PENDING,
    STEP_RUNNING,
    WAIT_FILE_EXISTS,
    WAIT_PERCEPT_EVENT,
    WAIT_TIME,
    SpineStore,
    TaskGoal,
    TaskStep,
    TaskWait,
    configure_spine_store,
    get_spine_store,
    iso_of,
    parse_iso,
)

__all__ = [
    # 状态与类型常量
    "GOAL_ACTIVE",
    "GOAL_PAUSED",
    "GOAL_DONE",
    "GOAL_FAILED",
    "GOAL_CANCELLED",
    "STEP_PENDING",
    "STEP_RUNNING",
    "STEP_DONE",
    "STEP_BLOCKED",
    "STEP_FAILED",
    "BLOCK_WAIT",
    "BLOCK_TIMEOUT",
    "BLOCK_ERROR",
    "BLOCK_APPROVAL",
    "WAIT_TIME",
    "WAIT_FILE_EXISTS",
    "WAIT_PERCEPT_EVENT",
    "NOTIFY_SOURCE",
    # 台账模型与存储
    "TaskWait",
    "TaskStep",
    "TaskGoal",
    "SpineStore",
    "get_spine_store",
    "configure_spine_store",
    "parse_iso",
    "iso_of",
    # 目标写入与管控（命令 / 工具 / RPC 三入口共用）
    "OpResult",
    "CONTROL_ACTIONS",
    "split_steps",
    "parse_wait",
    "wait_from_spec",
    "create_goal",
    "control_goal",
    "find_goal",
    "goal_detail",
    "describe_steps",
    "wait_readable",
    "snapshot",
    # 驱动器与默认执行通路
    "StepOutcome",
    "default_step_definition",
    "default_spawn",
    "TaskDriver",
    "CliSpineHost",
    "build_driver",
    "make_gate_notifier",
    "get_driver",
    "set_driver",
    "wake_driver",
]
