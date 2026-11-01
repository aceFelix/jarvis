"""对话引擎层 —— ``jarvis --serve`` 与 jarvis-desktop 共用，不含任何界面代码。

本包是纯逻辑：对话引擎（``engine.ChatEngine``）、指令/事件 API 门面
（``api.WorkbenchAPI``）、系统指标采集（``metrics``）、模型管理与扩展
面板接口（``model_admin`` / ``extensions_api`` / ``slash_bridge`` /
``checkpoint_ops``）等。``jarvis --serve`` 拉起的 headless 服务与
jarvis-desktop 桌面壳都装配这套零件，界面渲染全在宿主侧。

**不渲染任何 UI**：本包只生产事件、消费指令，宿主是谁（WebSocket 客户端 /
Electron 窗口）与本包无关。类名里的 ``Workbench`` 是历史遗留——``--serve``
的 WS 协议契约名，jarvis-desktop 的 ``contracts.ts`` 按此镜像，故保留未改。

位置沿革（2026-11，aceFelix）：本包原在 ``agent/ui/workbench/``。三栏
pywebview 窗口壳（``app.py`` / ``single_instance.py`` / ``win32_icon.py`` /
``window_geometry.py`` 与 ``assets/``）已于 2026-10 删除，入口参数
``jarvis --gui`` / ``--talk`` 已于 2026-11 删除；把纯引擎留在终端 UI 层目录下
只剩误导，故整体迁入 ``agent/serve/`` 并改名 ``engine``。判断依据与影响面见
``docs/fixlogs/workbench-gui-retired.md``。
"""

from __future__ import annotations

__all__: list[str] = []
