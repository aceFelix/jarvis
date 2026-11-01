"""agent.serve —— headless API 服务模式（供 jarvis-desktop 等外部前端接入）。

本包分两层：``app/server/hub/protocol/rpc_*`` 是 WebSocket 宿主（127.0.0.1
+ 随机端口 + token 认证），``engine/`` 是被宿主编排的对话引擎层
（ChatEngine + WorkbenchAPI + MetricsCollector）。引擎层与原 pywebview 三栏
窗口壳完全相同，宿主从窗口换成 WebSocket 后一字未改。

对外入口：``run_serve(settings)``（``jarvis --serve`` 与
``python -m agent.serve`` 共用）。

@author aceFelix
"""

from __future__ import annotations

from agent.serve.app import run_serve

__all__ = ["run_serve"]
