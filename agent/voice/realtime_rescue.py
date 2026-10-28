"""实时语音的响应收尾、响应救援与看门狗（从 realtime_engine.py 拆出）。

三件事：
- ``_end_response``：一轮响应结束时把状态归位并通知 UI；
- ``_response_rescue``：turn_detected 取消后服务端不再补答、或轮次提交后服务端未自动
  创建响应时，按 RESCUE_* 阈值补发 response.create，并在补发后强制静音保持
  （否则用户任何声响都会被当成新轮次把响应掐死）；
- ``_watchdog``：兜底看门狗。

阈值数值统一来自 realtime_const.py（单一来源，不与 realtime_engine 各写一份）。
方法体逐行原样搬迁，行为与拆分前完全一致。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
import time as _t
from typing import Any
from .realtime_audio import (
    MIC_MUTE_MAX_SECONDS,
    MIC_TAIL_SECONDS,
)

from .realtime_const import (
    RESCUE_HOLD_SECONDS,
    RESCUE_MIN_INTERVAL_SECONDS,
)


class RealtimeRescueMixin:
    """响应收尾 / 救援 / 看门狗的方法组（原样搬自 RealtimeEngine，行为不变）。"""

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
