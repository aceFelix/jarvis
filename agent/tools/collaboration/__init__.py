"""多 Agent 协作相关工具。

包含子 Agent 工具、团队管理工具、任务工具和 Plan Mode 工具。
"""

try:
    from agent.tools.collaboration.enter_plan import EnterPlanModeTool
    from agent.tools.collaboration.exit_plan import ExitPlanModeTool
    from agent.tools.collaboration.send_message import SendMessageTool
    from agent.tools.collaboration.subagent_tool import SubagentTool
    from agent.tools.collaboration.task_create import TaskCreateTool
    from agent.tools.collaboration.task_get import TaskGetTool
    from agent.tools.collaboration.task_list import TaskListTool
    from agent.tools.collaboration.task_stop import TaskStopTool
    from agent.tools.collaboration.task_update import TaskUpdateTool
    from agent.tools.collaboration.team_create import TeamCreateTool
    from agent.tools.collaboration.team_delete import TeamDeleteTool
except ImportError:
    # 协作工具依赖的 collaboration 包未就绪时，不阻塞导入
    pass

__all__ = [
    "EnterPlanModeTool",
    "ExitPlanModeTool",
    "SendMessageTool",
    "SubagentTool",
    "TaskCreateTool",
    "TaskGetTool",
    "TaskListTool",
    "TaskStopTool",
    "TaskUpdateTool",
    "TeamCreateTool",
    "TeamDeleteTool",
]
