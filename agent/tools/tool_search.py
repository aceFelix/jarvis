"""ToolSearch —— 延迟工具按需发现。

参考 Claude Code 的 deferred tool loading 机制：
核心工具始终携带，MCP/harness/可选工具仅发名字摘要到 system prompt，
模型需要时调用 ToolSearch 搜索 → 返回完整 schema → 标记为"已发现" →
下一轮迭代 _build_tool_defs() 自动包含已发现的工具。

@author aceFelix
"""

from __future__ import annotations

import json
from typing import Any

from agent.core.context import ToolContext
from agent.core.result import ToolResult
from agent.core.tool import Tool, ToolRegistry


class ToolSearchTool(Tool):
    """搜索并加载延迟工具。

    当模型需要某个功能但当前工具列表中没有时，用关键词搜索延迟工具池。
    搜索命中后，工具的完整 JSON Schema 会返回给模型，同时标记为"已发现"，
    后续迭代中该工具会自动出现在可用工具列表中。
    """

    name = "ToolSearch"
    description = (
        "搜索并加载延迟工具。当你需要某个功能但当前可用工具列表中没有时，"
        "用关键词搜索。返回匹配工具的完整定义，之后即可直接调用。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词（工具名、功能描述、server 名等）",
            },
            "max_results": {
                "type": "integer",
                "description": "最多返回几个结果（默认 5）",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    # 自身是核心工具，始终携带
    deferred = False

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = (args.get("query") or "").strip().lower()
        max_results = int(args.get("max_results", 5))

        if not query:
            return ToolResult.error("请提供搜索关键词（query 参数）")

        # 从延迟工具池中搜索
        deferred_tools = self._registry.all_deferred()
        keywords = query.replace(",", " ").replace("，", " ").split()

        scored: list[tuple[int, Tool]] = []
        for tool in deferred_tools:
            score = self._score(tool, keywords)
            if score > 0:
                scored.append((score, tool))

        # 按得分降序
        scored.sort(key=lambda x: x[0], reverse=True)
        matches = [tool for _, tool in scored[:max_results]]

        if not matches:
            # 列出所有延迟工具名供参考
            all_names = [t.name for t in deferred_tools]
            hint = f"未找到匹配「{query}」的工具。"
            if all_names:
                sample = ", ".join(all_names[:30])
                hint += f"\n可用延迟工具（共 {len(all_names)} 个）: {sample}"
                if len(all_names) > 30:
                    hint += f" ... 等 {len(all_names) - 30} 个"
            return ToolResult.ok(hint)

        # 标记为已发现
        discovered: set[str] = ctx.extra.setdefault("discovered_tools", set())
        for tool in matches:
            discovered.add(tool.name)

        # 构造返回文本：完整 JSON Schema
        lines = [f"已找到 {len(matches)} 个工具，已加载到可用列表（下轮迭代生效）：\n"]
        for tool in matches:
            schema_str = json.dumps(tool.input_schema, ensure_ascii=False, indent=2)
            lines.append(f"## {tool.name}")
            lines.append(f"描述: {tool.description.split(chr(10), 1)[0]}")
            lines.append(f"参数:\n```json\n{schema_str}\n```\n")

        return ToolResult.ok("\n".join(lines))

    @staticmethod
    def _score(tool: Tool, keywords: list[str]) -> int:
        """简单关键词匹配打分。"""
        name_lower = tool.name.lower()
        desc_lower = tool.description.lower()
        score = 0
        for kw in keywords:
            if kw in name_lower:
                score += 10  # 名字命中权重高
            if kw in desc_lower:
                score += 3
            # 工具名按 __ 分割后的片段匹配（如 mcp__browser__navigate）
            parts = name_lower.replace("__", " ").replace("_", " ").split()
            if kw in parts:
                score += 5
        return score

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True
