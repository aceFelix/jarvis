"""贾维斯桌面入口辅助模块（作者：aceFelix）。

原"无窗口 daemon + 托盘遥控"架构已下线（新 GUI 工作台取代），
本包仅保留与常驻外壳无关的桌面入口工具：

- ``autostart``: 开机自启 + 桌面快捷方式（.lnk/.command/.desktop）
- ``hotkey`` / ``hotkey_native``: 全局热键监听（新 GUI 阶段用于召唤窗口）
- ``platform_utils``: 平台判断与可选依赖检测纯函数

主动感知后台服务（提醒/简报/监控等）位于 ``agent.core.daemon``，
将在新 GUI 阶段由窗口进程接管。
"""
