"""语音对话循环 —— 贾维斯的核心（解耦版：事件协议驱动）。

阶段三第三刀（闭环）+ 第四刀（打断 & 体验优化）；二期解耦：移除对终端
RichCLI 的直接依赖，一切对外输出经 ``voice_events.VoiceSessionEvents``
回调协议外抛，打断经外部 ``interrupt_event`` 注入，使同一循环可被 REPL
（RichCLIVoiceAdapter）与 serve / 桌面壳（ServeVoiceAdapter）双宿主复用。

把 STT（听）、LLM（想）、TTS（说）串成实时语音闭环:

    用户说话 → STT 识别 → LLM 流式回复 → 回复流式送 TTS → 边生成边播

第四刀新增:
- **打断（barge-in）**: TTS 播报期间检测到打断信号（interrupt_event 置位，
  来源可为键盘 ESC / 麦克风能量 / 桌面壳按钮）立即 tts.stop() + abort_event，
  切回聆听。无需等它说完才能纠正/追问。
- **TTS 文本清洗**: LLM 回复常带 markdown，直接喂 TTS 会读出符号噪声。
  清洗逻辑见 tts_text 模块。
- **错误恢复**: STT/TTS 单轮失败不退出语音模式，自动继续下一轮。

设计要点:
- **流式低延迟**: LLM 每吐一个 TextDelta → TTS 清洗 → tts.feed，同时经
  events.on_ai_text_delta 外抛给宿主上屏。
- **复用 QueryLoop**: 语音模式走完整 QueryLoop（含工具调用），给 ctx 注入
  on_assistant_text = 组合 feeder（喂 TTS + 外抛 delta）。
- **TTS 会话包裹**: 每轮 start_stream → feed* → finish。finish 阻塞到音频播完。
  音频在本地（serve 子进程 / REPL 同机）pyaudio 播放，不向 GUI 传音频流。
- **打断线程模型**: 每轮起一个 daemon 轮询线程监听 interrupt_event（50ms
  粒度），置位即触发 _on_barge（abort + 停 TTS）。打断来源由宿主适配器挂接
  （REPL=键盘 ESC，serve=麦克风 _BargeInWatcher + 桌面壳 voice.interrupt）。
- **音频 I/O 离事件循环**: stt.listen / StreamTTSPlayer.start / finish 均为
  阻塞 pyaudio / 网络调用，统一经 asyncio.to_thread 放工作线程跑，保证宿主
  事件循环（serve 指令循环 / REPL 主循环）不被录音播放饿死，voice.stop /
  voice.interrupt 等指令在聆听/播报期间也能即时被消费（修复桌面壳按钮无响应）。
- **回声抑制**: 麦克风 barge-in 用较高阈值缓解 TTS 自触发（见 barge_in）。

依赖: dashscope + pyaudio，agent.core.query_loop.QueryLoop

@author aceFelix
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from agent.config.settings import Settings
from agent.core.context import ToolContext
from agent.core.query_loop import QueryLoop

from agent.voice.tts_text import _STANDBY_TAG
from agent.voice.voice_config import (
    _EXIT_WORDS,
    _STANDBY_MAX_SECONDS,
    _STANDBY_SILENCE_SECONDS,
    _VOICE_MODE_PROMPT,
    _WAKE_WORDS,
    _contains_any,
    _voice_api_key,
    _voice_log,
)
from agent.voice.voice_events import (
    LEVEL_WARN,
    STATE_DIALOG,
    STATE_EXITED,
    STATE_LISTENING,
    STATE_SPEAKING,
    STATE_STANDBY,
    STATE_THINKING,
    VoiceSessionEvents,
)

# 打断轮询粒度（秒）：interrupt_event 置位后最多 50ms 内响应
_INTERRUPT_POLL_SEC = 0.05


def _detect_standby(messages: list, since_index: int = 0) -> bool:
    """检测最近一条 assistant 回复是否含 <standby/> 退下标记。

    遍历 messages 找最后一条 role=assistant，调 get_text() 检测标记。
    工具调用轮次会产生多条 assistant 消息，取最后一条（最终回复）。

    防止误触发：若去掉 <standby/> 后的正文超过 30 字，
    说明模型在正常回答的同时输出了标记（如 deepseek-v4-flash 误触），
    此时忽略标记，不进入待机。

    Args:
        messages: 消息列表
        since_index: 只检查此索引之后的消息（用于限制检测范围到本轮新增消息）。
                     为 0 时检查全部消息（兼容旧调用）。
    """
    for msg in reversed(messages):
        if getattr(msg, "role", None) == "assistant":
            # 如果指定了 since_index，跳过旧消息
            if since_index > 0:
                try:
                    msg_idx = messages.index(msg)
                    if msg_idx < since_index:
                        continue
                except ValueError:
                    pass
            text = msg.get_text() if hasattr(msg, "get_text") else ""
            if text and _STANDBY_TAG.search(text):
                body = _STANDBY_TAG.sub("", text).strip()
                if len(body) > 30:
                    return False  # 有实质内容，不是真正的退下
                return True
            # 只查最后一条 assistant 消息即可
            break
    return False


def _clean_standby_messages(messages: list) -> None:
    """移除最后一条含 <standby/> 的 goodbye 消息及其前一条 user 消息。

    在 voice_loop() 退出时调用，避免残留的"退下"意图在下次语音中
    混淆模型或导致 _detect_standby 误触发。

    只移除正文 <= 30 字的短 goodbye 消息，不影响正常对话内容。
    同时移除 goodbye 前一条 user 消息（通常是"退下吧"），
    防止模型在下轮看到"退下吧"无对应回复而困惑。
    """
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if getattr(msg, "role", None) == "assistant":
            text = msg.get_text() if hasattr(msg, "get_text") else ""
            if text and _STANDBY_TAG.search(text):
                body = _STANDBY_TAG.sub("", text).strip()
                if len(body) <= 30:
                    # 移除 goodbye 消息
                    del messages[i]
                    # 一并移除前一条 user 消息（"退下吧"等退下意图）
                    for j in range(i - 1, -1, -1):
                        if getattr(messages[j], "role", None) == "user":
                            del messages[j]
                            return
                    return


async def _voice_loop_round(
    events: VoiceSessionEvents,
    settings: Settings,
    loop: QueryLoop,
    ctx: ToolContext,
    tts: Any,
    stt: Any,
    interrupt_event: threading.Event,
    stop_event: threading.Event,
) -> bool:
    """跑一轮语音对话（听→想→说）。返回是否继续下一轮。

    False 表示用户要退出语音模式（退下 / 待机）。
    stop_event: 外部停止信号；与 stt 停止标志组合区分「退出意图」
    （Ctrl+C / voice.stop，抛 KeyboardInterrupt 退出）与「打断意图」
    （voice.interrupt 仅中断录音，返回 True 继续聆听）。
    """
    # ---- 1. 听：STT 录音识别 ----
    events.on_state(STATE_LISTENING)
    events.on_info("🎤 聆听中...（说话即可，停顿后自动结束）")

    t_listen = time.time()
    try:
        # 阻塞录音必须放工作线程：pyaudio 的 C 扩展调用会直接卡死宿主 asyncio
        # 循环，serve 宿主下饿死指令循环（桌面壳 voice.stop / interrupt 指令
        # 无法被消费，表现为按钮无响应）；to_thread 期间宿主循环保持空闲。
        result = await asyncio.to_thread(
            stt.listen,
            max_seconds=settings.voice_max_seconds,
            silence_seconds=settings.stt_silence_seconds,
            silence_threshold=settings.stt_silence_threshold,
            on_partial=events.on_user_partial,
            on_open=lambda: None,
        )
        # 停止标志被触发（Ctrl+C 信号 / voice.stop / voice.interrupt 均经此标志中断录音）
        from agent.voice.stt import _is_stopped
        if _is_stopped():
            if stop_event.is_set():
                # 退出意图（Ctrl+C / voice.stop）：抛出让 voice_loop 干净退出
                raise KeyboardInterrupt
            # 打断意图（voice.interrupt 仅中断录音）：清进度行后返回，
            # 由 voice_loop 清标志并继续聆听，不退出语音模式
            events.on_user_partial("")
            return True
    except KeyboardInterrupt:
        # Ctrl+C 在聆听阶段（REPL 主线程信号在 await 点抛出）: 清进度行并重新
        # 抛出，让 voice_loop 的 except 捕获并退出语音模式（不能 return False，
        # 否则误判"退下"进待机）
        events.on_user_partial("")
        raise

    # 清识别进度行（适配器收到空 partial 即清行）
    events.on_user_partial("")
    listen_elapsed = time.time() - t_listen

    if result.get("error"):
        events.on_error(f"识别失败: {result['error']}，再说一次")
        return True

    user_text = result.get("text", "").strip()
    if not user_text:
        events.on_info("没听清，再说一次", LEVEL_WARN)
        return True

    _exit_detected = _contains_any(user_text, _EXIT_WORDS)
    if _exit_detected:
        events.on_info("🛌 退下意图，让 LLM 告别后进入待机...")
        # 不走独立 TTS（和 standby 麦克风 PyAudio 冲突），
        # 把用户的话正常喂给 LLM，LLM 会回复告别语 + <standby/> 标记，
        # 走和自然语言退下完全相同的路径，TTS 生命周期已验证可靠。

    events.on_user_transcript(user_text)

    # ---- 2. 想：LLM 流式推理（状态外抛，宿主适配器决定渲染）----
    events.on_state(STATE_THINKING)

    # 阶段 1 流式 TTS：用 StreamTTSPlayer 实现句子级流式播放。
    # LLM 每输出一个完整句子 → 立即送 CosyVoice 合成 → 边来边播。
    from agent.voice.stream_tts import StreamTTSPlayer
    stream_player = StreamTTSPlayer(
        api_key=_voice_api_key(settings),
        model=settings.tts_model,
        voice=settings.tts_voice,
        volume=settings.tts_volume,
        speech_rate=settings.tts_speech_rate,
        pitch_rate=settings.tts_pitch_rate,
        debug_log=settings.verbose,
    )
    # 建 TTS 连接是阻塞网络调用，同样放工作线程，避免饿死宿主指令循环
    stream_ok = await asyncio.to_thread(stream_player.start)

    def _feed(text: str) -> None:
        """组合 feeder：喂 TTS 合成 + 外抛流式 delta 给宿主上屏。"""
        if stream_ok:
            stream_player.feed(text)
        events.on_ai_text_delta(text)

    ctx.on_assistant_text = _feed

    # 打断监听：轮询 interrupt_event（来源由宿主适配器挂接：键盘/麦克风/按钮）
    interrupted = False
    round_done = threading.Event()

    def _on_barge() -> None:
        nonlocal interrupted
        interrupted = True
        ctx.abort_event.set()
        # 立即停止当前 TTS 播报（stop 置位回调 stop_flag → on_data 丢弃音频）
        try:
            stream_player.stop()
        except Exception:
            pass

    def _poll_interrupt() -> None:
        while not round_done.is_set():
            # stop_event（voice.stop）也走 barge-in：播报中立即停 TTS，让工作
            # 线程里的 finish() 提前返回，避免宿主 wait_for 超时硬取消残留音频
            if interrupt_event.is_set() or stop_event.is_set():
                _on_barge()
                return
            round_done.wait(_INTERRUPT_POLL_SEC)

    poll_thread = threading.Thread(target=_poll_interrupt, daemon=True)
    poll_thread.start()

    t_reply = time.time()
    msg_count_before = len(ctx.messages)  # 记录本轮前的消息数，供 _detect_standby 限制检测范围
    # 重置 abort_event：确保上一轮残留不会导致本轮 LLM 调用被跳过
    ctx.abort_event = asyncio.Event()
    try:
        stats = await loop.run(user_text, ctx)
    except KeyboardInterrupt:
        ctx.abort_event.set()
        round_done.set()
        events.on_info("\n已打断（继续聆听）", LEVEL_WARN)
        ctx.abort_event = asyncio.Event()
        ctx.on_assistant_text = None
        stream_player.stop()
        return True
    except Exception as e:
        round_done.set()
        events.on_error(f"回复出错: {type(e).__name__}: {e}")
        ctx.on_assistant_text = None
        stream_player.stop()
        return True
    finally:
        ctx.on_assistant_text = None

    if interrupted:
        # LLM 推理中被打断：已停止流式输出，回到聆听
        round_done.set()
        events.on_info("🔇 检测到打断，已停止回复（继续聆听）", LEVEL_WARN)
        ctx.abort_event = asyncio.Event()
        stream_player.stop()
        return True

    # ---- 3. 说：StreamTTSPlayer 已通过 feeder 流式接收文本 ----
    events.on_state(STATE_SPEAKING)
    reply_elapsed = time.time() - t_reply

    # 从最后一条 assistant message 提取完整文本（用于退下检测、外抛全量、日志）
    reply_text = ""
    reply_thinking = ""
    for msg in reversed(ctx.messages):
        if msg.role == "assistant":
            reply_text = msg.get_text()
            reply_thinking = msg.get_thinking()
            if reply_text.strip() or reply_thinking.strip():
                break
    events.on_ai_text(reply_text)

    _voice_log(
        "LLM reply: text=%r  thinking=%r  stopped_reason=%s  iterations=%d  tools=%d",
        reply_text[:200], reply_thinking[:200],
        getattr(stats, 'stopped_reason', '?'),
        getattr(stats, 'iterations', 0),
        getattr(stats, 'tool_calls', 0),
    )

    try:
        # finish 阻塞到音频播完：放工作线程跑，播报期间宿主指令循环仍可消费
        # voice.stop / interrupt（打断经 _poll_interrupt 停 TTS 提前返回）
        player_stats = await asyncio.to_thread(stream_player.finish)
    except KeyboardInterrupt:
        # Ctrl+C 在播报阶段：立即停止播报并退出语音模式
        round_done.set()
        stream_player.stop()
        raise
    except Exception:
        player_stats = {}
    finally:
        # 轮结束：停止打断轮询线程
        round_done.set()

    if interrupted:
        # 播报阶段被打断：音频已被 _on_barge 停止，finish() 提前返回
        events.on_info("🔇 检测到打断，已停止播报（继续聆听）", LEVEL_WARN)
        ctx.abort_event = asyncio.Event()
        return True

    if settings.verbose:
        degraded = " (降级)" if player_stats.get("degraded") else ""
        audio_s = player_stats.get("audio_seconds", 0) or 0
        first_ms = player_stats.get("first_feed_to_first_audio_ms", "?")
        events.on_info(
            f"  [回复 {reply_elapsed:.1f}s，音频 {audio_s}s{degraded}，"
            f"首句 {first_ms}ms，"
            f"iter={stats.iterations} tools={stats.tool_calls}]"
        )

    # LLM 退下意图检测：回复含 <standby/> 标记 → 进待机。
    # 覆盖"不聊了"/"闭嘴"/"去忙吧"等任意自然语言表达，比硬编码关键词更智能。
    # 标记已被 TTS 清洗逻辑剥除，用户听不到，只在此检测。
    if _detect_standby(ctx.messages, since_index=msg_count_before) or _exit_detected:
        events.on_state(STATE_STANDBY)
        events.on_info("🛌 贾维斯已退下（说「贾维斯」唤醒）")
        return False

    return True


async def _standby_round(settings: Settings, stt: Any) -> str:
    """待机一轮：录短段，返回识别文本。不在这里判断唤醒词（调用方判断）。

    返回识别到的文本（可能为空）。Ctrl+C 由调用方处理。
    """
    try:
        # 与对话阶段同理：阻塞短录放工作线程，待机期间宿主指令循环保持空闲
        result = await asyncio.to_thread(
            stt.listen,
            max_seconds=_STANDBY_MAX_SECONDS,
            silence_seconds=_STANDBY_SILENCE_SECONDS,
            silence_threshold=settings.stt_silence_threshold,
            on_partial=lambda t: None,
            on_open=lambda: None,
        )
    except KeyboardInterrupt:
        # Ctrl+C（REPL 主线程信号在 await 点抛出）：交调用方退出语音模式
        raise
    # 停止标志不在这里抛：调用方按 stop_event 区分「退出」与「打断继续待机」
    if result.get("error"):
        # 待机时偶发错误不刷屏，静默继续
        return ""
    return result.get("text", "").strip()


async def voice_loop(
    events: VoiceSessionEvents,
    settings: Settings,
    loop: QueryLoop,
    ctx: ToolContext,
    pause_event: threading.Event | None = None,
    *,
    stop_event: threading.Event | None = None,
    interrupt_event: threading.Event | None = None,
) -> None:
    """进入语音模式。对话 ⇄ 待机 循环，直到外部停止 / Ctrl+C 彻底退出。

    流程:
        对话阶段（听→想→说 多轮）
          └─ 说「退下」或 LLM 识别到结束意图（<standby/>）→ 进入待机阶段
        待机阶段（循环短录，等唤醒词「贾维斯」）
          └─ 听到唤醒词 → 回到对话阶段
        外部 stop_event / Ctrl+C → 彻底退出

    退下意图识别双层机制:
        1. 快速匹配: 听阶段检测 _EXIT_WORDS（"退下"/"拜拜"等明确词），
           不走独立 TTS（避免 PyAudio 冲突），正常喂给 LLM 走到别+<standby/> 路径
        2. LLM 意图: 自然语言（"不聊了"/"闭嘴"/"去忙吧"等）由 LLM 理解，
           回复末尾输出 <standby/> 标记，系统检测后进待机（附告别语）

    解耦说明（二期）:
        - 不再接收 RichCLI；一切输出经 ``events`` 回调外抛（宿主适配器渲染）。
        - 打断来源经 ``interrupt_event`` 注入（REPL=键盘 ESC，serve=麦克风
          barge-in + 桌面壳 voice.interrupt 按钮）；本函数轮询该 event。
        - 键盘 ESC / 麦克风 watcher 的创建与生命周期由宿主适配器负责。

    pause_event: 已废弃，保留参数仅为向后兼容，内部不再使用。
    stop_event: 外部停止信号。宿主停止语音会话时设置，循环关键点检查并干净退出；
        同时与 stt 停止标志组合区分「退出意图」与 voice.interrupt 的「打断意图」
        （后者仅中断当前录音，继续聆听/待机）。None 时内部自建（REPL 宿主由
        Ctrl+C 信号处理器置位）。
    interrupt_event: 外部打断信号；None 时内部自建（无即时打断来源）。

    @author aceFelix
    """
    if interrupt_event is None:
        interrupt_event = threading.Event()
    if stop_event is None:
        # 未传外部停止信号的宿主（REPL）：内部自建，Ctrl+C 信号处理器置位，
        # 保证「退出意图」判定不依赖宿主传参
        stop_event = threading.Event()

    try:
        from agent.voice import create_stt
        from agent.voice.tts import CosyVoiceTTS
    except ImportError as e:
        events.on_error(f"语音模块不可用: {e}")
        return

    stt = create_stt(
        api_key=_voice_api_key(settings),
        model=settings.stt_model,
    )
    tts = CosyVoiceTTS(
        api_key=_voice_api_key(settings),
        model=settings.tts_model,
        voice=settings.tts_voice,
        volume=settings.tts_volume,
        speech_rate=settings.tts_speech_rate,
        pitch_rate=settings.tts_pitch_rate,
    )

    # 语音对话模式：保持 thinking 开启，让思考走 reasoning_content 通道
    # （ThinkingDelta 不触发 on_assistant_text，TTS 不会朗读思考过程）。
    _thinking_was_enabled = loop.is_thinking_enabled()
    loop.set_thinking_enabled(True)

    # 追加语音模式指令到 system prompt：让 LLM 理解自然语言退下意图，
    # 用 <standby/> 标记通知系统切换待机。
    try:
        if hasattr(loop, "_system") and _VOICE_MODE_PROMPT not in loop._system:
            loop._system = loop._system + _VOICE_MODE_PROMPT
    except Exception:
        pass

    events.on_info("=" * 56)
    events.on_info("🎙️  语音对话模式已开启")
    _voice_log("[voice_loop] 语音对话循环启动")
    events.on_info(f"   STT: {settings.stt_model}")
    events.on_info(f"   TTS: {settings.tts_model} / {settings.tts_voice}")
    events.on_info("   说「退下/不聊了/去忙吧」进入待机 · 说「贾维斯」唤醒 · 打断/退出由宿主控制")
    events.on_info("=" * 56)
    events.on_state(STATE_DIALOG)

    # ---- 跨进程语音互斥锁 ----
    # 防止 REPL /voice 与 serve / 桌面壳语音同时开，导致麦克风冲突。
    from agent.voice.voice_state import acquire_voice_lock, release_voice_lock
    lock_ok, lock_info = acquire_voice_lock()
    if not lock_ok:
        events.on_error(f"🎙️ 语音模式已被另一个 jarvis 进程占用: {lock_info}")
        events.on_error("   请先关闭另一个 jarvis 的语音模式后再试")
        _voice_log("[voice_loop] 获取语音锁失败: %s", lock_info)
        return

    tts_fail_count = 0
    DEGRADE_THRESHOLD = 3

    # 注册 Ctrl+C 信号处理器：Windows 上 pyaudio stream.read() 阻塞时
    # KeyboardInterrupt 无法被 Python asyncio 捕获（C 扩展阻塞）。
    # 用 signal handler 触发 stt._request_stop() 来非阻塞地中断录音循环。
    # 注意：signal.signal 只能在主线程调用；serve 子线程运行语音时跳过，
    # 依赖 stop_event / interrupt_event 退出。
    from agent.voice import stt as stt_module
    _in_main_thread = threading.current_thread() == threading.main_thread()
    prev_sigint = None
    if _in_main_thread:
        def _on_sigint(sig, frame):
            stt_module._request_stop()
            stop_event.set()  # 标记退出意图：录音返回后的标志检查走退出分支
            raise KeyboardInterrupt
        prev_sigint = __import__("signal").signal(__import__("signal").SIGINT, _on_sigint)

    try:
        in_dialog = True  # True=对话阶段, False=待机阶段
        while True:
            if stop_event and stop_event.is_set():
                _voice_log("[voice_loop] 收到外部停止信号，退出语音循环")
                events.on_info("🔇 语音会话已停止")
                break

            if in_dialog:
                # 对话阶段：连续多轮，直到用户说退下
                cont = True
                while cont:
                    if stop_event and stop_event.is_set():
                        break
                    interrupt_event.clear()  # 每轮重置打断信号
                    cont = await _voice_loop_round(
                        events, settings, loop, ctx, tts, stt, interrupt_event,
                        stop_event
                    )
                    if stop_event and stop_event.is_set():
                        break
                    # ESC / 停止标志打断了 stt.listen() → 清标志回到聆听
                    if cont and stt_module._is_stopped():
                        stt_module._reset_stop()
                        events.on_info("   ⏎ 已打断（继续聆听）")
                        continue
                    # 降级计数：TTS 失败时记录
                    if cont and not getattr(ctx, 'tts_ok', True):
                        tts_fail_count += 1
                    else:
                        tts_fail_count = 0

                    if tts_fail_count >= DEGRADE_THRESHOLD and not getattr(ctx, 'tts_degraded_shown', False):
                        events.on_info("⚠ TTS 连续失败 3 次，已切换纯文本模式（语音对话结束后恢复）", LEVEL_WARN)
                        ctx.tts_degraded_shown = True
                    ctx.tts_ok = True
                # _voice_loop_round 返回 False = 用户退下，切到待机
                if stop_event and stop_event.is_set():
                    break
                in_dialog = False
                events.on_state(STATE_STANDBY)
                events.on_info("💤 待机中，说「贾维斯」唤醒我")
            else:
                # 待机阶段：循环短录，等唤醒词
                text = await _standby_round(settings, stt)
                if stop_event.is_set():
                    break
                # 打断按钮中断了待机录音：清标志继续待机（退出意图由上面的
                # stop_event / Ctrl+C 分支处理，不误杀语音会话）
                if stt_module._is_stopped():
                    stt_module._reset_stop()
                    continue
                if text and _contains_any(text, _WAKE_WORDS):
                    if stop_event and stop_event.is_set():
                        break
                    events.on_state(STATE_DIALOG)
                    events.on_info("🔊 唤醒，回到对话模式")
                    in_dialog = True
                # 不含唤醒词则静默继续待机（不打印识别内容，避免刷屏）
    except KeyboardInterrupt:
        events.on_info("\n退出语音模式")
        # 重置 abort_event，避免打断信号残留导致下次语音会话被跳过
        try:
            ctx.abort_event = asyncio.Event()
        except Exception:
            pass
    finally:
        # 确保 stop flag 被重置（signal handler 只 set，不自动清）
        try:
            stt_module._reset_stop()
        except Exception:
            pass
        # 恢复信号处理器（仅主线程注册过才恢复）
        try:
            if _in_main_thread and prev_sigint is not None:
                __import__("signal").signal(__import__("signal").SIGINT, prev_sigint)
        except Exception:
            pass
        # 恢复思考模式 + 清理 TTS + 释放语音互斥锁
        try:
            loop.set_thinking_enabled(_thinking_was_enabled)
            try:
                tts.stop()
            except Exception:
                pass
        except Exception:
            pass
        try:
            release_voice_lock()
        except Exception:
            pass
        # 清理残留的 <standby/> goodbye 消息，避免下次语音会话误触发待机
        try:
            _clean_standby_messages(ctx.messages)
        except Exception:
            pass
        _voice_log("[voice_loop] 语音对话循环退出")
        events.on_state(STATE_EXITED)
        events.on_info("已回到文本模式（输入 /help 查看命令）")
