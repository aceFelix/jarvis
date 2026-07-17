"""FileRead 工具 —— 读取文件内容。

支持:
- 按行范围读取（offset + limit）
- 二进制文件检测（拒绝读取，提示用户）
- 大文件截断（超过 max_result_chars 时保留首尾）
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, PermissionMatcher, Tool
from agent.tools.base import resolve_path, truncate_for_llm


class FileReadTool(Tool):
    name = "FileRead"
    description = (
        "读取本地文件内容。可指定 offset（起始行，从1开始）和 limit（读取行数）。"
        "二进制文件会拒绝读取。相对路径以当前工作目录为根。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "文件路径（相对或绝对）"},
            "offset": {
                "type": "integer",
                "description": "起始行号（从1开始，默认1）",
                "minimum": 1,
            },
            "limit": {
                "type": "integer",
                "description": "读取行数（默认200，最大2000）",
                "minimum": 1,
                "maximum": 2000,
            },
        },
        "required": ["file_path"],
    }
    max_result_chars = 50_000  # 文件读取结果永不落盘，但做截断

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True  # 只读，可并行

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        # 只读工具默认放行（路径守护仍由 checker 统一处理）
        return PermissionResult.allow("只读操作")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        path = resolve_path(ctx, args.get("file_path", ""))
        if not path.exists():
            return ValidationResult.fail(f"文件不存在: {path}")
        if not path.is_file():
            return ValidationResult.fail(f"不是文件: {path}")
        return ValidationResult.pass_()

    def get_path(self, args: dict[str, Any]) -> str:
        return args.get("file_path", "")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = resolve_path(ctx, args["file_path"])
        offset = max(1, int(args.get("offset", 1)))
        limit = min(2000, max(1, int(args.get("limit", 200))))

        # 二进制检测: 读前 4KB 看是否有 NUL 字节
        try:
            with path.open("rb") as f:
                head = f.read(4096)
            if b"\x00" in head:
                return ToolResult.error(
                    f"二进制文件，拒绝读取: {path}（建议用专门工具处理）"
                )
        except PermissionError as e:
            return ToolResult.error(f"无读取权限: {e}")

        # 文本读取
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult.error(f"读取失败: {e}")

        lines = text.splitlines(keepends=True)
        total = len(lines)
        start = offset - 1
        end = start + limit
        sliced = "".join(lines[start:end])

        header = f"[{path} | 共 {total} 行 | 显示 {offset}..{min(end, total)}]\n\n"
        body = truncate_for_llm(sliced, self.max_result_chars)

        # 记录文件状态（供 FileEdit/FileWrite 冲突检测用）
        from agent.core.memory.file_state import record_file_read
        record_file_read(ctx, str(path))

        return ToolResult.ok(data=header + body)

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and args.get("file_path"):
            return f"读取 {args['file_path']}"
        return None
