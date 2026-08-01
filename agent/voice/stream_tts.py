"""句子级流式 TTS 播放器 — 语音模块阶段 1 改造核心。

把 LLM 文本流实时转为语音，不等生成完毕就开始播：

    LLM 输出 "今天天气不错，22到30度。建议带伞。"
              ↑ 第一句完整              ↑ 第二句完整
              → 立即送 TTS 合成          → 排队播放

与现有 CosyVoiceTTS.speak() 的差异:
- speak(): 等 LLM 全文 → 一次合成播放，延迟 = LLM 生成 + TTS 合成
- StreamTTSPlayer: 检测句子边界 → 立即送 TTS 合成，延迟 = 首句生成 + 首句合成

设计要点:
- **句子级流式**: 缓冲直到句末标点（。！？!?\n），完整句子立即送
  CosyVoice WebSocket。TTS 合成与 LLM 生成并行，首句延迟控制在 ~500ms。
- **Markdown 清洗**: 喂给 TTS 前剥离 **bold**、`code`、# 标题、- 列表、
  [链接](url) 等标记，<think> 标签和 <standby/> 标记一并过滤。
- **线程安全**: 句子缓冲+清洗在主线程（ctx.on_assistant_text 回调），
  TTS 合成调用在同一个调用栈（streaming_call 非阻塞，仅发送文本到 WS）。
- **WS 异常降级**: 若 CosyVoice WS 中途断开，自动切换到整段播放模式
  （收集剩余文本，finish 时用 CosyVoiceTTS.speak() 兜底）。
- **复用现有基建**: 通过 audio.py 的全局 PyAudio 单例播放，避免 segfault。

用法:
    player = StreamTTSPlayer(api_key=..., model=..., voice=...)
    player.start()

    # LLM 流式输出期间（ctx.on_assistant_text 回调）
    player.feed("今天天气不错，")      # 缓冲，不合成
    player.feed("22到30度。")          # 第一句完整 → 送 TTS（音频开始播）
    player.feed("建议带伞。出门注意")   # 第二句完整 → 排队送 TTS

    # LLM 输出完毕
    player.finish()  # flush 剩余缓冲 + 阻塞等待全部音频播完
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

# 复用 tts.py 的 CosyVoiceTTS 基建
from agent.voice.tts import CosyVoiceTTS

# ---- Markdown 清洗正则（与 voice_loop._TTSFeeder 保持一致）----
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_HEADING = re.compile(r"^\s*#{1,6}\s*", re.MULTILINE)
_BULLETS = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_QUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__|~~|`)")
_STANDBY_TAG = re.compile(r"<standby\s*/?>", re.IGNORECASE)
_THINK_TAG = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN_TAG = re.compile(r"<think>.*", re.IGNORECASE | re.DOTALL)
# 模型幻觉的工具调用占位符标签（未走 function calling，仅输出成文本）：
# <bash>...</bash> / <location>...</location> / <mcp__xxx>...</mcp__xxx> / <mcp__xxx/>
# <bash_command> / <deferred_tool> / <tool_call> / <execute_command> 等变种
# 朗读前必须剥离，否则会被 TTS 读出来。
_TOOL_TAG_NAMES = (
    r"bash|shell|location|mcp__[\w.-]+|tool|command|command_exec|exec|"
    r"bash_command|deferred_tool|tool_call|execute_command|run_command|"
    r"user_instructions|function_call"
)
_TOOL_TAG_PAIR = re.compile(
    rf"<\s*(?:{_TOOL_TAG_NAMES})\b[^>]*>.*?</\s*(?:{_TOOL_TAG_NAMES})\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TOOL_TAG_ANY = re.compile(
    rf"<\s*/?\s*(?:{_TOOL_TAG_NAMES})\b[^>]*>",
    re.IGNORECASE,
)
# 状态机用：开标签 / 闭标签（分别匹配，避免自闭合被误判为进入块）
_TOOL_TAG_OPEN = re.compile(
    rf"<\s*(?:{_TOOL_TAG_NAMES})\b[^>]*>",
    re.IGNORECASE,
)
_TOOL_TAG_CLOSE = re.compile(
    rf"</\s*(?:{_TOOL_TAG_NAMES})\s*>",
    re.IGNORECASE,
)
_FENCE = "```"

# 首句阈值：缓冲到 20 字就算没有句末标点也送 TTS，让声音尽快出来
_FIRST_SENTENCE_MIN = 20

# WS 空闲超时：若 LLM 两句话之间间隔超过此值，认为 WS 可能已被 RST
_WS_IDLE_TIMEOUT = 10.0


def _clean_inline(text: str) -> str:
    """剥离单行内的 markdown 标记。"""
    # 工具调用占位符标签优先处理（避免 markdown 剥离 __ 破坏 <mcp__...> 标签）
    text = _TOOL_TAG_PAIR.sub("", text)
    text = _TOOL_TAG_ANY.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _HEADING.sub("", text)
    text = _BULLETS.sub("", text)
    text = _QUOTE.sub("", text)
    text = _EMPHASIS.sub("", text)
    text = _STANDBY_TAG.sub("", text)
    return text


class StreamTTSPlayer:
    """句子级流式 TTS 播放器。

    与 CosyVoiceTTS 配合：start() 创建 WS 会话，feed() 逐句推送，
    finish() 等全部音频播放完毕。
    """

    # 内部状态：_INIT → _PLAYING → _DONE
    # 若 WS 断开则 _DEGRADED（降级到整段播放）

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "cosyvoice-v3-flash",
        voice: str = "longanlang_v3",
        volume: int = 50,
        speech_rate: float = 1.0,
        pitch_rate: float = 1.0,
        # 降级用
        debug_log: bool = False,
    ) -> None:
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY 未配置")
        self._tts = CosyVoiceTTS(
            api_key=api_key,
            model=model,
            voice=voice,
            volume=volume,
            speech_rate=speech_rate,
            pitch_rate=pitch_rate,
        )
        self._debug = debug_log

        # 缓冲与状态
        self._buf = ""                # 未完成的句子缓冲
        self._in_code = False         # 代码块围栏状态机
        self._in_think = False        # <think> 标签状态机
        self._in_tool_tag = False     # 工具调用占位符标签状态机（跨 chunk）
        self._started = False
        self._degraded = False        # WS 断开 → 降级，收集文本用 speak() 兜底
        self._fallback_buf = ""       # 降级时收集的剩余文本

        # 统计
        self._first_feed_time: float | None = None
        self._first_audio_time: float | None = None
        self._sentence_count = 0
        self._total_chars = 0

    # ---- 公开 API ----

    def start(self) -> bool:
        """创建 CosyVoice TTS 流式会话。

        Returns:
            True 成功，False 失败（此时后续 feed 会被丢弃，finish 走降级）。
        """
        if self._started:
            return True
        self._started = True

        ok = self._tts.start_stream(on_first_data=self._on_first_audio)
        if not ok:
            self._degraded = True
            if self._debug:
                self._log("TTS 流式会话创建失败，降级到整段播放")
        else:
            if self._debug:
                self._log("TTS 流式会话已创建")
        return ok

    def feed(self, text: str) -> None:
        """喂入 LLM 文本增量（ctx.on_assistant_text 回调）。

        线程安全：此方法在 async event loop 线程被调用，
        内部只有缓冲操作（append 字符串）+ 非阻塞 WS 发送。
        """
        if not text:
            return
        if self._first_feed_time is None:
            self._first_feed_time = time.time()

        self._total_chars += len(text)

        # 清洗流程：<think> 过滤 → 代码块围栏 → markdown 剥离
        cleaned = self._clean_text(text)
        if not cleaned:
            return

        if self._degraded:
            # 降级模式：只收集，finish 时统一 speak()
            self._fallback_buf += cleaned
            return

        self._buf += cleaned
        self._flush_sentences()

    def finish(self) -> dict[str, Any]:
        """喂完最后文本，阻塞直到全部音频播完。

        Returns:
            统计字典: {sentence_count, total_chars, first_feed_to_first_audio_ms,
                      first_feed_to_finish_ms, degraded?}
        """
        if self._degraded:
            return self._finish_degraded()

        # flush 剩余缓冲（未完成句 + 代码围栏内残留）
        self._in_code = False
        self._in_think = False
        self._in_tool_tag = False
        if self._buf.strip():
            text = _clean_inline(self._buf)
            if text.strip():
                self._sentence_count += 1
                self._tts.feed(text)
        self._buf = ""

        # 结束流式合成，阻塞到全部音频播完
        stats = self._tts.finish()

        return self._build_stats(stats)

    def stop(self) -> None:
        """中断当前播放/合成（打断用）。"""
        self._tts.stop()

    # ---- 内部：句子检测与推送 ----

    def _flush_sentences(self) -> None:
        """检测句子边界，将完整句子通过 streaming_call 送 TTS。

        策略:
        - 首句降阈值 (20 字)——让声音尽快出来
        - 后续等句末标点（。！？!?\n）自然断句
        - 缓冲区超过 80 字强制断句
        """
        if self._sentence_count == 0:
            # 首句：尽快出声
            threshold = _FIRST_SENTENCE_MIN
        else:
            threshold = 80

        # 先找句末标点
        flush_to = 0
        for m in re.finditer(r"[。！？!?\n]", self._buf):
            flush_to = m.end()
            break  # 只取第一个句末标点

        if flush_to > 0:
            # 找到一个完整句
            sentence = self._buf[:flush_to]
            self._buf = self._buf[flush_to:]
            if sentence.strip():
                self._send_sentence(sentence)
            return

        # 没有句末标点但超长：强制断句
        if len(self._buf) >= threshold:
            # 在最后一个逗号/空格处断开
            cut = 0
            for m in re.finditer(r"[，,;\s]", self._buf):
                cut = m.end()
            if cut >= threshold // 2:
                sentence = self._buf[:cut]
                self._buf = self._buf[cut:]
            else:
                sentence = self._buf
                self._buf = ""
            if sentence.strip():
                self._send_sentence(sentence)

    def _send_sentence(self, text: str) -> None:
        """送一句到 CosyVoice WS。同时尝试重连（若上次 WS 已断开）。"""
        cleaned = _clean_inline(text)
        if not cleaned.strip():
            return

        try:
            self._tts.feed(cleaned)
        except Exception as e:
            # WS 写入失败 → 降级
            self._degraded = True
            self._fallback_buf += cleaned
            self._fallback_buf += self._buf  # 把未冲刷的也收了
            self._buf = ""
            if self._debug:
                self._log(f"WS 写入失败: {e}，降级到整段播放")

        self._sentence_count += 1
        if self._debug:
            preview = cleaned[:30].replace("\n", "\\n")
            self._log(f"句 #{self._sentence_count} ({len(cleaned)}字): {preview}...")

    # ---- 内部：Markdown 清洗 ----

    def _clean_text(self, text: str) -> str:
        """清洗 LLM 输出文本：<think> / 工具标签过滤 + 代码块围栏 + markdown 剥离。

        维护 _in_code / _in_think / _in_tool_tag 三个跨 chunk 状态机。
        """
        # 1. <think> 标签过滤（跨 chunk 状态机）
        text = self._strip_think(text)
        if not text:
            return ""

        # 1.5 工具调用占位符标签过滤（跨 chunk 状态机）
        text = self._strip_tool_tags(text)
        if not text:
            return ""

        # 2. 代码块围栏状态机
        parts = text.split(_FENCE)
        cleaned_parts: list[str] = []
        for i, part in enumerate(parts):
            if i > 0:
                self._in_code = not self._in_code
            if not self._in_code:
                cleaned_parts.append(_clean_inline(part))
        return "".join(cleaned_parts)

    def _strip_think(self, text: str) -> str:
        """过滤 <think>...</think> 内容（跨 chunk 流式状态机）。"""
        out: list[str] = []
        i = 0
        while i < len(text):
            if self._in_think:
                end = text.lower().find("</think>", i)
                if end == -1:
                    return "".join(out)  # 整段在 think 内，丢弃
                i = end + len("</think>")
                self._in_think = False
            else:
                start = text.lower().find("<think>", i)
                if start == -1:
                    out.append(text[i:])
                    break
                out.append(text[i:start])
                i = start + len("<think>")
                self._in_think = True
        return "".join(out)

    def _strip_tool_tags(self, text: str) -> str:
        """过滤模型幻觉的工具调用占位符标签（跨 chunk 流式状态机）。

        模型可能在正文输出 <bash>...</bash> / <location>...</location> /
        <mcp__xxx>...</mcp__xxx> 等文本而非走 function calling，朗读前丢弃。
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

    # ---- 内部：降级 & 统计 ----

    def _finish_degraded(self) -> dict[str, Any]:
        """降级路径：用 CosyVoiceTTS.speak() 整段播放收集到的文本。"""
        self._in_code = False
        self._in_think = False
        self._in_tool_tag = False
        if self._buf.strip():
            self._fallback_buf += _clean_inline(self._buf)
            self._buf = ""

        text = self._fallback_buf
        # 对整段文本做 think 标签过滤
        text = _THINK_TAG.sub("", text)
        text = _THINK_OPEN_TAG.sub("", text)
        text = _STANDBY_TAG.sub("", text)
        # 工具调用占位符标签
        text = _TOOL_TAG_PAIR.sub("", text)
        text = _TOOL_TAG_ANY.sub("", text)
        text = text.strip()

        if text:
            if self._debug:
                self._log(f"降级 speak() {len(text)} 字")
            stats = self._tts.speak(text)
        else:
            stats = {}

        return self._build_stats(stats, degraded=True)

    def _on_first_audio(self) -> None:
        """收到首块音频时的回调（CosyVoice 回调线程触发）。"""
        if self._first_audio_time is None:
            self._first_audio_time = time.time()
            if self._debug:
                delay_ms = int((self._first_audio_time - (self._first_feed_time or self._first_audio_time)) * 1000)
                self._log(f"首块音频到达，延迟 {delay_ms}ms")

    def _build_stats(self, tts_stats: dict[str, Any] | None = None,
                     *, degraded: bool = False) -> dict[str, Any]:
        """组装统计信息。"""
        t0 = self._first_feed_time or 0
        t1 = self._first_audio_time or 0
        t2 = time.time()

        result: dict[str, Any] = {
            "sentence_count": self._sentence_count,
            "total_chars": self._total_chars,
            "degraded": degraded,
        }

        if t0 and t1:
            result["first_feed_to_first_audio_ms"] = int((t1 - t0) * 1000)
        if t0:
            result["first_feed_to_finish_ms"] = int((t2 - t0) * 1000)

        if isinstance(tts_stats, dict):
            result["audio_seconds"] = tts_stats.get("total_seconds", 0)
            result["first_package_delay_ms"] = tts_stats.get("first_package_delay_ms")

        return result

    # ---- 工具方法 ----

    @property
    def is_degraded(self) -> bool:
        return self._degraded

    @property
    def stats_snapshot(self) -> dict[str, Any]:
        """运行时统计快照（不阻塞）。"""
        return {
            "sentence_count": self._sentence_count,
            "total_chars": self._total_chars,
            "buf_pending": len(self._buf),
            "degraded": self._degraded,
        }

    @staticmethod
    def _log(msg: str) -> None:
        """调试日志。"""
        import os as _os
        try:
            log_path = _os.path.join(_os.path.expanduser("~"), ".jarvis", "daemon.log")
            _os.makedirs(_os.path.dirname(log_path), exist_ok=True)
            import time as _time
            ts = _time.strftime("%H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] [stream_tts] {msg}\n")
        except Exception:
            pass
