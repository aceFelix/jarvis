"""消息历史裁剪（prune）—— 旧截图淘汰与旧工具结果折叠。

上下文压缩前的轻量裁剪：不动最近的消息，只把「已失去复用价值」的旧内容
替换成短摘要占位，控制 token 增长。

- evict_old_images：只保留最新一张截图数据，其余换成文字占位
  （截图每张约 1500 tokens，LLM 看完即无复用价值）
- collapse_old_tool_results：只保留最近 N 个工具结果全文，其余折成一行

两函数都是原地改写 messages 内元素，供 LayeredContext（每轮召回前裁剪）
与 QueryLoop 共用；与 compactor.compact_messages 的分层压缩配合工作。

@author aceFelix
"""

from __future__ import annotations

from agent.core.message import ToolResultContent


def evict_old_images(messages: list) -> None:
    """淘汰旧截图：只保留最新一张的图片数据，其余替换为文字摘要。

    此函数从后往前扫描，保留「第一个（最新）」遇到的 ToolResultContent.images，
    其余全部替换为 "[截图已处理：{content}]" 文本占位（~20 tokens）。
    """
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


def collapse_old_tool_results(messages: list, *, keep_recent: int = 4) -> None:
    """工具结果折叠：旧工具输出缩成一行摘要，节省 token。

    只保留最近 keep_recent 个 tool_result 的完整内容，
    其余替换为 "[工具 {tool_use_id} 已完成]" (~15 tokens)。
    """
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
