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
3. **工具表必须瘦身**：响应创建延迟随工具数近似线性增长（探针实测：2 个 0.67s
   → 294 个 3.45s → 450 个 4.89s；服务端始终会建响应，不是拒绝）。默认只注册
   2 个内置工具；tools_mode="all" 才全量。全量时该延迟与响应救援、server_vad
   尾音重检叠加会造成响应被取消的死循环。工具调用场景请走半双工 /voice。
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
    ECHO_SUPPRESS_FACTOR,
    _attenuate_pcm,
    _rms,
    mic_muted,
    silence_like,
    voice_gap,
    should_attenuate,
)
from .realtime_events import TalkEventLog
from .realtime_tools import (
    execute_tool,
    sanitize_tools_for_realtime,
)
# 音频参数与时序常量：单一来源在 realtime_const.py（本模块与拆出的
# realtime_recv / realtime_rescue 共用同一份阈值，不各写一份）
from .realtime_const import (
    CHUNK_BYTES,
    RESCUE_HOLD_SECONDS,
    SEND_INTERVAL,
    VOLUME_REPORT_INTERVAL,
)

from .realtime_recv import RealtimeRecvMixin
from .realtime_rescue import RealtimeRescueMixin

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

# ---- 音频参数与时序常量 ----
# 数值本身住在 realtime_const.py（见文件顶部的 import）。
# 移动原因：协议接收循环与响应救援拆成独立模块后，三处共用同一份阈值，
# 分开各写一份迟早漂移。


class RealtimeEngine(RealtimeRecvMixin, RealtimeRescueMixin):
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

