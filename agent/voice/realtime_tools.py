"""实时双工语音的 Function Calling 工具层 —— /talk 专用。

2026-09 从 [realtime_talk.py](realtime_talk.py) 拆出（原文件 820 行已超
800 行上限，需先拆再改）。本模块集中三件职责：

1. 内置工具定义（``BUILTIN_TOOLS``）：时间查询、结束对话等语音场景专用工具
2. 工具装配（``build_all_tools``）：把 ToolRegistry 全部工具转成 realtime API 格式
3. 工具执行（``execute_tool``）：内置工具 → ToolRegistry 工具的两级分派

realtime_talk.py 只保留会话生命周期与音频链路的职责。

@author aceFelix
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

# ---- Function Calling 内置工具 ----
# 实时语音场景适用的工具集合，通过 session.update 注册给模型。
# 模型自主判断是否需要调用工具获取实时信息（如时间、日期）。
BUILTIN_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前的日期、时间、星期几。当用户询问时间、日期、星期、几号时调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_conversation",
            "description": "结束当前对话并退出。仅当用户明确说出\"退下\"、\"贾维斯退下\"、\"结束对话\"、\"再见\"、\"拜拜\"、\"没事了\"等表示结束意图的话时才调用。不要在一次普通回答结束后调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

# 工具执行结果最大字符数：语音播报场景下超长输出无意义，截断保护上下文
_MAX_RESULT_CHARS = 4000


def execute_builtin_tool(name: str, args: dict[str, Any]) -> str:
    """执行内置工具函数，返回 JSON 格式的结果字符串。

    实时语音场景下工具执行在本地同步完成，不经过 ToolRegistry 权限系统，
    因为用户已在语音交互中，工具仅限只读信息查询类。
    @author aceFelix
    """
    from datetime import datetime

    if name == "get_current_time":
        now = datetime.now()
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return json.dumps({
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "date": now.strftime("%Y年%m月%d日"),
            "time": now.strftime("%H:%M"),
            "weekday": weekdays[now.weekday()],
            "timestamp": int(now.timestamp()),
        }, ensure_ascii=False)

    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)


def build_all_tools(workdir: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """从默认 ToolRegistry 构建工具集，转换为 realtime API tools 格式。

    接入所有工具（包括写操作），但排除以下不适用于语音场景的工具：
    - AskUser：需要键盘输入，语音场景无法使用

    高风险操作的安全性通过两层保障：
    1. instructions 引导模型对高风险操作先语音询问用户确认
    2. 代码层面 check_permissions 返回 deny 的操作拒绝执行

    Args:
        workdir: 工作目录，用于工具执行时的 ToolContext。

    Returns:
        (tools_schema, tool_map) 元组：
        - tools_schema: 符合 realtime API 格式的工具定义列表
        - tool_map: 工具名 → Tool 对象映射，用于 function_call 执行

    @author aceFelix
    """
    try:
        from agent.core.tool import build_default_registry
        registry = build_default_registry()
    except Exception:
        return [], {}

    tools_schema: list[dict[str, Any]] = []
    tool_map: dict[str, Any] = {}

    # 语音场景不适用的工具（需要键盘输入或 GUI 交互）
    _EXCLUDED_TOOLS = {"AskUser"}

    for tool in registry.all():
        # 排除不适合语音场景的工具
        if tool.name in _EXCLUDED_TOOLS:
            continue

        tools_schema.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema or {"type": "object", "properties": {}},
            },
        })
        tool_map[tool.name] = tool

    return tools_schema, tool_map


# Function Calling 函数名合法性约束（与 OpenAI 实时协议一致的安全子集）：
# 仅允许 [a-zA-Z0-9_-]，最长 64 字符。MCP 工具名形如 mcp__{server}__{tool}，
# 若上游工具名含中文/空格/点号或超长，注册给服务端后可能让模型的受限解码
# 停滞（响应创建后无任何输出，无 error 事件）。注册前统一清洗成合法别名。
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def sanitize_tools_for_realtime(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """清洗工具 schema 的函数名，返回 (清洗后的 schema, 别名→原名 映射)。

    名字合法则原样保留（别名映射为空时行为零变化）；不合法则替换非法字符
    为 ``_``、截断至 64 字符，并保证会话内唯一。模型按别名发起 function_call，
    执行层经 alias_map 还原原名分派。

    Args:
        tools: 符合 realtime API 格式的工具定义列表。

    Returns:
        (clean_schema, alias_map) 元组；alias_map 仅在存在非法名时非空。

    @author aceFelix
    """
    clean: list[dict[str, Any]] = []
    alias_map: dict[str, str] = {}
    used: set[str] = set()
    for tool in tools:
        fn = (tool.get("function") or {}).get("name", "")
        if isinstance(fn, str) and _TOOL_NAME_RE.match(fn):
            used.add(fn)
            clean.append(tool)
            continue
        # 构造合法别名
        base = re.sub(r"[^a-zA-Z0-9_-]", "_", str(fn))[:64].strip("_")
        if not base:
            base = "tool"
        alias = base
        n = 2
        while alias in used:
            suffix = f"_{n}"
            alias = base[: 64 - len(suffix)] + suffix
            n += 1
        used.add(alias)
        alias_map[alias] = str(fn)
        new_tool = dict(tool)
        new_fn = dict(new_tool.get("function") or {})
        new_fn["name"] = alias
        new_tool["function"] = new_fn
        clean.append(new_tool)
    return clean, alias_map


async def execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    registry_tool_map: dict[str, Any],
    workdir: str,
    ui: Any,
    on_end_conversation: Callable[[], None] | None = None,
    tool_alias_map: dict[str, str] | None = None,
) -> str:
    """执行 Function Calling 工具，返回 JSON 格式的结果字符串。

    执行顺序：
    1. 内置工具（get_current_time 等同步快速工具）
    2. ToolRegistry 工具（FileRead、Glob、Bash、FileWrite 等全部工具）

    安全策略：
    - instructions 引导模型对高风险操作先语音询问用户确认
    - check_permissions 返回 deny 的操作拒绝执行
    - 工具结果超长时截断，避免语音播报过长

    Args:
        name: 工具名。
        args: 工具参数。
        registry_tool_map: 工具名 → Tool 对象映射（由 build_all_tools 产出）。
        workdir: 工作目录，用于 ToolContext。
        ui: UI 适配器，透传给工具用于交互反馈。
        on_end_conversation: end_conversation 触发时的回调，由会话层设置
            优雅停止宽限期（工具层不直接持有会话状态）。
        tool_alias_map: sanitize_tools_for_realtime 产出的别名→原名映射，
            模型以别名调用时先还原再分派（通常为 None，无清洗时零开销）。

    @author aceFelix
    """
    # 1. 内置工具
    builtin_names = {t["function"]["name"] for t in BUILTIN_TOOLS}
    if name in builtin_names:
        # end_conversation 需要停止会话循环（等待告别语音播放后再停）
        if name == "end_conversation":
            if on_end_conversation is not None:
                on_end_conversation()
            return json.dumps({"result": "好的，我先退下了。需要时随时叫我。"}, ensure_ascii=False)
        return execute_builtin_tool(name, args)

    # 2. ToolRegistry 工具（先还原注册前的清洗别名，如 mcp__ 工具被改名的情况）
    if tool_alias_map:
        name = tool_alias_map.get(name, name)
    if name in registry_tool_map:
        tool = registry_tool_map[name]
        try:
            from agent.core.context import ToolContext
            ctx = ToolContext(
                workdir=workdir,
                messages=[],
                permission_mode="yolo",
                ui=ui,
            )

            # 权限检查：deny 拒绝，ask/allow 放行
            # 高风险操作的确认由模型通过 instructions 引导处理
            try:
                perm = tool.check_permissions(args, ctx)
                if hasattr(perm, "action") and perm.action == "deny":
                    reason = getattr(perm, "reason", "安全策略拒绝")
                    return json.dumps(
                        {"error": f"操作被安全策略拒绝: {reason}"},
                        ensure_ascii=False,
                    )
            except Exception:
                pass

            tool_result = await tool.call(args, ctx)
            # ToolResult.data → JSON 字符串
            data = tool_result.data
            if isinstance(data, str):
                result_str = data
            elif data is None:
                result_str = json.dumps({"result": "（无输出）"}, ensure_ascii=False)
            else:
                result_str = json.dumps(data, ensure_ascii=False, default=str)

            # 截断超长结果，避免语音播报过长
            if len(result_str) > _MAX_RESULT_CHARS:
                result_str = result_str[:_MAX_RESULT_CHARS] + "\n...（结果已截断）"
            return result_str
        except Exception as e:
            return json.dumps(
                {"error": f"工具 {name} 执行失败: {e}"},
                ensure_ascii=False,
            )

    # 3. 未知工具
    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
