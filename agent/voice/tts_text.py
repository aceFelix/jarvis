"""TTS 文本清洗 —— 把 LLM 回复中的 markdown 符号与标签噪音剥掉再喂给 TTS。

从 voice_loop 拆出。含:
- 全部清洗正则（markdown 符号、URL、代码围栏、<think>、工具调用占位符）
- _TTSFeeder: 流式增量清洗器（跨 chunk 状态机）

注意: stream_tts.StreamTTSPlayer 内置一份等价清洗逻辑，改动本模块时保持同步。
"""

from __future__ import annotations

import re
from typing import Any

# ---- TTS 文本清洗正则 ----
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")        # [text](url) → text
_URL = re.compile(r"https?://\S+")                      # 裸 URL 删除
_HEADING = re.compile(r"^\s*#{1,6}\s*", re.MULTILINE)   # # 标题
_BULLETS = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)    # - 列表
_QUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)          # > 引用
_EMPHASIS = re.compile(r"(\*\*|__|~~|`)")               # **bold** _em_ ~~del~ `code`
_STANDBY_TAG = re.compile(r"<standby\s*/?>", re.IGNORECASE)  # <standby/> 退下标记（用户听不到）
# <think>...</think> 思考标签：模型按 system prompt 要求把分析性思考放标签里，
# TTS 朗读前过滤掉（用户听不到思考过程，只听最终回答）。DOTALL 让 . 跨行。
_THINK_TAG = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
# 兜底：未闭合的 <think> 标签（模型只开了头没结尾，流式中常见）
_THINK_OPEN_TAG = re.compile(r"<think>.*", re.IGNORECASE | re.DOTALL)
# 模型幻觉的工具调用占位符标签（未走 function calling，仅输出成文本）：
# <bash>...</bash> / <location>...</location> / <mcp__xxx>...</mcp__xxx> / <mcp__xxx/>
# 朗读前必须剥离，否则会被 TTS 读出来。
_TOOL_TAG_PAIR = re.compile(
    r"<\s*(?:bash|shell|location|mcp__[\w.-]+|tool|command|command_exec|exec)\b[^>]*>.*?</\s*(?:bash|shell|location|mcp__[\w.-]+|tool|command|command_exec|exec)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TOOL_TAG_ANY = re.compile(
    r"<\s*/?\s*(?:bash|shell|location|mcp__[\w.-]+|tool|command|command_exec|exec)\b[^>]*>",
    re.IGNORECASE,
)
# 状态机用：开标签 / 闭标签（分别匹配，避免自闭合被误判为进入块）
_TOOL_TAG_OPEN = re.compile(
    r"<\s*(?:bash|shell|location|mcp__[\w.-]+|tool|command|command_exec|exec)\b[^>]*>",
    re.IGNORECASE,
)
_TOOL_TAG_CLOSE = re.compile(
    r"</\s*(?:bash|shell|location|mcp__[\w.-]+|tool|command|command_exec|exec)\s*>",
    re.IGNORECASE,
)
_FENCE = "```"


class _TTSFeeder:
    """把 LLM 文本增量清洗后喂给 TTS。

    处理:
    - 代码块围栏 ```：进入代码块后跳过（不喂 TTS），出来恢复
    - markdown 标记剥离：** ` # - > 等符号去掉，保留文字
    - [text](url) → text；裸 URL 删除
    - 句子级缓冲：累积到句末标点（。！？.!?\n）或 120 字才 flush 给 TTS，
      避免把破碎片段喂给 TTS 导致合成不自然
    """

    def __init__(self, tts: Any) -> None:
        self._tts = tts
        self._in_code = False
        self._buf = ""
        # <think> 标签状态机：True=当前在 <think>...</think> 块内，跳过内容
        self._in_think = False
        # 工具调用占位符标签状态机：True=当前在 <bash> 等标签块内，跳过内容
        self._in_tool_tag = False

    def feed(self, chunk: str) -> None:
        """处理一个 LLM 文本增量。"""
        if not chunk:
            return
        # 先过滤 <think>...</think> 思考内容（跨 chunk 状态机）
        chunk = self._strip_think(chunk)
        if not chunk:
            return
        # 过滤工具调用占位符标签（跨 chunk 状态机）
        chunk = self._strip_tool_tags(chunk)
        if not chunk:
            return
        # 按代码围栏切分，围栏内跳过
        parts = chunk.split(_FENCE)
        cleaned_parts: list[str] = []
        for i, part in enumerate(parts):
            if i > 0:
                self._in_code = not self._in_code
            if not self._in_code:
                cleaned_parts.append(self._clean_inline(part))
        cleaned = "".join(cleaned_parts)
        if not cleaned:
            return
        self._buf += cleaned
        self._maybe_flush()

    def _strip_think(self, text: str) -> str:
        """过滤 <think>...</think> 内容（流式状态机，跨 chunk 处理）。

        - 不在 think 块内: 找 <think> 开始标记，之前的文本保留，
          之后进入 think 块（找 </think> 结束标记）
        - 在 think 块内: 找 </think> 结束标记，之前丢弃，之后保留并退出 think 块
        - 流式末尾未闭合的 <think>: 整段丢弃（_in_think 保持 True）
        """
        out: list[str] = []
        i = 0
        while i < len(text):
            if self._in_think:
                # 找 </think> 结束标记
                end = text.lower().find("</think>", i)
                if end == -1:
                    # 整段都在 think 块内，丢弃
                    return ""
                # 找到结束标记，跳过到标记之后
                i = end + len("</think>")
                self._in_think = False
            else:
                # 找 <think> 开始标记
                start = text.lower().find("<think>", i)
                if start == -1:
                    # 没有开始标记，保留剩余全部
                    out.append(text[i:])
                    break
                # 保留 start 之前的文本
                out.append(text[i:start])
                i = start + len("<think>")
                self._in_think = True
        return "".join(out)

    def _strip_tool_tags(self, text: str) -> str:
        """过滤模型幻觉的工具调用占位符标签（流式状态机，跨 chunk 处理）。

        模型可能在正文输出 <bash>...</bash> / <location>...</location> /
        <mcp__xxx>...</mcp__xxx> 等文本标签而非走 function calling，朗读前丢弃。
        自闭合标签（<mcp__xxx/>）直接跳过，不进入块状态。
        """
        out: list[str] = []
        i = 0
        while i < len(text):
            if self._in_tool_tag:
                m = _TOOL_TAG_CLOSE.search(text, i)
                if not m:
                    return "".join(out)  # 整段仍在标签块内，丢弃
                self._in_tool_tag = False
                i = m.end()
                continue
            m = _TOOL_TAG_OPEN.search(text, i)
            if not m:
                out.append(text[i:])
                break
            if text[m.start():m.end()].rstrip().endswith("/>"):
                # 自闭合标签 <mcp__xxx/>：跳过标签本身，继续扫描
                i = m.end()
                continue
            out.append(text[i:m.start()])
            self._in_tool_tag = True
            i = m.end()
        return "".join(out)

    def flush(self) -> None:
        """收尾：把缓冲区剩余文本喂给 TTS。LLM 回复结束后调用。"""
        # 重置状态机（一轮结束）
        self._in_think = False
        self._in_tool_tag = False
        if self._buf.strip():
            c = self._clean_inline(self._buf)
            if c.strip():
                self._tts.feed(c)
        self._buf = ""

    def _maybe_flush(self) -> None:
        """缓冲到句末标点或超长时 flush。

        首句降低阈值（20 字），让 TTS 尽快开播，减少"文字出完声音才来"的延迟。
        之后等句末标点（。！？）自然断句，保语音节奏感。
        """
        # 优先找句末标点
        flush_to = 0
        for m in re.finditer(r"[。！？!?\n]", self._buf):
            flush_to = m.end()
        if flush_to > 0:
            text = self._buf[:flush_to]
            self._buf = self._buf[flush_to:]
            if text.strip():
                self._tts.feed(text)
            return

        # 逗号/空格处可提前 flush，让 TTS 跟着文字走
        if len(self._buf) >= 20:
            # 找最后一个逗号或空格作为分界
            for m in re.finditer(r"[，,;\s]", self._buf):
                flush_to = m.end()
            if flush_to > 0 and flush_to >= 10:
                text = self._buf[:flush_to]
                self._buf = self._buf[flush_to:]
                if text.strip():
                    self._tts.feed(text)
                return

        # 超过 80 字，强制断句
        if len(self._buf) >= 80:
            text = self._buf
            self._buf = ""
            self._tts.feed(text)

    @staticmethod
    def _clean_inline(text: str) -> str:
        # 工具调用占位符标签优先处理（避免 markdown 剥离 __ 破坏 <mcp__...> 标签）
        text = _TOOL_TAG_PAIR.sub("", text)
        text = _TOOL_TAG_ANY.sub("", text)
        text = _MD_LINK.sub(r"\1", text)
        text = _URL.sub("", text)
        text = _HEADING.sub("", text)
        text = _BULLETS.sub("", text)
        text = _QUOTE.sub("", text)
        text = _EMPHASIS.sub("", text)
        text = _STANDBY_TAG.sub("", text)  # 剥除退下标记（用户听不到）
        return text
