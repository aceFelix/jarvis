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
    estimate_text_tokens,
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
        tool_result_keep_recent: int = 4,
        vendor_fallback: str = "",
        custom_models: dict | None = None,
        deferred_loading: bool = True,
        chat_detection: bool = True,
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
        self._tool_result_keep_recent = tool_result_keep_recent
        self._vendor_fallback = vendor_fallback
        self._custom_models = custom_models or {}
        self._failover_tried = False
        self._task_budget_remaining: int | None = None  # 任务预算追踪（Phase 2）
        # 思考模式覆盖标志：voice_loop 通过 set_thinking_enabled() 设置后，
        # _try_failover 重建 provider 时会把这个状态同步到新 provider，
        # 避免故障转移后思考模式被意外恢复（语音模式下必须保持关闭）。
        # None 表示不强制，使用 provider 默认状态。
        self._thinking_override: bool | None = None
        # 工具延迟加载开关（参考 Claude Code deferred tool loading）
        self._deferred_loading = deferred_loading
        # 纯聊天零工具检测开关
        self._chat_detection = chat_detection
        # 网络错误重试计数（每轮 run() 只重试一次）
        self._network_retried: bool = False
        # 会话级 token 累计（跨多轮 run() 累加，供 /cost 展示缓存命中率）
        self._session_usage = Usage()

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
            # 压缩成功后，把摘要持久化到会话记忆文件
            update_session_memory(ctx.workdir, result)
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
        self._network_retried = False  # 每轮重置重试计数

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
            pass  # 非关键异常，不影响主流程

        # 追加用户消息（文本 + 可选图片）
        content_blocks: list[ContentBlock] = [TextContent(text=user_text)]

        # ── Skill 按需加载：触发词匹配 ──
        # system prompt 只含 skill 摘要（省 60k+ token），
        # 用户消息匹配到触发词时，把完整正文作为上下文附加到当前消息。
        try:
            from agent.core.extensions.skills import match_skills_for_message
            skill_content = match_skills_for_message(user_text, ctx.workdir)
            if skill_content:
                content_blocks.insert(0, TextContent(text=skill_content))
        except Exception:
            pass  # skill 加载失败不影响主流程

        if images:
            content_blocks.extend(images)
        ctx.messages.append(Message(role="user", content=content_blocks))

        # ── 分层上下文：冻结前缀 + 滑动窗口（缓存友好）──
        # 原水位线方案原地篡改历史消息 → 破坏 LLM 前缀缓存。
        # LayeredContext 将压缩后的摘要锁定为"冻结区"永不修改，
        # 后续请求前缀稳定 → 缓存持续命中。
        from agent.core.layered_context import LayeredContext
        layered = LayeredContext(ctx.messages)

        if self._enable_compaction:
            # 冻结：活跃窗口超阈值 → 一次性压缩 + 锁定前缀
            # base_tokens 计入 system prompt 固定开销，避免阈值被系统性低估
            frozen = await layered.freeze_if_needed(
                self._provider, self._model,
                window_limit=self._compaction_threshold,
                keep_recent=self._keep_recent_messages,
                base_tokens=estimate_text_tokens(self._system),
                on_progress=ctx.ui.info if ctx.ui else None,
            )
            if frozen and ctx.ui:
                ctx.ui.info(f"上下文冻结完成（活跃 {layered.active_tokens()} → 总计 {layered.total_tokens()} tokens）")
            # 压缩成功后，把摘要持久化到会话记忆文件
            if frozen and layered.last_compact_result:
                update_session_memory(ctx.workdir, layered.last_compact_result)
            # 工具结果折叠（仅活跃窗口，不影响冻结区）
            layered.collapse_old_tool_results(keep_recent=self._tool_result_keep_recent)
            # 图片淘汰（仅活跃窗口，不影响冻结区）
            layered.evict_old_images()

        for iteration in range(self._max_iterations):
            if ctx.abort_event.is_set():
                stats.stopped_reason = "aborted"
                break

            stats.iterations = iteration + 1

            # 给 LLM 发送分层上下文的冻结快照（冻结区永不改 → 缓存友好）
            # 注意: 用 in-place 切片同步而非重绑定。重绑定会让 ctx.messages 与
            # 调用方持有的列表（如 repl 的 messages）脱钩，导致 assistant 回复
            # 永远进不了调用方的对话历史（自动保存丢回复、len(messages) 不涨、
            # 第 2 轮 LLM 标题永不触发）。
            ctx.messages[:] = layered.messages

            try:
                assistant_msg, stop_event = await self._stream_once(ctx)
            except asyncio.CancelledError:
                # 用户 Ctrl+C 中断 LLM 流式输出
                stats.stopped_reason = "aborted"
                ctx.abort_event.set()
                # 重置 abort_event，否则后续 run() 第一行检查就 break
                ctx.abort_event = asyncio.Event()
                break
            except ProviderError as e:
                # 反应式压缩：API 报 context too long → 强制压缩后重试
                error_str = str(e).lower()
                if self._enable_compaction and any(
                    kw in error_str for kw in ("prompt_too_long", "context_length", "reduce length", "too long")
                ):
                    # 使用 LayeredContext 的反应式压缩（冻结前缀不变）
                    if await layered.compact_reactive(
                        self._provider, self._model,
                        keep_recent=self._keep_recent_messages,
                    ):
                        # 压缩成功后，把摘要持久化到会话记忆文件
                        if layered.last_compact_result:
                            update_session_memory(ctx.workdir, layered.last_compact_result)
                        if ctx.ui:
                            ctx.ui.warn(
                                f"上下文过长，已自动压缩后重试"
                                f"（{layered.total_tokens()} tokens）"
                            )
                        continue  # 重试本轮
                # 网络错误：自动重试一次（1.5s 退避），最多重试 1 次
                if "网络错误" in error_str and not self._network_retried:
                    self._network_retried = True
                    await asyncio.sleep(1.5)
                    if ctx.ui:
                        ctx.ui.warn("网络异常，自动重试中...")
                    continue  # 重试本轮
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

            # 过滤空 assistant 消息
            if not assistant_msg.content:
                if ctx.ui:
                    ctx.ui.error("模型返回了空回复，未加入对话历史")
                stats.stopped_reason = "empty_response"
                break

            # 追加到分层上下文（而非 ctx.messages）
            layered.append(assistant_msg)
            if isinstance(stop_event, Stop):
                stats.usage = stop_event.usage

            # 输出截断恢复
            if isinstance(stop_event, Stop) and stop_event.reason == "length":
                if ctx.ui:
                    ctx.ui.warn("⚠ 输出被截断（max_tokens），自动续写...")
                layered.append(Message(
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
                # 用户 Ctrl+C → 不再 re-raise，优雅退出本轮
                stats.stopped_reason = "aborted"
                ctx.abort_event.set()
                ctx.abort_event = asyncio.Event()  # 重置，否则后续 run() 直接跳过
                break

            # 工具结果追加到分层上下文
            layered.append(
                Message(role="user", content=list(tool_results))
            )

            # Phase 1: 多 Agent 团队——自动检查队友邮箱
            # 先同步 ctx.messages 到 layered 最新状态（含刚追加的工具结果），
            # 让 _inject_teammate_notifications 能看到完整对话历史。
            # 之前缺少这步同步 → 注入函数读到的是旧快照、看不到工具结果，
            # 且注入的消息因长度比较基准错误而无法同步回 layered（死代码）。
            ctx.messages[:] = layered.messages
            _inject_teammate_notifications(ctx)
            # 检查 ctx.messages 是否被注入额外消息（比 layered.messages 多）
            if len(ctx.messages) > len(layered.messages):
                for extra in ctx.messages[len(layered.messages):]:
                    layered.append(extra)
        else:
            # for-else: 达到 max_iterations 仍未结束
            stats.stopped_reason = "max_iterations"
            if ctx.ui:
                ctx.ui.warn(
                    f"达到最大迭代次数 {self._max_iterations}，强制停止"
                )

        # ---- Hook: assistant_response ----
        # 同步 ctx.messages 到分层上下文的最新快照（hooks 读 ctx.messages）
        # in-place 同步，保证调用方持有的原列表引用仍能看到完整对话历史
        ctx.messages[:] = layered.messages

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
            pass  # 非关键异常，不影响主流程

        # 会话级累计（本轮 usage 并入，供 /cost 展示缓存命中统计）
        self._session_usage = Usage(
            input_tokens=self._session_usage.input_tokens + stats.usage.input_tokens,
            output_tokens=self._session_usage.output_tokens + stats.usage.output_tokens,
            cache_read_tokens=self._session_usage.cache_read_tokens + stats.usage.cache_read_tokens,
            cache_creation_tokens=self._session_usage.cache_creation_tokens + stats.usage.cache_creation_tokens,
        )

        return stats

    @property
    def session_usage(self) -> Usage:
        """会话级累计 token 用量（跨多轮对话）。"""
        return self._session_usage

    @property
    def keep_recent_messages(self) -> int:
        """压缩时保留的最近消息数（供 /compact 提前判断是否值得压缩）。"""
        return self._keep_recent_messages

    @property
    def enable_compaction(self) -> bool:
        """压缩功能是否启用（供 /compact 显示准确原因）。"""
        return self._enable_compaction

    async def _stream_once(
        self, ctx: ToolContext,
        messages: list[Message] | None = None,
    ) -> tuple[Message, LLMEvent | None]:
        """调用一次 LLM 流式接口，累积成 assistant Message。

        处理顺序:
        1. ThinkingDelta → 累积为 ThinkingContent（思考阶段）
        2. TextDelta → 累积为 TextContent（正式回复）
        3. ToolCall → 累积为 ToolUseContent（工具调用）

        Args:
            ctx: 工具上下文
            messages: 可选的消息列表（None 则用 ctx.messages）。
                      LayeredContext 场景下传入快照，避免原地篡改破坏缓存。

        返回 (assistant_message, 最后一个事件)。
        """
        msgs = messages if messages is not None else ctx.messages
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

        tool_defs = self._build_tool_defs(ctx)

        try:
            async for event in self._provider.stream(
                model=self._model,
                system=self._system,
                messages=msgs,
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
                msgs.append(Message(role="assistant", content=content_blocks))
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
                    from agent.bootstrap import _build_provider

                    # 用自定义模型的配置重建 provider
                    # 优先使用新的 api_format 字段，兼容旧的 provider_type
                    api_fmt = cfg.get("api_format") or cfg.get("provider_type", "openai")
                    settings = Settings(provider=api_fmt)
                    custom_settings = replace(
                        settings,
                        provider=api_fmt,
                        api_format=api_fmt,
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

    def _build_tool_defs(self, ctx: ToolContext | None = None) -> list[ToolDef]:
        """把注册表里的工具转成给 LLM 的 ToolDef。

        延迟加载模式（参考 Claude Code）：
        - 核心工具（deferred=False）始终携带
        - 延迟工具（deferred=True）仅在被 ToolSearch 发现后才携带完整 schema
        - 纯聊天检测：短消息 + 无动作意图 → 不发任何工具（0 token）
        """
        # 延迟加载关闭 → 回退到全量发送
        if not self._deferred_loading:
            defs: list[ToolDef] = []
            for tool in self._registry.all():
                defs.append(ToolDef(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                ))
            return defs

        # 纯聊天检测：首轮 + 短消息 + 无动作意图 → 0 工具
        if self._chat_detection and ctx is not None and self._is_chat_only(ctx):
            return []

        # 分组发送：核心工具 + 已发现的延迟工具
        discovered: set[str] = set()
        if ctx is not None:
            discovered = ctx.extra.get("discovered_tools", set())

        defs = []
        for tool in self._registry.all():
            if not tool.deferred:
                # 核心工具: 始终携带
                defs.append(ToolDef(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                ))
            elif tool.name in discovered:
                # 已发现的延迟工具: 携带完整 schema
                defs.append(ToolDef(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                ))
            # 未发现的延迟工具: 不发送
        return defs

    def _is_chat_only(self, ctx: ToolContext) -> bool:
        """纯聊天检测：保守策略，宁可多发不可漏发。

        条件（全部满足才返回 True）：
        - 用户消息 <= 20 字
        - 不含动作关键词
        - 对话历史中无工具调用记录
        """
        # 取最后一条 user 消息
        last_user_text = ""
        has_tool_use = False
        for msg in reversed(ctx.messages):
            if msg.role == "user" and not last_user_text:
                for block in msg.content:
                    if hasattr(block, "text"):
                        last_user_text = block.text
                        break
            if msg.role == "assistant":
                if msg.get_tool_uses():
                    has_tool_use = True
                    break

        if has_tool_use:
            return False

        text = last_user_text.strip()
        if len(text) > 20:
            return False

        # 动作关键词（中英文）：含任一则认为有工具意图
        # 分两类: 动作词 (do keywords) + 信息查询词 (ask keywords)
        # 策略: 保守，宁可多发不可漏发
        action_keywords = (
            # 动作词 — 明确要求执行操作
            "帮我", "查", "搜", "打开", "写", "创建", "运行", "执行", "安装",
            "删除", "修改", "编辑", "发送", "截图", "点击", "输入", "下载",
            "上传", "连接", "启动", "停止", "关闭", "设置", "配置",
            "help", "search", "open", "write", "create", "run", "exec",
            "install", "delete", "edit", "send", "screenshot", "click",
            "download", "upload", "connect", "start", "stop", "close",
            "文件", "命令", "终端", "浏览器", "网页", "邮件", "日程", "提醒",
            # 信息查询词 — 短问句可能需工具获取实时数据
            "几点", "几号", "多少", "天气", "温度", "湿度", "时间", "日期",
            "星期", "今天", "明天", "昨天", "今年", "版本", "日历",
            "how", "what", "when", "who", "where", "which", "why",
            "time", "date", "today", "weather", "version", "calendar",
        )
        text_lower = text.lower()
        for kw in action_keywords:
            if kw in text_lower:
                return False

        return True


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
    """自动读取 team-lead 邮箱，将队友状态更新注入对话。

    队友完成一轮工作后通过文件邮箱发 idle_notification。
    leader 的主循环每轮工具执行后调用此函数，确保 leader "看到"队友动向，
    无需 Sleep 轮询或手动检查。

    支持的消息类型：idle_notification、task_claimed、task_completed、
    plan_approval_request、permission_request、shutdown_response。
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
            summary = msg.summary or "空闲，等待新任务"
            lines.append(f"- {msg.from_name}: {summary}")
            has_info = True
        elif msg.type == "task_claimed":
            subject = msg.task_subject or "未命名任务"
            lines.append(f"- {msg.from_name}: 领取任务 #{msg.task_id} {subject}")
            has_info = True
        elif msg.type == "task_completed":
            status = msg.status or "completed"
            summary = msg.summary or f"完成任务 #{msg.task_id}"
            lines.append(f"- {msg.from_name}: [{status}] {summary}")
            has_info = True
        elif msg.type == "plan_approval_request":
            plan_text = (msg.text or "未提供计划详情")[:200]
            lines.append(
                f"- {msg.from_name}: 请求审批计划 (request_id={msg.request_id})\n"
                f"  计划: {plan_text}"
            )
            has_info = True
        elif msg.type == "permission_request":
            action = msg.action or "执行操作"
            tool = msg.tool or "未知工具"
            lines.append(
                f"- {msg.from_name}: 请求权限 (request_id={msg.request_id})\n"
                f"  操作: {action} | 工具: {tool}"
            )
            has_info = True
        elif msg.type == "shutdown_response":
            action = "同意关闭" if msg.approve else "拒绝关闭"
            lines.append(f"- {msg.from_name}: {action}")
            has_info = True
        elif msg.type == "heartbeat":
            # 心跳不渲染到对话，仅内部更新健康时间戳（如后续需要）
            continue

    if not has_info:
        return

    lines.append("[以上为团队状态更新，请据此调整任务分配]")

    ctx.messages.append(
        Message(role="user", content=[TextContent(text="\n".join(lines))])
    )
