"""DesktopBridgeServer 端到端集成测试（真实端口 + websockets 客户端）。

覆盖：
- 随机端口回填与 127.0.0.1 绑定收敛（不对局域网暴露）
- token 认证：错误/缺失 token 被拒（close 4401）
- 正确 token 连接后指令 → reply 回执全链路
- 事件泵 broadcast → WS 客户端实际收到事件信封

websockets 为可选依赖，缺失时整组跳过（与 bridge 同口径）。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
import queue

import pytest

from agent.bridge import server as bridge_server
from agent.config.settings import Settings
from agent.serve.server import DesktopBridgeServer
from agent.ui.workbench.api import WorkbenchAPI
from agent.ui.workbench.engine import ChatEngine

pytestmark = pytest.mark.skipif(
    bridge_server.websockets is None, reason="websockets 未安装"
)


def _make_server() -> tuple[DesktopBridgeServer, queue.Queue]:
    settings = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)
    server = DesktopBridgeServer(api, settings, token="test-token")
    return server, event_queue


async def _e2e() -> None:
    """一次起停内完成全部断言（端口/认证/指令/事件泵）。"""
    import websockets

    server, event_queue = _make_server()
    await server.start()
    try:
        # 随机端口已回填且非默认值
        assert server.ws_port > 0
        assert server.http_port > 0
        base = f"ws://127.0.0.1:{server.ws_port}/"

        # 1. 错误 token 被拒（close 4401）
        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc_info:
            async with websockets.connect(base + "?token=wrong") as ws:
                await ws.recv()
        assert exc_info.value.rcvd is not None
        assert exc_info.value.rcvd.code == 4401

        # 2. 缺 token 同样被拒
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            async with websockets.connect(base) as ws:
                await ws.recv()

        # 3. 正确 token：按连接首推 init 首帧 → 指令 → reply 回执
        async with websockets.connect(base + "?token=test-token") as ws:
            # 每连接首帧：init 事件（payload 同 state.get，桌面壳首屏/设置回填数据源）
            init_evt = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert init_evt["event"] == "init"
            assert "provider" in init_evt["data"]
            await ws.send(json.dumps({"type": "state.get"}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert reply["event"] == "reply"
            assert reply["data"]["type"] == "state.get"
            assert reply["data"]["ok"] is True
            assert "provider" in reply["data"]["result"]

            # 参数校验路径：sessions.open 缺 name → 失败回执（连接不中断）
            await ws.send(json.dumps({"type": "sessions.open"}))
            bad = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert bad["data"]["ok"] is False
            assert "name" in bad["data"]["error"]

            # 4. 事件泵：引擎事件 → broadcast → 客户端收到事件信封
            server.start_event_pump(event_queue)
            event_queue.put_nowait({"type": "info", "payload": "泵测试"})
            evt = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert evt == {"event": "info", "data": "泵测试"}
            server.stop_event_pump()
    finally:
        await server.stop()


def test_serve_ws_end_to_end():
    """起真实服务跑全链路（单事件循环内完成，避免跨 loop 资源清理问题）。"""
    asyncio.run(_e2e())


def test_serve_binds_loopback_only():
    """桌面模式绑定收敛为 127.0.0.1（区别于手机协同的 0.0.0.0）。"""
    server, _ = _make_server()
    assert server._host == "127.0.0.1"
