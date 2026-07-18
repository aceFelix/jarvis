"""实时双工语音对话的独立窗口 UI。

基于 pywebview + HTML5 Canvas，提供方舟反应炉粒子动画背景与聊天气泡。

@author aceFelix
"""

from __future__ import annotations

from agent.ui.realtime_window.bridge import WebviewRealtimeTalkUI
from agent.ui.realtime_window.process import JSBridge
from agent.ui.realtime_window.window import RealtimeTalkWindow

__all__ = ["RealtimeTalkWindow", "JSBridge", "WebviewRealtimeTalkUI"]
