"""日程提醒工具 —— 让主 agent 能安排定时提醒。

阶段五第三刀。主 agent 调用这些工具安排/查询/取消提醒任务。
LLM 负责理解自然语言时间（"明天下午3点"），转成 ISO 时间字符串。

这是贾维斯独有的"主动感知"能力。

工具列表:
- **ScheduleReminder**: 安排一个提醒。输入：时间(ISO)、内容、重复模式。
- **ListSchedule**: 列出所有待触发的提醒。
- **CancelSchedule**: 取消一个提醒。

设计要点:
- Scheduler 实例通过构造函数注入（全局单例，daemon 启动时创建）
- LLM 负责时间解析：用户说"明天下午3点"，LLM 转成 "2026-06-20T15:00:00"
- 重复模式让 LLM 判断：用户说"每天提醒我喝水"→ daily；"每周一开会"→ weekly
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.core.daemon.scheduler import Scheduler
from agent.core.tool import JSONSchema, Tool


class ScheduleReminderTool(Tool):
    """安排一个定时提醒。

    主 agent 调用此工具为用户设置提醒。到时间后贾维斯会主动通知用户
    （托盘通知 + 语音播报）。
    """

    name = "ScheduleReminder"
    description = (
        "安排一个定时提醒。到时间后贾维斯会主动通知用户（托盘通知+语音播报）。"
        "用于用户说'提醒我...'/'XX点叫我...'等场景。"
        "你需要把用户的自然语言时间转成 ISO 格式（如'明天下午3点'→'2026-06-20T15:00:00'）。"
        "repeat: once=一次性, daily=每天重复, weekly=每周重复。默认 once。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "trigger_at": {
                "type": "string",
                "description": (
                    "触发时间，ISO 8601 格式 'YYYY-MM-DDTHH:MM:SS'。"
                    "把用户的自然语言时间转成此格式。"
                    "例: '明天下午3点'→'2026-06-20T15:00:00', "
                    "'下周一早上9点'→'2026-06-22T09:00:00'"
                ),
            },
            "content": {
                "type": "string",
                "description": "提醒内容。简洁明了，如'开会''喝水''该起身活动了'。",
            },
            "repeat": {
                "type": "string",
                "description": "重复模式。once=一次性(默认), daily=每天重复, weekly=每周重复。",
                "enum": ["once", "daily", "weekly"],
                "default": "once",
            },
            "note": {
                "type": "string",
                "description": "备注（可选）。附加说明，如提醒的背景信息。",
            },
        },
        "required": ["trigger_at", "content"],
    }
    max_result_chars = 2000

    def __init__(self, scheduler: Scheduler) -> None:
        self._scheduler = scheduler

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False  # 创建提醒是写操作

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("安排提醒")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args is None:
            return "安排提醒"
        content = args.get("content", "")
        return f"安排提醒: {content[:30]}"

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        trigger_at = args.get("trigger_at", "").strip()
        content = args.get("content", "").strip()
        repeat = args.get("repeat", "once")
        note = args.get("note", "").strip()

        if not trigger_at:
            return ToolResult.error("trigger_at 不能为空（ISO 时间格式，如 2026-06-20T15:00:00）")
        if not content:
            return ToolResult.error("content 不能为空（提醒内容）")

        # 校验时间格式
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(trigger_at)
        except (ValueError, TypeError):
            return ToolResult.error(
                f"trigger_at 格式错误: {trigger_at}（需要 ISO 格式 YYYY-MM-DDTHH:MM:SS）"
            )

        # 校验不能是过去时间
        from datetime import datetime as _dt
        if dt < _dt.now() and repeat == "once":
            return ToolResult.error(f"触发时间 {trigger_at} 已过去，请设置未来时间")

        # 校验 repeat
        if repeat not in ("once", "daily", "weekly"):
            return ToolResult.error(f"repeat 必须是 once/daily/weekly，收到: {repeat}")

        task = self._scheduler.add_task(
            content=content,
            trigger_at=trigger_at,
            repeat=repeat,
            note=note,
        )

        # 构造用户友好的确认信息
        repeat_desc = {"once": "一次性", "daily": "每天重复", "weekly": "每周重复"}.get(repeat, repeat)
        return ToolResult(
            data=(
                f"✓ 已安排提醒\n"
                f"  内容: {content}\n"
                f"  时间: {trigger_at}\n"
                f"  模式: {repeat_desc}\n"
                f"  任务ID: {task.id}"
            )
        )


class ListScheduleTool(Tool):
    """列出所有待触发的提醒任务。"""

    name = "ListSchedule"
    description = (
        "列出所有待触发的定时提醒任务。"
        "用于用户问'我有什么提醒'/'待办事项'等场景。"
        "返回按时间排序的待触发任务列表。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "include_done": {
                "type": "boolean",
                "description": "是否包含已触发/已取消的任务。默认 false（只看待触发的）。",
                "default": False,
            }
        },
    }
    max_result_chars = 5000

    def __init__(self, scheduler: Scheduler) -> None:
        self._scheduler = scheduler

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("查询提醒")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        include_done = args.get("include_done", False)

        if include_done:
            tasks = self._scheduler.list_all()
        else:
            tasks = self._scheduler.list_pending()

        if not tasks:
            return ToolResult(data="当前没有待触发的提醒任务。")

        lines = [f"共 {len(tasks)} 个{'提醒任务' if include_done else '待触发提醒'}："]
        for i, t in enumerate(tasks, 1):
            repeat_desc = {"once": "", "daily": "（每天）", "weekly": "（每周）"}.get(t.repeat, "")
            status_desc = {"pending": "⏳", "fired": "✓", "cancelled": "✗"}.get(t.status, "?")
            note_str = f"\n   备注: {t.note}" if t.note else ""
            lines.append(
                f"{i}. {status_desc} {t.trigger_at} {t.content}{repeat_desc} (ID: {t.id}){note_str}"
            )

        return ToolResult(data="\n".join(lines))


class CancelScheduleTool(Tool):
    """取消一个定时提醒任务。"""

    name = "CancelSchedule"
    description = (
        "取消一个定时提醒任务。"
        "用于用户说'取消提醒''不要提醒了'等场景。"
        "需要提供任务ID（可用 ListSchedule 查询）。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要取消的任务ID（可用 ListSchedule 工具查询）。",
            }
        },
        "required": ["task_id"],
    }
    max_result_chars = 1000

    def __init__(self, scheduler: Scheduler) -> None:
        self._scheduler = scheduler

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("取消提醒")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        task_id = args.get("task_id", "").strip()
        if not task_id:
            return ToolResult.error("task_id 不能为空")

        if self._scheduler.cancel_task(task_id):
            return ToolResult(data=f"✓ 已取消提醒任务 {task_id}")
        else:
            return ToolResult.error(
                f"未找到待触发任务 {task_id}（可能已触发或已取消，用 ListSchedule 查看当前任务）"
            )


# ---------------------------------------------------------------------------
# 注册函数
# ---------------------------------------------------------------------------


def register_schedule_tools(registry, scheduler: Scheduler) -> int:
    """注册日程提醒相关工具。返回注册数。

    Args:
        registry: ToolRegistry 实例。
        scheduler: Scheduler 实例（daemon 启动时创建的全局单例）。

    Returns: 注册的工具数。
    """
    count = 0
    for tool_cls in [ScheduleReminderTool, ListScheduleTool, CancelScheduleTool]:
        name = tool_cls.name
        if name in registry:
            continue
        try:
            registry.register(tool_cls(scheduler))
            count += 1
        except Exception:
            pass
    return count
