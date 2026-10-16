"""贾维斯桌面入口辅助模块（作者：aceFelix）。

原“无窗口 daemon + 托盘遥控”架构已下线；开机自启（``autostart``）也已于
2026-10 随 pywebview 三栏工作台窗口一并退役——开机自启改由
jarvis-desktop 桌面应用自身提供（退役背景见
``docs/fixlogs/workbench-gui-retired.md``）。本包现保留：

- ``hotkey`` / ``hotkey_native``: 全局热键监听（待桌面接线）
- ``platform_utils``: 平台判断与可选依赖检测纯函数

主动感知后台服务（提醒/简报/监控等）位于 ``agent.core.daemon``，由
``jarvis --serve`` 的 ProactiveHub 与终端的 CliReminderHost 装配。热键
召唤窗口的能力原服务于已退役的 GUI 窗口，现仅供桌面壳后续接线。
"""
