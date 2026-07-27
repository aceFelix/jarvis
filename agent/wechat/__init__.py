"""WeChat 模块 —— 微信 ClawBot 接入（iLink Bot API）。

通过腾讯官方 iLink Bot API 将 JARVIS 接入微信 ClawBot：
- 扫码登录（/connect-wechat）
- 长轮询接收微信消息
- 调用 QueryLoop 处理（共享 REPL 对话历史）
- 回复发回微信

架构概览::

    ┌─────────────┐         ┌──────────────┐         ┌─────────────────────┐
    │  微信 APP   │ ←────→  │  腾讯云 iLink │ ←────→  │  agent/wechat/      │
    │ (ClawBot)   │         │  (消息中转)   │         │  ilink.py (HTTP)    │
    └─────────────┘         └──────────────┘         └─────────┬───────────┘
                                                               │
                                                     ┌─────────▼───────────┐
                                                     │  server.py          │
                                                     │  WeChatBridge       │
                                                     │  (独立线程+单例)    │
                                                     └─────────┬───────────┘
                                                               │
                                                     ┌─────────▼───────────┐
                                                     │  QueryLoop          │
                                                     │  (共享 messages)    │
                                                     └─────────────────────┘

@author aceFelix
"""

from agent.wechat.server import (
    WeChatBridge,
    get_wechat_bridge,
    start_wechat_in_thread,
    start_wechat_loop,
    stop_wechat,
)
from agent.wechat.ui import WeChatUI

__all__ = [
    "WeChatBridge",
    "WeChatUI",
    "get_wechat_bridge",
    "start_wechat_in_thread",
    "start_wechat_loop",
    "stop_wechat",
]
