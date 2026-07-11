"""语音对话循环 —— 贾维斯的核心。

阶段三第三刀（闭环）+ 第四刀（打断 & 体验优化）。

把 STT（听）、LLM（想）、TTS（说）串成实时语音闭环:

    用户说话 → STT 识别 → LLM 流式回复 → 回复流式送 TTS → 边生成边播

第四刀新增:
- **打断（barge-in）**: TTS 播报期间后台监听麦克风，检测到用户开口立即
  tts.stop() + abort_event，切回聆听。无需等它说完才能纠正/追问。
- **TTS 文本清洗**: LLM 回复常带 markdown（**bold**、`code`、# 标题、- 列表、
  > 引用、[text](url)），直接喂 TTS 会读出符号噪声。_TTSFeeder 在喂之前
  清洗，并跳过代码块，让播报自然。
- **错误恢复**: STT/TTS 单轮失败不退出语音模式，自动继续下一轮。

设计要点:
- **流式低延迟**: LLM 每吐一个 TextDelta → _TTSFeeder.feed 清洗 → tts.feed。
  TTS 边收边合成边播，首包延迟约 1s，后续实时。
- **复用 QueryLoop**: 语音模式走完整 QueryLoop（含工具调用），只是给 ctx 注入
  on_assistant_text = feeder.feed。工具调用期间模型说的话也会被播报。
- **TTS 会话包裹**: 每轮 start_stream → feed* → finish。finish 阻塞到音频播完。
- **打断线程模型**: _BargeInWatcher 后台 daemon 线程读麦克风 RMS，超阈值持续
  min_speak_seconds 视为用户开口 → 触发 on_barge_in。主线程在 reply 阶段
  start/stop watcher。与 STT 的麦克风使用时段错开（STT 在 listen 阶段，
  watcher 在 reply 阶段），不冲突。
- **回声抑制**: TTS 音频可能被麦克风拾取导致自打断。用较高阈值
  （_BARGE_IN_THRESHOLD=2500，远高于 STT 的 500）+ 最短发声时长 0.4s 缓解。
  现代笔记本多带 AEC，多数情况 OK；若自触发可调高阈值或关闭 barge_in。

依赖: dashscope + pyaudio，agent.core.query_loop.QueryLoop
"""

from __future__ import annotations

import asyncio
import re
import sys
import threading
import time
from typing import Any

from agent.config.settings import Settings
from agent.core.context import ToolContext
from agent.core.query_loop import QueryLoop
from agent.ui.cli import RichCLI

# 复用 stt 模块的 RMS 计算（Python 3.13 无 audioop）
from agent.voice.stt import _rms

# ---- 退出/唤醒词 ----
# 包含即触发（用户可能说"贾维斯退下吧"），不用完全匹配
_EXIT_WORDS = (
    "退下", "退出", "结束", "拜拜", "再见", "去休息", "休息吧",
    "退下吧", "不用了", "先这样", "exit", "quit", "bye",
)
# 语音打断词：TTS 播报期间检测到这些词立刻停止说话
_INTERRUPT_WORDS = (
    "闭嘴", "停停停", "停停", "停", "等一下", "你别说了", "别说了",
    "你先别说", "等等", "安静", "先别说话", "stop", "wait", "hold on",
    "打住", "听我说",
)
# 唤醒词（容错谐音：Qwen3-ASR 可能把"贾维斯"识别成近音）
_WAKE_WORDS = (
    "贾维斯", "贾维思", "加维斯", "加维思", "贾维",
    "jarvis", "j a r v i s",
)


def _contains_any(text: str, words) -> bool:
    """文本是否包含任一关键词（不区分大小写）。"""
    t = text.lower()
    return any(w.lower() in t for w in words)


def _detect_standby(messages: list) -> bool:
    """检测最近一条 assistant 回复是否含 <standby/> 退下标记。

    遍历 messages 找最后一条 role=assistant，调 get_text() 检测标记。
    工具调用轮次会产生多条 assistant 消息，取最后一条（最终回复）。
    """
    for msg in reversed(messages):
        if getattr(msg, "role", None) == "assistant":
            text = msg.get_text() if hasattr(msg, "get_text") else ""
            if text and _STANDBY_TAG.search(text):
                return True
            # 只查最后一条 assistant 消息即可
            break
    return False


# 待机阶段录音参数（比对话阶段更短，快速循环及时响应唤醒）
_STANDBY_MAX_SECONDS = 6.0
_STANDBY_SILENCE_SECONDS = 1.0

def _voice_log(fmt: str, *args: object) -> None:
    """写调试日志到 daemon.log。"""
    import os as _os, time as _time
    try:
        log_path = _os.path.join(_os.path.expanduser("~"), ".jarvis", "daemon.log")
        _os.makedirs(_os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as _f:
            ts = _time.strftime("%H:%M:%S")
            msg = fmt % args if args else fmt
            _f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

_BARGE_IN_THRESHOLD = 2500     # RMS 阈值，远高于 STT 的 500，避免 TTS 自回声触发
_BARGE_IN_MIN_SPEAK = 0.4      # 持续发声多少秒视为用户开口（防瞬时噪音误触发）
_BARGE_IN_RATE = 16000         # 监听采样率
_BARGE_IN_FRAMES = 1600        # 100ms/帧

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
_FENCE = "```"

# ---- 语音模式 system prompt 追加 ----
# 让 LLM 理解自然语言退下意图（"不聊了"/"闭嘴"/"去忙吧"等），用 <standby/> 标记
# 通知系统切换待机。比硬编码关键词更智能，能覆盖任意表达方式。
_VOICE_MODE_PROMPT = """

# 语音模式特殊指令

你现在处于语音对话模式（STT 听用户说话，TTS 播报你的回复）。

## 退下/待机意图识别（重要）

当用户表达"结束对话、让你退下、待机、闭嘴"等意图时——无论用什么措辞，
例如："退下"、"不聊了"、"你可以闭嘴了"、"行了去忙吧"、"拜拜"、"先这样"、
"去休息"、"不用了"、"我要忙了"、"先这样吧"等——请：

1. 说一句简短礼貌的告别语（如"好的先生，我先退下了，有事随时叫我"）
2. 在告别语之后紧接 <standby/> 标记作为回复的结尾

要求：
- 仅当用户明确想结束对话时才输出 <standby/> 标记
- 正常对话、提问、任务交代绝不使用此标记
- 标记会被系统自动过滤（用户听不到），仅用于通知系统切换到待机状态
- 告别语要简短自然，符合管家身份，不啰嗦

## 语音回复风格

- 思考过程会自动走 reasoning_content 通道（用户看不到也听不到），
  你直接在正文输出最终回答即可，**不要**用 `<think>` 标签包裹思考。
- 回复口语化、简短，适合听（不像文字聊天那样长篇大论）
- 不用 markdown 符号（**、`、#、- 等），直接说自然的话
- 像真人管家一样说话，不要像机器人分析问题
- 不用写代码块，复杂操作用简洁语言描述
- **正文只输出给用户听的最终回答**，不要在正文里写"用户问的是..."、"我应该..."
  等分析性内容——这些放到 reasoning_content 里去思考
"""


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

    def feed(self, chunk: str) -> None:
        """处理一个 LLM 文本增量。"""
        if not chunk:
            return
        # 先过滤 <think>...</think> 思考内容（跨 chunk 状态机）
        chunk = self._strip_think(chunk)
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

    def flush(self) -> None:
        """收尾：把缓冲区剩余文本喂给 TTS。LLM 回复结束后调用。"""
        # 重置 think 状态机（一轮结束）
        self._in_think = False
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
        text = _MD_LINK.sub(r"\1", text)
        text = _URL.sub("", text)
        text = _HEADING.sub("", text)
        text = _BULLETS.sub("", text)
        text = _QUOTE.sub("", text)
        text = _EMPHASIS.sub("", text)
        text = _STANDBY_TAG.sub("", text)  # 剥除退下标记（用户听不到）
        return text


class _BargeInWatcher:
    """后台麦克风能量监听器：检测用户说话，触发打断。

    在 TTS 播报阶段运行（与 STT 录音阶段错开，不冲突）。daemon 线程读麦克风
    帧 → 算 RMS → 持续超阈值 min_speak 秒 → 调 on_barge_in。
    """

    def __init__(self, on_barge_in, *, threshold: int = _BARGE_IN_THRESHOLD,
                 min_speak: float = _BARGE_IN_MIN_SPEAK) -> None:
        self._on_barge = on_barge_in
        self._threshold = threshold
        self._min_speak = min_speak
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pa = None
        self._stream = None
        self._triggered = False

    def start(self) -> None:
        self._stop.clear()
        self._triggered = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._stream is not None:
                self._stream.close()
        except Exception:
            pass
        try:
            if self._pa is not None:
                self._pa.terminate()
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    @property
    def triggered(self) -> bool:
        return self._triggered

    def _run(self) -> None:
        try:
            import pyaudio
            self._pa = pyaudio.PyAudio()
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=_BARGE_IN_RATE,
                input=True,
                frames_per_buffer=_BARGE_IN_FRAMES,
            )
            speaking_since: float | None = None
            while not self._stop.is_set():
                try:
                    frame = self._stream.read(_BARGE_IN_FRAMES, exception_on_overflow=False)
                except Exception:
                    break
                rms = _rms(frame, 2)
                if rms >= self._threshold:
                    if speaking_since is None:
                        speaking_since = time.time()
                    elif time.time() - speaking_since >= self._min_speak:
                        self._triggered = True
                        self._stop.set()
                        try:
                            self._on_barge()
                        except Exception:
                            pass
                        break
                else:
                    speaking_since = None
        except Exception:
            # 麦克风监听失败静默处理，打断功能不可用但不影响对话
            pass


class _KeyBargeInWatcher:
    """键盘打断监听器：TTS 播报期间轮询 ESC 键，按下立即打断。

    用 keyboard.is_pressed() 轮询，不依赖全局钩子（Windows 上全局钩子需管理员权限）。
    后台线程每 100ms 检查一次，响应延迟 < 200ms。
    """

    def __init__(self, on_barge_in, *, key: str = "esc") -> None:
        self._on_barge = on_barge_in
        self._key = key
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._triggered = False
        self._available = False
        try:
            import keyboard  # noqa: F401
            self._available = True
        except Exception:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def start(self) -> None:
        if not self._available:
            return
        self._triggered = False
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def _poll(self) -> None:
        import keyboard
        import time
        while not self._stop.is_set():
            try:
                if keyboard.is_pressed(self._key):
                    self._triggered = True
                    self._on_barge()
                    break
            except Exception:
                pass
            time.sleep(0.1)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None

    @property
    def triggered(self) -> bool:
        return self._triggered


class _VoiceBargeInDetector:
    """语音打断检测器：TTS 播报期间开短 STT 录音，检测中断词。

    为避免 Windows PyAudio 双实例 segfault，每次短录后立即释放 PyAudio。
    若 PyAudio 冲突则静默放弃当次检测，连续无冲突检测到中断词则触发打断。
    """

    def __init__(self, stt: Any, on_barge) -> None:
        self._stt = stt
        self._on_barge = on_barge
        self._stop = threading.Event()

    def run(self) -> None:
        """在后台线程中循环短录，检测到中断词则回调。"""
        import time
        self._stop.clear()
        # 初始延迟：避免和 TTS WS 同时建立连接触发 DashScope RST
        time.sleep(1.0)
        while not self._stop.is_set():
            try:
                result = self._stt.listen(
                    max_seconds=1.5,
                    silence_seconds=0.5,
                    silence_threshold=500,
                    on_partial=lambda t: None,
                    on_open=lambda: None,
                )
                text = (result.get("text") or "").strip()
                if text and _contains_any(text, _INTERRUPT_WORDS):
                    self._on_barge()
                    break
            except Exception:
                pass
            # 节流：每 3 秒最多一次 STT 短录，防止 WS 连接风暴
            if not self._stop.is_set():
                time.sleep(3.0)

    def stop(self) -> None:
        self._stop.set()


async def _voice_loop_round(
    ui: RichCLI,
    settings: Settings,
    loop: QueryLoop,
    ctx: ToolContext,
    tts: Any,
    stt: Any,
) -> bool:
    """跑一轮语音对话（听→想→说）。返回是否继续下一轮。

    False 表示用户要退出语音模式。
    """
    # ---- 1. 听：STT 录音识别 ----
    ui.info("🎤 聆听中...（说话即可，停顿后自动结束）")

    def _on_partial(text: str) -> None:
        sys.stdout.write(f"\r  识别中: {text}")
        sys.stdout.flush()

    t_listen = time.time()
    try:
        result = stt.listen(
            max_seconds=settings.voice_max_seconds,
            silence_seconds=settings.stt_silence_seconds,
            silence_threshold=settings.stt_silence_threshold,
            on_partial=_on_partial,
            on_open=lambda: None,
        )
    except KeyboardInterrupt:
        # Ctrl+C 在聆听阶段: 重新抛出，让 voice_loop 的 except 捕获并退出语音模式
        # （不能 return False，否则 voice_loop 会误认为"退下"进入待机而非退出）
        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()
        raise

    sys.stdout.write("\r" + " " * 80 + "\r")
    sys.stdout.flush()
    listen_elapsed = time.time() - t_listen

    if result.get("error"):
        ui.error(f"识别失败: {result['error']}，再说一次")
        return True

    user_text = result.get("text", "").strip()
    if not user_text:
        ui.warn("没听清，再说一次")
        return True

    if _contains_any(user_text, _EXIT_WORDS):
        ui.info("🛌 退下意图，让 LLM 告别后进入待机...")
        # 不走独立 TTS（和 standby 麦克风 PyAudio 冲突），
        # 把用户的话正常喂给 LLM，LLM 会回复告别语 + <standby/> 标记，
        # 走和自然语言退下完全相同的路径，TTS 生命周期已验证可靠。
        # 不在此处 return，继续往下走 LLM 流式回复流程。

    ui.info(f"🧑 你说: {user_text}（{listen_elapsed:.1f}s）")

    # ---- 2. 想：LLM 流式推理（文字显示在终端，后台不打 TTS）----
    ui._voice_mode = True
    ui._voice_tts_feed = None      # 语音模式：思考/工具进度走 TTS 简短提示
    # 重置思考状态：若上一轮异常退出 Live 可能还存活，先清理
    ui._thinking_started = False
    if getattr(ui, "_thinking_live", None) is not None:
        try:
            ui._thinking_live.stop()
        except Exception:
            pass
        ui._thinking_live = None
    ui._thinking_buf = ""

    # 不预先开 TTS 流——等 LLM 输出完毕，拿到完整文本后再调用 tts.speak()。
    # 原因：流式 TTS 的 WebSocket 长期存活容易被 DashScope RST，切成整段播放。
    ctx.on_assistant_text = None

    # 键盘 ESC 打断监听（LLM 推理中按 ESC = 中断回复）
    interrupted = False

    def _on_barge() -> None:
        nonlocal interrupted
        interrupted = True
        ctx.abort_event.set()

    watchers: list = []
    if settings.voice_barge_in_key:
        kw = _KeyBargeInWatcher(_on_barge)
        if kw.available:
            watchers.append(kw)
    for w in watchers:
        w.start()

    t_reply = time.time()
    try:
        stats = await loop.run(user_text, ctx)
    except KeyboardInterrupt:
        ctx.abort_event.set()
        ui.warn("\n已打断（继续聆听）")
        ctx.abort_event = asyncio.Event()
        ctx.on_assistant_text = None
        ui._voice_mode = False
        ui._voice_tts_feed = None
        for w in watchers:
            w.stop()
        return True
    except Exception as e:
        ui.error(f"回复出错: {type(e).__name__}: {e}")
        ctx.on_assistant_text = None
        ui._voice_mode = False
        ui._voice_tts_feed = None
        for w in watchers:
            w.stop()
        return True
    finally:
        ctx.on_assistant_text = None
        ui._voice_mode = False
        ui._voice_tts_feed = None

    for w in watchers:
        w.stop()

    if interrupted:
        ui.warn("🔇 检测到打断（ESC），已停止回复（继续聆听）")
        ctx.abort_event = asyncio.Event()
        return True

    # ---- 3. 说：拿到"想"阶段的完整文本，整段转语音 ----
    # 从最后一条 assistant message 提取纯文本（不含思考内容）
    reply_text = ""
    for msg in reversed(ctx.messages):
        if msg.role == "assistant":
            reply_text = msg.get_text()
            if reply_text.strip():
                break

    reply_elapsed = time.time() - t_reply

    if reply_text.strip():
        # 清洗特殊标记:
        # - <standby/> 退下标记（用户听不到）
        # - <think>...</think> 思考内容（模型按 system prompt 把分析放标签里，用户听不到）
        # - 未闭合的 <think> 标签（流式末尾可能没结尾，兜底过滤）
        clean_text = _STANDBY_TAG.sub("", reply_text)
        clean_text = _THINK_TAG.sub("", clean_text)
        clean_text = _THINK_OPEN_TAG.sub("", clean_text)
        clean_text = clean_text.strip()
        if clean_text:
            try:
                tts_stats = tts.speak(clean_text, on_first_data=lambda: None)
            except Exception:
                tts_stats = {}
            if settings.verbose and isinstance(tts_stats, dict):
                ui.info(
                f"  [回复 {reply_elapsed:.1f}s，音频 {tts_stats.get('total_seconds', 0)}s，"
                f"首包 {tts_stats.get('first_package_delay_ms', '?')}ms，"
                f"iter={stats.iterations} tools={stats.tool_calls}]"
            )
    else:
        # 无文本（纯工具调用等），跳过 TTS
        if settings.verbose:
            ui.info(f"  [回复 {reply_elapsed:.1f}s，iter={stats.iterations} tools={stats.tool_calls}]")

    # TTS speak 内部已完成 WS 关闭，无需额外等待

    # LLM 退下意图检测：回复含 <standby/> 标记 → 进待机。
    # 覆盖"不聊了"/"闭嘴"/"去忙吧"等任意自然语言表达，比硬编码关键词更智能。
    # 标记已被 _TTSFeeder 清洗，用户听不到，只在此检测。
    ui._voice_mode = False
    ui._voice_tts_feed = None
    if _detect_standby(ctx.messages):
        ui.info("🛌 贾维斯已退下（说「贾维斯」唤醒）")
        return False

    return True


async def _standby_round(ui: RichCLI, settings: Settings, stt: Any) -> str:
    """待机一轮：录短段，返回识别文本。不在这里判断唤醒词（调用方判断）。

    返回识别到的文本（可能为空）。Ctrl+C 由调用方处理。
    """
    try:
        result = stt.listen(
            max_seconds=_STANDBY_MAX_SECONDS,
            silence_seconds=_STANDBY_SILENCE_SECONDS,
            silence_threshold=settings.stt_silence_threshold,
            on_partial=lambda t: None,
            on_open=lambda: None,
        )
    except KeyboardInterrupt:
        raise
    if result.get("error"):
        # 待机时偶发错误不刷屏，静默继续
        return ""
    return result.get("text", "").strip()


async def voice_loop(
    ui: RichCLI,
    settings: Settings,
    loop: QueryLoop,
    ctx: ToolContext,
    pause_event: threading.Event | None = None,
) -> None:
    """进入语音模式。对话 ⇄ 待机 循环，直到 Ctrl+C 彻底退出。

    流程:
        对话阶段（听→想→说 多轮）
          └─ 说「退下」或 LLM 识别到结束意图（<standby/>）→ 进入待机阶段
        待机阶段（循环短录，等唤醒词「贾维斯」）
          └─ 听到唤醒词 → 回到对话阶段
        任何阶段 Ctrl+C → 彻底退出，回文本 REPL

    退下意图识别双层机制:
        1. 快速匹配: 听阶段检测 _EXIT_WORDS（"退下"/"拜拜"等明确词），
           不走独立 TTS（避免 PyAudio 冲突），正常喂给 LLM 走到别+<standby/> 路径
        2. LLM 意图: 自然语言（"不聊了"/"闭嘴"/"去忙吧"等）由 LLM 理解，
           回复末尾输出 <standby/> 标记，系统检测后进待机（附告别语）

    语音开关（跨进程文件信号）:
        托盘「语音对话」菜单切换 ~/.jarvis/voice_enabled 文件:
        - false → voice_loop 进入待机（类似说"退下"），但仍听唤醒词"贾维斯"
        - true  → voice_loop 恢复正常对话
        用文件而非 threading.Event: daemon 以 DETACHED_PROCESS 子进程运行，
        文件是跨进程单一可信源（SSOT）。

    pause_event: 已废弃，保留参数仅为向后兼容，内部不再使用。

    @author aceFelix
    """
    try:
        from agent.voice import create_stt
        from agent.voice.tts import CosyVoiceTTS
    except ImportError as e:
        ui.error(f"语音模块不可用: {e}")
        return

    stt = create_stt(
        api_key=settings.api_key,
        model=settings.stt_model,
    )
    tts = CosyVoiceTTS(
        api_key=settings.api_key,
        model=settings.tts_model,
        voice=settings.tts_voice,
        volume=settings.tts_volume,
        speech_rate=settings.tts_speech_rate,
        pitch_rate=settings.tts_pitch_rate,
    )

    # 语音对话模式：保持 thinking 开启，让思考走 reasoning_content 通道
    # （ThinkingDelta 不触发 on_assistant_text，TTS 不会朗读思考过程）。
    # 若关闭 thinking，Qwen3 仍会在 content 通道输出"思考-然后-答案"格式，
    # 反而会被 TTS 朗读出来——更糟。
    # 用 QueryLoop.set_thinking_enabled 统一接口（覆盖所有 provider 类型），
    # _try_failover 重建 provider 后会自动同步此状态。
    _thinking_was_enabled = loop.is_thinking_enabled()
    loop.set_thinking_enabled(True)

    # 追加语音模式指令到 system prompt：让 LLM 理解自然语言退下意图，
    # 用 <standby/> 标记通知系统切换待机。覆盖"不聊了"/"闭嘴"等任意表达。
    # 访问 loop._system（构造时传入，run 里用 system=self._system 发给 provider）
    try:
        if hasattr(loop, "_system") and _VOICE_MODE_PROMPT not in loop._system:
            loop._system = loop._system + _VOICE_MODE_PROMPT
    except Exception:
        pass

    ui.info("=" * 56)
    ui.info("🎙️  语音对话模式已开启")
    ui.info(f"   STT: {settings.stt_model}")
    ui.info(f"   TTS: {settings.tts_model} / {settings.tts_voice}")
    hints = []
    if settings.voice_barge_in_key:
        try:
            import keyboard  # noqa: F401
            hints.append("播报中按 ESC 打断")
        except ImportError:
            ui.info("   ⚠ keyboard 库未安装，ESC 打断不可用（pip install keyboard）")
    if settings.voice_barge_in:
        hints.append("说「闭嘴」打断")
    barge_hint = " · ".join(hints)
    barge_hint = (barge_hint + " · ") if barge_hint else ""
    ui.info(f"   {barge_hint}说「退下/不聊了/去忙吧」进入待机 · 说「贾维斯」唤醒 · Ctrl+C 退出")
    ui.info("=" * 56)

    # 降级链：连续失败 N 次自动降级
    tts_fail_count = 0
    stt_fail_count = 0
    DEGRADE_THRESHOLD = 3

    # 跨进程语音开关状态（从文件读取，托盘菜单控制）
    from agent.daemon.voice_state import is_voice_enabled
    voice_was_enabled = is_voice_enabled()

    try:
        in_dialog = True  # True=对话阶段, False=待机阶段
        while True:
            # ---- 语音开关检查（跨进程文件信号）----
            # 托盘「语音对话」菜单切换 ~/.jarvis/voice_enabled 文件:
            # - false → 进入待机（类似说"退下"），但仍听唤醒词"贾维斯"
            # - true  → 恢复正常对话
            voice_now = is_voice_enabled()
            if not voice_now:
                # 语音被关闭 → 进入待机（但不退出 voice_loop）
                if voice_was_enabled:
                    _voice_log("[voice_loop] 语音开关已关闭，进入待机")
                    ui.info("🔇 语音对话已关闭（进入待机，说「贾维斯」仍可唤醒）")
                    in_dialog = False
                # 待机阶段：循环短录，等唤醒词
                text = await _standby_round(ui, settings, stt)
                if text and _contains_any(text, _WAKE_WORDS):
                    # 唤醒词触发 → 临时恢复对话（即使开关是关闭的）
                    ui.info("🔊 唤醒，回到对话模式（语音开关仍为关闭，可随时说「退下」）")
                    in_dialog = True
                voice_was_enabled = voice_now
                continue

            if voice_now and not voice_was_enabled:
                # 语音从关闭→开启，恢复正常对话
                _voice_log("[voice_loop] 语音开关已开启，恢复对话")
                ui.info("🎙️ 语音对话已开启，随时待命")
                in_dialog = True
            voice_was_enabled = voice_now

            if in_dialog:
                # 对话阶段：连续多轮，直到用户说退下
                cont = True
                while cont:
                    cont = await _voice_loop_round(ui, settings, loop, ctx, tts, stt)
                    # 降级计数：TTS 失败时记录
                    if cont and not getattr(ctx, 'tts_ok', True):
                        tts_fail_count += 1
                    else:
                        tts_fail_count = 0

                    if tts_fail_count >= DEGRADE_THRESHOLD and not getattr(ctx, 'tts_degraded_shown', False):
                        ui.warn("⚠ TTS 连续失败 3 次，已切换纯文本模式（语音对话结束后恢复）")
                        ctx.tts_degraded_shown = True
                    ctx.tts_ok = True
                # _voice_loop_round 返回 False = 用户退下，切到待机
                in_dialog = False
                ui.info("💤 待机中，说「贾维斯」唤醒我")
            else:
                # 待机阶段：循环短录，等唤醒词
                text = await _standby_round(ui, settings, stt)
                if text and _contains_any(text, _WAKE_WORDS):
                    ui.info("🔊 唤醒，回到对话模式")
                    in_dialog = True
                # 不含唤醒词则静默继续待机（不打印识别内容，避免刷屏）
    except KeyboardInterrupt:
        ui.info("\n退出语音模式")
    finally:
        # 恢复思考模式 + 清理 TTS（用 try 包裹整个 finally，
        # 防止用户快速双击 Ctrl+C 时二次 KeyboardInterrupt 打断清理流程）
        try:
            loop.set_thinking_enabled(_thinking_was_enabled)
            try:
                tts.stop()
            except Exception:
                pass
        except Exception:
            pass
        ui.info("已回到文本模式（输入 /help 查看命令）")
