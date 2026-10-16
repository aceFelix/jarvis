"""JARVIS 工作台引擎层（Workbench）—— serve 与桌面壳共用的后端零件。

本包内容：对话引擎（``engine.ChatEngine``）、指令/事件 API 门面
（``api.WorkbenchAPI``）、系统指标采集（``metrics``）、模型管理与扩展
面板接口（``model_admin`` / ``extensions_api`` / ``slash_bridge`` /
``checkpoint_ops``）等。``jarvis --serve`` 拉起的 headless 服务与
jarvis-desktop 桌面壳都装配这套零件，宿主只是从窗口换成 WebSocket。

退役说明（2026-10，aceFelix）：本包原有的 pywebview 三栏 GUI 窗口壳
（``app.py`` / ``single_instance.py`` / ``win32_icon.py`` /
``window_geometry.py`` 与 ``assets/`` 前端资源）已整体删除，入口参数
``jarvis --gui`` / ``--talk`` 同步下线——图形化桌面由独立仓库
**jarvis-desktop**（Electron）承担，两套 GUI 并存属重复建设。
判断依据与影响面见
``docs/fixlogs/workbench-gui-retired.md``。

不再导出 ``run_workbench``；如需启动后端请用 ``jarvis --serve``。
"""

from __future__ import annotations

__all__: list[str] = []
