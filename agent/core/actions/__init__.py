"""W3.2 副作用账本包：把工具调用记成"可执行的补偿计划"。

门面只导出账本与延时窗口的入口，具体口径见 ``journal`` 模块头部说明。
播报侧不许直接绕过 W1 闸门判断"该不该说"，本包只负责记账与回滚。

用法::

    from agent.core.actions import EffectRecord, get_journal, get_window

@author aceFelix
"""

from __future__ import annotations

from agent.core.actions.journal import (
    ALL_STATES,
    STATE_APPLIED,
    STATE_CANCELLED,
    STATE_FAILED,
    STATE_HELD,
    STATE_UNDONE,
    UNDO_CHECKPOINT,
    UNDO_NONE,
    ActionJournal,
    DelayWindow,
    EffectRecord,
    configure_journal,
    describe_undo,
    get_journal,
    get_window,
    manager_for,
    resume_pending,
    undo_record,
    undo_task,
)

__all__ = [
    "ALL_STATES",
    "STATE_HELD",
    "STATE_APPLIED",
    "STATE_UNDONE",
    "STATE_FAILED",
    "STATE_CANCELLED",
    "UNDO_CHECKPOINT",
    "UNDO_NONE",
    "ActionJournal",
    "DelayWindow",
    "EffectRecord",
    "configure_journal",
    "describe_undo",
    "get_journal",
    "get_window",
    "manager_for",
    "resume_pending",
    "undo_record",
    "undo_task",
]
