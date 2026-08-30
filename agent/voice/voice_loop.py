"""语音对话循环 —— 贾维斯的核心。

阶段三第三刀（闭环）+ 第四刀（打断 & 体验优化）。

把 STT（听）、LLM（想）、TTS（说）串成实时语音闭环:

    用户说话 → STT 识别 → LLM 流式回复 → 回复流式送 TTS → 边生成边播

第四刀新增:
- **打断（barge-in）**: TTS 播报期间后台监听麦克风，检测到用户开口立即
  tts.stop() + abort_event，切回聆听。无需等它说完才能纠正/追问。
- **TTS 文本清洗**: LLM 回复常带 markdown（**bold**、`code`、# 标题、- 列表、
  > 引用、[text](url)），直接喂 TTS 会读出符号噪声。清洗逻辑见 tts_text 模块。
- **错误恢复**: STT/TTS 单轮失败不退出语音模式，自动继续下一轮。

设计要点:
- **流式低延迟**: LLM 每吐一个 TextDelta → TTS 清洗 → tts.feed。
  TTS 边收边合成边播，首包延迟约 1s，后续实时。
- **复用 QueryLoop**: 语音模式走完整 QueryLoop（含工具调用），只是给 ctx 注入
  on_assistant_text = feeder.feed。工具调用期间模型说的话也会被播报。
- **TTS 会话包裹**: 每轮 start_stream → feed* → finish。finish 阻塞到音频播完。
- **打断线程模型**: 见 barge_in 模块的各类 watcher。主线程在 reply 阶段
  start/stop watcher。与 STT 的麦克风使用时段错开（STT 在 listen 阶段，
  watcher 在 reply 阶段），不冲突。
- **回声抑制**: TTS 音频可能被麦克风拾取导致自打断。用较高阈值
  （_BARGE_IN_THRESHOLD=2500，远高于 STT 的 500）+ 最短发声时长 0.4s 缓解。
  现代笔记本多带 AEC，多数情况 OK；若自触发可调高阈值或关闭 barge_in。

依赖: dashscope + pyaudio，agent.core.query_loop.QueryLoop
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from typing import Any

from agent.config.settings import Settings
from agent.core.context import ToolContext
from agent.core.query_loop import QueryLoop
from agent.ui.cli import RichCLI

from agent.voice.barge_in import _KeyBargeInWatcher
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
                        # 不是本轮新增的，继续检查更早的消息
                        continue
                except ValueError:
                    pass
            text = msg.get_text() if hasattr(msg, "get_text") else ""
            if text and _STANDBY_TAG.search(text):
                # 去掉标记后看正文长度
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
        # ESC 或 Ctrl+C 触发了停止标志
        from agent.voice.stt import _is_stopped
        if _is_stopped():
            raise KeyboardInterrupt
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

    _exit_detected = _contains_any(user_text, _EXIT_WORDS)
    if _exit_detected:
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

    # 阶段 1 流式 TTS：用 StreamTTSPlayer 实现句子级流式播放。
    # LLM 每输出一个完整句子 → 立即送 CosyVoice 合成 → 边来边播。
    # 替代原来"等 LLM 全文 → tts.speak() 整段播放"的阻塞模式。
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
    stream_ok = stream_player.start()
    if stream_ok:
        ctx.on_assistant_text = stream_player.feed
    else:
        ctx.on_assistant_text = None

    # 键盘 ESC 打断监听（LLM 推理 / 播报中按 ESC = 立即停止当前回复或播报）
    interrupted = False

    def _on_barge() -> None:
        nonlocal interrupted
        interrupted = True
        ctx.abort_event.set()
        # 朗读中按 ESC：立即停止当前 TTS 播报。
        # stop() 置位回调 stop_flag → on_data 丢弃音频（声音立刻停），
        # finish() 检测到 stop 后快速返回（见 CosyVoiceTTS.finish）。
        try:
            stream_player.stop()
        except Exception:
            pass

    watchers: list = []
    if settings.voice_barge_in_key:
        kw = _KeyBargeInWatcher(_on_barge)
        if kw.available:
            watchers.append(kw)
    for w in watchers:
        w.start()

    t_reply = time.time()
    msg_count_before = len(ctx.messages)  # 记录本轮前的消息数，供 _detect_standby 限制检测范围
    # 重置 abort_event：确保上一轮残留（如 ESC 监听器在退出时意外设置）不会导致
    # 本轮 LLM 调用被跳过（iterations=0 → stopped_reason=aborted）。
    ctx.abort_event = asyncio.Event()
    try:
        stats = await loop.run(user_text, ctx)
    except KeyboardInterrupt:
        ctx.abort_event.set()
        ui.warn("\n已打断（继续聆听）")
        ctx.abort_event = asyncio.Event()
        ctx.on_assistant_text = None
        stream_player.stop()
        ui._voice_mode = False
        ui._voice_tts_feed = None
        for w in watchers:
            w.stop()
        return True
    except Exception as e:
        ui.error(f"回复出错: {type(e).__name__}: {e}")
        ctx.on_assistant_text = None
        stream_player.stop()
        ui._voice_mode = False
        ui._voice_tts_feed = None
        for w in watchers:
            w.stop()
        return True
    finally:
        ctx.on_assistant_text = None
        ui._voice_mode = False
        ui._voice_tts_feed = None

    if interrupted:
        # LLM 推理中按 ESC：已停止流式输出的内容，回到聆听
        ui.warn("🔇 检测到打断（ESC），已停止回复（继续聆听）")
        ctx.abort_event = asyncio.Event()
        stream_player.stop()
        for w in watchers:
            w.stop()
        return True

    # ---- 3. 说：StreamTTSPlayer 已通过 ctx.on_assistant_text 流式接收文本 ----
    # finish() 冲刷剩余缓冲 + 阻塞等待全部音频播放完毕。
    # 若 WS 中途断开，自动降级到整段 tts.speak() 兜底。
    reply_elapsed = time.time() - t_reply

    # 从最后一条 assistant message 提取完整文本（用于退下检测、verbose 日志）
    reply_text = ""
    reply_thinking = ""
    for msg in reversed(ctx.messages):
        if msg.role == "assistant":
            reply_text = msg.get_text()
            reply_thinking = msg.get_thinking()
            if reply_text.strip() or reply_thinking.strip():
                break

    _voice_log(
        "LLM reply: text=%r  thinking=%r  stopped_reason=%s  iterations=%d  tools=%d",
        reply_text[:200], reply_thinking[:200],
        getattr(stats, 'stopped_reason', '?'),
        getattr(stats, 'iterations', 0),
        getattr(stats, 'tool_calls', 0),
    )

    try:
        player_stats = stream_player.finish()
    except KeyboardInterrupt:
        # Ctrl+C 在播报阶段：立即停止播报并退出语音模式
        stream_player.stop()
        for w in watchers:
            w.stop()
        raise
    except Exception:
        player_stats = {}

    # watchers 保持运行到播报结束，让朗读中按 ESC 也能立即停止
    for w in watchers:
        w.stop()

    if interrupted:
        # 播报阶段按 ESC：音频已被 _on_barge 停止，finish() 提前返回
        ui.warn("🔇 检测到打断（ESC），已停止播报（继续聆听）")
        ctx.abort_event = asyncio.Event()
        return True

    if settings.verbose:
        degraded = " (降级)" if player_stats.get("degraded") else ""
        audio_s = player_stats.get("audio_seconds", 0) or 0
        first_ms = player_stats.get("first_feed_to_first_audio_ms", "?")
        ui.info(
            f"  [回复 {reply_elapsed:.1f}s，音频 {audio_s}s{degraded}，"
            f"首句 {first_ms}ms，"
            f"iter={stats.iterations} tools={stats.tool_calls}]"
        )

    # TTS speak 内部已完成 WS 关闭，无需额外等待

    # LLM 退下意图检测：回复含 <standby/> 标记 → 进待机。
    # 覆盖"不聊了"/"闭嘴"/"去忙吧"等任意自然语言表达，比硬编码关键词更智能。
    # 标记已被 TTS 清洗逻辑剥除，用户听不到，只在此检测。
    ui._voice_mode = False
    ui._voice_tts_feed = None
    if _detect_standby(ctx.messages, since_index=msg_count_before) or _exit_detected:
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
        from agent.voice import stt as _stt_mod
        if _stt_mod._is_stopped():
            raise KeyboardInterrupt
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
    *,
    stop_event: threading.Event | None = None,
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

    pause_event: 已废弃，保留参数仅为向后兼容，内部不再使用。
    stop_event: 外部停止信号。外部宿主（未来 GUI 窗口等）停止语音会话时设置，
        voice_loop 在循环关键点检查并干净退出。

    @author aceFelix
    """
    try:
        from agent.voice import create_stt
        from agent.voice.tts import CosyVoiceTTS
    except ImportError as e:
        ui.error(f"语音模块不可用: {e}")
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
    _voice_log("[voice_loop] 语音对话循环启动")
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

    # ---- 跨进程语音互斥锁 ----
    # 防止 REPL /voice 与 `jarvis --talk` 窗口（或未来 GUI）同时开语音，导致麦克风冲突。
    from agent.voice.voice_state import acquire_voice_lock, release_voice_lock
    lock_ok, lock_info = acquire_voice_lock()
    if not lock_ok:
        ui.error(f"🎙️ 语音模式已被另一个 jarvis 进程占用: {lock_info}")
        ui.error("   请先关闭另一个 jarvis 的语音模式后再试")
        _voice_log("[voice_loop] 获取语音锁失败: %s", lock_info)
        return

    tts_fail_count = 0
    stt_fail_count = 0
    DEGRADE_THRESHOLD = 3

    # 注册 Ctrl+C 信号处理器：Windows 上 pyaudio stream.read() 阻塞时
    # KeyboardInterrupt 无法被 Python asyncio 捕获（C 扩展阻塞）。
    # 用 signal handler 触发 stt._request_stop() 来非阻塞地中断录音循环。
    from agent.voice import stt as stt_module
    # 全局 ESC 监听器：对话/待机/聆听阶段均可按下 ESC
    # - 对话阶段 → 打断当前操作，返回聆听
    # - 待机阶段 → 退出语音模式，回到文本 REPL
    # ESC 会同时触发 _request_stop()（中断阻塞中的 stt.listen()）
    esc_watcher = _KeyBargeInWatcher(
        lambda: stt_module._request_stop() if not _esc_exit.get("triggered") else None,
    )
    _esc_exit: dict[str, bool] = {"triggered": False}
    if esc_watcher.available:

        def _on_esc_barge() -> None:
            """ESC 回调：优先打断当前操作，若已在待机则退出语音模式。"""
            stt_module._request_stop()  # 中断阻塞中的 stt.listen()
            ctx.abort_event.set()       # 中断 LLM 推理

        esc_watcher._on_barge = _on_esc_barge
        esc_watcher.start()
        ui.info("   按 ESC 打断说话/推理，待机中按 ESC 退出语音模式")
    else:
        ui.info("   ⚠ keyboard 库未安装，ESC 打断不可用（pip install keyboard）")

    # 注册 Ctrl+C 信号处理器：Windows 上 pyaudio stream.read() 阻塞时
    # KeyboardInterrupt 无法被 Python asyncio 捕获（C 扩展阻塞）。
    # 用 signal handler 触发 stt._request_stop() 来非阻塞地中断录音循环。
    # 注意：signal.signal 只能在主线程调用，语音会话若运行在子线程（未来 GUI），
    # 则跳过信号注册，依赖 stop_event / ESC watcher 退出。
    from agent.voice import stt as stt_module
    _in_main_thread = threading.current_thread() == threading.main_thread()
    prev_sigint = None
    if _in_main_thread:
        def _on_sigint(sig, frame):
            # 1) 中断阻塞中的 stt.listen()（pyaudio C 阻塞无法直接被信号打断）
            stt_module._request_stop()
            # 2) 重新抛出 KeyboardInterrupt，让 Ctrl+C 在 LLM 推理 / 工具执行阶段
            #    也能生效（原实现吞掉信号导致"卡住时 Ctrl+C 退不出来"）。
            #    聆听阶段抛出 → voice_loop 外层 except KeyboardInterrupt 退出语音模式；
            #    LLM 阶段抛出 → _voice_loop_round 捕获后打断回复（继续聆听）。
            raise KeyboardInterrupt
        prev_sigint = __import__("signal").signal(__import__("signal").SIGINT, _on_sigint)

    try:
        in_dialog = True  # True=对话阶段, False=待机阶段
        while True:
            if stop_event and stop_event.is_set():
                _voice_log("[voice_loop] 收到外部停止信号，退出语音循环")
                ui.info("🔇 语音会话已停止")
                break

            if in_dialog:
                # 对话阶段：连续多轮，直到用户说退下
                cont = True
                while cont:
                    if stop_event and stop_event.is_set():
                        break
                    cont = await _voice_loop_round(ui, settings, loop, ctx, tts, stt)
                    if stop_event and stop_event.is_set():
                        break
                    # ESC 打断了 stt.listen() → cont 仍然为 True 但 _stop_flag 已置位
                    if cont and stt_module._is_stopped():
                        # 用户在对话阶段按 ESC：打断 → 清标志 → 回到聆听（继续该阶段）
                        stt_module._reset_stop()
                        ui.info("   ⏎ 已打断（继续聆听）")
                        # 不改变 in_dialog 状态，继续对话阶段
                        continue
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
                if stop_event and stop_event.is_set():
                    break
                in_dialog = False
                ui.info("💤 待机中，说「贾维斯」唤醒我")
            else:
                # 待机阶段：循环短录，等唤醒词
                text = await _standby_round(ui, settings, stt)
                if stop_event and stop_event.is_set():
                    break
                # ESC 在待机阶段按 → 退出语音模式
                if stt_module._is_stopped():
                    ui.info("\n🛑 ESC 退出语音模式")
                    break
                if text and _contains_any(text, _WAKE_WORDS):
                    if stop_event and stop_event.is_set():
                        break
                    ui.info("🔊 唤醒，回到对话模式")
                    in_dialog = True
                # 不含唤醒词则静默继续待机（不打印识别内容，避免刷屏）
    except KeyboardInterrupt:
        ui.info("\n退出语音模式")
        # 重置 abort_event，避免 ESC 监听器在退出时意外设置导致下次语音会话被跳过
        try:
            ctx.abort_event = asyncio.Event()
        except Exception:
            pass
    finally:
        # 停止 ESC 监听器
        try:
            esc_watcher.stop()
        except Exception:
            pass
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
        ui.info("已回到文本模式（输入 /help 查看命令）")
