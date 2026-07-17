"""多 Agent 协作模块。

包含子 Agent、团队、队友、邮箱通信、共享任务列表等能力。
"""

from agent.collaboration.mailbox import (
    TeammateMessage,
    clear_mailbox,
    has_unread,
    make_idle_notification,
    read_mailbox,
    write_mailbox,
)
from agent.collaboration.subagent import (
    AgentDefinition,
    SubagentResult,
    run_subagent,
    run_subagents_parallel,
)
from agent.collaboration.task_list import TaskList, TodoTask
from agent.collaboration.team import (
    TEAM_LEAD_NAME,
    TeamManager,
    TeamMember,
    get_team_manager,
    sanitize_name,
    team_inbox_dir,
)
from agent.collaboration.teammate import (
    TeammateIdentity,
    TeammateRunner,
    TeammateState,
)

__all__ = [
    "TeammateMessage",
    "clear_mailbox",
    "has_unread",
    "make_idle_notification",
    "read_mailbox",
    "write_mailbox",
    "AgentDefinition",
    "SubagentResult",
    "run_subagent",
    "run_subagents_parallel",
    "TaskList",
    "TodoTask",
    "TEAM_LEAD_NAME",
    "TeamManager",
    "TeamMember",
    "get_team_manager",
    "sanitize_name",
    "team_inbox_dir",
    "TeammateIdentity",
    "TeammateRunner",
    "TeammateState",
]
