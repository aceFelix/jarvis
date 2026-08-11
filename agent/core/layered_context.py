"""分层上下文管理器 —— 冻结前缀 + 滑动窗口。

解决原水位线压缩方案"原地篡改历史消息 → 破坏 LLM 前缀缓存"的问题:

- **冻结区**：压缩后的摘要，一旦锁定永不修改 → 后续请求前缀稳定 → 缓存持续命中
- **滑动窗口**：最近 N 条原始消息，自然追加增长 → 前缀缓存继续命中
- **冻结触发**：窗口 token 超阈值 → 一次性压缩合并进冻结区 → 窗口重置

注意: 此模块不得放在 agent/core/context/ 目录下，因为 agent/core/context.py 已存在，
Python 会将其视作模块而非包，导致 import 失败。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.message import Message
    from agent.llm.base import LLMProvider


class LayeredContext:
    """分层上下文：冻结前缀永不修改 → LLM 缓存友好。

    用法:
        layered = LayeredContext()
        layered.append(user_msg)

        # 每轮发送 LLM 前，取完整消息快照
        messages_for_llm = layered.snapshot()

        # 工具结果也通过 append 追加到窗口
        layered.append(tool_result_msg)

        # 窗口过大时触发冻结（一次性压缩，后续前缀不再变化）
        await layered.freeze_if_needed(provider, model, threshold=8000)

    @author aceFelix
    """

    # 默认窗口 token 上限（超此触发冻结）
    DEFAULT_WINDOW_LIMIT: int = 8000
    # 冻结时保留最近 N 条不压缩（留在窗口继续活跃）
    DEFAULT_KEEP_RECENT: int = 4

    def __init__(self, messages: list[Message] | None = None) -> None:
        self._frozen: list[Message] = []       # 📦 冻结区：压缩后的摘要，永不修改
        self._active: list[Message] = []        # 🪟 活跃窗口：最近的消息，继续增长
        self._frozen_tokens: int = 0            # 冻结区估算 token 数（缓存，避免重复计算）
        self._last_compact_result = None        # 📝 最近一次成功压缩的 CompactResult（供记忆落盘）
        if messages:
            self._active = list(messages)

    @property
    def last_compact_result(self):
        """最近一次成功压缩的结果（含摘要、token 统计），未压缩过为 None。

        供调用方在压缩后把摘要持久化到会话记忆文件（SESSION_MEMORY.md）。
        """
        return self._last_compact_result

    # ── 属性 ──

    @property
    def messages(self) -> list[Message]:
        """给 LLM 的完整消息列表（冻结区 + 活跃窗口）。"""
        return self._frozen + self._active

    @property
    def frozen(self) -> list[Message]:
        """冻结区消息（只读）。"""
        return list(self._frozen)

    @property
    def active(self) -> list[Message]:
        """活跃窗口消息（可继续追加）。"""
        return self._active

    # ── 消息操作 ──

    def append(self, msg: Message) -> None:
        """追加消息到活跃窗口。"""
        self._active.append(msg)

    def extend(self, msgs: list[Message]) -> None:
        """批量追加消息到活跃窗口。"""
        self._active.extend(msgs)

    def replace_active(self, msgs: list[Message]) -> None:
        """替换整个活跃窗口（如压缩后重建）。"""
        self._active = list(msgs)

    def snapshot(self) -> list[Message]:
        """返回当前完整消息快照（冻结区 + 活跃窗口）。"""
        return self._frozen + self._active

    # ── Token 估算 ──

    def active_tokens(self) -> int:
        """活跃窗口估算 token 数。"""
        from agent.core.memory.compactor import estimate_tokens
        return estimate_tokens(self._active)

    def total_tokens(self) -> int:
        """总 token 估算（冻结区 + 活跃窗口）。"""
        return self._frozen_tokens + self.active_tokens()

    # ── 冻结 ──

    async def freeze_if_needed(
        self,
        provider: LLMProvider,
        model: str,
        *,
        window_limit: int | None = None,
        keep_recent: int | None = None,
        base_tokens: int = 0,
        on_progress: object = None,
    ) -> bool:
        """活跃窗口超阈值 → 压缩整个上下文并冻结前缀。

        压缩逻辑:
        1. 把冻结区 + 活跃窗口合并成完整消息列表
        2. 调 compact_messages 压缩成摘要 + 保留最近 N 条
        3. 摘要部分进入冻结区（永不修改），最近 N 条回到活跃窗口

        Args:
            provider: LLM provider（用于生成摘要）
            model: 模型名
            window_limit: 窗口 token 上限，超此触发。默认 DEFAULT_WINDOW_LIMIT
            keep_recent: 冻结时保留最近 N 条不压缩。默认 DEFAULT_KEEP_RECENT
            base_tokens: 固定开销 token（system prompt 等），计入阈值判断。
                estimate_tokens 不计 system，若忽略此值会系统性低估真实请求体积。
            on_progress: UI 回调（可选）

        Returns:
            True 触发了冻结，False 无需冻结或冻结失败

        @author aceFelix
        """
        limit = window_limit or self.DEFAULT_WINDOW_LIMIT
        keep = keep_recent or self.DEFAULT_KEEP_RECENT

        # 活跃窗口 + 固定开销（system prompt）超阈值才触发，避免低估
        if self.active_tokens() + base_tokens < limit:
            return False

        from agent.core.memory.compactor import compact_messages

        all_msgs = self._frozen + self._active
        try:
            result = await compact_messages(
                provider=provider,
                model=model,
                messages=all_msgs,
                keep_recent=keep,
                on_progress=on_progress,
            )
            if not result.messages_summarized:
                return False

            # 冻结区 = 压缩摘要（前面部分），活跃窗口 = 保留的最近消息
            total = len(result.new_messages)
            split_at = max(0, total - keep)
            self._frozen = result.new_messages[:split_at]
            self._active = result.new_messages[split_at:]
            self._frozen_tokens = self._estimate_frozen()
            self._last_compact_result = result   # 记录压缩结果，供调用方落盘记忆
            return True
        except Exception:
            from agent.core.logging import get_logger
            get_logger().warning("LayeredContext: freeze_if_needed failed", exc_info=True)
            return False

    async def compact_reactive(
        self,
        provider: LLMProvider,
        model: str,
        *,
        keep_recent: int | None = None,
    ) -> bool:
        """反应式压缩：API 报 context too long 时强制压缩。

        与 freeze_if_needed 的区别：不检查阈值，无条件压缩。

        @author aceFelix
        """
        keep = keep_recent or self.DEFAULT_KEEP_RECENT
        from agent.core.memory.compactor import compact_messages

        all_msgs = self._frozen + self._active
        try:
            result = await compact_messages(
                provider=provider,
                model=model,
                messages=all_msgs,
                keep_recent=keep,
            )
            if not result.messages_summarized:
                return False

            total = len(result.new_messages)
            split_at = max(0, total - keep)
            self._frozen = result.new_messages[:split_at]
            self._active = result.new_messages[split_at:]
            self._frozen_tokens = self._estimate_frozen()
            self._last_compact_result = result   # 记录压缩结果，供调用方落盘记忆
            return True
        except Exception:
            from agent.core.logging import get_logger
            get_logger().warning("LayeredContext: compact_reactive failed", exc_info=True)
            return False

    # ── 辅助 ──

    def _estimate_frozen(self) -> int:
        """估算冻结区 token 数。"""
        if not self._frozen:
            return 0
        from agent.core.memory.compactor import estimate_tokens
        return estimate_tokens(self._frozen)

    # ── 清理（保留向后兼容的原地操作，但只作用于活跃窗口）──

    def evict_old_images(self) -> None:
        """淘汰活跃窗口中的旧图片（仅影响活跃窗口，冻结区不可变）。

        延迟导入避免循环依赖。
        """
        from agent.core.query_loop import _evict_old_images  # noqa: PLC0415
        _evict_old_images(self._active)

    def collapse_old_tool_results(self, keep_recent: int = 4) -> None:
        """折叠活跃窗口中的旧工具结果（仅影响活跃窗口，冻结区不可变）。

        延迟导入避免循环依赖。
        """
        from agent.core.query_loop import _collapse_old_tool_results  # noqa: PLC0415
        _collapse_old_tool_results(self._active, keep_recent=keep_recent)
