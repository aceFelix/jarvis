"""实时双工语音对话模块 —— /talk 命令。

基于 DashScope qwen-audio-3.0-realtime-flash，
通过 WebSocket 全双工连接实现实时语音对话。

特点：
- 全双工：同时听说，无需等待
- 服务端 VAD：自动检测说话开始/结束
- 语音打断：用户开口时自动停止 AI 播报
- 实时转录：显示对话文本

用法: /talk 启动，ESC 退出
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import struct
import sys
from typing import Any

try:
    import pyaudio
except ImportError:
    pyaudio = None

try:
    import websockets
except ImportError:
    websockets = None

# 回声消除（AEC）：消除扬声器回声，防止 AI 自言自语，同时保留打断能力
try:
    from .aec import EchoCanceller, is_available as _aec_available
    _HAS_AEC = _aec_available()
except ImportError:
    _HAS_AEC = False
    EchoCanceller = None  # type: ignore

# ---- 默认配置（可被 settings.toml [realtime_talk] 覆盖） ----
# DashScope 实时语音/多模态公共 WebSocket 端点。
# 如需业务空间专属域名，在 settings.toml [realtime_talk] 中覆盖 ws_url。
DEFAULT_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_VOICE = "longanqian"
DEFAULT_SILENCE_MS = 500
DEFAULT_VAD_THRESHOLD = 0.5

# ---- 音频参数 ----
INPUT_RATE = 16000    # 麦克风：16kHz
OUTPUT_RATE = 24000   # 扬声器：24kHz
CHUNK_BYTES = 3200    # 每次读取 3200 字节（~100ms @ 16kHz mono 16bit）
SEND_INTERVAL = 0.02  # 发送间隔 20ms
VOLUME_REPORT_INTERVAL = 8  # 每 8 个 chunk 上报一次音量（~160ms）
# AI 说话时麦克风衰减系数（0~1），防止 AEC 失效时扬声器回声被送回服务器导致自说自话
ECHO_SUPPRESS_FACTOR = 0.25
# 单条 AI 回复最大持续时长（秒），超过后主动截断，避免服务端 1006 断连
MAX_RESPONSE_SECONDS = 90.0


def _rms(data: bytes) -> float:
    """计算 16bit PCM 音频数据的 RMS 音量，返回 0.0 ~ 1.0。

    @author aceFelix
    """
    count = len(data) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"{count}h", data[: count * 2])
    mean_square = sum(s * s for s in samples) / count
    return min(1.0, math.sqrt(mean_square) / 32768.0)


def _attenuate_pcm(data: bytes, factor: float) -> bytes:
    """按比例衰减 16bit PCM 音频，factor 在 0~1 之间。

    用于 AI 说话时临时压低麦克风增益，减少扬声器回声被 VAD 误判。
    采用 numpy 向量运算，未安装 numpy 时回退标准库 array。
    """
    if not data or factor <= 0:
        return b"\x00" * len(data)
    if factor >= 1:
        return data
    try:
        import numpy as np
        arr = np.frombuffer(data, dtype=np.int16)
        scaled = (arr.astype(np.float32) * factor).astype(np.int16)
        return scaled.tobytes()
    except Exception:
        import array
        samples = array.array("h", data)
        scaled = array.array("h", (max(-32768, min(32767, int(s * factor))) for s in samples))
        return scaled.tobytes()


# ---- Function Calling 内置工具 ----
# 实时语音场景适用的工具集合，通过 session.update 注册给模型。
# 模型自主判断是否需要调用工具获取实时信息（如时间、日期）。
_BUILTIN_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前的日期、时间、星期几。当用户询问时间、日期、星期、几号时调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_conversation",
            "description": "结束当前对话并退出。仅当用户明确说出\"退下\"、\"贾维斯退下\"、\"结束对话\"、\"再见\"、\"拜拜\"、\"没事了\"等表示结束意图的话时才调用。不要在一次普通回答结束后调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


def _execute_builtin_tool(name: str, args: dict[str, Any]) -> str:
    """执行内置工具函数，返回 JSON 格式的结果字符串。

    实时语音场景下工具执行在本地同步完成，不经过 ToolRegistry 权限系统，
    因为用户已在语音交互中，工具仅限只读信息查询类。
    @author aceFelix
    """
    from datetime import datetime

    if name == "get_current_time":
        now = datetime.now()
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return json.dumps({
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "date": now.strftime("%Y年%m月%d日"),
            "time": now.strftime("%H:%M"),
            "weekday": weekdays[now.weekday()],
            "timestamp": int(now.timestamp()),
        }, ensure_ascii=False)

    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)


def _build_all_tools(workdir: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """从默认 ToolRegistry 构建工具集，转换为 realtime API tools 格式。

    接入所有工具（包括写操作），但排除以下不适用于语音场景的工具：
    - AskUser：需要键盘输入，语音场景无法使用

    高风险操作的安全性通过两层保障：
    1. instructions 引导模型对高风险操作先语音询问用户确认
    2. 代码层面 check_permissions 返回 deny 的操作拒绝执行

    Args:
        workdir: 工作目录，用于工具执行时的 ToolContext。

    Returns:
        (tools_schema, tool_map) 元组：
        - tools_schema: 符合 realtime API 格式的工具定义列表
        - tool_map: 工具名 → Tool 对象映射，用于 function_call 执行

    @author aceFelix
    """
    try:
        from agent.core.tool import build_default_registry
        registry = build_default_registry()
    except Exception:
        return [], {}

    tools_schema: list[dict[str, Any]] = []
    tool_map: dict[str, Any] = {}

    # 语音场景不适用的工具（需要键盘输入或 GUI 交互）
    _EXCLUDED_TOOLS = {"AskUser"}

    for tool in registry.all():
        # 排除不适合语音场景的工具
        if tool.name in _EXCLUDED_TOOLS:
            continue

        tools_schema.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema or {"type": "object", "properties": {}},
            },
        })
        tool_map[tool.name] = tool

    return tools_schema, tool_map


class RealtimeTalk:
    """实时双工语音对话。"""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "qwen-audio-3.0-realtime-flash",
        voice: str = DEFAULT_VOICE,
        instructions: str = "",
        ws_url: str = DEFAULT_WS_URL,
        silence_duration_ms: int = DEFAULT_SILENCE_MS,
        vad_threshold: float = DEFAULT_VAD_THRESHOLD,
        workdir: str = "",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._instructions = instructions or (
            "你是贾维斯，先生的全能管家。用简洁自然的口语回复，不要输出思考过程。"
            "\n\n你拥有丰富的工具集，涵盖文件读写、代码搜索、Bash命令、浏览器操作、"
            "GUI控制、MCP外部服务（天气/时间/票务等）、网页搜索等。"
            "根据用户意图自行判断该调用哪个工具，优先使用专用工具而非 WebSearch。"
            "\n【地域性查询规则】涉及天气、新闻、本地服务、附近推荐等地域性查询时，"
            "如果你不知道先生当前所在城市，必须先询问先生所在地，不要自行假设任何城市。"
            "\n高风险操作（执行命令、删除文件、发送邮件）先用语音简短确认，一般操作直接执行。"
        )
        self._ws_url = ws_url
        self._silence_ms = silence_duration_ms
        self._vad_threshold = vad_threshold
        self._workdir = workdir or os.getcwd()

        self._pya: Any = None
        self._mic: Any = None
        self._spk: Any = None
        self._running = False
        self._ai_speaking = False
        # 优雅停止：end_conversation 工具触发后等待告别语音播放完毕
        self._graceful_stop_at: float | None = None
        # 防御性超时：记录当前回复首个 audio delta 时间，超时主动截断
        self._response_start_ts: float | None = None
        # 打断代数：每次用户打断时递增，用于丢弃旧回复的残余音频
        self._response_gen: int = 0
        # WebRTC AEC3 回声消除器（可选，依赖 aec-audio-processing + numpy）
        self._aec: Any = None
        if _HAS_AEC:
            try:
                self._aec = EchoCanceller()
            except Exception:
                self._aec = None
        # Function Calling：call_id → 工具名映射，等待参数到达后执行
        self._pending_calls: dict[str, str] = {}
        # 构建工具集：内置工具 + ToolRegistry 全部工具
        self._registry_tools, self._registry_tool_map = _build_all_tools(self._workdir)
        # 合并后的完整工具 schema（发给服务端 session.update）
        self._all_tools = _BUILTIN_TOOLS + self._registry_tools

    @property
    def running(self) -> bool:
        """会话是否仍在运行。"""
        return self._running

    async def _init_mcp_tools(self, ui) -> None:
        """连接 MCP server 并将 MCP 工具合并到 _all_tools 和 _registry_tool_map。

        在 run() 中 WebSocket 连接前调用，确保 session.update 时工具已就绪。
        MCP client 引用保存在 self._mcp_client 上防止 GC。

        @author aceFelix
        """
        self._mcp_client = None
        try:
            from agent.core.extensions.mcp_client import MCPClient, load_mcp_config
            from agent.tools.extensions.mcp_tool import register_mcp_tools
            from agent.core.tool import ToolRegistry

            mcp_client = MCPClient()
            if not mcp_client.available:
                return

            config = load_mcp_config()
            if not config:
                return

            results = await mcp_client.connect_all(config)
            connected = sum(1 for v in results.values() if v)
            if connected == 0:
                return

            mcp_registry = ToolRegistry()
            count = register_mcp_tools(mcp_registry, mcp_client)

            for tool in mcp_registry.all():
                self._registry_tool_map[tool.name] = tool
                self._all_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema or {"type": "object", "properties": {}},
                    },
                })

            self._mcp_client = mcp_client  # 保持引用防止 GC
            ui.info(f"MCP: {connected}/{len(config)} server 已连接，注册 {count} 个工具")
        except ImportError:
            pass  # MCP SDK 未安装
        except Exception as e:
            ui.warn(f"MCP 接入异常: {e}")

    async def _load_mcp_tools_async(self, ws: Any, ui: Any) -> None:
        """后台异步加载 MCP 工具，并在加载完成后更新会话。

        该协程在 WebSocket 建立后与其他任务并发运行，
        不会阻塞语音对话启动。

        @author aceFelix
        """
        try:
            await self._init_mcp_tools(ui)
            # 如果成功加载了 MCP 工具，发送 session.update 更新会话
            if self._mcp_client is not None:
                await ws.send(json.dumps({
                    "type": "session.update",
                    "session": {
                        "tools": self._all_tools,
                    },
                }))
                mcp_count = len(self._all_tools) - len(_BUILTIN_TOOLS) - len(self._registry_tools)
                mcp_count = max(mcp_count, 0)
                if mcp_count > 0:
                    ui.info(f"🛠️ MCP 外部工具已就绪（+{mcp_count} 个），已加入对话")
        except asyncio.TimeoutError:
            ui.warn("MCP 外部工具加载超时，继续使用当前工具集")
        except Exception as e:
            ui.warn(f"MCP 外部工具加载异常: {e}")

    async def run(self, ui) -> None:
        """启动实时对话。ESC 退出。"""

        if websockets is None:
            ui.error("缺少 websockets 库，请运行: pip install websockets")
            return
        if pyaudio is None:
            ui.error("缺少 pyaudio 库，请运行: pip install pyaudio（Windows 上可能需要从 https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio 下载 whl 安装）")
            return

        # 初始化 PyAudio
        self._pya = pyaudio.PyAudio()
        try:
            self._mic = self._pya.open(
                format=pyaudio.paInt16, channels=1, rate=INPUT_RATE, input=True
            )
            self._spk = self._pya.open(
                format=pyaudio.paInt16, channels=1, rate=OUTPUT_RATE, output=True
            )
        except Exception as e:
            ui.error(f"音频设备初始化失败: {e}")
            self._cleanup()
            return

        # 连接 MCP server 的工作放到 WebSocket 建立后后台执行，
        # 不阻塞实时语音会话启动。

        # WebSocket URL
        url = f"{self._ws_url}?model={self._model}"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        ui.on_status("connecting")
        ui.info("=" * 56)
        ui.info("🎙️  实时双工语音对话已开启")
        ui.info(f"   模型: {self._model}  ·  音色: {self._voice}")
        if self._aec is not None:
            ui.info("   AEC 回声消除已启用 · smart_turn · 说话打断 · ESC 退出")
        else:
            ui.info("   smart_turn 模式 · 语义理解防回声 · 说话打断 · ESC 退出")
            ui.info("   （未启用 AEC，建议 pip install aec-audio-processing 以消除回声）")
        ui.info("=" * 56)

        # 连接 WebSocket
        # 禁用默认 ping/pong 心跳超时（默认 20s），避免服务端生成长音频时
        # 来不及回复 ping 被客户端判定为死连接（code=1006）。
        ws = await asyncio.wait_for(
            websockets.connect(
                url,
                additional_headers=headers,
                ping_interval=None,
                ping_timeout=None,
            ),
            timeout=60.0,
        )

        try:
            async with ws:
                # 发送 session.update，注册 Function Calling 工具
                await ws.send(json.dumps({
                    "type": "session.update",
                    "session": {
                        "modalities": ["text", "audio"],
                        "voice": self._voice,
                        "instructions": self._instructions,
                        "turn_detection": {
                            "type": "smart_turn",
                        },
                        "tools": self._all_tools,
                    },
                }))

                self._running = True
                ui.on_status("standby")

                # 并发：发音频 + 收事件 + ESC 监听 + 后台加载 MCP 工具 + 看门狗
                await asyncio.gather(
                    self._send_audio(ws, ui),
                    self._recv_events(ws, ui),
                    self._esc_watcher(ui),
                    self._load_mcp_tools_async(ws, ui),
                    self._watchdog(ws),
                )

        except websockets.exceptions.ConnectionClosed as e:
            # 记录关闭原因，便于排查服务端/网络导致的异常退出
            close_reason = getattr(e, "reason", "") or ""
            close_code = getattr(e, "code", "")
            ui.warn(f"实时语音连接已关闭 (code={close_code}, reason={close_reason})")
        except Exception as e:
            msg = str(e)
            if "401" in msg or "403" in msg:
                ui.error(
                    "实时对话鉴权失败（HTTP 401/403）。"
                    "请检查：1) DASHSCOPE_API_KEY 是否配置且有效；"
                    "2) 是否已开通 DashScope 实时语音/多模态服务；"
                    "3) [realtime_talk] 中的 ws_url 是否与你的业务空间一致。"
                )
                ui.on_status("error")
            else:
                ui.error(f"实时对话异常: {e}")
                ui.on_status("error")
        finally:
            self._running = False
            self._cleanup()
            ui.on_status("standby")
            ui.info("\n已退出实时语音对话")

    async def _send_audio(self, ws, ui) -> None:
        """持续读取麦克风并发送音频，同时周期性上报音量。

        若 AEC 已启用，麦克风数据先经过回声消除再发送：
        - AI 说话时的扬声器回声被 AEC3 消除
        - 用户真实语音保留，可正常触发打断
        @author aceFelix
        """
        chunk_count = 0
        while self._running:
            # 优雅停止期间不再发送麦克风数据
            if self._graceful_stop_at is not None:
                await asyncio.sleep(0.1)
                continue
            try:
                data = await asyncio.to_thread(self._mic.read, CHUNK_BYTES, False)

                # AEC 回声消除：用扬声器参考信号消除麦克风中的回声分量
                if self._aec is not None:
                    try:
                        data = await asyncio.to_thread(self._aec.process_mic, data)
                    except Exception:
                        pass
                    # AEC 可能因帧对齐缓冲产生空数据，跳过发送
                    if not data:
                        await asyncio.sleep(SEND_INTERVAL)
                        continue

                # 二次回声抑制：AI 正在说话时，压低麦克风增益，
                # 防止 AEC 没完全消除的残余回声被服务器 VAD 误判成人声。
                if self._ai_speaking:
                    data = _attenuate_pcm(data, ECHO_SUPPRESS_FACTOR)

                b64 = base64.b64encode(data).decode()
                await ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": b64,
                }))

                chunk_count += 1
                if chunk_count >= VOLUME_REPORT_INTERVAL:
                    chunk_count = 0
                    try:
                        ui.on_volume(_rms(data))
                    except Exception:
                        pass

                await asyncio.sleep(SEND_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.05)

    async def _recv_events(self, ws, ui) -> None:
        """接收并处理服务器事件。"""
        async for msg in ws:
            if not self._running:
                break
            # 优雅停止：宽限期过后停止接收
            if self._graceful_stop_at is not None:
                import time as _time
                if _time.monotonic() > self._graceful_stop_at:
                    self._running = False
                    break
            try:
                event = json.loads(msg)
            except json.JSONDecodeError:
                continue

            t = event.get("type", "")

            if t == "response.audio.delta":
                # AI 语音 → 直接播放（与官方示例一致）
                delta = event.get("delta", "")
                if delta:
                    if not self._ai_speaking:
                        self._ai_speaking = True
                        ui.on_ai_speaking(True)
                        ui.on_status("speaking")
                    # 记录当前回复开始时间，用于防御性超时检测
                    if self._response_start_ts is None:
                        import time as _t
                        self._response_start_ts = _t.monotonic()
                    else:
                        # 防御性超时：单条回复超过阈值，主动截断防服务端断连
                        import time as _t
                        elapsed = _t.monotonic() - self._response_start_ts
                        if elapsed > MAX_RESPONSE_SECONDS:
                            ui.warn(
                                f"⏰ 单条回复已持续 {int(elapsed)}s，主动截断防止服务端断连"
                            )
                            try:
                                await ws.send(json.dumps({"type": "response.cancel"}))
                            except Exception:
                                pass
                            self._response_start_ts = None
                    audio = base64.b64decode(delta)
                    # AEC：把扬声器即将播放的音频作为远端参考信号喂给回声消除器，
                    # 这样 AEC3 能在麦克风数据中识别并消除这部分回声。
                    if self._aec is not None and audio:
                        try:
                            self._aec.feed_reference(audio)
                        except Exception:
                            pass
                    # 打断保护：捕获当前代数，写入前检查是否已被用户打断
                    gen = self._response_gen
                    await asyncio.to_thread(self._spk.write, audio)
                    if gen != self._response_gen:
                        # 用户已打断，跳过本次回复的剩余音频
                        continue

            elif t == "response.audio.done":
                self._ai_speaking = False
                self._response_start_ts = None  # 重置超时计时器
                ui.on_ai_speaking(False)
                ui.on_status("standby")
                # 优雅停止：告别语音播放完毕，立即结束会话
                if self._graceful_stop_at is not None:
                    self._running = False
                    ui.info("🛑 end_conversation 优雅停止触发，会话即将结束")

            elif t == "input_audio_buffer.speech_started":
                # 用户开始说话 → 立即打断 AI 回复
                self._response_gen += 1  # 递增代数，丢弃残余音频
                self._ai_speaking = False
                self._response_start_ts = None
                ui.on_ai_speaking(False)
                ui.on_user_speaking(True)
                ui.on_status("listening")
                # 通知服务器停止生成，避免继续下发音频
                try:
                    await ws.send(json.dumps({"type": "response.cancel"}))
                except Exception:
                    pass
                # 清空扬声器缓冲区
                if self._spk:
                    try:
                        self._spk.stop_stream()
                        self._spk.start_stream()
                    except Exception:
                        pass

            elif t == "input_audio_buffer.speech_stopped":
                ui.on_user_speaking(False)
                if not self._ai_speaking:
                    ui.on_status("standby")

            elif t == "conversation.item.input_audio_transcription.completed":
                transcript = event.get("transcript", "")
                if transcript:
                    # 统一交给 UI 协议中的 on_user_transcript 显示，
                    # 不再额外调用 info()，避免 Webview 实现中气泡重复。
                    ui.on_user_transcript(transcript)

            elif t == "response.audio_transcript.delta":
                # 流式转写：AI 语音对应的文字逐块下发，实时显示
                delta_text = event.get("delta", "")
                if delta_text:
                    try:
                        ui.on_ai_transcript_delta(delta_text)
                    except Exception:
                        pass

            elif t == "response.audio_transcript.done":
                transcript = event.get("transcript", "")
                if transcript:
                    # 统一交给 UI 协议中的 on_ai_transcript 显示。
                    ui.on_ai_transcript(transcript)

            elif t == "response.output_item.added":
                # Function Calling：模型决定调用工具时，先收到此事件记录 call_id→name
                item = event.get("item", {})
                if item.get("type") == "function_call":
                    call_id = item.get("call_id", "")
                    name = item.get("name", "")
                    if call_id and name:
                        self._pending_calls[call_id] = name

            elif t == "response.function_call_arguments.done":
                # Function Calling：参数接收完毕，执行工具并写回结果
                call_id = event.get("call_id", "")
                arguments_str = event.get("arguments", "{}")
                name = self._pending_calls.pop(call_id, "")

                if name:
                    # 解析工具参数
                    try:
                        args = json.loads(arguments_str) if arguments_str else {}
                    except json.JSONDecodeError:
                        args = {}

                    ui.info(f"\n🔧 调用工具: {name}({args})")

                    # 执行工具：内置工具优先，其次查 registry 只读工具
                    result = await self._execute_tool(name, args, ui)

                    # 写回工具执行结果到对话上下文
                    await ws.send(json.dumps({
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": result,
                        },
                    }))

                    # 触发二轮推理：模型基于工具结果生成语音回复
                    await ws.send(json.dumps({
                        "type": "response.create",
                        "response": {
                            "modalities": ["audio", "text"],
                        },
                    }))

            elif t == "error":
                err = event.get("error", {})
                ui.warn(f"\n⚠ {err.get('message', str(event))}")
                ui.on_status("error")

    async def _execute_tool(self, name: str, args: dict[str, Any], ui) -> str:
        """执行 Function Calling 工具，返回 JSON 格式的结果字符串。

        执行顺序：
        1. 内置工具（get_current_time 等同步快速工具）
        2. ToolRegistry 工具（FileRead、Glob、Bash、FileWrite 等全部工具）

        安全策略：
        - instructions 引导模型对高风险操作先语音询问用户确认
        - check_permissions 返回 deny 的操作拒绝执行
        - 工具结果超长时截断，避免语音播报过长

        @author aceFelix
        """
        # 1. 内置工具
        builtin_names = {t["function"]["name"] for t in _BUILTIN_TOOLS}
        if name in builtin_names:
            # end_conversation 需要停止会话循环（等待告别语音播放后再停）
            if name == "end_conversation":
                import time as _time
                self._graceful_stop_at = _time.monotonic() + 4.0  # 4秒宽限期
                return json.dumps({"result": "好的，我先退下了。需要时随时叫我。"}, ensure_ascii=False)
            return _execute_builtin_tool(name, args)

        # 2. ToolRegistry 工具
        if name in self._registry_tool_map:
            tool = self._registry_tool_map[name]
            try:
                from agent.core.context import ToolContext
                ctx = ToolContext(
                    workdir=self._workdir,
                    messages=[],
                    permission_mode="yolo",
                    ui=ui,
                )

                # 权限检查：deny 拒绝，ask/allow 放行
                # 高风险操作的确认由模型通过 instructions 引导处理
                try:
                    perm = tool.check_permissions(args, ctx)
                    if hasattr(perm, "action") and perm.action == "deny":
                        reason = getattr(perm, "reason", "安全策略拒绝")
                        return json.dumps(
                            {"error": f"操作被安全策略拒绝: {reason}"},
                            ensure_ascii=False,
                        )
                except Exception:
                    pass

                tool_result = await tool.call(args, ctx)
                # ToolResult.data → JSON 字符串
                data = tool_result.data
                if isinstance(data, str):
                    result_str = data
                elif data is None:
                    result_str = json.dumps({"result": "（无输出）"}, ensure_ascii=False)
                else:
                    result_str = json.dumps(data, ensure_ascii=False, default=str)

                # 截断超长结果，避免语音播报过长（最多 4000 字符）
                _MAX_RESULT_CHARS = 4000
                if len(result_str) > _MAX_RESULT_CHARS:
                    result_str = result_str[:_MAX_RESULT_CHARS] + "\n...（结果已截断）"
                return result_str
            except Exception as e:
                return json.dumps(
                    {"error": f"工具 {name} 执行失败: {e}"},
                    ensure_ascii=False,
                )

        # 3. 未知工具
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)

    async def _watchdog(self, ws: Any) -> None:
        """看门狗：检测到 _running 变为 False 时主动关闭 WebSocket，解除 _recv_events 阻塞。

        避免用户点击"结束"/ESC/"退下"后，recv_events 仍在空等消息导致会话无法退出。
        """
        try:
            while self._running:
                await asyncio.sleep(0.2)
            # 触发 WebSocket 关闭，让 recv_events 立即退出
            try:
                await ws.close()
            except Exception:
                pass
        except Exception:
            pass

    async def _esc_watcher(self, ui) -> None:
        """ESC 键退出。"""
        try:
            import keyboard
            while self._running:
                if keyboard.is_pressed("esc"):
                    ui.info("\nESC 退出...")
                    self._running = False
                    return
                await asyncio.sleep(0.15)
        except ImportError:
            # keyboard 库不可用 → 静默等待（靠 Ctrl+C 退出）
            while self._running:
                await asyncio.sleep(0.5)

    def _cleanup(self) -> None:
        # 断开 MCP 连接
        mcp = getattr(self, "_mcp_client", None)
        if mcp:
            try:
                import asyncio as _asyncio
                _asyncio.get_event_loop().create_task(mcp.disconnect_all())
            except Exception:
                pass

        for dev in ("_mic", "_spk"):
            obj = getattr(self, dev, None)
            if obj:
                try:
                    obj.stop_stream()
                    obj.close()
                except Exception:
                    pass
        if self._pya:
            try:
                self._pya.terminate()
            except Exception:
                pass
