"""插件市场搜索工具 —— LLM 可通过此工具搜索可安装的插件。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool


class MarketSearchTool(Tool):
    name = "MarketSearch"
    description = (
        "搜索 J.A.R.V.I.S 插件市场，查找可用插件。"
        "返回匹配的插件名称、版本、描述和安装方式。"
        "用户可使用 /plugin install <名称> 安装感兴趣的插件。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词（插件名或功能描述）。留空返回全部可用插件。",
            },
        },
        "required": [],
    }
    max_result_chars = 4_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读插件市场搜索")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio
        keyword = args.get("keyword", "").strip()
        if ctx.ui:
            label = keyword if keyword else "全部"
            ctx.ui.info(f"搜索插件: {label}")

        def _search():
            from agent.core.plugins import PluginManager
            pm = PluginManager()
            return pm.search(keyword)

        try:
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(None, _search)
        except Exception as e:
            return ToolResult(data=f"插件市场搜索失败: {e}", is_error=True)

        if not results:
            return ToolResult(data=f"未找到匹配的插件（关键词: {keyword or '全部'}）。")

        lines = [f"插件市场搜索结果（{len(results)} 个）:\n"]
        for i, p in enumerate(results, 1):
            lines.append(f"{i}. **{p.get('name', '?')}** v{p.get('version', '?')}")
            lines.append(f"   {p.get('description', '')}")
            author = p.get("author", "")
            if author:
                lines.append(f"   作者: {author}")
            lines.append(f"   安装: `/plugin install {p.get('name', '?')}`")
            lines.append("")
        return ToolResult(data="\n".join(lines))
