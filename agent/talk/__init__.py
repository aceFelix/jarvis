"""/talk 实时全双工语音链路。

职责：DashScope Realtime WebSocket 会话、终端/桌面音频适配、AEC、打断与音频闸门、
Function Calling 与响应救援、转写落库。

分层约束（见 docs/plans/voice/talk-voice-layering.md）：
- 与 agent.voice 互不 import；共用音频能力从 agent.audio 取；
- 包内相对导入保持原样，宿主（commands / serve）负责装配与生命周期。

@author aceFelix
@since 2026-11-26
"""

__all__ = []
