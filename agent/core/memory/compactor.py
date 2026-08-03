"""上下文压缩（Context Compaction）—— Claude Code 风格。

对话历史越长，token 消耗越大。本模块在对话达到阈值时，把较旧的消息
摘要成一段结构化文本替换掉，保留最近若干条原消息不动。

核心改进（ref Claude Code services/compact/）：
- 代码感知的 9 段摘要提示词（文件名、代码片段、错误修复）
- 图片剥离后摘要（图片数据无摘要价值）
- 压缩后自动回灌最近操作过的文件

压缩后消息结构:
    [摘要 user message] + [保留的最近 N 条原消息]
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

from agent.core.message import (
    ImageContent,
    Message,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)
from agent.llm.base import LLMEvent, LLMProvider, ProviderError, Stop, TextDelta

# 粗略 token 估算系数：中英文混合约 4 字符/token
_CHARS_PER_TOKEN = 4.0

# 默认压缩参数
DEFAULT_THRESHOLD_TOKENS = 8000   # 上下文超过此值触发压缩
DEFAULT_KEEP_RECENT = 6            # 保留最近 N 条原消息（含当前轮）

# 摘要请求的输出 token 上限
COMPACT_MAX_OUTPUT_TOKENS = 2048  # 升级到 2048，代码场景需要更多细节

# ---------- Claude Code 风格的压缩提示词（代码感知）----------

_COMPACT_SYSTEM = (
    "你是一个对话摘要助手，专门为 AI 编程 Agent 压缩长对话历史。"
    "你的摘要必须保留足够的代码细节，让 Agent 在压缩后能无缝继续工作。"
    "用中文输出，保持技术准确性。"
)

_COMPACT_USER_TEMPLATE = """请将以下对话历史压缩成一段结构化摘要。你的摘要将替代原始消息，Agent 会依赖它来继续工作——丢失关键细节会导致它犯错。

<分析要求>
在输出摘要前，先在 <analysis> 标签中逐条梳理：
1. 用户的每个显式请求和意图
2. Agent 采用的方案和关键决策
3. 涉及的文件名、完整代码片段、函数签名
4. 遇到的错误及修复方式
5. 用户给出的具体反馈（尤其是纠正性意见）
</分析要求>

<摘要要求>
按以下 9 个部分组织摘要，每部分用 `### N. 标题` 开头：

### 1. 用户请求与意图
全部显式请求，按时间列出。引用用户原话。

### 2. 关键技术决策
技术选型、架构选择、设计模式及选择理由。

### 3. 文件与代码变更
每个涉及的文件单独列出，格式：
- `文件路径`
  - 操作类型（读取/创建/修改）
  - 关键代码片段（含函数签名、类定义）
  - 变更原因

### 4. 错误与修复
每条错误包括：错误信息、根因分析、修复方案。

### 5. 用户反馈
用户的所有纠正、偏好表达、风格要求。

### 6. 用户消息列表
罗列所有非工具结果的用户消息（反映意图变化）。

### 7. 待办事项
明确未完成的任务。

### 8. 当前工作状态
摘要前最后一轮正在做什么，包括涉及的文件和代码位置。

### 9. 下一步行动
基于用户最新请求的下一步操作。如果当前任务已完成，无需列出。
</摘要要求>

对话历史:
{history}
"""


def estimate_tokens(messages: list[Message]) -> int:
    """粗估消息列表的 token 数。

    遍历每条消息的每个 content block，按字符数 / 4 估算。图片块按
    固定值估算（视觉 token 通常远多于文本）。够用，不追求精确。
    """
    total = 0
    for msg in messages:
        for block in msg.content:
            if isinstance(block, TextContent):
                total += max(1, len(block.text) // _CHARS_PER_TOKEN)
            elif isinstance(block, ToolUseContent):
                # 工具调用: name + input JSON
                import json
                try:
                    payload = json.dumps(block.input, ensure_ascii=False)
                except Exception:
                    payload = str(block.input)
                total += max(1, (len(block.name) + len(payload)) // _CHARS_PER_TOKEN)
            elif isinstance(block, ToolResultContent):
                total += max(1, len(block.content) // _CHARS_PER_TOKEN)
                # 图片块: 每张约 1000 token（保守估计）
                total += len(block.images) * 1000
            elif isinstance(block, ImageContent):
                total += 1000
    return int(total)


def should_compact(
    messages: list[Message],
    *,
    threshold: int = DEFAULT_THRESHOLD_TOKENS,
    min_messages: int = 8,
) -> bool:
    """判断是否需要压缩。

    条件:
    1. 消息数 >= min_messages（太少没压缩价值，摘要本身也要花 token）
    2. 估算 token >= threshold
    """
    if len(messages) < min_messages:
        return False
    return estimate_tokens(messages) >= threshold


def _strip_images(messages: list[Message]) -> list[Message]:
    """剥离图片块，替换为文本标记 [image]。摘要不需要真实图片数据。"""
    stripped: list[Message] = []
    for msg in messages:
        new_blocks: list[Any] = []
        changed = False
        for block in msg.content:
            if isinstance(block, ImageContent):
                new_blocks.append(TextContent(text="[图片]"))
                changed = True
            elif isinstance(block, ToolResultContent) and block.images:
                # 工具结果带图片: 保留文本，丢图片
                new_blocks.append(
                    ToolResultContent(
                        tool_use_id=block.tool_use_id,
                        content=block.content + "\n[附图已省略]",
                        is_error=block.is_error,
                        images=[],
                    )
                )
                changed = True
            else:
                new_blocks.append(block)
        if changed:
            stripped.append(Message(role=msg.role, content=new_blocks))
        else:
            stripped.append(msg)
    return stripped


def _format_history_for_summary(messages: list[Message]) -> str:
    """把消息列表格式化成摘要请求用的纯文本。"""
    lines: list[str] = []
    for msg in messages:
        role_label = "用户" if msg.role == "user" else "助手"
        parts: list[str] = []
        for block in msg.content:
            if isinstance(block, TextContent) and block.text.strip():
                parts.append(block.text)
            elif isinstance(block, ToolUseContent):
                parts.append(f"[调用工具 {block.name}]")
            elif isinstance(block, ToolResultContent):
                # 工具结果可能很长，截断到 200 字
                snippet = block.content[:200]
                if len(block.content) > 200:
                    snippet += "..."
                parts.append(f"[工具结果: {snippet}]")
        if parts:
            text = " ".join(parts)
            # 单条消息也截断，防止单条过长
            if len(text) > 500:
                text = text[:500] + "..."
            lines.append(f"{role_label}: {text}")
    return "\n".join(lines)


@dataclass
class CompactResult:
    """压缩结果。"""
    new_messages: list[Message]           # 压缩后的消息列表
    summary: str                           # LLM 生成的摘要文本
    pre_compact_tokens: int                # 压缩前估算 token
    post_compact_tokens: int               # 压缩后估算 token
    messages_summarized: int               # 被摘要的消息数
    messages_kept: int                     # 保留的原消息数


async def compact_messages(
    provider: LLMProvider,
    model: str,
    messages: list[Message],
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT,
    max_output_tokens: int = COMPACT_MAX_OUTPUT_TOKENS,
    on_progress: Any = None,  # callable(str) -> None，可选进度回调
    task_budget_remaining: int | None = None,  # Phase 2: 任务预算追踪
) -> CompactResult:
    """压缩对话历史。

    1. 把 messages 分成 [to_summarize] + [to_keep]
       - to_keep = 最后 keep_recent 条原消息（含当前轮用户消息）
       - to_summarize = 前面的所有消息
    2. 剥离 to_summarize 里的图片
    3. 调 LLM 生成摘要
    4. 返回 [摘要消息] + to_keep

    Args:
        provider: LLM provider（复用主对话的）
        model: 模型名
        messages: 当前完整消息列表
        keep_recent: 保留最近 N 条原消息
        max_output_tokens: 摘要输出的 token 上限
        on_progress: 进度回调（如 ui.info）

    Returns:
        CompactResult，包含压缩后的消息列表和统计

    Raises:
        ProviderError: LLM 调用失败（调用方决定是否重试/回退）
    """
    pre_tokens = estimate_tokens(messages)

    # 分割: 保留尾部，摘要头部
    if len(messages) <= keep_recent:
        # 没什么可摘要的，原样返回
        return CompactResult(
            new_messages=list(messages),
            summary="",
            pre_compact_tokens=pre_tokens,
            post_compact_tokens=pre_tokens,
            messages_summarized=0,
            messages_kept=len(messages),
        )

    to_summarize = messages[:-keep_recent]
    to_keep = messages[-keep_recent:]

    if on_progress:
        on_progress(f"压缩上下文：摘要 {len(to_summarize)} 条历史消息...")

    # 剥离图片
    to_summarize_stripped = _strip_images(to_summarize)

    # 构造摘要请求
    history_text = _format_history_for_summary(to_summarize_stripped)
    user_prompt = _COMPACT_USER_TEMPLATE.format(history=history_text)

    summary_request = [Message.user_text(user_prompt)]

    # 调 LLM 流式生成摘要
    summary_buf = ""
    try:
        async for event in provider.stream(
            model=model,
            system=_COMPACT_SYSTEM,
            messages=summary_request,
            tools=[],  # 摘要模式不给工具
            max_tokens=max_output_tokens,
            temperature=0.0,  # 摘要要确定性
        ):
            if isinstance(event, TextDelta) and event.text:
                summary_buf += event.text
            elif isinstance(event, Stop):
                pass
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"压缩摘要生成失败: {e}") from e

    summary = summary_buf.strip()
    if not summary:
        # 摘要为空（异常情况），回退: 不压缩，原样返回
        return CompactResult(
            new_messages=list(messages),
            summary="",
            pre_compact_tokens=pre_tokens,
            post_compact_tokens=pre_tokens,
            messages_summarized=0,
            messages_kept=len(messages),
        )

    # 构造摘要消息（用 user 角色承载，标记为压缩摘要）
    summary_text = (
        "[以下是对话历史的摘要，供你了解之前聊了什么]\n"
        f"{summary}\n"
        "[摘要结束]"
    )
    # Phase 2: 注入任务预算提示
    if task_budget_remaining is not None and task_budget_remaining > 0:
        summary_text += (
            f"\n\n[任务预算] 本轮对话的总 token 预算还剩约 {task_budget_remaining} tokens。"
            "请在预算耗尽前完成任务或建议用户开启新对话。"
        )
    summary_message = Message(
        role="user",
        content=[TextContent(text=summary_text)],
    )

    new_messages = [summary_message] + list(to_keep)
    post_tokens = estimate_tokens(new_messages)

    if on_progress:
        saved = pre_tokens - post_tokens
        on_progress(
            f"上下文压缩完成：{pre_tokens} → {post_tokens} tokens"
            f"（节省 {saved}，摘要 {len(to_summarize)} 条 → 1 条）"
        )

    return CompactResult(
        new_messages=new_messages,
        summary=summary,
        pre_compact_tokens=pre_tokens,
        post_compact_tokens=post_tokens,
        messages_summarized=len(to_summarize),
        messages_kept=len(to_keep),
    )


# ---------- 压缩后文件回灌（Post-Compact File Restoration）----------
# 参考 Claude Code 的 createPostCompactFileAttachments:
# 压缩后重新注入最近操作过的文件，让 LLM 不丢失关键文件上下文。

_MAX_RESTORE_FILES = 5       # 最多回灌 5 个文件
_MAX_CHARS_PER_FILE = 5000   # 每个文件最多 5000 字符


def track_file_access(ctx, file_path: str, access_type: str = "read") -> None:
    """记录文件访问（在工具执行时调用）。

    存在 ctx.extra["_recent_files"] 中，按访问时间排序，去重保留最近 20 个。
    """
    import time
    if not hasattr(ctx, 'extra') or ctx.extra is None:
        return
    files = ctx.extra.setdefault("_recent_files", [])
    # 去重：同名文件移除旧记录，新记录追加到末尾
    files[:] = [(p, t, a) for p, t, a in files if p != file_path]
    files.append((file_path, time.time(), access_type))
    # 只保留最近 20 个
    if len(files) > 20:
        files[:] = files[-20:]


def restore_recent_files(ctx) -> int:
    """压缩后回灌最近访问的文件内容。

    从 ctx.extra["_recent_files"] 取最近 N 个文件，读取内容截断后
    作为新的 user 消息追加到 ctx.messages。返回回灌的文件数。
    """
    if not hasattr(ctx, 'extra') or ctx.extra is None:
        return 0

    files = ctx.extra.get("_recent_files", [])
    if not files:
        return 0

    # 取最近 _MAX_RESTORE_FILES 个，倒序（最新的优先）
    recent = list(reversed(files[-_MAX_RESTORE_FILES:]))
    restored = 0

    for file_path, _ts, access_type in recent:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            continue

        if len(content) > _MAX_CHARS_PER_FILE:
            content = content[:_MAX_CHARS_PER_FILE] + "\n... [文件过长，已截断]"

        label = { "read": "已读取", "write": "已修改", "edit": "已编辑" }.get(access_type, "已访问")
        attachment_text = (
            f"[压缩后文件回灌: {label}] `{file_path}`\n"
            f"```\n{content}\n```"
        )
        ctx.messages.append(Message(
            role="user",
            content=[TextContent(text=attachment_text)],
        ))
        restored += 1

    # 回灌后清空记录，避免重复注入
    ctx.extra["_recent_files"] = []
    return restored


# ---------- Session Memory（会话自动记忆）----------
# 参考 Claude Code 的 SessionMemory:
# 长对话自动将关键决策、错误修复、用户偏好持久化到 .jarvis/SESSION_MEMORY.md。
# 每次压缩时触发更新——压缩摘要已有结构化内容，直接追加到记忆文件。

def _session_memory_path(workdir: str) -> str:
    """返回项目级会话记忆文件路径。"""
    import os
    jarvis_dir = os.path.join(workdir, ".jarvis")
    os.makedirs(jarvis_dir, exist_ok=True)
    return os.path.join(jarvis_dir, "SESSION_MEMORY.md")


_SESSION_MEMORY_HEADER = """# 会话自动记忆

> 此文件由贾维斯在对话过程中自动维护。记录关键决策、错误修复、用户偏好、待办事项。
> 每次上下文压缩时更新，注入到 system prompt 中供后续对话参考。

"""


def update_session_memory(workdir: str, compact_result: CompactResult) -> str | None:
    """根据压缩摘要更新会话记忆文件。

    在 compact_messages() 成功后调用。追加新的记忆条目。
    返回记忆文件路径，失败返回 None。
    """
    if not compact_result.summary:
        return None

    try:
        mem_path = _session_memory_path(workdir)
        import time
        timestamp = time.strftime("%Y-%m-%d %H:%M")

        # 提取摘要摘要的前 2000 字符作为记忆内容
        summary_snippet = compact_result.summary[:2000]
        if len(compact_result.summary) > 2000:
            summary_snippet += "\n\n... (完整摘要省略)"

        entry = (
            f"\n## {timestamp}\n\n"
            f"压缩了 {compact_result.messages_summarized} 条消息 "
            f"({compact_result.pre_compact_tokens} → {compact_result.post_compact_tokens} tokens)\n\n"
            f"{summary_snippet}\n"
        )

        # 追加写入
        if not os.path.exists(mem_path):
            with open(mem_path, "w", encoding="utf-8") as f:
                f.write(_SESSION_MEMORY_HEADER)

        with open(mem_path, "a", encoding="utf-8") as f:
            f.write(entry)

        return mem_path
    except Exception:
        return None


def load_session_memory(workdir: str) -> str:
    """加载会话记忆文件内容。注入到 system prompt 作为长期记忆。"""
    import os
    mem_path = _session_memory_path(workdir)
    if not os.path.exists(mem_path):
        return ""
    try:
        with open(mem_path, "r", encoding="utf-8") as f:
            content = f.read()
        # 只取最后 4000 字符（记忆太多反而稀释当前上下文）
        if len(content) > 4000:
            content = "... (早期记忆已省略)\n\n" + content[-4000:]
        return content
    except Exception:
        return ""
