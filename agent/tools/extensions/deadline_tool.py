"""截止日期管理工具 —— 让主 agent 能管理用户的截止日期。

P2-3 主动提醒系统。主 agent 调用这些工具注册/查询/完成/删除截止日期。
DeadlineTracker 负责分级提醒逻辑（提前 7/3/1/0 天 + 逾期每天）。

工具列表:
- **AddDeadline**: 注册一个截止日期。输入：标题、到期日、提醒天数。
- **ListDeadlines**: 列出所有活跃截止日期。
- **CompleteDeadline**: 标记截止日期为已完成。
- **RemoveDeadline**: 删除一个截止日期。

设计要点:
- DeadlineTracker 实例通过构造函数注入（全局单例，daemon 启动时创建）
- LLM 负责日期解析：用户说"下周五"，LLM 转成 "2026-07-25"
- remind_days 让 LLM 判断：默认 [7, 3, 1, 0]，用户可自定义
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.core.daemon.deadline import DeadlineTracker
from agent.core.tool import JSONSchema, Tool


class AddDeadlineTool(Tool):
    """注册一个截止日期。

    主 agent 调用此工具为用户设置截止日期追踪。
    贾维斯会在临近时分级提醒（提前 7/3/1/0 天 + 逾期每天）。
    """

    name = "AddDeadline"
    description = (
        "注册一个截止日期。贾维斯会在临近时自动分级提醒。"
        "用于用户说'XX之前要完成...'/'截止日期是...'/'deadline是...'等场景。"
        "你需要把用户的自然语言日期转成 ISO 格式（如'下周五'→'2026-07-25'）。"
        "remind_days: 提前几天提醒的列表，默认 [7, 3, 1, 0]（提前7天、3天、1天、当天）。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "截止日期标题。简洁明了，如'Q3项目报告''论文终稿''房租缴纳'。",
            },
            "due_date": {
                "type": "string",
                "description": (
                    "到期日，ISO 格式 'YYYY-MM-DD'。"
                    "把用户的自然语言日期转成此格式。"
                    "例: '下周五'→'2026-07-25', '8月15号'→'2026-08-15'"
                ),
            },
            "remind_days": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "提前几天提醒的列表。默认 [7, 3, 1, 0]。"
                    "0=当天提醒。如用户说'提前两天提醒我'，设为 [2, 0]。"
                ),
                "default": [7, 3, 1, 0],
            },
            "note": {
                "type": "string",
                "description": "备注（可选）。附加说明，如截止日期的背景信息。",
            },
        },
        "required": ["title", "due_date"],
    }
    max_result_chars = 2000

    def __init__(self, tracker: DeadlineTracker) -> None:
        self._tracker = tracker

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("注册截止日期")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args is None:
            return "注册截止日期"
        title = args.get("title", "")
        return f"注册截止日期: {title[:30]}"

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        title = args.get("title", "").strip()
        due_date = args.get("due_date", "").strip()
        remind_days = args.get("remind_days", [7, 3, 1, 0])
        note = args.get("note", "").strip()

        if not title:
            return ToolResult.error("title 不能为空（截止日期标题）")
        if not due_date:
            return ToolResult.error("due_date 不能为空（ISO 日期格式，如 2026-08-15）")

        # 校验 remind_days
        if not isinstance(remind_days, list):
            remind_days = [7, 3, 1, 0]
        remind_days = [int(d) for d in remind_days if isinstance(d, (int, float))]

        try:
            deadline = self._tracker.add(
                title=title,
                due_date=due_date,
                remind_days=remind_days,
                note=note,
            )
        except ValueError as e:
            return ToolResult.error(str(e))

        remind_desc = "、".join(f"提前{d}天" if d > 0 else "当天" for d in sorted(remind_days, reverse=True))
        return ToolResult(
            data=(
                f"✓ 已注册截止日期\n"
                f"  标题: {title}\n"
                f"  到期: {due_date}\n"
                f"  提醒: {remind_desc}\n"
                f"  ID: {deadline.id}"
            )
        )


class ListDeadlinesTool(Tool):
    """列出所有活跃的截止日期。"""

    name = "ListDeadlines"
    description = (
        "列出所有活跃的截止日期（按到期日排序）。"
        "用于用户问'我有什么截止日期'/'还有什么没完成'等场景。"
        "返回标题、到期日、剩余天数、状态。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "include_done": {
                "type": "boolean",
                "description": "是否包含已完成的截止日期。默认 false（只看活跃的）。",
                "default": False,
            }
        },
    }
    max_result_chars = 5000

    def __init__(self, tracker: DeadlineTracker) -> None:
        self._tracker = tracker

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("查询截止日期")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        include_done = args.get("include_done", False)

        if include_done:
            deadlines = self._tracker.list_all()
        else:
            deadlines = self._tracker.list_active()

        if not deadlines:
            return ToolResult(data="当前没有活跃的截止日期。")

        lines = [f"共 {len(deadlines)} 个截止日期："]
        for i, d in enumerate(deadlines, 1):
            days_left = d.days_left
            if days_left is None:
                days_desc = "日期无效"
            elif days_left < 0:
                days_desc = f"已逾期 {abs(days_left)} 天"
            elif days_left == 0:
                days_desc = "今天截止！"
            else:
                days_desc = f"还剩 {days_left} 天"

            status_icon = {"active": "📌", "overdue": "⚠️", "done": "✓"}.get(d.status, "?")
            note_str = f"\n   备注: {d.note}" if d.note else ""
            lines.append(
                f"{i}. {status_icon} {d.title} | 截止: {d.due_date} | {days_desc} (ID: {d.id}){note_str}"
            )

        return ToolResult(data="\n".join(lines))


class CompleteDeadlineTool(Tool):
    """标记截止日期为已完成。"""

    name = "CompleteDeadline"
    description = (
        "标记一个截止日期为已完成。"
        "用于用户说'XX已经完成了'/'搞定了'等场景。"
        "需要提供截止日期ID（可用 ListDeadlines 查询）。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "deadline_id": {
                "type": "string",
                "description": "要标记完成的截止日期ID（可用 ListDeadlines 工具查询）。",
            }
        },
        "required": ["deadline_id"],
    }
    max_result_chars = 1000

    def __init__(self, tracker: DeadlineTracker) -> None:
        self._tracker = tracker

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("完成截止日期")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        deadline_id = args.get("deadline_id", "").strip()
        if not deadline_id:
            return ToolResult.error("deadline_id 不能为空")

        if self._tracker.complete(deadline_id):
            return ToolResult(data=f"✓ 已标记截止日期 {deadline_id} 为完成")
        else:
            return ToolResult.error(
                f"未找到活跃截止日期 {deadline_id}（可能已完成或已删除，用 ListDeadlines 查看）"
            )


class RemoveDeadlineTool(Tool):
    """删除一个截止日期。"""

    name = "RemoveDeadline"
    description = (
        "删除一个截止日期（彻底移除，不再提醒）。"
        "用于用户说'不用追踪XX了'/'删掉那个截止日期'等场景。"
        "需要提供截止日期ID（可用 ListDeadlines 查询）。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "deadline_id": {
                "type": "string",
                "description": "要删除的截止日期ID（可用 ListDeadlines 工具查询）。",
            }
        },
        "required": ["deadline_id"],
    }
    max_result_chars = 1000

    def __init__(self, tracker: DeadlineTracker) -> None:
        self._tracker = tracker

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("删除截止日期")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        deadline_id = args.get("deadline_id", "").strip()
        if not deadline_id:
            return ToolResult.error("deadline_id 不能为空")

        if self._tracker.remove(deadline_id):
            return ToolResult(data=f"✓ 已删除截止日期 {deadline_id}")
        else:
            return ToolResult.error(f"未找到截止日期 {deadline_id}")


# ---------------------------------------------------------------------------
# 注册函数
# ---------------------------------------------------------------------------


def register_deadline_tools(registry, tracker: DeadlineTracker) -> int:
    """注册截止日期相关工具。返回注册数。

    Args:
        registry: ToolRegistry 实例。
        tracker: DeadlineTracker 实例（daemon 启动时创建的全局单例）。

    Returns: 注册的工具数。
    """
    count = 0
    for tool_cls in [AddDeadlineTool, ListDeadlinesTool, CompleteDeadlineTool, RemoveDeadlineTool]:
        name = tool_cls.name
        if name in registry:
            continue
        try:
            registry.register(tool_cls(tracker))
            count += 1
        except Exception:
            pass
    return count
