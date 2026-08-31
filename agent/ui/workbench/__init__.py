"""JARVIS 三栏工作台（Workbench）—— 桌面图标的新宿主。

单窗口三栏 GUI：
- 左栏：模式切换（文本/实时）、模型与音色选择、历史会话列表（双面板切换）
- 中栏：对话主区（气泡消息流 + 文本输入框）
- 右栏：系统指标（CPU/内存/磁盘）

基于 pywebview + HTML5 Canvas，主进程主线程跑窗口，
对话引擎/实时语音/指标采集跑在工作线程，通过事件队列与前端通信。

窗口行为：最大化（透明背景）、最小化到任务栏、单实例（二次双击聚焦）。

@author aceFelix
"""

from __future__ import annotations

from agent.ui.workbench.app import run_workbench

__all__ = ["run_workbench"]
