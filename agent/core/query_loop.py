"""Agent 主循环 —— Agent 的大脑。

（含 3 种压缩、context collapse、token budget、stop hooks、SDK 适配……），
v0.1 实现最核心的闭环:

    user 输入 -> LLM 流 -> (有 tool_use? -> 执行工具 -> 回灌 -> 再调 LLM) -> 结束

核心循环逻辑:
    while True:
        events = await provider.stream(messages, tools)
        assistant_msg = accumulate(events)         # 文本 + tool_use
        messages.append(assistant_msg)
        if not assistant_msg.has_tool_use:
            break                                   # 模型说完了
        tool_results = await orchestrator.execute(assistant_msg.tool_uses)
        messages.append(user(tool_results))        # 工具结果作为新 user 消息
        # 继续下一轮，让模型看到工具结果

防护:
- max_iterations: 防止无限循环（默认 25）
- abort_event: 用户中断
- 单轮失败不炸主循环，错误回灌给模型让它自我修正
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from agent.core.context import ToolContext
from agent.core.message import (
    ContentBlock,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
)
from agent.core.orchestrator import ToolOrchestrator
from agent.core.tool import ToolRegistry
from agent.core.memory.compactor import (
    DEFAULT_KEEP_RECENT,
    DEFAULT_THRESHOLD_TOKENS,
    compact_messages,
    estimate_tokens,
    restore_recent_files,
    should_compact,
    update_session_memory,
)
from agent.llm.base import (
    LLMEvent,
    LLMProvider,
    Stop,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolCallEnd,
    ToolDef,
    Usage,
)
from agent.llm.base import ProviderError


@dataclass
class QueryStats:
    """单次 query 的统计信息。"""

    iterations: int = 0
    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    stopped_reason: str = "stop"


class QueryLoop:
    """Agent 主循环。

    用法::

        loop = QueryLoop(provider=..., registry=..., orchestrator=..., system=...)
        await loop.run(user_text, ctx)
    """

    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        orchestrator: ToolOrchestrator,
        *,
        system: str = "",
        model: str | None = None,
        max_iterations: int = 25,
        max_tokens: int = 4096,
        temperature: float | None = None,
        enable_compaction: bool = True,
        compaction_threshold: int = DEFAULT_THRESHOLD_TOKENS,
        keep_recent_messages: int = DEFAULT_KEEP_RECENT,
        vendor_fallback: str = "",
        custom_models: dict | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._orchestrator = orchestrator
        self._system = system
        self._model = model or provider.default_model
        self._max_iterations = max_iterations
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._enable_compaction = enable_compaction
        self._compaction_threshold = compaction_threshold
        self._keep_recent_messages = keep_recent_messages
        self._vendor_fallback = vendor_fallback
        self._custom_models = custom_models or {}
        self._failover_tried = False
        self._task_budget_remaining: int | None = None  # 任务预算追踪（Phase 2）
        # 思考模式覆盖标志：voice_loop 通过 set_thinking_enabled() 设置后，
        # _try_failover 重建 provider 时会把这个状态同步到新 provider，
        # 避免故障转移后思考模式被意外恢复（语音模式下必须保持关闭）。
        # None 表示不强制，使用 provider 默认状态。
        self._thinking_override: bool | None = None

    def set_thinking_enabled(self, enabled: bool | None) -> None:
        """统一开关思考模式，同时同步到当前 provider。

        enabled=None 表示清除覆盖（恢复 provider 默认状态）。
        enabled=True/False 会同时设置 provider 并记录到 _thinking_override，
        这样 _try_failover 重建 provider 后能自动同步状态。
        """
        self._thinking_override = enabled
        if enabled is not None:
            self._provider.set_thinking_enabled(enabled)

    def is_thinking_enabled(self) -> bool | None:
        """返回思考模式覆盖状态。None 表示未覆盖（使用 provider 默认）。"""
        if self._thinking_override is not None:
            return self._thinking_override
        return self._provider.is_thinking_enabled()

    async def compact_now(self, ctx: ToolContext) -> bool:
        """手动触发一次上下文压缩。成功返回 True，跳过/失败返回 False。"""
        if not self._enable_compaction:
            return False
        try:
            result = await compact_messages(
                provider=self._provider,
                model=self._model,
                messages=ctx.messages,
                keep_recent=self._keep_recent_messages,
                on_progress=ctx.ui.info if ctx.ui else None,
                task_budget_remaining=self._task_budget_remaining,
            )
            if result.messages_summarized == 0:
                return False
            ctx.messages[:] = result.new_messages
            return True
        except Exception as e:
            if ctx.ui:
                ctx.ui.warn(f"上下文压缩失败，继续使用完整历史: {e}")
            return False

    async def run(
        self,
        user_text: str,
        ctx: ToolContext,
        images: list[ImageContent] | None = None,
    ) -> QueryStats:
        """跑一轮对话（从用户消息开始，直到模型不再调用工具）。

        会修改 ctx.messages（追加 assistant/user 消息）。
        传入的 images 会作为 user message 的 image content block 一并发给模型。
        """
        stats = QueryStats()

        # ---- Hook: user_prompt ----
        # 钩子可以修改用户输入（modify_input 为字符串则替换）
        try:
            from agent.core.hooks import get_hooks, HookEvent
            hook_result = await get_hooks().trigger(HookEvent.USER_PROMPT, {
                "text": user_text,
                "ctx": ctx,
            })
            if hook_result.modify_input is not None and isinstance(hook_result.modify_input, str):
                user_text = hook_result.modify_input
        except Exception:
            pass

        # 追加用户消息（文本 + 可选图片）
        content_blocks: list[ContentBlock] = [TextContent(text=user_text)]
        if images:
            content_blocks.extend(images)
        ctx.messages.append(Message(role="user", content=content_blocks))

        # 动态水位线压缩：根据当前 token 水位应用不同策略
        if self._enable_compaction:
            tokens = estimate_tokens(ctx.messages)
            water = tokens / self._compaction_threshold if self._compaction_threshold else 0

            # 水位 30% → 激进压缩（只保留最近 2 条）
            if water >= 0.3 and len(ctx.messages) > 2:
                try:
                    result = await compact_messages(
                        provider=self._provider, model=self._model,
                        messages=ctx.messages, keep_recent=2,
                        on_progress=ctx.ui.info if ctx.ui else None,
                        task_budget_remaining=self._task_budget_remaining,
                    )
                    if result.messages_summarized > 0:
                        ctx.messages[:] = result.new_messages
                        _restored = restore_recent_files(ctx)
                        _mem = update_session_memory(ctx.workdir, result)
                        if ctx.ui: ctx.ui.info(f"激进压缩完成（水位 {water:.0%}，保留 2 条，回灌 {_restored} 文件）")
                except Exception as e:
                    if ctx.ui: ctx.ui.warn(f"压缩失败: {e}")

            # 水位 60% → 工具结果折叠（旧 tool result 缩成一行）
            elif water >= 0.6:
                _collapse_old_tool_results(ctx.messages, keep_recent=4)
                if ctx.ui and ctx.verbose: ctx.ui.info(f"工具结果折叠完成（水位 {water:.0%}）")

            # 水位 90% → 标准摘要压缩
            elif water >= 0.8:
                try:
                    result = await compact_messages(
                        provider=self._provider, model=self._model,
                        messages=ctx.messages, keep_recent=self._keep_recent_messages,
                        on_progress=ctx.ui.info if ctx.ui else None,
                        task_budget_remaining=self._task_budget_remaining,
                    )
                    if result.messages_summarized > 0:
                        ctx.messages[:] = result.new_messages
                        _restored = restore_recent_files(ctx)
                        _mem = update_session_memory(ctx.workdir, result)
                        if ctx.ui: ctx.ui.info(f"上下文压缩完成（水位 {water:.0%}，回灌 {_restored} 文件）")
                except Exception as e:
                    if ctx.ui: ctx.ui.warn(f"压缩失败: {e}")

        for iteration in range(self._max_iterations):
            if ctx.abort_event.is_set():
                stats.stopped_reason = "aborted"
                break

            stats.iterations = iteration + 1

            try:
                assistant_msg, stop_event = await self._stream_once(ctx)
            except ProviderError as e:
                # 反应式压缩：API 报 context too long → 自动压缩后重试
                error_str = str(e).lower()
                if self._enable_compaction and any(
                    kw in error_str for kw in ("prompt_too_long", "context_length", "reduce length", "too long")
                ):
                    try:
                        result = await compact_messages(
                            provider=self._provider, model=self._model,
                            messages=ctx.messages, keep_recent=self._keep_recent_messages,
                            on_progress=ctx.ui.info if ctx.ui else None,
                            task_budget_remaining=self._task_budget_remaining,
                        )
                        if result.messages_summarized > 0:
                            ctx.messages[:] = result.new_messages
                            _restored = restore_recent_files(ctx)
                            if ctx.ui:
                                ctx.ui.warn(
                                    f"上下文过长，已自动压缩后重试"
                                    f"（{result.pre_compact_tokens}→{result.post_compact_tokens} tokens，回灌{_restored}文件）"
                                )
                            continue  # 重试本轮
                    except Exception as compress_err:
                        if ctx.ui:
                            ctx.ui.error(f"反应式压缩失败: {compress_err}")
                # Provider 故障转移：尝试切到备选厂商
                if self._try_failover():
                    if ctx.ui:
                        ctx.ui.warn(f"主 provider 失败，已切到备选厂商: {e}")
                    continue  # 重试本轮

                # LLM 错误: 通知并结束（不回灌错误给 LLM，避免循环）
                if ctx.ui:
                    ctx.ui.error(f"LLM 调用失败: {e}")
                stats.stopped_reason = "provider_error"
                break

            ctx.messages.append(assistant_msg)
            if isinstance(stop_event, Stop):
                stats.usage = stop_event.usage

            # 输出截断恢复：stop_reason="length" → 自动续写
            # 模型生成大文件时可能被 max_tokens 截断，工具调用 JSON 不完整。
            # 检测到后追加"请继续"消息，让模型从中断处续写。
            if isinstance(stop_event, Stop) and stop_event.reason == "length":
                if ctx.ui:
                    ctx.ui.warn("⚠ 输出被截断（max_tokens），自动续写...")
                ctx.messages.append(Message(
                    role="user",
                    content=[TextContent(text="[输出被截断，请从中断处继续，不要重复已输出的内容]")],
                ))
                continue

            tool_uses = assistant_msg.get_tool_uses()
            if not tool_uses:
                # 模型输出纯文本，本轮结束
                stats.stopped_reason = stop_event.reason if isinstance(stop_event, Stop) else "stop"
                break

            # 执行工具
            stats.tool_calls += len(tool_uses)
            try:
                tool_results = await self._orchestrator.execute_calls(tool_uses, ctx)
            except asyncio.CancelledError:
                stats.stopped_reason = "aborted"
                raise

            # 把工具结果作为新 user 消息回灌
            ctx.messages.append(
                Message(role="user", content=list(tool_results))
            )
            # 图片淘汰：已看过的旧截图替换为文字摘要，节省 token
            _evict_old_images(ctx.messages)
            
            # Phase 1: 多 Agent 团队——自动检查队友邮箱
            # 队友 idle/finished 后通过 mailbox 通知 leader，
            # 这里自动读取邮箱并注入到对话中，leader 下一轮就能看到。
            _inject_teammate_notifications(ctx)
            
            # 继续下一轮，让模型看到工具结果
        else:
            # for-else: 达到 max_iterations 仍未结束
            stats.stopped_reason = "max_iterations"
            if ctx.ui:
                ctx.ui.warn(
                    f"达到最大迭代次数 {self._max_iterations}，强制停止"
                )

        # ---- Hook: assistant_response ----
        # 取最后一条 assistant 消息的文本作为响应
        try:
            from agent.core.hooks import get_hooks, HookEvent
            last_assistant = None
            for m in reversed(ctx.messages):
                if m.role == "assistant":
                    last_assistant = m
                    break
            if last_assistant:
                resp_text = "".join(
                    b.text for b in last_assistant.content
                    if hasattr(b, "text")
                )
                await get_hooks().trigger(HookEvent.ASSISTANT_RESPONSE, {
                    "text": resp_text,
                    "stats": stats,
                    "ctx": ctx,
                })
        except Exception:
            pass

        return stats

    async def _stream_once(self, ctx: ToolContext) -> tuple[Message, LLMEvent | None]:
        """调用一次 LLM 流式接口，累积成 assistant Message。

        处理顺序:
        1. ThinkingDelta → 累积为 ThinkingContent（思考阶段）
        2. TextDelta → 累积为 TextContent（正式回复）
        3. ToolCall → 累积为 ToolUseContent（工具调用）

        返回 (assistant_message, 最后一个事件)。
        """
        content_blocks: list[TextContent | ThinkingContent | ToolUseContent] = []
        thinking_buf = ""
        text_buf = ""
        last_event: LLMEvent | None = None

        # 通知 UI: 开始流式
        def _flush_text() -> None:
            nonlocal text_buf
            if text_buf:
                content_blocks.append(TextContent(text=text_buf))

        def _flush_thinking() -> None:
            nonlocal thinking_buf
            if thinking_buf:
                content_blocks.append(ThinkingContent(text=thinking_buf))

        tool_defs = self._build_tool_defs()

        try:
            async for event in self._provider.stream(
                model=self._model,
                system=self._system,
                messages=ctx.messages,
                tools=tool_defs,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            ):
                last_event = event
                if isinstance(event, ThinkingDelta):
                    # 深度思考：reasoning_content 先于 content 到达
                    if event.text:
                        thinking_buf += event.text
                        if ctx.ui:
                            ctx.ui.assistant_thinking(event.text)
                elif isinstance(event, TextDelta):
                    if event.text:
                        text_buf += event.text
                        if ctx.ui:
                            ctx.ui.assistant_text(event.text)
                        # 阶段三语音模式：文本增量同时喂给 TTS 流式合成
                        if ctx.on_assistant_text is not None:
                            try:
                                ctx.on_assistant_text(event.text)
                            except Exception:
                                pass
                elif isinstance(event, ToolCall):
                    _flush_thinking()
                    _flush_text()
                    thinking_buf = ""
                    text_buf = ""
                    content_blocks.append(
                        ToolUseContent(
                            id=event.id, name=event.name, input=event.input
                        )
                    )
                elif isinstance(event, ToolCallEnd):
                    pass  # 简化: ToolCall 已带完整 input
                elif isinstance(event, Stop):
                    pass  # 记录在 last_event
        except ProviderError:
            _flush_thinking()
            _flush_text()
            if content_blocks:
                ctx.messages.append(Message(role="assistant", content=content_blocks))
            raise

        _flush_thinking()
        _flush_text()
        assistant_msg = Message(role="assistant", content=content_blocks)
        return assistant_msg, last_event

    def _try_failover(self) -> bool:
        """尝试切换到备选厂商的模型。成功返回 True，失败/不移用返回 False。"""
        if self._failover_tried or not self._vendor_fallback:
            return False

        self._failover_tried = True
        vendor = self._vendor_fallback

        # 在 custom_models 中找同 vendor 的模型
        for name, cfg in self._custom_models.items():
            if isinstance(cfg, dict) and cfg.get("vendor") == vendor:
                try:
                    from agent.config.settings import Settings
                    from dataclasses import replace
                    from agent.main import _build_provider

                    # 用自定义模型的配置重建 provider
                    settings = Settings(provider=cfg.get("provider_type", "openai"))
                    custom_settings = replace(
                        settings,
                        provider=cfg.get("provider_type", "openai"),
                        base_url=cfg.get("base_url", ""),
                        api_key=cfg.get("api_key", ""),
                    )
                    self._provider = _build_provider(custom_settings, model_type=cfg.get("model_type", "multimodal"))
                    self._model = name
                    # 故障转移后同步思考模式状态：语音模式下必须保持关闭
                    if self._thinking_override is not None:
                        self._provider.set_thinking_enabled(self._thinking_override)
                    return True
                except Exception:
                    continue

        return False

    def _build_tool_defs(self) -> list[ToolDef]:
        """把注册表里的工具转成给 LLM 的 ToolDef。"""
        defs: list[ToolDef] = []
        for tool in self._registry.all():
            defs.append(
                ToolDef(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                )
            )
        return defs


def _evict_old_images(messages: list) -> None:
    """淘汰旧截图：只保留最新一张的图片数据，其余替换为文字摘要。

    截图每张 ~1500 tokens（base64 JPEG），LLM 看完后数据无再使用价值。
    此函数从后往前扫描，保留「第一个（最新）」遇到的 ToolResultContent.images，
    其余全部替换为 "[截图已处理：{content}]" 文本占位（~20 tokens）。
    """
    from agent.core.message import ToolResultContent, TextContent

    found_latest = False
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        for block in list(msg.content):
            if not isinstance(block, ToolResultContent):
                continue
            if block.images:
                if not found_latest:
                    found_latest = True  # 最新一张保留
                    continue
                # 旧图 → 替换为文字
                summary = (block.content or "截图").strip()[:80]
                msg.content = [
                    ToolResultContent(
                        tool_use_id=block.tool_use_id,
                        content=f"[截图已处理: {summary}]",
                        is_error=block.is_error,
                    )
                    if isinstance(b, ToolResultContent) and b is block
                    else b
                    for b in msg.content
                ]


def _collapse_old_tool_results(messages: list, *, keep_recent: int = 4) -> None:
    """工具结果折叠：旧工具输出缩成一行摘要，节省 token。

    只保留最近 keep_recent 个 tool_result 的完整内容，
    其余替换为 "[工具 {tool_use_id} 已完成]" (~15 tokens)。
    """
    from agent.core.message import ToolResultContent

    # 收集所有 tool_result 消息索引（从后往前）
    tool_msg_indices: list[int] = []
    for i in range(len(messages) - 1, -1, -1):
        for block in messages[i].content:
            if isinstance(block, ToolResultContent):
                tool_msg_indices.append(i)
                break

    # 前 keep_recent 个保留，其余折叠
    for idx in tool_msg_indices[keep_recent:]:
        msg = messages[idx]
        msg.content = [
            ToolResultContent(
                tool_use_id=block.tool_use_id,
                content=f"[工具 {block.tool_use_id} 已完成]",
                is_error=block.is_error,
            )
            if isinstance(block, ToolResultContent)
            else block
            for block in msg.content
        ]


# ---- 多 Agent 团队邮箱自动同步 (Phase 1+) ----


def _inject_teammate_notifications(ctx: ToolContext) -> None:
    """自动读取 team-lead 邮箱，将队友的 idle_notification 注入对话。

    队友完成一轮工作后通过文件邮箱发 idle_notification。
    leader 的主循环每轮工具执行后调用此函数，确保 leader "看到"队友动向，
    无需 Sleep 轮询或手动检查。
    """
    try:
        from agent.collaboration.team import get_team_manager
        from agent.collaboration.mailbox import read_mailbox
    except ImportError:
        return

    mgr = get_team_manager()
    team_name = mgr.active_team
    if team_name is None:
        return

    messages = read_mailbox("team-lead", team_name, unread_only=True, mark_read=True)
    if not messages:
        return

    # 构造注入文本
    lines = ["[以下来自团队队友的状态更新]"]
    has_info = False

    for msg in messages:
        if msg.type == "idle_notification":
            lines.append(f"- {msg.from_name}: 空闲，等待新任务" + (f" ({msg.summary})" if msg.summary else ""))
            has_info = True
        elif msg.type == "shutdown_response":
            action = "同意关闭" if msg.approve else "拒绝关闭"
            lines.append(f"- {msg.from_name}: {action}")
            has_info = True
        elif msg.type == "plain" and has_info:
            break  # 只看通知，不看闲聊

    if not has_info:
        return

    lines.append("[以上为团队状态更新，请据此调整任务分配]")

    ctx.messages.append(
        Message(role="user", content=[TextContent(text="\n".join(lines))])
    )
