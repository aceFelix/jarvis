"""实时语音引擎核心 —— 协议状态机与音频策略（2026-09-28 重构，aceFelix）。

从 realtime_talk.py 拆出的**传输无关核心**：只负责 DashScope Realtime
WebSocket 协议、会话状态机、半双工静音策略、响应救援与 Function Calling。
音频输入/输出通过鸭子类型接口注入（``self._mic.read(n, False)`` /
``self._spk.write(pcm)`` / ``stop_stream/start_stream``）：

- 终端适配器（realtime_talk.RealtimeTalk）：PyAudio 流
- 桌面端（jarvis-desktop）：任意实现同三方法表面的对象——Web Audio 采集桥、
  本地回环 socket 或 WebRTC 轨道均可复用本引擎，无需改动协议层

重构时沉淀的三条硬经验（详见各分支注释与 fixlogs）：

1. **会话配置一次性在 IDLE 状态完成**：MCP 工具必须在首个 session.update
   之前加载并入 tools；中途补发 session.update 会让此后每条响应在 ~100ms 内
   被服务端 cancelled（无 speech_started、无客户端 cancel、无 error）。
2. **smart_turn 的语义提前判停是"尾音重检"灾难之源**：判停后客户端仍在直播
   真实麦克风，尾音/换气被服务端 VAD 重新检出为新轮次 → 刚创建的响应被
   response.done[cancelled, reason=turn_detected] 掐死，且被检出的"轮次"再无
   下文。故**默认切换为官方对免提场景推荐的 server_vad**（尾音会延长当前轮
   而非触发新轮次），smart_turn 仅作可选（配轮末静音窗口缓解）。
3. **工具表必须瘦身**：299 个工具 schema 的会话出现过"轮次提交后服务端迟迟
   不自动创建响应"。默认只注册 2 个内置工具；tools_mode="all" 才全量。
   另需 sanitize_tools_for_realtime 清洗非法函数名（防模型受限解码停滞）。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import base64
import json
import time as _t
from typing import Any

from .realtime_audio import (
    ECHO_GUARD_RMS,
    ECHO_SUPPRESS_FACTOR,
    MIC_MUTE_MAX_SECONDS,
    MIC_TAIL_SECONDS,
    MIC_TURN_END_MUTE_SECONDS,
    _attenuate_pcm,
    _rms,
    mic_muted,
    silence_like,
    voice_gap,
    should_attenuate,
)
from .realtime_events import AMBIENT_COMPLETED, AMBIENT_DELTA, TalkEventLog
from .realtime_tools import (
    execute_tool,
    sanitize_tools_for_realtime,
)

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

# ---- 音频参数 ----
INPUT_RATE = 16000    # 麦克风：16kHz
OUTPUT_RATE = 24000   # 扬声器：24kHz
CHUNK_BYTES = 3200    # 每次读取 3200 字节（~100ms @ 16kHz mono 16bit）
SEND_INTERVAL = 0.02  # 发送间隔 20ms
VOLUME_REPORT_INTERVAL = 8  # 每 8 个 chunk 上报一次音量（~160ms）
# 单条 AI 回复最大持续时长（秒），超过后主动截断，避免服务端 1006 断连
MAX_RESPONSE_SECONDS = 90.0
# 响应救援时延（秒）：turn_detected 取消后服务端不再补答的等待；
# 轮次提交后服务端未自动创建响应的等待；两次救援最小间隔
RESCUE_AFTER_CANCEL_SECONDS = 1.2
RESCUE_AFTER_COMMIT_SECONDS = 1.8
RESCUE_MIN_INTERVAL_SECONDS = 2.0
# 救援后强制静音保持（秒）：补发 response.create 到 response.created 之间
# 用户任何声响都会被服务端当成新轮次掐死响应，故救援后立即重置静音窗口
RESCUE_HOLD_SECONDS = 1.0


class RealtimeEngine:
    """DashScope Realtime 协议引擎（传输无关）。

    音频传输约定（由适配器注入的对象实现）：
    - ``self._mic.read(n_bytes, exception_on_overflow) -> bytes``
    - ``self._spk.write(pcm_bytes)``；打断时可选 ``stop_stream()/start_stream()``

    会话生命周期：适配器负责采集设备与 UI 装配后调用
    ``await engine.run_session(ui, extra_tasks=[...])``。
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "qwen-audio-3.0-realtime-flash",
        voice: str = "longanqian",
        instructions: str = "",
        ws_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        turn_detection: str = "server_vad",
        silence_duration_ms: int = 500,
        vad_threshold: float = 0.5,
        workdir: str = "",
        event_log: bool = False,
        echo_suppress_with_aec: bool = True,
        half_duplex: bool = True,
        rescue: bool = True,
        use_aec: bool = True,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._instructions = instructions
        self._ws_url = ws_url
        # 轮次检测模式：server_vad（官方免提推荐，默认）| smart_turn（语义判停，
        # 对"嗯/啊"过滤更好但尾音重检敏感，配轮末静音缓解）
        self._turn_detection = turn_detection
        self._silence_ms = silence_duration_ms
        self._vad_threshold = vad_threshold
        self._workdir = workdir

        # 可观测性：事件时间线 + 环境音转写（实现见 realtime_events.py）。
        # 默认只做内存计数；event_log=True 时把事件时间线写入 ~/.jarvis/logs/diag.log
        self._events = TalkEventLog(enabled=event_log)

        # 半双工静音状态：仅在“进入/退出静音”的时刻各写一条时间线（见 _mic_muted）
        self._mic_mute_logged: bool | None = None
        # 本地语音活动：最后一次检测到“用户正在说话”的时刻（单调时钟），
        # 0 表示当前没有说话。仅用于提前复位“正在说话”界面指示，不参与静音
        # 决策（服务端的 speech_stopped 到达时也会复位，重复调用无副作用）
        self._last_voice_ts = 0.0
        self._mic: Any = None    # 由适配器注入（鸭子类型 read 接口）
        self._spk: Any = None    # 由适配器注入（鸭子类型 write 接口）
        self._running = False
        self._ai_speaking = False
        # 服务端是否正处于一次活动响应中（response.created → response.done）。
        # 与 _ai_speaking（本地播放态）区分开：打断判定应以此为准，
        # 否则一次状态残留就会让后续每轮都误发 response.cancel。
        self._response_active = False
        # 优雅停止：end_conversation 工具触发后等待告别语音播放完毕
        self._graceful_stop_at: float | None = None
        # 防御性超时：记录当前回复首个 audio delta 时间，超时主动截断
        self._response_start_ts: float | None = None
        # 打断代数：每次用户打断时递增，用于丢弃旧回复的残余音频
        self._response_gen: int = 0
        # 最近一帧衰减后麦克风的实时 RMS（供回声保护判定，仅 AI 说话期间更新）
        self._last_mic_rms: float = 0.0
        # AI 说话期间是否对麦克风二次压低增益（兜底抑制 AEC 未消尽的回声）。
        # 默认 True：WebRTC AEC3 在免提外放 + 非理想延迟对齐下只能消除部分
        # 回声，残留会被服务端重新识别为用户语音→触发打断→response.cancel，
        # 表现为“AI 永远不开口”。去掉该衰减会直接导致对话不可用。
        self._echo_suppress_with_aec = echo_suppress_with_aec
        # 半双工开关（默认 True）：AI 说话期间不上传麦克风音频，AI 说完后留
        # 一段回声尾迹静默期再恢复拾取。免提外放 + 软件 AEC 下唯一可靠的多轮
        # 方案（否则回声会让服务端误判“用户仍在说话”→ AI 永远不开下一轮）。
        # 置 False 恢复全双工“随口打断”（适合戴耳机），此时依赖衰减+回声保护。
        self._half_duplex = half_duplex
        # 半双工：回声尾迹窗口的到期时刻（monotonic 秒），None 表示不在窗口内
        self._mic_resume_at: float | None = None
        # 当前响应的开始时刻（response.created 时记录），供半双工静音的悬挂保护
        self._response_begin_ts: float | None = None
        # 服务端视角的用户说话状态（speech_started→speech_stopped 窗口），
        # 由服务端事件维护，比本地 RMS 判定权威；供响应救援判断"用户是否真在
        # 说话"（不 rescue 进行中的轮次，避免 response.create 与轮次冲突）
        self._user_speaking_server = False
        # 响应救援（rescue=True 启用）：_rescue_at 为待补发 response.create 的
        # 到期时刻。设置点——① 用户轮次已提交但 response.created 迟迟未到；
        # ② 响应被 turn_detected 取消后服务端未自动补答。官方约束：等待用户
        # 下一轮输入时允许手动 response.create；轮内禁止（被服务端拒绝也只是
        # 良性 error，连接不受影响）。救援时置 _rescue_hold，防止用户即时声响
        # 在响应创建的关键窗口被当成新轮次（见 RESCUE_HOLD_SECONDS）。
        self._rescue = rescue
        self._rescue_at: float | None = None
        self._rescue_hold = False
        self._last_rescue_ts: float = 0.0
        # 救援 v2（2026-09-28 实机修正）——防"重复回答"三闸门：
        # ① _last_response_done_ts：任何 response.done 都会覆盖此前已提交的
        #   用户轮次（回答即应答），故到达即撤销待执行救援；救援触发还要求
        #   距上次应答完成 ≥1s，避免在"刚答完"的窗口内对 phantom 轮补发。
        # ② phantom 抑制（转写完成处）：单字/纯标点轮（多为尾音、环境音被
        #   VAD 误开的空轮）和与 AI 刚播内容重合的回声轮不武装救援——服务端
        #   本就不应答它们，补发只会产生重复回答。
        # ③ _last_ai_transcript：回声判定的比对基准。
        self._last_response_done_ts: float = 0.0
        self._last_ai_transcript: str = ""
        self._rescue_armed_at: float = 0.0
        # WebRTC AEC3 回声消除器（可选，依赖 aec-audio-processing + numpy）。
        # use_aec=False 用于桌面全双工桥接：渲染进程 getUserMedia 已带浏览器
        # 级 AEC（官方 WebSocket 协议"回声消除：无"的补齐项），Python 侧再消
        # 一遍会双重消除导致语音发闷/丢字。
        self._aec: Any = None
        if _HAS_AEC and use_aec:
            try:
                self._aec = EchoCanceller()
            except Exception:
                self._aec = None
        # Function Calling：call_id → 工具名映射，等待参数到达后执行；
        # 工具 schema 与别名映射由适配器在会话启动前注入 self._tools
        self._pending_calls: dict[str, str] = {}
        self._tools: list[dict[str, Any]] = []
        self._tool_alias_map: dict[str, str] = {}
        self._registry_tool_map: dict[str, Any] = {}
        self._mcp_client: Any = None

    @property
    def running(self) -> bool:
        """会话是否仍在运行。"""
        return self._running

    def stop(self) -> None:
        """请求结束会话（适配器的 ESC/退出按钮调用）。"""
        self._running = False

    # ------------------------------------------------------------------
    # 会话装配（适配器在 run_session 前调用）
    # ------------------------------------------------------------------

    def attach_audio(self, mic: Any, spk: Any) -> None:
        """注入音频传输对象（鸭子类型：read / write / stop/start_stream）。"""
        self._mic = mic
        self._spk = spk

    def set_tools(
        self,
        tools: list[dict[str, Any]],
        registry_tool_map: dict[str, Any],
        alias_map: dict[str, str] | None = None,
    ) -> None:
        """注入 Function Calling 工具集（schema 需为服务端合法函数名）。"""
        self._tools, extra = sanitize_tools_for_realtime(tools)
        self._registry_tool_map = registry_tool_map
        self._tool_alias_map = dict(alias_map or {})
        self._tool_alias_map.update(extra)

    # ------------------------------------------------------------------
    # 会话主循环
    # ------------------------------------------------------------------

    async def run_session(self, ui: Any, extra_tasks: list | None = None) -> None:
        """建立连接并运行会话主循环，直到 stop() 或连接关闭。

        Args:
            ui: 实现 RealtimeTalkUI 协议（agent.core.context）的 UI 适配器。
            extra_tasks: 适配器侧的附加协程（如 ESC 监听），与本引擎的
                收发/救援/看门狗协程并发运行。
        """
        if websockets is None:
            ui.error("缺少 websockets 库，请运行: pip install websockets")
            return

        url = f"{self._ws_url}?model={self._model}"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        ui.on_status("connecting")

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
                # 唯一一次 session.update：全部会话配置必须在任何音频帧发送
                # 之前送达（IDLE 状态约束，见模块 docstring 经验 ①）。
                td: dict[str, Any] = {"type": "smart_turn"} \
                    if self._turn_detection == "smart_turn" else {
                        "type": "server_vad",
                        "threshold": self._vad_threshold,
                        "silence_duration_ms": self._silence_ms,
                    }
                await ws.send(json.dumps({
                    "type": "session.update",
                    "session": {
                        "modalities": ["text", "audio"],
                        "voice": self._voice,
                        "instructions": self._instructions,
                        "turn_detection": td,
                        "tools": self._tools,
                    },
                }))
                self._events.note_client(
                    "session.update",
                    f"tools={len(self._tools)} td={self._turn_detection} (init)",
                )
                self._running = True
                ui.on_status("standby")

                tasks = [
                    self._send_audio(ws, ui),
                    self._recv_events(ws, ui),
                    self._response_rescue(ws, ui),
                    self._watchdog(ws),
                ]
                if extra_tasks:
                    tasks.extend(extra_tasks)
                await asyncio.gather(*tasks)

        except websockets.exceptions.ConnectionClosed as e:
            # 记录关闭原因，便于排查服务端/网络导致的异常退出
            close_reason = getattr(e, "reason", "") or ""
            close_code = getattr(e, "code", "")
            ui.warn(f"实时语音连接已关闭 (code={close_code}, reason={close_reason})")
            # 防呆翻译（aceFelix）：DashScope 鉴权拒绝（1007 Access denied /
            # account in good standing）把英文服务端报错映射成可操作的中文
            # 指引，覆盖 key 错误 / 账号欠费 / 实时模型未开通三种情况。
            if (
                close_code == 1007
                or "Access denied" in close_reason
                or "good standing" in close_reason
            ):
                ui.error(
                    "DashScope 鉴权被拒：API Key 无效，或百炼账号欠费/未开通"
                    "实时语音模型。请检查 dashscope_api_key（或环境变量 "
                    "DASHSCOPE_API_KEY）配置，并登录阿里云百炼控制台确认"
                    "账号状态与实时语音模型开通情况。"
                )
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
            ui.on_status("standby")
            # 会话诊断摘要：仅当有语音被服务端判为非有效轮次时才输出
            for line in self._events.report_lines():
                ui.info(line)

    # ------------------------------------------------------------------
    # 音频发送循环
    # ------------------------------------------------------------------

    async def _send_audio(self, ws: Any, ui: Any) -> None:
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

                # 半双工：AI 说话期间及等待窗口内不上传麦克风。必须放在 read
                # 之后——静音时若连读都不读，音频驱动缓冲会积压旧数据，恢复上传
                # 时首批送出的就是 AI 说话期间的回声，等于没静音。
                muted = self._mic_muted()
                # 本地语音活动跟踪：只用于复位“正在说话”指示与提前解除静音，
                # 不再据此静音（响应前静音会让服务端取消本轮，详见 voice_gap）
                was_speaking = self._last_voice_ts
                self._last_voice_ts, self._mic_resume_at = voice_gap(
                    _rms(data), self._last_voice_ts, self._mic_resume_at,
                    _t.monotonic(), muted=muted,
                )
                if self._rescue_hold and not self._response_active:
                    # 救援保持：补发 response.create 后、响应创建前的关键窗口，
                    # 用户任何声响都会被服务端当成新轮次掐死响应——此期间禁止
                    # voice_gap 解除静音（由 response.created/speech_started 释放）
                    self._mic_resume_at = max(
                        self._mic_resume_at or 0.0,
                        _t.monotonic() + RESCUE_HOLD_SECONDS,
                    )
                if was_speaking and not self._last_voice_ts:
                    # 刚判定“说完”：提前复位“正在说话”指示，不必等 speech_stopped
                    # （服务端随后下发该事件时也会复位，重复调用无副作用）
                    ui.on_user_speaking(False)
                if muted:
                    # 半双工：**不中断上传**，改为发送等长静音帧（原因见 silence_like）
                    data = silence_like(data)
                else:
                    # AEC 回声消除：用扬声器参考信号消除麦克风中的回声分量（静音帧无需处理）
                    if self._aec is not None:
                        try:
                            data = await asyncio.to_thread(self._aec.process_mic, data)
                        except Exception:
                            pass
                        # AEC 可能因帧对齐缓冲产生空数据，跳过发送
                        if not data:
                            await asyncio.sleep(SEND_INTERVAL)
                            continue

                    # 二次回声抑制：AI 说话期间压低麦克风增益，减少残余回声被
                    # 服务端重新识别为用户语音。衰减后实时电平用于回声保护判定。
                    if self._should_attenuate():
                        data = _attenuate_pcm(data, ECHO_SUPPRESS_FACTOR)
                        # 仅在 AI 说话期间计算电平（回声保护只此时生效），避免
                        # 非说话期逐帧算 RMS 的无谓开销
                        self._last_mic_rms = _rms(data)

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

    # ------------------------------------------------------------------
    # 事件接收循环
    # ------------------------------------------------------------------

    async def _recv_events(self, ws: Any, ui: Any) -> None:
        """接收并处理服务器事件。"""
        async for msg in ws:
            if not self._running:
                break
            # 优雅停止：宽限期过后停止接收
            if self._graceful_stop_at is not None:
                if _t.monotonic() > self._graceful_stop_at:
                    self._running = False
                    break
            try:
                event = json.loads(msg)
            except json.JSONDecodeError:
                continue

            t = event.get("type", "")

            # 可观测性（零行为风险）：每条事件计数，开关开启时写时间线日志。
            # speech_started 额外标注「当时是否有活动响应」，用于区分打断与新一轮。
            if t == "input_audio_buffer.speech_started":
                # 权威依据：服务端是否有活动响应（response.created 尚未配对
                # response.done）。不用本地播放态推断，避免状态残留误发取消。
                had_active_response = self._response_active
                self._events.observe(
                    t, event, detail=f"active_response={had_active_response}"
                )
                self._events.note_interrupt()
            else:
                self._events.observe(t, event)

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
                        self._response_start_ts = _t.monotonic()
                    else:
                        # 防御性超时：单条回复超过阈值，主动截断防服务端断连
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

            elif t == "response.created":
                # 一次新的服务端响应开始 → 标记为活动响应，供打断判定使用
                self._response_active = True
                # 响应已创建：撤销救援并释放救援保持
                self._rescue_at = None
                self._rescue_hold = False
                # 半双工：记录响应开始时刻；从此刻起就静音麦克风（不能等 AI 出声，
                # 否则思考/工具调用空窗期内上传的尾音会被服务端当成新用户轮次而取消本轮）
                self._response_begin_ts = _t.monotonic()

            elif t in ("response.done", "response.audio.done"):
                # 响应结束。官方协议的权威事件是 response.done
                # （status=completed/cancelled/failed），response.audio.done
                # 作为历史兼容一并接受。此前只监听后者 → AI 说完第一句后
                # _ai_speaking 永久为 True，引发两个连锁故障：
                # ① 后续每轮 speech_started 都误判"有活动响应"并发 response.cancel
                # ② 麦克风增益被永久压低，用户语音变小
                resp_obj = event.get("response") or {}
                status = resp_obj.get("status") or event.get("status") or ""
                # 取消原因（官方 response.done 携带 status_details / reason，
                # 如客户端取消=client_cancelled）：定性"谁取消了这一轮"的
                # 第一手证据，直接打进警告与终端，不再只靠事件链反推
                cancel_reason = (
                    resp_obj.get("status_details")
                    or resp_obj.get("reason")
                    or event.get("status_details")
                    or event.get("reason")
                    or ""
                )
                if isinstance(cancel_reason, dict):
                    cancel_reason = json.dumps(
                        cancel_reason, ensure_ascii=False
                    ) if cancel_reason else ""
                if status == "cancelled":
                    self._events.note_cancelled()
                    reason_text = f"（原因: {cancel_reason}）" if cancel_reason else ""
                    ui.info(f"⚠️ 回复被打断取消{reason_text}（如反复出现，"
                            "多为回声残留或会话状态异常，请降低音量或检查扬声器外放）")
                    hint = self._events.cancel_hint()
                    if hint:
                        ui.info(f"   事件链回溯（新→旧）: {hint}")
                    # 终端的链会随会话滚走，同步落盘一份（不依赖 event_log 开关）
                    self._events.dump_cancel_diag()
                # 工具轮：output 全部是 function_call（一轮响应也可能同时含普通
                # 消息与函数调用，故必须"全为"才判定为工具轮）
                output = resp_obj.get("output") or []
                is_tool_turn = bool(output) and all(
                    isinstance(it, dict) and it.get("type") == "function_call"
                    for it in output
                )
                self._end_response(ui, is_tool_turn=is_tool_turn)
                # turn_detected 取消的救援（见 _response_rescue）：服务端检测到
                # "新轮次"后可能再无下文（尾音被判无效），已提交的用户问题悬在
                # 上下文里无人应答——1.2s 后由救援协程补发 response.create。
                # 用户真在说话时（服务端 speech_started 已到）不救援，避免与
                # 轮次冲突（response.create 在轮内被服务端拒绝，属良性 error）。
                # 注意：必须在 _end_response 之后武装——收尾会统一撤销待执行救援。
                if (
                    status == "cancelled"
                    and "turn_detected" in cancel_reason
                    and not self._user_speaking_server
                ):
                    self._rescue_at = _t.monotonic() + RESCUE_AFTER_CANCEL_SECONDS
                    self._rescue_armed_at = _t.monotonic()

            elif t == "input_audio_buffer.speech_started":
                # 服务端确认用户正在说话：更新权威状态并撤掉待执行的响应救援
                # （用户真在说话时绝不补发 response.create，避免与轮次冲突）
                self._user_speaking_server = True
                self._rescue_at = None
                self._rescue_hold = False
                # 半双工：响应期间麦克风本就静音，不存在“合法的插话打断”。此时到达
                # 的 speech_started 是服务端对管道中已收音频的延迟判定（VAD 会提前
                # 判停启动响应，尾巴音频稍后才被处理到），直接忽略。
                # 半双工下用户的新提问发生在响应结束之后，走下方正常流程。
                if self._half_duplex and had_active_response:
                    self._events.note_echo_guard()
                    continue
                # 回声保护：AI 正在说话、但衰减后麦克风实时电平仍很低，
                # 说明本次 speech_started 大概率是扬声器回声被服务端 VAD 误触发
                # （而非用户真实插话），忽略它、不取消回复，避免 AI 被自己
                # 的回声打断。用户真实插话需说够响使电平超过门限才能打断。
                if self._ai_speaking and self._last_mic_rms < ECHO_GUARD_RMS:
                    self._events.note_echo_guard()
                    continue
                # 用户开始说话（全双工模式）：服务端会自动取消当前响应
                # （官方文档：模型播报期间检测到用户开始说话，服务端返回
                # response.done[cancelled）——客户端只需立即清掉本地播放缓冲，
                # 不必再发 response.cancel（避免 "no active response" 报错噪音）。
                # had_active_response 已在事件分发前（状态未清时）记下。
                self._response_gen += 1  # 递增代数，丢弃残余音频
                self._ai_speaking = False
                self._response_start_ts = None
                ui.on_ai_speaking(False)
                ui.on_user_speaking(True)
                ui.on_status("listening")
                # 清空扬声器缓冲区
                if self._spk:
                    try:
                        self._spk.stop_stream()
                        self._spk.start_stream()
                    except Exception:
                        pass

            elif t == "input_audio_buffer.speech_stopped":
                ui.on_user_speaking(False)
                self._user_speaking_server = False
                if not self._ai_speaking:
                    ui.on_status("standby")
                # smart_turn：已判定有效的语音可能被服务端撤回
                # （reason=turn_invalid），此时不触发推理 —— 这是"说了话
                # 却没有回复"的一条路径，显式提示避免误判为"没听到"
                if event.get("reason") == "turn_invalid":
                    ui.info("🔇 本轮语音被服务端撤回（turn_invalid），未触发回复，请继续说")
                # smart_turn 轮末静音（server_vad 不需要）：语义判停偏早，判停到
                # response.created 之间直播的用户尾音会被服务端重新检出为新轮次
                # （response.done[cancelled, reason=turn_detected]），在 ~100ms 内
                # 掐死刚创建的响应。server_vad 要求真实静音才判停，天然免疫。
                # 用户若真在继续说话（RMS 超门限），voice_gap 立即清窗口，不吞话。
                if (
                    self._half_duplex
                    and not self._response_active
                    and self._turn_detection == "smart_turn"
                ):
                    self._mic_resume_at = _t.monotonic() + MIC_TURN_END_MUTE_SECONDS

            elif t == "conversation.item.input_audio_transcription.completed":
                # 转写完成即代表这段用户语音已说完，复位“正在说话”指示
                # （实机反馈：“我都说完话了，麦克风依然一直在检测我”）。
                ui.on_user_speaking(False)
                transcript = event.get("transcript", "")
                stripped = transcript.strip()
                if stripped:
                    self._events.note_user_turn()
                    # 统一交给 UI 协议中的 on_user_transcript 显示，
                    # 不再额外调用 info()，避免 Webview 实现中气泡重复。
                    ui.on_user_transcript(transcript)
                    # 轮次已提交：若 1.8s 内服务端未创建响应，由 _response_rescue
                    # 补发 response.create（正常流会被 response.created 提前撤掉
                    # _rescue_at，此机制零干扰）。
                    # phantom 抑制：单字/纯标点轮（尾音、环境音被 VAD 误开的
                    # 空轮）与回声轮（转写与 AI 刚播内容重合）不武装——服务端
                    # 本就不应答它们，补发只会产生"重复回答"（实机 17:18 会话）。
                    is_echo = bool(self._last_ai_transcript) and (
                        stripped in self._last_ai_transcript
                        or self._last_ai_transcript in stripped
                    )
                    if len(stripped) >= 2 and not is_echo:
                        self._rescue_at = _t.monotonic() + RESCUE_AFTER_COMMIT_SECONDS
                        self._rescue_armed_at = _t.monotonic()

            elif t in (AMBIENT_DELTA, AMBIENT_COMPLETED):
                # 环境音转写（仅 smart_turn）：服务端检测到语音活动、但语义判定为非
                # 有效轮次（噪声、"嗯""啊"、与 AI 刚说内容高度重合的回声）时不触发
                # 对话轮，只经这条通道透传，且不写入对话上下文。此前未订阅该事件 →
                # 用户说了话、服务端也听到了，屏幕上却一个字都不显示，表现为
                # "一直在说、它不回复"。现显式显示，使"话去了哪条路"可判定。
                ambient_text = self._events.feed_ambient(t, event)
                if ambient_text:
                    # 服务端已判为非有效轮次 → 不会有响应到来，顺手清掉可能残留的
                    # 静音窗口，让用户立刻能被重新拾取
                    self._mic_resume_at = None
                    ui.info(f"🔇 未入对话轮（服务端判为非有效语音）: {ambient_text}")

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
                    self._events.note_ai_turn()
                    # 统一交给 UI 协议中的 on_ai_transcript 显示。
                    ui.on_ai_transcript(transcript)
                    # 记录 AI 最近播读内容：供 transcription.completed 判定
                    # 回声轮（外放被麦克风重新拾取、服务端误转写为用户语音）
                    self._last_ai_transcript = transcript.strip()

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

                    # 执行工具：内置工具优先，其次查 registry 工具
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
                    self._events.note_client("response.create", f"tool={name}")

            elif t == "error":
                err = event.get("error", {})
                message = err.get("message", str(event))
                # 良性错误 ①：无活动响应时收到 response.cancel（打断与回复完成
                # 竞态偶发），忽略不提示。
                if "no active response" in message.lower():
                    continue
                # 良性错误 ②：response.create 与服务器自动响应竞态（救援触发时
                # 服务器的自动响应已在创建中）——响应确实存在，忽略不提示。
                if "in progress" in message.lower():
                    self._rescue_at = None
                    self._rescue_hold = False
                    continue
                ui.warn(f"\n⚠ {message}")
                ui.on_status("error")

    # ------------------------------------------------------------------
    # 静音策略与响应收尾
    # ------------------------------------------------------------------

    def _end_response(self, ui: Any, *, is_tool_turn: bool = False) -> None:
        """一次服务端响应结束时的统一复位。

        由 response.done / response.audio.done 触发，保证活动响应标志、说话态、
        超时计时器与 UI 状态一次清干净，避免残留状态污染下一轮。

        @author aceFelix
        """
        self._response_active = False
        self._ai_speaking = False
        self._rescue_hold = False
        self._response_start_ts = None  # 重置防御性超时计时器
        self._response_begin_ts = None  # 响应已结束，半双工悬挂计时清零
        # 救援 v2 闸门①：任何响应完成都意味着"已提交的用户轮次得到了应答"
        # （哪怕是 cancelled——被取消的轮由 turn_detected 分支单独决定是否
        # 重新武装），刚完成的回答覆盖此前一切 pending 轮 → 撤销待执行救援，
        # 并记录完成时刻供救援协程做"距上次应答 ≥1s"判定（防 phantom 轮
        # 在回答刚结束时触发重复补答，实机 17:18 会话的"重复回答"成因）。
        self._rescue_at = None
        self._last_response_done_ts = _t.monotonic()
        # 半双工：说完后留一段回声尾迹静默期，期间仍不上传麦克风，待扬声器
        # 余音/混响散尽再恢复拾取，避免服务端把尾迹当成用户说话
        if self._half_duplex:
            # 工具轮之后还有一轮基于工具结果的最终回复，工具执行 + 二次推理通常
            # 1~3s：此时若按普通尾迹恢复上传，环境声会被当成新用户语音并取消
            # 最终回复（表现为"问时间这类要调工具的提问没反应"）。故工具轮改用
            # 静音上限兜底，最终回复的 response.done 会用回声尾迹覆盖它。
            gap = MIC_MUTE_MAX_SECONDS if is_tool_turn else MIC_TAIL_SECONDS
            self._mic_resume_at = _t.monotonic() + gap
        ui.on_ai_speaking(False)
        ui.on_status("standby")
        # 优雅停止：告别语音播放完毕，立即结束会话
        if self._graceful_stop_at is not None:
            self._running = False
            ui.info("🛑 end_conversation 优雅停止触发，会话即将结束")

    def _mic_muted(self) -> bool:
        """半双工下是否暂停上传麦克风（响应进行中 + 说完后的回声尾迹窗口）。

        响应期含模型思考、Function Calling 工具执行的空窗期；判定规则与悬挂保护
        详见 realtime_audio.mic_muted。全双工（耳机）恒为 False。

        @author aceFelix
        """
        muted, self._mic_resume_at = mic_muted(
            self._half_duplex,
            self._response_active,
            self._ai_speaking,
            self._response_begin_ts,
            self._mic_resume_at,
            _t.monotonic(),
        )
        # 静音窗口进出各记一条时间线：与 speech_started 的时刻对照，即可判定
        # 服务端"打断"时麦克风到底有没有在上传音频
        if muted != self._mic_mute_logged:
            self._mic_mute_logged = muted
            self._events.note_client("mic", "静音" if muted else "恢复上传")
        return muted

    def _should_attenuate(self) -> bool:
        """AI 说话期间是否二次压低麦克风增益（仅供全双工，详见 should_attenuate）。"""
        return should_attenuate(
            self._ai_speaking, self._aec is not None, self._echo_suppress_with_aec
        )

    def _request_graceful_stop(self) -> None:
        """end_conversation：设置优雅停止宽限期，等告别语音播完再退出。

        由工具层通过回调触发（工具层不直接持有会话状态）。
        """
        self._graceful_stop_at = _t.monotonic() + 4.0  # 4秒宽限期

    async def _execute_tool(self, name: str, args: dict[str, Any], ui: Any) -> str:
        """执行 Function Calling 工具（实现见 realtime_tools.execute_tool）。

        本方法只负责把会话状态（工具映射、工作目录、优雅停止回调）注入工具层，
        执行分派与安全策略全部在 realtime_tools.py。

        @author aceFelix
        """
        return await execute_tool(
            name,
            args,
            registry_tool_map=self._registry_tool_map,
            workdir=self._workdir,
            ui=ui,
            on_end_conversation=self._request_graceful_stop,
            tool_alias_map=self._tool_alias_map,
        )

    # ------------------------------------------------------------------
    # 响应救援与看门狗
    # ------------------------------------------------------------------

    async def _response_rescue(self, ws: Any, ui: Any) -> None:
        """响应救援：已提交的用户轮次迟迟无响应时，补发 response.create 兜底。

        触发点（_rescue_at 由两处设置）：turn_detected 取消后服务端不再补答
        （+1.2s）、轮次提交后无 response.created（+1.8s）。发射条件：无活动
        响应、服务端视角用户未在说话、距上次救援 ≥2s（防风暴）。发射时立即
        重置静音窗口并置 _rescue_hold——补发后到响应创建前的窗口内，用户任何
        声响都会被服务端当成新轮次掐死响应。被服务端拒绝（轮内禁止
        response.create）只是良性 error 事件，连接不受影响。

        @author aceFelix
        """
        try:
            while self._running:
                await asyncio.sleep(0.2)
                if not self._rescue or self._rescue_at is None:
                    continue
                now = _t.monotonic()
                if (
                    now >= self._rescue_at
                    and not self._response_active
                    and not self._user_speaking_server
                    and now - self._last_rescue_ts >= RESCUE_MIN_INTERVAL_SECONDS
                    # 闸门①：刚应答完（<1s）不补发——已完成的回答覆盖此前
                    # 所有已提交轮次，此时到期的一律视为 phantom 轮的残留武装
                    and now - self._last_response_done_ts >= 1.0
                ):
                    self._rescue_at = None
                    self._last_rescue_ts = now
                    self._rescue_hold = True
                    self._mic_resume_at = now + RESCUE_HOLD_SECONDS
                    self._events.note_client("response.create", "rescue")
                    try:
                        from agent.core import diag
                        diag.diag_log(
                            "realtime",
                            f"rescue fired, armed {(now - self._rescue_armed_at):.1f}s ago",
                        )
                        await ws.send(json.dumps({
                            "type": "response.create",
                            "response": {"modalities": ["audio", "text"]},
                        }))
                        ui.info("🛟 响应救援：已提交的用户问题未获应答，补发 response.create")
                    except Exception:
                        self._rescue_hold = False
        except Exception:
            pass

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
