"""实时双工语音对话 —— /talk 终端适配器（2026-09-28 重构，aceFelix）。

本模块是 :class:`RealtimeEngine`（realtime_engine.py，协议状态机核心）的
**终端适配层**：负责 PyAudio 采集设备、ESC 退出监听、会话横幅与工具装配，
随后把音频流注入引擎并驱动会话主循环。

架构（为 jarvis-desktop 桥接预留）::

    RealtimeEngine（协议/状态机/静音/救援/Function Calling，传输无关）
        ↑ 鸭子类型音频接口：_mic.read(n, False) / _spk.write(pcm)
    RealtimeTalk（本文件：PyAudio 终端适配器）
    未来桌面适配器：Web Audio 采集 / 本地回环 / WebRTC 轨道实现同一接口

重构要点（详见 realtime_engine.py docstring 与 fixlogs）：
- 默认 ``turn_detection = "server_vad"``（官方免提推荐）：尾音延长当前轮，
  规避 smart_turn 语义提前判停导致的"尾音重检 → turn_detected 掐死响应"。
- 默认 ``tools_mode = "builtin"``：只注册 get_current_time / end_conversation
  两个工具；299 工具 schema 的全量会话实测会出现"轮次提交后服务端迟迟不
  创建响应"。tools_mode = "all" 恢复注册表 + MCP 全量（需自行验证）。
- MCP 工具仍在首个 session.update 之前加载（会话配置一次性 IDLE 完成）。

用法: /talk 启动，ESC 退出
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from .realtime_engine import RealtimeEngine
from .realtime_mcp import load_mcp_tools_async as mcp_load_tools
from .realtime_tools import BUILTIN_TOOLS, build_all_tools

try:
    import pyaudio
except ImportError:
    pyaudio = None

# 常量再导出：保持重构前 realtime_talk 的模块级公共面（测试与外部调用方
# 以 rt_mod.CHUNK_BYTES / rt_mod.ECHO_GUARD_RMS 等方式引用，行为不变）
from .realtime_engine import (  # noqa: F401
    CHUNK_BYTES,
    INPUT_RATE,
    MAX_RESPONSE_SECONDS,
    OUTPUT_RATE,
    RESCUE_AFTER_CANCEL_SECONDS,
    RESCUE_AFTER_COMMIT_SECONDS,
    RESCUE_HOLD_SECONDS,
    RESCUE_MIN_INTERVAL_SECONDS,
    SEND_INTERVAL,
)
from .realtime_audio import (  # noqa: F401
    ECHO_GUARD_RMS,
    ECHO_SUPPRESS_FACTOR,
    MIC_MUTE_MAX_SECONDS,
    MIC_TAIL_SECONDS,
    MIC_TURN_END_MUTE_SECONDS,
)

# ---- 默认配置（可被 settings.toml [realtime_talk] 覆盖） ----
# DashScope 实时语音/多模态公共 WebSocket 端点。
# 如需业务空间专属域名，在 settings.toml [realtime_talk] 中覆盖 ws_url。
DEFAULT_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_VOICE = "longanqian"
DEFAULT_SILENCE_MS = 500
DEFAULT_VAD_THRESHOLD = 0.5

# 内置工具模式（默认）的系统指令：工具面收敛为时间查询，话术同步收敛
_INSTRUCTIONS_BUILTIN = (
    "你是贾维斯，先生的全能管家。用简洁自然的口语回复，不要输出思考过程。"
    "\n用户询问当前时间、日期、星期几时，先调用 get_current_time 工具获取"
    "实时信息，再用自然口语回答。"
    "\n高风险操作（执行命令、删除文件、发送邮件）先用语音简短确认，一般操作直接执行。"
)

# 全量工具模式（tools_mode="all"）的系统指令：覆盖注册表 + MCP 工具面
_INSTRUCTIONS_ALL = (
    "你是贾维斯，先生的全能管家。用简洁自然的口语回复，不要输出思考过程。"
    "\n\n你拥有丰富的工具集，涵盖文件读写、代码搜索、Bash命令、浏览器操作、"
    "GUI控制、MCP外部服务（天气/时间/票务等）、网页搜索等。"
    "根据用户意图自行判断该调用哪个工具，优先使用专用工具而非 WebSearch。"
    "\n【地域性查询规则】涉及天气、新闻、本地服务、附近推荐等地域性查询时，"
    "先用 Location 工具自动定位获取所在城市；只有定位失败时才询问先生所在地，"
    "不要自行假设任何城市。"
    "\n高风险操作（执行命令、删除文件、发送邮件）先用语音简短确认，一般操作直接执行。"
)


class RealtimeTalk(RealtimeEngine):
    """实时双工语音对话（终端 PyAudio 适配器）。

    构造函数参数与重构前保持兼容（voice_commands / workbench 两处调用点
    无需修改），新增 ``turn_detection`` 与 ``tools_mode`` 两个开关。
    """

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
        event_log: bool = False,
        echo_suppress_with_aec: bool = True,
        half_duplex: bool = True,
        rescue: bool = True,
        turn_detection: str = "server_vad",
        tools_mode: str = "builtin",
    ) -> None:
        self._tools_mode = tools_mode
        # 未显式传入 instructions 时按工具模式选择默认话术
        if not instructions:
            instructions = (
                _INSTRUCTIONS_ALL if tools_mode == "all" else _INSTRUCTIONS_BUILTIN
            )
        super().__init__(
            api_key,
            model=model,
            voice=voice,
            instructions=instructions,
            ws_url=ws_url,
            turn_detection=turn_detection,
            silence_duration_ms=silence_duration_ms,
            vad_threshold=vad_threshold,
            workdir=workdir,
            event_log=event_log,
            echo_suppress_with_aec=echo_suppress_with_aec,
            half_duplex=half_duplex,
            rescue=rescue,
        )
        self._pya: Any = None

    async def run(self, ui) -> None:
        """启动实时对话（终端路径）。ESC 退出。"""

        if pyaudio is None:
            ui.error("缺少 pyaudio 库，请运行: pip install pyaudio（Windows 上可能需要从 https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio 下载 whl 安装）")
            return

        # 初始化 PyAudio（音频传输对象，注入引擎鸭子类型接口）
        from .realtime_engine import INPUT_RATE, OUTPUT_RATE
        self._pya = pyaudio.PyAudio()
        try:
            mic = self._pya.open(
                format=pyaudio.paInt16, channels=1, rate=INPUT_RATE, input=True
            )
            spk = self._pya.open(
                format=pyaudio.paInt16, channels=1, rate=OUTPUT_RATE, output=True
            )
        except Exception as e:
            ui.error(f"音频设备初始化失败: {e}")
            self._cleanup()
            return
        self.attach_audio(mic, spk)

        mode = "半双工轮替·AI 说完你再说" if self._half_duplex else "全双工·说话打断"
        ui.info("=" * 56)
        ui.info("🎙️  实时双工语音对话已开启")
        ui.info(f"   模型: {self._model}  ·  音色: {self._voice}")
        ui.info(f"   轮次检测: {self._turn_detection} · {mode} · ESC 退出")
        if self._aec is not None:
            ui.info("   AEC 回声消除已启用")
        else:
            ui.info("   （未启用 AEC，建议 pip install aec-audio-processing 以消除回声）")
        if self._tools_mode == "all":
            ui.info("   工具模式: 全量（注册表 + MCP）—— 会话启动稍慢")
        else:
            ui.info("   工具模式: 内置（时间/结束对话）—— 低延迟优先")
        # 识别结果图例：服务端判为非有效轮次的语音走环境音转写通道（🔇 前缀）
        ui.info("   识别结果：✅ 进入对话轮 ｜ 🔇 未入对话轮（服务端判为非有效语音）")
        log_hint = self._events.log_hint()
        if log_hint:
            ui.info(f"   事件时间线日志: 开 → {log_hint}")
        ui.info("=" * 56)

        # 工具装配（2026-09-28 重构）：默认只注册内置工具；tools_mode="all"
        # 时装配注册表 + MCP 全量。MCP 必须在首个 session.update 之前加载
        # （会话配置一次性 IDLE 完成，详见 realtime_engine docstring 经验 ①）。
        tools = list(BUILTIN_TOOLS)
        registry_tool_map: dict[str, Any] = {}
        if self._tools_mode == "all":
            registry_tools, registry_tool_map = build_all_tools(self._workdir)
            mcp_schemas: list[dict] = []
            try:
                mcp_schemas = await asyncio.wait_for(
                    mcp_load_tools(self, ui), timeout=20.0
                )
            except asyncio.TimeoutError:
                ui.warn("MCP 工具加载超时（20s），本次会话使用内置 + Registry 工具")
            except Exception as e:
                ui.warn(f"MCP 工具加载异常: {e}")
            tools = list(BUILTIN_TOOLS) + registry_tools + mcp_schemas
        self.set_tools(tools, registry_tool_map)

        try:
            await self.run_session(ui, extra_tasks=[self._esc_watcher(ui)])
        finally:
            self._cleanup()
            ui.info("\n已退出实时语音对话")

    # ------------------------------------------------------------------
    # 终端侧辅助
    # ------------------------------------------------------------------

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
                asyncio.get_event_loop().create_task(mcp.disconnect_all())
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
