"""工具编排器 —— 把模型发起的 tool_use 批量调度执行。

核心策略:
1. 并发安全分组: is_concurrency_safe=True 的工具可并行；False 的串行。
2. 权限校验: 每个工具调用前过 PermissionChecker，ASK 时通过 UI 问用户。
3. 失败隔离: 一个工具失败不影响其他工具（返回 is_error=True 的结果给 LLM）。
4. 中断响应: 检查 ctx.abort_event，被取消时停止后续调度。

返回: 每个 tool_use 对应一个 ToolResultContent，按 tool_use_id 对齐。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from agent.core.context import ToolContext
from agent.core.message import ToolResultContent, ToolUseContent
from agent.core.result import PermissionBehavior, PermissionResult
from agent.core.tool import Tool, ToolRegistry
from agent.permissions import PermissionChecker


@dataclass
class _PendingCall:
    """一个待执行的工具调用。"""

    tool_use: ToolUseContent
    tool: Tool


class ToolOrchestrator:
    """工具编排器。

    持有 ToolRegistry + PermissionChecker，负责把模型返回的 tool_use 批量调度执行。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        permission_checker: PermissionChecker,
        max_concurrency: int = 5,
    ) -> None:
        self._registry = registry
        self._checker = permission_checker
        self._max_concurrency = max_concurrency

    async def execute_calls(
        self,
        tool_uses: list[ToolUseContent],
        ctx: ToolContext,
    ) -> list[ToolResultContent]:
        """执行一批 tool_use，返回对应的 tool_result 列表（顺序与输入一致）。

        流程:
        1. 校验工具存在、权限通过
        2. 按并发安全分两组: safe（并行）+ unsafe（串行）
        3. 执行，收集结果
        4. 结果落回 ToolResultContent（含 is_error 标记）
        """
        if not tool_uses:
            return []

        # 1. 解析 + 权限校验，产出"待执行/被拒"两类
        pending: list[_PendingCall] = []
        results_by_id: dict[str, ToolResultContent] = {}

        for tu in tool_uses:
            tool = self._registry.get(tu.name)
            if tool is None:
                results_by_id[tu.id] = ToolResultContent(
                    tool_use_id=tu.id,
                    content=f"错误: 未知工具 '{tu.name}'",
                    is_error=True,
                )
                if ctx.ui:
                    ctx.ui.warn(f"模型调用了未知工具: {tu.name}")
                continue

            # 权限校验
            perm = self._checker.check(tool, tu.input, ctx)
            decision = await self._resolve_permission(perm, tool, tu, ctx)
            if decision.behavior == PermissionBehavior.DENY:
                reason = decision.reason or "权限拒绝"
                results_by_id[tu.id] = ToolResultContent(
                    tool_use_id=tu.id,
                    content=f"权限拒绝: {reason}",
                    is_error=True,
                )
                if ctx.ui:
                    ctx.ui.warn(f"拒绝执行 {tu.name}: {reason}")
                continue

            pending.append(_PendingCall(tool_use=tu, tool=tool))

        # 2. 执行待执行项
        if pending:
            executed = await self._execute_pending(pending, ctx)
            for tu_id, content in executed.items():
                results_by_id[tu_id] = content

        # 3. 按输入顺序对齐输出
        results = [results_by_id[tu.id] for tu in tool_uses if tu.id in results_by_id]

        # 4. 记录文件访问（供压缩后回灌）
        _track_file_accesses(tool_uses, ctx)

        return results

    async def _resolve_permission(
        self,
        perm: PermissionResult,
        tool: Tool,
        tu: ToolUseContent,
        ctx: ToolContext,
    ) -> PermissionResult:
        """处理权限结果: ALLOW/DENY 直接返回，ASK 走 UI 问用户。"""
        if perm.behavior != PermissionBehavior.ASK:
            return perm
        if not ctx.ui:
            # 无 UI 又需要询问，fail-closed 拒绝
            return PermissionResult.deny("需要用户确认但当前环境无 UI")

        # 通过 UI 问用户
        question = self._format_ask(tool, tu, perm.reason)
        answer = ctx.ui.ask_user(question)
        normalized = answer.strip().lower()
        if normalized in ("y", "yes", "允许", "好", "确认"):
            return PermissionResult.allow("用户确认")
        if normalized in ("a", "always", "总是"):
            # v0.1: 不持久化到规则集，仅会话内放行
            return PermissionResult.allow("用户选择总是允许（本会话）")
        return PermissionResult.deny("用户拒绝")

    @staticmethod
    def _format_ask(tool: Tool, tu: ToolUseContent, reason: str | None) -> str:
        import json

        try:
            args_str = json.dumps(tu.input, ensure_ascii=False, indent=2)
        except Exception:
            args_str = str(tu.input)
        header = f"工具 {tu.name} 请求执行"
        if reason:
            header += f"（{reason}）"
        return (
            f"{header}:\n{args_str}\n\n"
            "输入 [y] 允许 / [a] 总是允许 / 其他键拒绝: "
        )

    async def _execute_pending(
        self,
        pending: list[_PendingCall],
        ctx: ToolContext,
    ) -> dict[str, ToolResultContent]:
        """执行待执行项: 并发安全的并行，不安全的串行。"""
        results: dict[str, ToolResultContent] = {}

        # 分组
        safe = [p for p in pending if p.tool.is_concurrency_safe(p.tool_use.input)]
        unsafe = [p for p in pending if not p.tool.is_concurrency_safe(p.tool_use.input)]

        # 通知 UI: 工具调用开始
        if ctx.ui:
            for p in pending:
                ctx.ui.tool_use(p.tool.name, p.tool_use.input, p.tool_use.id)

        # 串行组: 顺序执行（避免竞争）
        for p in unsafe:
            if ctx.abort_event.is_set():
                results[p.tool_use.id] = ToolResultContent(
                    tool_use_id=p.tool_use.id,
                    content="已取消（用户中断）",
                    is_error=True,
                )
                continue
            content = await self._run_one(p, ctx)
            results[p.tool_use.id] = content

        # 并行组: 用信号量限流
        if safe:
            sem = asyncio.Semaphore(self._max_concurrency)

            async def run_safe(p: _PendingCall) -> tuple[str, ToolResultContent]:
                async with sem:
                    if ctx.abort_event.is_set():
                        return (
                            p.tool_use.id,
                            ToolResultContent(
                                tool_use_id=p.tool_use.id,
                                content="已取消（用户中断）",
                                is_error=True,
                            ),
                        )
                    return p.tool_use.id, await self._run_one(p, ctx)

            gathered = await asyncio.gather(
                *(run_safe(p) for p in safe), return_exceptions=False
            )
            for tu_id, content in gathered:
                results[tu_id] = content

        return results

    async def _run_one(self, p: _PendingCall, ctx: ToolContext) -> ToolResultContent:
        """执行单个工具调用，封装异常。"""
        tool = p.tool
        tu = p.tool_use

        # ---- Hook: tool_before ----
        # 钩子可以拒绝执行（返回 allow=False）或修改输入（返回 modify_input）
        try:
            from agent.core.hooks import get_hooks, HookEvent
            hook_payload = {
                "tool_name": tool.name,
                "tool_input": dict(tu.input),
                "tool_use_id": tu.id,
                "ctx": ctx,
            }
            hook_result = await get_hooks().trigger(HookEvent.TOOL_BEFORE, hook_payload)
            if not hook_result.allow:
                reason = hook_result.reason or "钩子拒绝执行"
                if ctx.ui:
                    ctx.ui.warn(f"钩子拒绝执行 {tool.name}: {reason}")
                return ToolResultContent(
                    tool_use_id=tu.id,
                    content=f"钩子拒绝: {reason}",
                    is_error=True,
                )
            # 钩子可修改输入
            effective_input = hook_result.modify_input or tu.input
        except Exception:
            effective_input = tu.input  # 钩子系统异常时不阻塞工具执行

        try:
            result = await tool.call(effective_input, ctx)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 工具抛异常: 转成 is_error 的 tool_result，不让单点失败炸掉整个批次
            # ---- Hook: error ----
            try:
                from agent.core.hooks import get_hooks, HookEvent
                await get_hooks().trigger(HookEvent.ERROR, {
                    "tool_name": tool.name,
                    "error": e,
                    "tool_use_id": tu.id,
                })
            except Exception:
                pass
            return ToolResultContent(
                tool_use_id=tu.id,
                content=f"工具执行异常: {type(e).__name__}: {e}",
                is_error=True,
            )

        # 序列化结果给 LLM
        data = result.data
        if isinstance(data, str):
            content_str = data
        elif data is None:
            content_str = "(无输出)"
        else:
            import json

            try:
                content_str = json.dumps(data, ensure_ascii=False, indent=2, default=str)
            except Exception:
                content_str = str(data)

        # 超长截断 / 落盘持久化
        max_chars = getattr(tool, "max_result_chars", 20_000)
        if len(content_str) > max_chars:
            # Phase 2: 超大结果落盘，模型只收预览
            try:
                persisted_path = _persist_result(content_str, tu.name, tu.id, ctx)
            except Exception:
                persisted_path = None

            preview = 500
            content_str = (
                content_str[:preview]
                + f"\n\n... [结果超长，已截断。完整结果 {len(content_str)} 字符"
            )
            if persisted_path:
                content_str += f"，已保存到 {persisted_path}"
            content_str += (
                "] ...\n\n"
                + content_str[-preview:]
            )

        if ctx.ui:
            ctx.ui.tool_result(tool.name, tu.id, content_str, is_error=result.is_error)

        # ---- Hook: tool_after ----
        try:
            from agent.core.hooks import get_hooks, HookEvent
            await get_hooks().trigger(HookEvent.TOOL_AFTER, {
                "tool_name": tool.name,
                "tool_input": dict(tu.input),
                "tool_use_id": tu.id,
                "result": content_str,
                "is_error": result.is_error,
            })
        except Exception:
            pass

        # ---- Hook: file_changed ----
        # 文件类工具执行后触发文件变更通知
        if tool.name in _FILE_TOOLS or tool.name.startswith("file_"):
            try:
                from agent.core.hooks import get_hooks, HookEvent
                raw_path = tu.input.get("file_path") or tu.input.get("path") or tu.input.get("filePath") or ""
                if raw_path:
                    await get_hooks().trigger(HookEvent.FILE_CHANGED, {
                        "tool_name": tool.name,
                        "path": str(raw_path),
                        "operation": "edit" if "edit" in tool.name.lower() else (
                            "write" if "write" in tool.name.lower() else "read"
                        ),
                    })
            except Exception:
                pass

        return ToolResultContent(
            tool_use_id=tu.id,
            content=content_str,
            is_error=result.is_error,
            # 多模态: 透传工具产出的图片（如 ScreenShot 截图）。
            # 图片走独立通道，不受上面 content_str 的超长截断影响。
            images=result.images,
        )


# ---- 文件访问追踪（供压缩后文件回灌）----

_FILE_TOOLS = {"FileRead", "FileWrite", "FileEdit", "read_file", "write_file", "edit_file"}


# ---- 工具结果持久化（Phase 2）----

def _persist_result(content: str, tool_name: str, tool_use_id: str, ctx) -> str | None:
    """将超大工具结果落盘，返回文件路径。"""
    import os
    import time
    jarvis_dir = os.path.join(ctx.workdir, ".jarvis")
    os.makedirs(jarvis_dir, exist_ok=True)
    safe_name = tool_name.replace("/", "_").replace("\\", "_")
    filename = f"result_{safe_name}_{tool_use_id}_{int(time.time())}.txt"
    filepath = os.path.join(jarvis_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath

def _track_file_accesses(tool_uses: list, ctx) -> None:
    """记录本轮工具调用中涉及的文件路径。"""
    from agent.core.memory.compactor import track_file_access
    for tu in tool_uses:
        name = getattr(tu, 'name', '')
        if name not in _FILE_TOOLS and not name.startswith("file_"):
            continue
        inp = getattr(tu, 'input', {}) or {}
        raw_path = inp.get("file_path") or inp.get("path") or inp.get("filePath") or ""
        if not raw_path:
            continue
        atype = "read" if "read" in name.lower() else ("edit" if "edit" in name.lower() else "write")
        track_file_access(ctx, str(raw_path), atype)
