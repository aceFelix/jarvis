"""tests/serve 共用装配助手 —— 造一台真实的 DesktopBridgeServer。

为什么单独成文件（aceFelix）：`DesktopBridgeServer` 要跑起来必须同时装配
Settings / ChatEngine / WorkbenchAPI 三件（它的指令全挂在 api 上）。协议契约测试
和感知 RPC 测试都要造它，把这段构造抄两份就会漂移（例如新增 hub 参数时只改一处）。
这里只做构造，不做断言，隔离仍由各目录的 conftest 夹具负责。

@author aceFelix
"""

from __future__ import annotations

import queue
from typing import Any

from agent.config.settings import Settings
from agent.serve.server import DesktopBridgeServer
from agent.ui.workbench.api import WorkbenchAPI
from agent.ui.workbench.engine import ChatEngine


def make_desktop_server(
    settings: Settings | None = None,
    hub: Any | None = None,
) -> tuple[DesktopBridgeServer, queue.Queue, queue.Queue]:
    """装配桌面服务器，返回 ``(server, event_queue, command_queue)``。

    Args:
        settings: 配置实例；None 用裸 Settings（各域字段都是出厂缺省）。
        hub: ProactiveHub 替身或真实 hub；None 表示宿主未装配（RPC 需能降级）。

    Returns:
        三元组。事件队列同时给了调用方，便于直接断言引擎侧投递出的事件。
    """
    cfg = settings or Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(cfg, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, cfg)
    return DesktopBridgeServer(api, cfg, hub=hub), event_queue, command_queue
