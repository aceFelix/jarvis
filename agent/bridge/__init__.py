"""Bridge 模块 —— 跨设备协同（手机 PWA ↔ 电脑端 JARVIS）。

提供 BridgeServer（HTTP + WebSocket 服务）和 BridgeUI（UIProtocol 的 WS 实现），
让手机通过 PWA 连接到电脑端 JARVIS，共享 daemon 的对话历史，实现"手机继续电脑上的对话"。

架构概览::

    ┌─────────────┐   HTTP(8765): 静态文件 + /api/config + /upload
    │  手机 PWA   │ ───────────────────────────────────────────────┐
    │ (index.html)│                                                  │
    └─────────────┘   WebSocket(8766): 双向通信（token 认证）        │
         │  ↑                                                            │
         ↓  │  event: assistant_text / tool_use / tool_result / done    │
    ┌─────────────────────────────────────────────────────────────────┐ │
    │                      BridgeServer (电脑端)                       │ │
    │  ┌──────────────┐   ┌──────────────┐   ┌─────────────────────┐  │ │
    │  │ ThreadingHTTP│   │ websockets   │   │ asyncio.Lock        │  │ │
    │  │ Server(线程) │   │ serve(loop)  │───┤ 并发控制: 单 query   │  │ │
    │  └──────────────┘   └──────────────┘   └─────────────────────┘  │ │
    │                                                │                │ │
    │                共享 messages ┌──────────────────┘                │ │
    │                              ▼                                    │ │
    │  ┌──────────────┐   ┌──────────────────┐   ┌─────────────────┐  │ │
    │  │  BridgeUI    │←──│  ToolContext     │←──│  QueryLoop      │  │ │
    │  │ (UIProtocol) │   │  (plan 权限)     │   │  (daemon 共享)  │  │ │
    │  └──────────────┘   └──────────────────┘   └─────────────────┘  │ │
    └─────────────────────────────────────────────────────────────────┘ │
         ↑                                                              │
         └──────────────────────────────────────────────────────────────┘

@author aceFelix
"""

from agent.bridge.server import BridgeServer, get_bridge_server, start_bridge_in_thread, stop_bridge
from agent.bridge.ui import BridgeUI, BroadcastUI

__all__ = [
    "BridgeServer",
    "BridgeUI",
    "BroadcastUI",
    "get_bridge_server",
    "start_bridge_in_thread",
    "stop_bridge",
]
