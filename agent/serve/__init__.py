"""agent.serve —— headless API 服务模式（供 jarvis-desktop 等外部前端接入）。

与 ``agent.ui.workbench``（pywebview 本地窗口）并存：同一套引擎零件
（ChatEngine + WorkbenchAPI + MetricsCollector），宿主从 pywebview 换成
WebSocket 服务器（127.0.0.1 + 随机端口 + token 认证）。

对外入口：``run_serve(settings)``（``jarvis --serve`` 与
``python -m agent.serve`` 共用）。

@author aceFelix
"""

from __future__ import annotations

from agent.serve.app import run_serve

__all__ = ["run_serve"]
