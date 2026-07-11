"""Glob 工具 —— 按通配符查找文件。

对应原项目 tools/GlobTool/。语义: 在工作目录下递归匹配 pattern。
返回匹配的文件路径列表（相对 workdir），按修改时间排序。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import JSONSchema, Tool
from agent.tools.base import resolve_path


class GlobTool(Tool):
    name = "Glob"
    description = (
        "按通配符模式查找文件。pattern 例如 '*.py'、'src/**/*.ts'。"
        "默认递归搜索工作目录。返回匹配的文件路径列表（最多 100 条），"
        "按修改时间倒序排列。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "通配符模式（如 '**/*.py'）"},
            "path": {
                "type": "string",
                "description": "搜索根目录（默认工作目录）",
            },
        },
        "required": ["pattern"],
    }
    max_result_chars = 10_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读搜索")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pattern = args["pattern"]
        root = resolve_path(ctx, args.get("path", "."))
        if not root.exists():
            return ToolResult.error(f"搜索根目录不存在: {root}")

        # Path.glob 支持 ** 递归
        matches = sorted(
            root.glob(pattern),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        # 过滤掉目录，只留文件
        files = [p for p in matches if p.is_file()][:100]

        if not files:
            return ToolResult.ok(data=f"未找到匹配 {pattern} 的文件")

        try:
            rel_paths = [str(p.relative_to(ctx.workdir)) for p in files]
        except ValueError:
            rel_paths = [str(p) for p in files]

        body = "\n".join(rel_paths)
        return ToolResult.ok(
            data=f"找到 {len(files)} 个文件（pattern={pattern}）:\n\n{body}"
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and args.get("pattern"):
            return f"搜索 {args['pattern']}"
        return None
