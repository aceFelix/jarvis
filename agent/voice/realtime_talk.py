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
- 默认 ``tools_mode = "builtin"``（勿改为 "all"）：只注册 get_current_time /
  end_conversation 两个工具。**全量工具表会让响应创建延迟随工具数近似线性增长**
  （探针实测：2 个 0.67s → 294 个 3.45s → 450 个 4.89s，服务端始终会建响应，
  只是越来越慢）。该延迟与「响应救援」(1.8s 补发 response.create) 和 server_vad
  尾音重检叠加，形成 response.done[cancelled] 死循环——表现为用户说完话半天无回复。
  2026-10-09 曾误把默认改成 "all" 导致实时语音完全不可用，已回退。产品定位：
  **实时语音只做闲聊/低延迟对话，需要工具时走半双工 /voice**。确需工具子集时
  精选 10~20 个（勿一次性注册全量），并实机验证。详见
  docs/fixlogs/voice/realtime-talk-tools-mode-default-fix.md。
- MCP 工具仍在首个 session.update 之前加载（会话配置一次性 IDLE 完成）。

用法: /talk 启动，ESC 退出
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from .realtime_engine import RealtimeEngine
from .realtime_mcp import load_mcp_tools_async as mcp_load_tools
from .realtime_tools import BUILTIN_TOOLS, build_all_tools
# 默认端点/音色/模型统一由注册表提供（阶段一只搬 dashscope，行为不变）
from .voice_providers import DASHSCOPE_QWEN_AUDIO

try:
    import pyaudio
except ImportError:
    pyaudio = None

# PyAudio 的 paInt16 常量（枚举值 8）。CI 等无 pyaudio 环境里模块为 None，
# 但 _TerminalSpeaker 的单测用假设备构造，仍需要一个格式值；
# 运行期真实打开设备时 pyaudio 必然存在，结果与 paInt16 一致。
_PA_INT16 = getattr(pyaudio, "paInt16", 8)

# 常量再导出：保持重构前 realtime_talk 的模块级公共面（测试与外部调用方
# 以 rt_mod.CHUNK_BYTES / rt_mod.ECHO_GUARD_RMS 等方式引用，行为不变）
from .realtime_const import (  # noqa: F401
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
# 数值统一从 voice_providers 的 dashscope 条目搬运（阶段一行为零变化）。
# 如需业务空间专属域名，在 settings.toml [realtime_talk] 中覆盖 ws_url。
DEFAULT_WS_URL = DASHSCOPE_QWEN_AUDIO.ws_url
DEFAULT_VOICE = DASHSCOPE_QWEN_AUDIO.default_voice
DEFAULT_MODEL = DASHSCOPE_QWEN_AUDIO.realtime_models[0]
DEFAULT_SILENCE_MS = 500
DEFAULT_VAD_THRESHOLD = 0.5

# 内置工具模式（默认）的系统指令：工具面收敛为时间查询，话术同步收敛
# 人设：星期五（Friday）——贾维斯之外的第二位智能管家（2026-11-26 改，aceFelix）。
# 两份指令的身份段必须保持一致，否则切 tools_mode 会换人格。
_INSTRUCTIONS_BUILTIN = (
    "你是星期五Friday（Female Replacement Intelligent Digital Assistant Youth），"
    "先生的第二位智能管家，贾维斯（JARVIS）之外的另一位管家。"
    "被问到身份时自称星期五，不要自称贾维斯。用简洁自然的口语回复，不要输出思考过程。"
    "\n用户询问当前时间、日期、星期几时，先调用 get_current_time 工具获取"
    "实时信息，再用自然口语回答。"
    "\n高风险操作（执行命令、删除文件、发送邮件）先用语音简短确认，一般操作直接执行。"
    "\n用户说“停一下 / 等一下 / 暂停 / 安静 / 别说了”时，只是要求停止播报或等待，"
    "直接回应并继续听，绝不能调用 end_conversation；只有明确说退下/结束对话/再见/"
    "拜拜/没事了才结束。普通回答结束后也绝不调用 end_conversation。"
    "\n调用 end_conversation 时，必须同时用一句简短自然的口语向先生告别"
    "（如「好的先生，我先退下了」），会话会等这句告别播完再退出。"
)

# 全量工具模式（tools_mode="all"）的系统指令：覆盖注册表 + MCP 工具面
# 身份段与 _INSTRUCTIONS_BUILTIN 同口径（星期五，2026-11-26 改，aceFelix）
_INSTRUCTIONS_ALL = (
    "你是星期五（Friday），先生的第二位智能管家，贾维斯（JARVIS）之外的另一位管家。"
    "被问到身份时自称星期五，不要自称贾维斯。用简洁自然的口语回复，不要输出思考过程。"
    "\n\n你拥有丰富的工具集，涵盖文件读写、代码搜索、Bash命令、浏览器操作、"
    "GUI控制、MCP外部服务（天气/时间/票务等）、网页搜索等。"
    "根据用户意图自行判断该调用哪个工具，优先使用专用工具而非 WebSearch。"
    "\n【地域性查询规则】涉及天气、新闻、本地服务、附近推荐等地域性查询时，"
    "先用 Location 工具自动定位获取所在城市；只有定位失败时才询问先生所在地，"
    "不要自行假设任何城市。"
    "\n高风险操作（执行命令、删除文件、发送邮件）先用语音简短确认，一般操作直接执行。"
    "\n用户说“停一下 / 等一下 / 暂停 / 安静 / 别说了”时，只是要求停止播报或等待，"
    "直接回应并继续听，绝不能调用 end_conversation；只有明确说退下/结束对话/再见/"
    "拜拜/没事了才结束。普通回答结束后也绝不调用 end_conversation。"
    "\n调用 end_conversation 时，必须同时用一句简短自然的口语向先生告别"
    "（如「好的先生，我先退下了」），会话会等这句告别播完再退出。"
)


def default_instructions(tools_mode: str = "builtin") -> str:
    """按工具模式返回默认系统指令（星期五人设）。

    RealtimeTalk 终端路径与桌面全双工路径（workbench engine
    `_handle_start_talk_duplex` 直接构造父类 RealtimeEngine）共用此回退，
    保证两条路径人设一致——桌面路径若不传 instructions，DashScope 实时
    模型会回落自带默认人设（自称"小云"，见 model-docs/dashscope 示例）。
    默认 tools_mode 与 settings.realtime_tools_mode 同步为 "builtin"
    （2026-10-09 误改 "all" 导致实时语音不可用后回退，勿再改）。

    @author aceFelix
    @updated 2026-11-26  # 人设由贾维斯改为星期五（第二位智能管家）
    """
    return _INSTRUCTIONS_ALL if tools_mode == "all" else _INSTRUCTIONS_BUILTIN


class _TerminalSpeaker:
    """可中断的 PyAudio 输出包装（终端专用）。

    正常路径与原来完全一致：直接 `write`，不排队、不预缓冲，保证播放连续。
    打断时只做 `stop_stream()/start_stream()`：**绝不 close 输出流**——
    2026-11-25 实机 `close()+open()` 与另一线程的麦克风 `read()` 并发，
    在 Windows 上直接 Segmentation fault（PortAudio 非线程安全）。

    残余缓冲靠“接收端分片写（≤ 40ms/片）+ 写前代数检查”压到最小：
    打断后最多再播一片。

    @author aceFelix
    """

    def __init__(self, pa: Any) -> None:
        self._pa = pa
        self._stream = self._open()
        # 同一输出流的 write/abort 可能来自不同任务（recv 写、send 侧本地 VAD 打断），
        # PortAudio 非线程安全，必须串行化。锁只包输出流，不阻塞麦克风 read。
        self._lock = threading.Lock()

    def _open(self) -> Any:
        return self._pa.open(
            format=_PA_INT16, channels=1, rate=OUTPUT_RATE, output=True
        )

    def write(self, pcm: bytes) -> None:
        stream = self._stream
        if stream is not None:
            with self._lock:
                stream.write(pcm)

    def abort(self) -> None:
        """打断：停流再启（不销毁流对象，避免与录音线程并发导致崩溃）。"""
        stream = self._stream
        if stream is None:
            return
        with self._lock:
            try:
                stream.stop_stream()
                stream.start_stream()
            except Exception:
                pass

    # ---- 兼容旧鸭子接口（看门狗/收尾/单测替身）----
    def stop_stream(self) -> None:
        if self._stream is not None:
            with self._lock:
                self._stream.stop_stream()

    def start_stream(self) -> None:
        if self._stream is not None:
            with self._lock:
                self._stream.start_stream()

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            with self._lock:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass


class RealtimeTalk(RealtimeEngine):
    """实时双工语音对话（终端 PyAudio 适配器）。

    构造函数参数与重构前保持兼容（voice_commands / workbench 两处调用点
    无需修改），新增 ``turn_detection`` 与 ``tools_mode`` 两个开关。
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
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
        # 工具模式（默认 "builtin" 仅内置两工具，与 settings.realtime_tools_mode
        # 同步；"all" 全量 294 schema 会让服务端不建响应，勿轻易切）
        tools_mode: str = "builtin",
    ) -> None:
        self._tools_mode = tools_mode
        # 未显式传入 instructions 时按工具模式选择默认话术（星期五人设回退）
        if not instructions:
            instructions = default_instructions(tools_mode)
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

    def _make_speaker(self) -> "_TerminalSpeaker":
        """构造终端输出流包装（支持打断时关流重开）。"""
        return _TerminalSpeaker(self._pya)

    async def run(self, ui) -> None:
        """启动实时对话（终端路径）。ESC 退出。"""

        if pyaudio is None:
            ui.error("缺少 pyaudio 库，请运行: pip install pyaudio（Windows 上可能需要从 https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio 下载 whl 安装）")
            return

        # 初始化 PyAudio（音频传输对象，注入引擎鸭子类型接口）
        # INPUT_RATE / OUTPUT_RATE 已在模块顶部从 realtime_const 导入，此处不再局部重复导入
        self._pya = pyaudio.PyAudio()
        try:
            mic = self._pya.open(
                format=_PA_INT16, channels=1, rate=INPUT_RATE, input=True
            )
        except Exception as e:
            ui.error(f"音频设备初始化失败: {e}")
            self._cleanup()
            return
        self.attach_audio(mic, self._make_speaker())

        # 麦克风互斥：/voice 与 /talk、以及两个 /talk 不能同时跑。
        # 2026-11-25 实机：两个引擎同时开麦/放音，互相把对方的声音当插话，
        # 表现为双份 local barge、声音混乱。没有锁时只能靠用户自己避。
        from agent.voice.voice_state import acquire_voice_lock, release_voice_lock

        lock_ok, lock_info = acquire_voice_lock()
        if not lock_ok:
            ui.error(f"麦克风被其他语音会话占用：{lock_info}")
            self._cleanup()
            return

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
            release_voice_lock()
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
