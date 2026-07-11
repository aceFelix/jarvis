"""Bash 工具 —— 执行 shell 命令。

对应原项目 tools/BashTool/。这是权限系统最关心的工具:
- 默认 ASK（无 allow 规则时必须确认）
- 命令分类（readonly/dangerous/unknown）由 permissions/shell_classifier.py 做
- prepare_permission_matcher 解析命令前缀，让规则 "Bash(git *)" 能命中

v0.1 用 asyncio subprocess 执行，统一 stdout/stderr 合并输出，有超时。
"""

from __future__ import annotations

import asyncio
import platform
from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior, PermissionResult, ToolResult
from agent.core.tool import JSONSchema, PermissionMatcher, Tool
from agent.permissions.shell_classifier import get_command_head


class BashTool(Tool):
    name = "Bash"
    description = (
        "执行 shell 命令并返回输出。命令在工作目录下执行。"
        "默认会询问用户确认；只读命令（ls/cat/grep 等）和用户配置了 allow 规则的命令会自动放行。"
        "有超时保护（默认 120 秒）。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout": {
                "type": "integer",
                "description": "超时秒数（默认 120，最大 600）",
                "minimum": 1,
                "maximum": 600,
            },
        },
        "required": ["command"],
    }
    max_result_chars = 20_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        # 用分类器判断（readonly 才算只读）
        from agent.permissions.shell_classifier import classify
        return classify(args.get("command", "")) == "readonly"

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        # 只读命令可并行；写类命令保守起见不可并行
        return self.is_read_only(args)

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        # 工具特判: 用分类器快速决定
        from agent.permissions.shell_classifier import classify
        cmd = args.get("command", "")
        kind = classify(cmd)
        if kind == "dangerous":
            return PermissionResult.deny(f"命令匹配危险模式: {cmd}")
        if kind == "readonly":
            return PermissionResult.allow("只读命令自动放行")
        # unknown 一律 ASK（fail-closed）
        return PermissionResult.ask(f"需要确认命令: {cmd}")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext):
        from agent.core.result import ValidationResult
        if not args.get("command", "").strip():
            return ValidationResult.fail("command 不能为空")
        return ValidationResult.pass_()

    def prepare_permission_matcher(
        self, args: dict[str, Any]
    ) -> PermissionMatcher | None:
        """解析命令头，让 "Bash(git *)" 这类规则能命中。"""
        cmd = args.get("command", "")
        head = get_command_head(cmd)
        # targets 同时给完整命令和命令头，匹配更宽松
        return PermissionMatcher(tool_name="Bash", targets=[cmd, head])

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = args["command"]
        timeout = min(600, max(1, int(args.get("timeout", 120))))

        # Windows 用 cmd，其他用 bash/sh
        is_win = platform.system() == "Windows"
        if is_win:
            shell_args = ["cmd", "/c", command]
        else:
            shell_args = ["/bin/bash", "-c", command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *shell_args,
                cwd=ctx.workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            return ToolResult.error(f"找不到 shell: {e}")

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return ToolResult.error(f"命令超时（{timeout}秒）: {command}")

        out = stdout.decode("utf-8", errors="replace") if stdout else ""
        err = stderr.decode("utf-8", errors="replace") if stderr else ""
        code = proc.returncode

        parts = []
        if out:
            parts.append(out.rstrip())
        if err:
            parts.append(f"[stderr]\n{err.rstrip()}")
        body = "\n\n".join(parts) if parts else "(无输出)"

        header = f"[exit={code} | cwd={ctx.workdir}]\n"
        if code != 0:
            return ToolResult(data=header + body, is_error=True)
        return ToolResult.ok(data=header + body)

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and args.get("command"):
            cmd = args["command"]
            return f"运行 {cmd[:60]}{'...' if len(cmd) > 60 else ''}"
        return None
