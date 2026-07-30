"""语音相关命令处理器。

包含 /listen, /voice, /talk 命令。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


def _say(ui, settings, text: str) -> None:
    """用 TTS 朗读一段文字。/say 命令的执行体。"""
    text = text.strip()
    if not text:
        ui.warn("用法: /say <要朗读的文字>")
        return
    try:
        from agent.voice.tts import CosyVoiceTTS
    except ImportError as e:
        ui.error(f"语音模块不可用: {e}")
        return

    tts = CosyVoiceTTS(
        api_key=settings.api_key,
        model=settings.tts_model,
        voice=settings.tts_voice,
        volume=settings.tts_volume,
        speech_rate=settings.tts_speech_rate,
        pitch_rate=settings.tts_pitch_rate,
    )
    ui.info(f"朗读（音色 {settings.tts_voice}）: {text[:60]}{'...' if len(text) > 60 else ''}")
    t0 = time.time()
    result = tts.speak(text, on_first_data=lambda: ui.info("🔊 开始播放..."))
    elapsed = time.time() - t0
    if result.get("error"):
        ui.error(f"语音合成失败: {result['error']}")
        return
    ui.info(
        f"播放完成（{elapsed:.1f}s，"
        f"音频 {result.get('total_seconds', 0)}s，"
        f"首包延迟 {result.get('first_package_delay_ms', '?')}ms）"
    )


def _listen(ui, settings, loop, ctx) -> None:
    """录音→识别成文字。/listen 命令的执行体。"""
    try:
        from agent.voice import create_stt
    except ImportError as e:
        ui.error(f"语音模块不可用: {e}")
        return

    stt = create_stt(
        api_key=settings.api_key,
        model=settings.stt_model,
    )
    ui.info(
        f"正在聆听...（模型 {settings.stt_model}，最多 {settings.stt_max_seconds}s，"
        f"停顿 {settings.stt_silence_seconds}s 自动结束）"
    )

    def _on_partial(text: str) -> None:
        sys.stdout.write(f"\r  识别中: {text}")
        sys.stdout.flush()

    def _on_open() -> None:
        ui.info("麦克风已就绪，请说话")

    t0 = time.time()
    result = stt.listen(
        max_seconds=settings.stt_max_seconds,
        silence_seconds=settings.stt_silence_seconds,
        silence_threshold=settings.stt_silence_threshold,
        on_partial=_on_partial,
        on_open=_on_open,
    )
    elapsed = time.time() - t0
    sys.stdout.write("\r" + " " * 80 + "\r")
    sys.stdout.flush()

    if result.get("error"):
        ui.error(f"语音识别失败: {result['error']}")
        return

    text = result.get("text", "").strip()
    duration = result.get("duration", 0)
    ui.info(f"识别完成（{elapsed:.1f}s，录音 {duration}s）")
    if text:
        ui.info(f"识别结果: {text}")
    else:
        ui.warn("未识别到内容（可能环境噪音或未说话）")


async def _voice_mode(ui, settings, loop, ctx) -> None:
    """进入语音对话模式。/voice 命令的执行体。"""
    try:
        from agent.voice.voice_loop import voice_loop
    except ImportError as e:
        ui.error(f"语音模块不可用: {e}")
        return
    await voice_loop(ui, settings, loop, ctx)


async def _realtime_talk(ui, settings, *, use_window: bool = True) -> None:
    """/talk 命令 —— 实时双工语音对话。

    实时语音/多模态服务由 DashScope 提供，必须使用 DashScope API Key。
    如果当前 LLM 是 deepseek/openai 等其它厂商，settings.api_key 会是另一家 key，
    不能直接用于 DashScope WebSocket 鉴权，因此优先使用独立的 dashscope_api_key。

    Args:
        use_window: 为 True 时优先使用 pywebview 独立窗口 UI；
                    未安装 pywebview 或环境不支持时回退到 RichCLI。

    @author aceFelix
    """
    api_key = (
        settings.dashscope_api_key
        or os.environ.get("DASHSCOPE_API_KEY", "")
        or settings.api_key
        or os.environ.get("OPENAI_API_KEY", "")
    )
    if not api_key:
        ui.error(
            "未配置 DashScope API Key。实时双工语音对话依赖 DashScope 服务，"
            "请设置环境变量 DASHSCOPE_API_KEY，或在 settings.toml 中配置 dashscope_api_key。"
        )
        return

    try:
        from agent.voice.realtime_talk import RealtimeTalk, DEFAULT_WS_URL
    except ImportError as e:
        ui.error(f"实时语音模块不可用: {e}")
        return

    config = {
        "api_key": api_key,
        "model": getattr(settings, "realtime_model", "qwen-audio-3.0-realtime-flash"),
        "voice": getattr(settings, "realtime_voice", "longanqian"),
        "ws_url": getattr(settings, "realtime_ws_url", "") or DEFAULT_WS_URL,
        "workdir": getattr(settings, "workdir", "") or os.getcwd(),
    }

    has_window = False
    window = None
    if use_window:
        try:
            from agent.ui.realtime_window import RealtimeTalkWindow

            window = RealtimeTalkWindow(on_close=lambda: None, standalone=True)
            window.set_config(config)
            window.show()
            has_window = window.is_open or True
        except ImportError:
            ui.warn("未安装 pywebview，实时聊天将回退到终端界面。")
        except Exception as e:
            ui.warn(f"启动实时聊天窗口失败: {e}，回退到终端界面。")

    if has_window and window is not None:
        while window.is_open:
            await asyncio.sleep(0.2)
    else:
        rt = RealtimeTalk(**config)
        await rt.run(ui)


async def handle_listen(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /listen /mic。"""
    _listen(ctx.ui, ctx.settings, ctx.loop, ctx.ctx)
    return True


async def handle_voice(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /voice。"""
    await _voice_mode(ctx.ui, ctx.settings, ctx.loop, ctx.ctx)
    return True


async def handle_talk(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /talk。"""
    await _realtime_talk(ctx.ui, ctx.settings)
    return True
