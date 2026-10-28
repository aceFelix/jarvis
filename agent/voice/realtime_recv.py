"""实时语音协议接收循环 —— 服务端事件的分发与状态推进（从 realtime_engine.py 拆出）。

职责单一：把 DashScope Realtime WebSocket 收到的事件流翻译成引擎状态与 UI 事件
（transcript / 音频增量 / turn_detected / response.done / 错误与取消）。

拆分动机是控制 realtime_engine.py 的规模（原 917 行，单是 _recv_events 一个方法就占
312 行，读改都要在同一个巨类里翻）。方法体逐行原样搬迁，未改任何判断分支、阈值或
事件顺序，行为与拆分前完全一致。

以 mixin 形式挂回 RealtimeEngine（``class RealtimeEngine(RealtimeRecvMixin, ...)``），
self 上的状态字段与其余方法照旧可用，调用方（realtime_talk / 桌面全双工桥接）无需改动。

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
    MIC_TURN_END_MUTE_SECONDS,
)
from .realtime_events import AMBIENT_COMPLETED, AMBIENT_DELTA

from .realtime_const import (
    MAX_RESPONSE_SECONDS,
    RESCUE_AFTER_CANCEL_SECONDS,
    RESCUE_AFTER_COMMIT_SECONDS,
)


class RealtimeRecvMixin:
    """服务端事件接收与分发的方法组（原样搬自 RealtimeEngine，行为不变）。"""

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
