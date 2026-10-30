"""工具编排器 —— 把模型发起的 tool_use 批量调度执行。

核心策略:
1. 并发安全分组: is_concurrency_safe=True 的工具可并行；False 的串行。
2. 权限校验: 每个工具调用前过 PermissionChecker，ASK 时通过 UI 问用户。
3. 失败隔离: 一个工具失败不影响其他工具（返回 is_error=True 的结果给 LLM）。
4. 中断响应: 检查 ctx.abort_event，被取消时停止后续调度。
5. W3.2 兑现层：出了本机就收不回的对外副作用（``Tool.is_irreversible``）先入
   延时窗口、不真执行，期间可 ``/undo cancel`` 撤回；本地可回滚的写操作照常
   执行但记进副作用账本，供 ``/undo`` 按本轮检查点回退。

返回: 每个 tool_use 对应一个 ToolResultContent，按 tool_use_id 对齐。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from agent.core.context import ToolContext
from agent.core.actions.effect_policy import (
    actions_config,
    is_irreversible_call,
    note_effect_id,
    turn_checkpoint_id,
)
from agent.core.error_recovery import ToolRecoveryExecutor
from agent.core.message import ToolResultContent, ToolUseContent
from agent.core.policy import policy_gate
from agent.core.policy.loop_detect import check_tool_loop
from agent.core.result import PermissionBehavior, PermissionResult, ToolResult
from agent.core.tool import Tool, ToolRegistry
from agent.core.tool_pairing import REASON_ORPHAN, make_placeholder_result
from agent.permissions import PermissionChecker

#: ``ctx.extra`` 里累计本回合权限拒绝的键。由宿主预先创建（与 ``effect_ids``
#: 同一套路：必须是同一个列表对象，否则编排器往里追加的项宿主读不到）。
#: 用途见 :func:`record_denial`。
EXTRA_DENIALS = "denials"


def record_denial(ctx: ToolContext, tool_name: str, reason: str) -> None:
    """把一次权限拒绝记进 ``ctx.extra["denials"]``（宿主据此判定“本回合受阻”）。

    为什么需要这个而不是让宿主去解析模型/子代理的文本汇报：无人值守的后台步骤
    被拦下时，子代理会如实汇报“需要授权”，但汇报是自然语言，拿关键词去匹配既
    会漏判也会误判。编排器这里是被拦下的**第一现场**，记一条结构化事实比事后
    猜文本可靠得多。

    只记工具名与原因，不记参数全文：参数可能含密钥/正文等敏感内容，而判断
    “需不需要授权”用不到它们。

    @author aceFelix
    """
    bucket = ctx.extra.get(EXTRA_DENIALS) if ctx.extra is not None else None
    if not isinstance(bucket, list):
        return  # 宿主没开这个容器就不记（主会话等路径不必为后台任务付这个代价）
    bucket.append({"tool": tool_name, "reason": (reason or "")[:200]})


def take_denials(ctx: ToolContext) -> list[dict[str, Any]]:
    """取出宿主登记的拒绝列表（没开容器时给空表，调用方据此判“未受阻”）。"""
    bucket = ctx.extra.get(EXTRA_DENIALS) if ctx.extra is not None else None
    return list(bucket) if isinstance(bucket, list) else []


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
        recovery_executor: ToolRecoveryExecutor | None = None,
    ) -> None:
        self._registry = registry
        self._checker = permission_checker
        self._max_concurrency = max_concurrency
        self._recovery = recovery_executor

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

            # 权限校验：五层管线结论后再过 W1 运行时层闸门
            # （授权租约→away 分流；DENY 不可放宽，fail-closed）
            # @author aceFelix
            perm = self._checker.check(tool, tu.input, ctx)
            perm = policy_gate(tool, tu.input, ctx, perm)
            # 空转循环检测：每次调用都记录签名（ASK/DENY 也记，保持窗口连续），
            # 但只把 ALLOW 强制升级 ASK 拦刹车（ASK 自有人工环节，DENY 不动）。
            # @author aceFelix
            loop_msg = check_tool_loop(tool.name, tu.input)
            if loop_msg and perm.behavior == PermissionBehavior.ALLOW:
                perm = PermissionResult.ask(loop_msg)
            decision = await self._resolve_permission(perm, tool, tu, ctx)
            if decision.behavior == PermissionBehavior.DENY:
                reason = decision.reason or "权限拒绝"
                # 受阻的第一现场登记：任务脊柱靠它把“被拦下”与“干完了”分开，
                # 绝不把权限拒绝当成一次正常完成报出去（见 record_denial）
                record_denial(ctx, tu.name, reason)
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

        # 3. 按输入顺序对齐输出（缺失项补占位结果，保证与输入等长）
        # 调度异常/中断可能让个别调用拿不到结果；若直接过滤掉，历史里就会留下
        # 悬空 tool_use，使该会话所有后续请求被 API 拒收。
        # 见 docs/fixlogs/dangling-tool-use-fix.md。
        # @author aceFelix
        results: list[ToolResultContent] = []
        for tu in tool_uses:
            content = results_by_id.get(tu.id)
            if content is None:
                content = make_placeholder_result(tu, reason=REASON_ORPHAN)
            results.append(content)

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

        # 通过 UI 问用户：异步宿主（serve 引擎循环）优先 ask_user_async，
        # 同步阻塞版会饿死宿主事件循环导致 answer_user 自死锁；
        # REPL 等同步宿主无 ask_user_async，回退同步版。
        # @author aceFelix
        question = self._format_ask(tool, tu, perm.reason)
        if hasattr(ctx.ui, "ask_user_async"):
            answer = await ctx.ui.ask_user_async(question)
        else:
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
        """执行单个工具调用，封装异常。

        S-02 改进：记录所有工具调用的审计日志。
        """
        import time as _time
        t0 = _time.time()
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

        # ---- W3.2 不可逆动作：先入延时窗口，本轮不真执行 ----
        # 位置刻意在权限判定与 W1 闸门之后、钩子之后：窗口只"延后"执行，
        # 不豁免任何一次确认；而钩子已经跑过，说明"允许做这件事"的所有判定
        # 都已成立，只是把"做"推迟到用户下一次能喊停的时机之前。
        # @author aceFelix
        held = self._maybe_hold_irreversible(tool, tu, effective_input, ctx)
        if held is not None:
            return held

        # ---- 文件级撤销：写前留底（2026-10）----
        # 位置苛求在「能不能做」全部定案之后、真正执行之前：再早会给被拒绝/被
        # 窗口拦下的调用白复制一份；再晚（执行后）原文已经没了，留底无从谈起。
        # 为什么放编排器而不是 FileWrite/FileEdit 自己：那样只能覆盖两个工具，
        # 将来新增的写类工具、带路径参数的 bash 工具都得再改一遍；这里是单点覆盖。
        # 留底失败一律降级为空串（辅助能力不得拖垮主流程）。
        # @author aceFelix
        write_snap = ""
        try:
            from agent.core.actions.file_effects import snapshot_before_write

            write_snap = snapshot_before_write(
                tool.name,
                effective_input,
                read_only=bool(tool.is_read_only(effective_input)),
                destructive=bool(tool.is_destructive(effective_input)),
                settings=getattr(ctx, "settings", None),
                workdir=str(getattr(ctx, "workdir", "") or ""),
            )
        except Exception:
            write_snap = ""

        # ---- 工具错误自愈 ----
        # 优先用 RecoveryExecutor 包装 call，失败时自动重试/降级/询问用户
        # 如果未配置 recovery 或自愈关闭，直接调用原始 call
        if self._recovery and self._recovery.is_enabled():
            recovery_result = await self._recovery.execute(
                tool_name=tool.name,
                call_fn=tool.call,
                args=effective_input,
                ctx=ctx,
                tool_is_read_only=tool.is_read_only(effective_input),
            )
            result = recovery_result.final_result
        else:
            try:
                result = await tool.call(effective_input, ctx)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                result = ToolResult.error(f"工具执行异常: {type(e).__name__}: {e}")

        # 序列化结果给 LLM
        data = result.data

        # ── S-02 审计日志 ──
        try:
            from agent.core.audit.tool_auditor import get_tool_auditor
            auditor = get_tool_auditor()
            duration_ms = (_time.time() - t0) * 1000
            auditor.log_call(
                tool_name=tool.name,
                args=dict(tu.input),
                permission_mode=str(ctx.permission_mode),
                duration_ms=duration_ms,
                success=not result.is_error,
                error_msg=str(result.data) if result.is_error else "",
            )
        except Exception:
            pass  # 审计失败不影响工具执行

        # ---- W3.2 副作用账本：把这次真实影响记成可执行的补偿计划 ----
        # 位置在审计之后、结果序列化之前：只记"确实执行完且未报错"的那些。
        # 失败的工具调用已经由工具自己把错误回给模型，再入账本会让 /undo
        # 面对一堆根本没发生的动作。
        # @author aceFelix
        self._record_effect(tool, tu, effective_input, ctx, result, write_snap=write_snap)

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

        # 把「这一步能反悔」写进回执：模型看得见才不会在写错之后只会道歉，
        # 用户也当场知道该敲什么命令，不用等出事再去翻 /undo list。
        # @author aceFelix
        if write_snap:
            try:
                from agent.core.actions.file_effects import describe_snapshot

                _hint = describe_snapshot(write_snap, getattr(ctx, "settings", None))
                if _hint:
                    content_str = f"{content_str}\n\n{_hint}"
            except Exception:
                pass

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

    # ---- W3.2 副作用账本与延时窗口 ----
    #
    # 纯判定与取值（读 [actions] 配置、判断调用是否不可逆、回填账本 id、取本轮
    # 检查点 cid）已搬到 ``agent/core/actions/effect_policy.py``：它们不依赖
    # 编排器状态，放在这里只会把主文件推过 800 行红线，也不方便单测直接打。
    # @author aceFelix

    def _maybe_hold_irreversible(
        self,
        tool: Tool,
        tu: ToolUseContent,
        args: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResultContent | None:
        """不可逆调用先入延时窗口；返回给模型的结果内容，None 表示不拦。

        为什么要返回一个正常结果而不能什么都不返：本轮历史里已经留下了这个
        tool_use，缺对应 tool_result 会让该会话之后所有请求被 API 拒收（见
        docs/fixlogs/dangling-tool-use-fix.md）。

        为什么文案里必须写死「不要绕道」：实机测过（2026-10-21），只告诉模型
        「已排队、稍后自动执行」时，它会自己判断「换个工具效果一样」，转头用
        Bash/curl 把被拦下的那件事真做掉——窗口拦的是一条工具调用，不拦等价
        路径就等于没拦，而且用户以为拦住了（详见
        docs/fixlogs/w3-delay-window-bypass-fix.md）。
        @author aceFelix
        """
        cfg = actions_config(ctx)
        if not cfg["enabled"] or cfg["window_s"] <= 0:
            return None  # 窗口关闭：执行照常，由 _record_effect 记一笔"不可撤销"
        if not is_irreversible_call(tool, args, cfg["extra_tools"]):
            return None

        import time as _time

        from agent.core.actions import (
            STATE_HELD, UNDO_NONE, EffectRecord, get_journal, get_window,
        )

        preview = ""
        try:
            preview = tool.describe_irreversible(args) or tool.name
        except Exception:
            preview = tool.name
        window_s = cfg["window_s"]
        journal = get_journal()
        rec = EffectRecord(
            tool=tool.name,
            args=dict(args),
            preview=preview,
            reversible=False,
            # 外发动作的补偿计划写不出真实回滚，只能如实标"不可撤销"：窗口到点之后
            # 连补偿的说法都不能编，否则 /undo 会给出一个假的"已撤销"。
            undo_spec={"kind": UNDO_NONE, "reason": "该动作已离开本机，无法撤回"},
            state=STATE_HELD,
            due_at=_time.time() + window_s,
            workdir=ctx.workdir,
            session_id=str((ctx.extra or {}).get("session_name", "") or ""),
            permission_mode=str(ctx.permission_mode),
            task_id=str((ctx.extra or {}).get("task_id", "") or ""),
        )
        if not journal.record(rec):
            return None  # 账本写不进就退回原行为：无人能查的"延后执行"比立即执行更糟

        loop = asyncio.get_running_loop()
        get_window().schedule(
            rec.id, window_s,
            lambda: loop.create_task(self._fire_irreversible(rec, ctx)),
            loop=loop,
            # label 是给权限层看的：窗口未结案时它要解释“为什么这条命令突然
            # 不自动放行”，不能为了取名字去重读整本账（见 tools/bash.py）
            label=preview,
        )
        note_effect_id(ctx, rec.id)
        text = (
            f"已排入 {window_s:.0f} 秒待发窗口，尚未执行：{preview}。\n"
            f"期间输入 /undo cancel 可撤回；到点自动执行，执行结果另行播报。\n"
            # 这段「不要绕道」是给模型看的行为约束，缺它就等于把拦截留成摆设：
            # 模型换一条等价路径就能把用户以为拦住的外发动作做掉。
            "【不要绕道】窗口结案前不得改用其它工具或命令行达成同一目的"
            "（如 Bash/curl、脚本、浏览器模拟发送或抓取）——那样用户以为已被拦住的"
            "动作会照常发生。请如实告知用户已排入待发窗口并等待；确实不能等就说明"
            "受阻原因，由用户决定，不要自行找替代通路。"
        )
        if ctx.ui:
            try:
                ctx.ui.info(f"待发窗口已开启（{window_s:.0f}s）：{preview}")
            except Exception:
                pass
            # 也要给 UI 一个收尾的 tool_result：上面的 tool_use 已经开了一张工具卡片，
            # 不给它结果就会永远转圈（桌面/工作台都靠 tool_result 结案）
            try:
                ctx.ui.tool_result(tool.name, tu.id, text, is_error=False)
            except Exception:
                pass
        return ToolResultContent(tool_use_id=tu.id, content=text, is_error=False)

    async def _fire_irreversible(self, rec: Any, ctx: ToolContext) -> None:
        """延时窗口到点：按账本里冻结的那一份参数真执行。

        三个刻意的设计：
        1. 不再走权限管线——用户在窗口前已经确认过一次，重走一遍会因这里没有
           可用 UI 而 fail-closed 拒掉自己刚批准的动作（无 UI 时 ASK 直接 DENY）；
        2. 用的是账本里的 args 而不是对方的 tool_use——模型后续改口也不该影响
           已经排队的动作，这是"冻结参数"的全部意义；
        3. 用户按了停止就不再发——中断意味着人还在且反悔了，此时自动执行外发动作
           是最不该发生的事。
        @author aceFelix
        """
        from agent.core.actions import STATE_APPLIED, STATE_CANCELLED, STATE_FAILED, STATE_HELD, get_journal

        journal = get_journal()
        current = journal.get(rec.id)
        if current is None or current.state != STATE_HELD:
            return  # 已被 /undo cancel 撤走，或被上一轮重置
        if ctx.abort_event.is_set():
            journal.set_state(rec.id, STATE_CANCELLED, error="用户已中断本轮，预约动作按取消处理")
            return
        tool = self._registry.get(rec.tool)
        if tool is None:
            journal.set_state(rec.id, STATE_FAILED, error="工具已不在注册表中，无从执行")
            return

        # 一个最小上下文：ui 置空（本轮 UI 的归属轮次已结束，工具不该往已收尾的对话里
        # 写气泡），messages 置空（工具约定只读不写），extra 浅拷贝保留工具自己需要的
        # 运行态（如已发现的工具名、工作目录相关缓存）。
        fire_ctx = ToolContext(
            workdir=rec.workdir or ctx.workdir,
            messages=[],
            abort_event=asyncio.Event(),
            permission_mode=rec.permission_mode or str(ctx.permission_mode),
            ui=None,
            extra=dict(ctx.extra or {}),
            settings=ctx.settings,
        )
        note: str
        try:
            result = await tool.call(dict(rec.args), fire_ctx)
        except Exception as e:  # noqa: BLE001 - 定时器回调里绝不能冒泡
            journal.set_state(rec.id, STATE_FAILED, error=f"{type(e).__name__}: {e}"[:200])
            note = f"预约动作执行失败：{rec.label} — {type(e).__name__}"
        else:
            if result.is_error:
                detail = str(result.data)[:160]
                journal.set_state(rec.id, STATE_FAILED, error=detail)
                note = f"预约动作执行失败：{rec.label} — {detail}"
            else:
                journal.set_state(rec.id, STATE_APPLIED)
                note = f"预约动作已执行：{rec.label}"
        # 反馈只在"说得出"的时候说：原轮次的 UI 在 REPL/桌面下通常还活着，能收到；
        # 收不到就算了，动作本身已经执行完，报告缺失不该再抛错打断事件循环。
        try:
            if ctx.ui:
                ctx.ui.info(note)
        except Exception:
            pass

    def _record_effect(
        self,
        tool: Tool,
        tu: ToolUseContent,
        args: dict[str, Any],
        ctx: ToolContext,
        result: ToolResult,
        *,
        write_snap: str = "",
    ) -> None:
        """把一次已执行的副作用记进账本。

        补偿计划的**优先级**（2026-10 重排）：
        1. 外发动作（不可逆）→ 如实标不可撤销，不留任何假希望；
        2. 有写前留底 → ``UNDO_FILES``，只撤这次碰过的那几个文件；
        3. 都没有但本轮有检查点 → ``UNDO_CHECKPOINT``，整轮回滚；
        4. 连检查点也没有 → **不记**（记不出可执行补偿的条目会让 /undo
           列出一堆撤不动的行）。

        第 2 条是本次新增的主路径：此前本地写一律退到整轮检查点，用户改错
        一个文件要连带退掉同一轮里所有正确的改动。

        Args:
            write_snap: 执行前留底的快照 ID（空串=没留底）。
        @author aceFelix
        """
        cfg = actions_config(ctx)
        if not cfg["enabled"] or getattr(result, "is_error", False):
            return
        try:
            irreversible = is_irreversible_call(tool, args, cfg["extra_tools"])
            destructive = bool(tool.is_destructive(args))
        except Exception:
            return
        if not irreversible and not (destructive and cfg["track_local"]):
            return

        from agent.core.actions import (
            STATE_APPLIED, UNDO_CHECKPOINT, UNDO_FILES, UNDO_NONE, EffectRecord, get_journal,
        )

        preview = ""
        try:
            preview = tool.activity_description(args) or tool.user_facing_name(args)
        except Exception:
            preview = tool.name

        undo_spec: dict[str, Any]
        reversible = False
        if irreversible:
            # 窗口关闭（window_s<=0）才会走到这里：确实是执行完了的外发动作。
            # 即便同时留了底也不走文件恢复 —— 外发的那一半收不回来，
            # 只恢复本地文件却回报“已撤销”反而是误报。
            undo_spec = {"kind": UNDO_NONE, "reason": "该动作已离开本机，无法撤回"}
        elif write_snap:
            undo_spec = {"kind": UNDO_FILES, "snap_id": write_snap}
            reversible = True
        else:
            cid = turn_checkpoint_id(ctx)
            if not cid:
                return
            undo_spec = {"kind": UNDO_CHECKPOINT, "cid": cid, "workdir": ctx.workdir}
            reversible = True

        rec = EffectRecord(
            tool=tool.name,
            args=dict(args),
            preview=preview,
            reversible=reversible,
            undo_spec=undo_spec,
            state=STATE_APPLIED,
            workdir=ctx.workdir,
            session_id=str((ctx.extra or {}).get("session_name", "") or ""),
            permission_mode=str(ctx.permission_mode),
            task_id=str((ctx.extra or {}).get("task_id", "") or ""),
        )
        if get_journal().record(rec):
            note_effect_id(ctx, rec.id)

    @staticmethod
    def _turn_checkpoint_id(ctx: ToolContext) -> str:
        """取本轮的文件检查点 ID。

        判定口径本身已经搬到 ``actions/effect_policy.turn_checkpoint_id``，
        这里只做转发：测试与外部已有对 ``ToolOrchestrator._turn_checkpoint_id``
        的直接调用，没必要因为搬家把它们全改一遍。
        @author aceFelix
        """
        return turn_checkpoint_id(ctx)



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
