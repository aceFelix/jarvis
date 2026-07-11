"""FileWrite 工具 —— 写入文件（覆盖或新建）。

对应原项目 tools/FileWriteTool/。语义: 把 content 完整写入指定文件。
已存在的文件会被覆盖（v0.1 不做备份，依赖外部 git/versionControl）。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool
from agent.tools.base import resolve_path


class FileWriteTool(Tool):
    name = "FileWrite"
    description = (
        "写入文件（覆盖）。如果文件已存在会被覆盖。"
        "用于创建新文件或整文件重写；小范围修改请用 FileEdit。"
        "父目录不存在时会自动创建。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "要写入的完整内容"},
        },
        "required": ["file_path", "content"],
    }
    max_result_chars = 2_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any]) -> bool:
        # 覆盖写，不可逆
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.ask("文件写入需确认")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        if not args.get("file_path"):
            return ValidationResult.fail("file_path 不能为空")
        if args.get("content") is None:
            return ValidationResult.fail("content 不能为空")
        return ValidationResult.pass_()

    def get_path(self, args: dict[str, Any]) -> str:
        return args.get("file_path", "")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from agent.core.file_state import check_file_stale, record_file_write, invalidate

        path = resolve_path(ctx, args["file_path"])
        content = args["content"]

        # 冲突检测：已存在的文件被外部修改时拒绝覆盖
        if path.exists():
            if check_file_stale(ctx, str(path)):
                return ToolResult.error(
                    f"⚠ 文件已被外部修改: {path}\n"
                    "覆盖会丢失外部改动。请先用 FileRead 重新读取，确认内容后再写入。"
                )

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except Exception as e:
            return ToolResult.error(f"写入失败: {e}")

        # 写入成功后更新缓存
        record_file_write(ctx, str(path))

        lines = content.count("\n") + (1 if content else 0)
        return ToolResult.ok(
            data=f"已写入 {path}（{len(content)} 字符，{lines} 行）"
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and args.get("file_path"):
            return f"写入 {args['file_path']}"
        return None
