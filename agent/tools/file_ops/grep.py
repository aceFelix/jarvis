"""Grep 工具 —— 在文件内容中搜索正则。

语义: 在工作目录递归搜索匹配 pattern 的行。
返回文件名 + 行号 + 匹配行内容。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool
from agent.tools.base import resolve_path


class GrepTool(Tool):
    name = "Grep"
    description = (
        "在文件内容中搜索正则表达式。默认递归搜索工作目录。"
        "返回每个匹配的 文件名:行号:匹配行。"
        "用 include 参数限定文件类型（如 '*.py'）。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式"},
            "path": {"type": "string", "description": "搜索路径（默认工作目录）"},
            "include": {"type": "string", "description": "文件名 glob 过滤（如 '*.py'）"},
            "ignore_case": {"type": "boolean", "description": "是否忽略大小写（默认 false）"},
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

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        try:
            re.compile(args.get("pattern", ""))
        except re.error as e:
            return ValidationResult.fail(f"正则编译失败: {e}")
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pattern = args["pattern"]
        root = resolve_path(ctx, args.get("path", "."))
        include = args.get("include", "*")
        ignore_case = bool(args.get("ignore_case", False))

        if not root.exists():
            return ToolResult.error(f"搜索路径不存在: {root}")

        flags = re.IGNORECASE if ignore_case else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult.error(f"正则错误: {e}")

        results: list[str] = []
        total_matches = 0
        max_files = 50
        max_matches_per_file = 20

        files_iter = root.rglob(include) if root.is_dir() else [root]
        for fp in files_iter:
            if not fp.is_file():
                continue
            # 跳过二进制（简单检测）
            try:
                text = fp.read_text(encoding="utf-8", errors="strict")
            except (UnicodeDecodeError, PermissionError):
                continue
            file_hits = 0
            try:
                rel = fp.relative_to(ctx.workdir)
            except ValueError:
                rel = fp
            for i, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    # 截断超长行
                    snippet = line.strip()[:200]
                    results.append(f"{rel}:{i}: {snippet}")
                    file_hits += 1
                    total_matches += 1
                    if file_hits >= max_matches_per_file:
                        break
            if len(results) >= max_files * 3:  # 粗略上限
                break

        if not results:
            return ToolResult.ok(data=f"未找到匹配 /{pattern}/ 的内容")

        body = "\n".join(results)
        return ToolResult.ok(
            data=f"找到 {total_matches} 处匹配（pattern=/{pattern}/）:\n\n{body}"
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and args.get("pattern"):
            return f"搜索 /{args['pattern']}/"
        return None
