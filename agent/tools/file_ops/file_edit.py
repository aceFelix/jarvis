"""FileEdit 工具 —— 精确替换文件内容。

语义: 在文件中找到 old_string，替换为 new_string。
要求 old_string 在文件中唯一，否则报错（避免改错地方）。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool
from agent.tools.base import resolve_path


class FileEditTool(Tool):
    name = "FileEdit"
    description = (
        "精确编辑文件: 找到 old_string 并替换为 new_string。"
        "要求 old_string 在文件中唯一出现（多次出现会报错，需提供更多上下文）。"
        "用于小范围修改；大段重写请用 FileWrite。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "文件路径"},
            "old_string": {"type": "string", "description": "要被替换的原文（必须唯一）"},
            "new_string": {"type": "string", "description": "替换后的文本"},
        },
        "required": ["file_path", "old_string", "new_string"],
    }
    max_result_chars = 5_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any]) -> bool:
        # 替换操作不可逆（但 checker 会配合 fileHistory 做快照，v0.1 暂不做）
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        # 默认 ASK，让 checker 统一处理路径守护和模式
        return PermissionResult.ask("文件编辑需确认")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        path = resolve_path(ctx, args.get("file_path", ""))
        if not path.exists():
            return ValidationResult.fail(f"文件不存在: {path}")
        old = args.get("old_string", "")
        if not old:
            return ValidationResult.fail("old_string 不能为空")
        if args.get("new_string") is None:
            return ValidationResult.fail("new_string 不能为空")
        return ValidationResult.pass_()

    def get_path(self, args: dict[str, Any]) -> str:
        return args.get("file_path", "")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from agent.core.file_state import check_file_stale, record_file_write

        path = resolve_path(ctx, args["file_path"])
        old_string = args["old_string"]
        new_string = args["new_string"]

        # 冲突检测：文件被外部修改时拒绝编辑
        if check_file_stale(ctx, str(path)):
            return ToolResult.error(
                f"⚠ 文件已被外部修改: {path}\n"
                "文件内容可能已变化，基于旧内容编辑会覆盖外部改动。\n"
                "请先用 FileRead 重新读取文件，再尝试编辑。"
            )

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult.error(f"读取失败: {e}")

        occurrences = text.count(old_string)
        if occurrences == 0:
            return ToolResult.error(
                f"未找到 old_string。请检查内容是否完全匹配（含缩进、空行）。"
            )
        if occurrences > 1:
            return ToolResult.error(
                f"old_string 出现了 {occurrences} 次，不唯一。"
                "请在 old_string 中包含更多上下文使其唯一。"
            )

        new_text = text.replace(old_string, new_string, 1)
        try:
            path.write_text(new_text, encoding="utf-8")
        except Exception as e:
            return ToolResult.error(f"写入失败: {e}")

        # 编辑成功后更新缓存
        record_file_write(ctx, str(path))

        return ToolResult.ok(
            data=f"已编辑 {path}\n替换 {len(old_string)} -> {len(new_string)} 字符"
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and args.get("file_path"):
            return f"编辑 {args['file_path']}"
        return None
