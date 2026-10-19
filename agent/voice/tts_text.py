"""TTS 文本清洗 —— 把 LLM 回复中的 markdown 符号与标签噪音剥掉再喂给 TTS。

从 voice_loop 拆出。含:
- 全部清洗正则（markdown 符号、URL、代码围栏、<think>、工具调用占位符）
- :func:`normalize_for_speech`：口播归一化（Windows 路径、`.exe`、文件后缀、
  `17:20` 这类“人眼写法”换成人话）——主动播报与流式朗读共用
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

# ---- 口播归一化（TN, text normalization）----
# 2026-10 人工走查发现：主动播报把 `C:\Users\...\.jarvis`、`explorer.exe`、
# `17:20` 这类“人眼一看就懂”的写法逐字符念出来，像个无情的阅读机器。屏幕上
# 的标点与路径不是语音，送给合成器前必须换成人说话的样子。
# 与 markdown 剥离的区别：那些是“去符号”，这里是“改读法”，因此单独成函数，
# 供 `clean_for_tts`（整段）与 `_TTSFeeder`（流式按句）共同复用。
_WIN_PATH = re.compile(r"[A-Za-z]:[\\/][^\s，。；、）)（(]*")   # C:\Users\me\.jarvis
_EXE_SUFFIX = re.compile(r"(?i)\.exe\b")
_FILE_SUFFIX = re.compile(
    r"(?i)\.(?:html?|txt|md|markdown|toml|ya?ml|jsonl?|py|js|ts|tsx|jsx|vue|css"
    r"|png|jpe?g|gif|webp|pdf|docx?|xlsx?|pptx?|zip|rar|7z|log|sh|bat|ps1|tmp|lock)\b"
)
_TIME_HM = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")      # 17:20 → 17点20分
_ELLIPSIS = re.compile(r"…+|\.{2,}")
_DASH = re.compile(r"\s+[-–—]{1,2}\s+")                        # 前后带空格才当破折号
_MULTI_PUNCT = re.compile(r"[，,、]{2,}")
_SENT_END = re.compile(r"[。！？!?]$")


def _speak_path(match: re.Match) -> str:
    """Windows 路径 → 只念末段（目录补“文件夹”，文件去扩展名）。

    取末段而不是全路径：用户说“在 jarvis 目录”和“在 C 盘 Users 许发明
    点 jarvis”传达的是同一个信息，后者却要多花四倍时长与一堆无意义音节。
    """
    raw = match.group(0).rstrip("\\/")
    segments = [s for s in re.split(r"[\\/]", raw) if s]
    if not segments:
        return "某个路径"
    tail = segments[-1]
    is_file = bool(_FILE_SUFFIX.search(tail))
    tail = _FILE_SUFFIX.sub("", tail).strip(".") or "某个"
    return f"{tail} 文件夹" if not is_file else tail


def normalize_for_speech(text: str) -> str:
    """把书面写法改成人说话的样子（路径/后缀/时间/省略号）。

    纯函数、零依赖、不调 LLM：播报是高频小事件，不能为“好听”每条烧一次
    token。改写次序有意为之：先处理路径（内部含冒号与反斜杠），再处理
    时间与标点，否则其它规则会把 `17:20` 拆坏。

    刻意**不动**中英文冒号与顿号：它们在散文里是有用的停顿（“提醒您：
    十分钟后开会”），而路径与“应用名：标题”那种拼读负担已在路径规则与
    感知层口播句式（`percept/speech.py`）里消掉，不再全局改写标点。

    @author aceFelix
    """
    if not text:
        return ""
    out = _WIN_PATH.sub(_speak_path, text)
    out = _EXE_SUFFIX.sub("", out)
    out = _TIME_HM.sub(lambda m: f"{int(m.group(1))}点{m.group(2)}分", out)
    out = _FILE_SUFFIX.sub("", out)
    out = _ELLIPSIS.sub("，", out)
    out = _DASH.sub("，", out)
    out = _MULTI_PUNCT.sub("，", out)
    out = re.sub(r"[，]{2,}", "，", out).strip("，,、 \n")
    if out and not _SENT_END.search(out):
        out += "。"
    return out


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
                self._emit(c)
        self._buf = ""

    def _emit(self, text: str) -> None:
        """一个 flush 点：按句归一后喂给合成器。

        为何在 flush 而不在 `_clean_inline`：整段归一需要完整句子，而
        `_clean_inline` 是逐 chunk 跑的——路径被切在两个 chunk 里时正则只能
        匹配半条，归一化反而会念出更碎的东西。
        """
        spoken = normalize_for_speech(text)
        if spoken.strip():
            self._tts.feed(spoken)

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
                self._emit(text)
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
                    self._emit(text)
                return

        # 超过 80 字，强制断句
        if len(self._buf) >= 80:
            text = self._buf
            self._buf = ""
            self._emit(text)

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


def clean_for_tts(text: str) -> str:
    """整段文本一次性清洗（非流式场景：主动播报 TTS 朗读等）。

    与 _TTSFeeder（流式增量状态机）的差异：不做句子级缓冲，直接返回
    清洗后的整段文本；代码围栏内容 / <think> / 工具占位标签按
    整段正则一次性丢弃（流式版需跨 chunk 状态机，整段版无需）。

    末尾统一步过一道口播归一化（路径/后缀/时间/标点），主动播报那些
    “人眼写法”就是在这一步变成人话。

    @author aceFelix
    """
    if not text:
        return ""
    text = _THINK_TAG.sub("", text)
    text = _THINK_OPEN_TAG.sub("", text)
    text = _TOOL_TAG_PAIR.sub("", text)
    text = _TOOL_TAG_ANY.sub("", text)
    # 代码围栏：split 后偶数下标段在围栏外，只保留这些段（围栏内代码不朗读）
    text = "".join(text.split(_FENCE)[0::2])
    return normalize_for_speech(_TTSFeeder._clean_inline(text))
