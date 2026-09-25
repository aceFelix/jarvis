"""工作台附件与历史渲染纯函数（自 engine.py 拆出，控单文件行数）。

职责单一：message 指令附件字段 → 引擎消息块的转换，以及历史消息 →
前端渲染结构的折叠；无状态、无 IO，便于单测（tests/ui 经 engine
再导出引用 _messages_to_render，口径不变）。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.message import Message

# 单个附带文本文件的正文上限（字符）：防一个巨型文件撑爆单轮上下文，
# 截断保留头部并附提示（与 serve/server.py 的入队校验上限独立，双保险）。
# @author aceFelix
_MAX_ATTACH_FILE_CHARS = 20_000


def _parse_images(images: Any) -> list[Any]:
    """把 message 指令的 images 字段（[{data, media_type}]）转 ImageContent 列表。

    非法条目（非 dict / 缺 data）静默跳过；base64 合法性校验留给 provider 层。
    @author aceFelix
    """
    from agent.core.message import ImageContent

    out: list[Any] = []
    for item in images or []:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, str) or not data:
            continue
        out.append(ImageContent(data=data, media_type=str(item.get("media_type") or "image/png")))
    return out


def _compose_with_files(text: str, files: Any) -> str:
    """把文本文件附件（[{name, content}]）以代码块形式拼进消息正文。

    超长文件截断到 _MAX_ATTACH_FILE_CHARS 并附提示；空内容文件跳过。
    @author aceFelix
    """
    composed = text
    for item in files or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "未命名.txt")
        content = str(item.get("content") or "")
        if not content.strip():
            continue
        if len(content) > _MAX_ATTACH_FILE_CHARS:
            content = (
                content[:_MAX_ATTACH_FILE_CHARS]
                + f"\n…（文件过长，仅展示前 {_MAX_ATTACH_FILE_CHARS} 字符）"
            )
        composed += f"\n\n【附带文件 {name}】\n```\n{content}\n```"
    return composed


def _messages_to_render(messages: list[Message]) -> list[dict[str, Any]]:
    """把历史消息转成前端渲染结构（只保留文本块，工具块折叠为提示）。

    图片块（用户附件）折叠为「[图片×N]」标记行，历史回放不传 base64。
    @author aceFelix
    """
    from agent.core.message import ImageContent, TextContent, ToolResultContent, ToolUseContent

    out: list[dict[str, Any]] = []
    for m in messages:
        texts: list[str] = []
        tool_count = 0
        img_count = 0
        for b in m.content:
            if isinstance(b, TextContent) and b.text.strip():
                texts.append(b.text)
            elif isinstance(b, (ToolUseContent, ToolResultContent)):
                tool_count += 1
            elif isinstance(b, ImageContent):
                img_count += 1
        if not texts and not tool_count and not img_count:
            continue
        # 纯工具结果消息（role=user 的工具回填）不进历史回放；
        # assistant 纯工具消息保留为"工具调用 ×N"提示行。
        if not texts and not img_count and m.role != "assistant":
            continue
        body = "\n\n".join(texts)
        if img_count:
            marker = f"[图片×{img_count}]"
            body = f"{body}\n{marker}" if body else marker
        item: dict[str, Any] = {"role": m.role, "text": body}
        if tool_count:
            item["tool_count"] = tool_count
        out.append(item)
    return out
