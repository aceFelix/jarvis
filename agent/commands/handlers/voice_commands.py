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


# ---- /tts-voice：TTS 音色选择 / 切换 / 添加（仅支持阿里云 DashScope）----

_ADD_VOICE_MARKER = "__jarvis_add_tts_voice__"


def _switch_tts_voice(ui, settings, voice_id: str, label: str) -> bool:
    """切换当前 TTS 音色（settings.tts_voice）并持久化到 [tts] voice。

    @author aceFelix
    """
    if not voice_id:
        return False
    settings.tts_voice = voice_id
    try:
        from agent.config.settings import save_tts_voice
        save_tts_voice(voice_id)
    except Exception:
        pass
    ui.info(f"🎙️ TTS 音色已切换: {label}（{voice_id}）")
    return True


def _add_tts_voice_flow(ui, settings) -> bool:
    """单屏表单添加自定义 TTS 音色（当前仅支持阿里云 DashScope 厂商）。

    音色名 + DashScope 音色 ID（voice 参数，含声音复刻 voice_id）→ 保存。

    @author aceFelix
    """
    from agent.ui.terminal_picker import form_input

    fields = [
        {
            "name": "音色名",
            "type": "text",
            "placeholder": "例如: 我的专属音色, 温柔女声",
            "default": "",
        },
        {
            "name": "DashScope 音色 ID",
            "type": "text",
            "placeholder": "例如: longxiaochun_v3（声音复刻的 voice_id 也可）",
            "default": "",
        },
        {
            "name": "描述",
            "type": "text",
            "placeholder": "例如: 声音复刻 - 我的声音（可留空）",
            "default": "",
        },
    ]

    result = form_input(title="添加自定义 TTS 音色（DashScope）", fields=fields)
    if result is None:
        ui.info("已取消")
        return False

    name = (result.get("音色名") or "").strip()
    voice_id = (result.get("DashScope 音色 ID") or "").strip()
    if not name:
        ui.warn("音色名不能为空")
        return False
    if not voice_id:
        ui.warn("DashScope 音色 ID 不能为空")
        return False
    description = (result.get("描述") or "").strip() or name

    config = {
        "name": name,
        "voice_id": voice_id,
        "description": description,
        "vendor": "dashscope",
    }
    try:
        from agent.config.settings import save_custom_voice
        if save_custom_voice(name, config):
            settings.custom_voices[name] = config
            ui.info(f"音色「{name}」已添加并保存（{voice_id}）")
            return True
        ui.warn("保存失败：找不到 ~/.jarvis/settings.toml")
        return False
    except Exception as e:
        ui.error(f"保存音色失败: {e}")
        return False


def _delete_custom_voice(ui, settings, name: str) -> None:
    """删除自定义音色（从内存与 settings.toml 中移除）。"""
    from agent.ui.terminal_picker import pick_from_list

    confirm_items = [
        ("no", "否，取消", "保留该音色"),
        ("yes", "是，删除", "从配置中移除该音色"),
    ]
    choice = pick_from_list(confirm_items, title=f"⚠ 确认删除音色「{name}」？", current="no")
    if choice != "yes":
        return

    settings.custom_voices.pop(name, None)
    try:
        import re as _re
        from pathlib import Path
        toml_path = Path.home() / ".jarvis" / "settings.toml"
        if toml_path.exists():
            content = toml_path.read_text(encoding="utf-8")
            marker = f'[tts.custom_voices."{name}"]'
            if marker in content:
                start = content.index(marker)
                rest = content[start + len(marker):]
                m = _re.search(r'\n\[', rest)
                end = start + len(marker) + m.start() if m else len(content)
                while end < len(content) and content[end] == '\n':
                    end += 1
                content = content[:start].rstrip() + "\n" + content[end:]
                toml_path.write_text(content, encoding="utf-8")
        ui.info(f"音色「{name}」已删除")
    except Exception as e:
        ui.error(f"删除音色失败: {e}")


async def handle_tts_voice(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /tts-voice：选择 / 切换 / 添加 TTS 音色（仅 DashScope）。

    用法:
        /tts-voice            → 交互式选择（Space 管理自定义音色）
        /tts-voice <前缀>      → 前缀匹配切换音色

    @author aceFelix
    """
    from agent.ui.terminal_picker import pick_from_list
    from agent.voice.tts_voices import all_tts_voices

    ui = ctx.ui
    settings = ctx.settings

    arg = stripped[len("/tts-voice"):].strip()
    if arg:
        # ---- 带参：前缀匹配切换 ----
        voices = all_tts_voices(settings)
        want = arg.lower()

        # 候选列表 (label, voice_id)：内置 name==voice_id；自定义 name 与 voice_id 都收录
        pairs: list[tuple[str, str]] = []
        for name, cfg in voices.items():
            pairs.append((name, cfg["voice_id"]))
            if cfg["voice_id"] != name:
                pairs.append((cfg["voice_id"], cfg["voice_id"]))

        exact = next(((n, vid) for n, vid in pairs if vid.lower() == want), None)
        if exact:
            _switch_tts_voice(ui, settings, exact[1], exact[0])
            return True

        matches = [(n, vid) for n, vid in pairs if n.lower().startswith(want)]
        seen: set[tuple[str, str]] = set()
        uniq: list[tuple[str, str]] = []
        for n, vid in matches:
            if (n, vid) not in seen:
                seen.add((n, vid))
                uniq.append((n, vid))

        if not uniq:
            ui.warn(f"无匹配音色: {arg}（用 /tts-voice 查看可用列表）")
        elif len(uniq) == 1:
            _switch_tts_voice(ui, settings, uniq[0][1], uniq[0][0])
        else:
            desc_map = {cfg["voice_id"]: cfg["description"] for cfg in voices.values()}
            items = [(vid, n, desc_map.get(vid, "")) for n, vid in uniq]
            picked = pick_from_list(items, title=f"「{arg}」匹配 {len(uniq)} 个音色", current=settings.tts_voice)
            if picked:
                _switch_tts_voice(ui, settings, picked, picked)
        return True

    # ---- 无参：交互式选择器 ----
    voices = all_tts_voices(settings)
    custom_names = set(getattr(settings, "custom_voices", {}).keys())

    items: list[tuple[str, str, str]] = []
    for name, cfg in voices.items():
        label = name if name == cfg["voice_id"] else f"{name} ({cfg['voice_id']})"
        tag = " ✎ 自定义" if name in custom_names else ""
        items.append((cfg["voice_id"], label, f"{cfg['description']}{tag}"))
    items.append((_ADD_VOICE_MARKER, "+ 添加音色", "新增自定义 TTS 音色（DashScope）"))

    space_tags = {
        str(cfg.get("voice_id", n))
        for n, cfg in getattr(settings, "custom_voices", {}).items()
        if isinstance(cfg, dict)
    }

    picked = pick_from_list(items, title="选择 TTS 音色", current=settings.tts_voice, space_tags=space_tags)
    if picked is None:
        return True

    if picked == _ADD_VOICE_MARKER:
        _add_tts_voice_flow(ui, settings)
        return True

    if picked.startswith("__SPACE__"):
        vid = picked[len("__SPACE__"):]
        cname = next(
            (n for n, cfg in getattr(settings, "custom_voices", {}).items()
             if isinstance(cfg, dict) and str(cfg.get("voice_id", n)) == vid),
            None,
        )
        if cname:
            _delete_custom_voice(ui, settings, cname)
        return True

    # 正常切换
    label = next((name for name, cfg in voices.items() if cfg["voice_id"] == picked), picked)
    _switch_tts_voice(ui, settings, picked, label)
    return True
