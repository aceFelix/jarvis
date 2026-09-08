"""serve 协议契约测试。

覆盖：
- build_handshake 握手 JSON 结构（Electron 主进程解析契约）
- build_reply 回执信封（ok / 失败两路）
- DESKTOP_COMMANDS 与 DesktopBridgeServer 处理器表一一对应（防漏注册）

@author aceFelix
"""

from __future__ import annotations

import queue

from agent.config.settings import Settings
from agent.serve import protocol
from agent.serve.server import DesktopBridgeServer
from agent.ui.workbench.api import WorkbenchAPI
from agent.ui.workbench.engine import ChatEngine


# ---- 握手 JSON ----

def test_build_handshake_shape():
    """握手 JSON 字段齐全，type 为约定标记（Electron 据此识别就绪行）。"""
    msg = protocol.build_handshake(51000, 51001, "abc123", 4242)
    assert msg["type"] == protocol.SERVE_READY_MARKER
    assert msg["port"] == 51000
    assert msg["http_port"] == 51001
    assert msg["token"] == "abc123"
    assert msg["pid"] == 4242


def test_handshake_marker_value():
    """标记值本身是稳定契约，改名会破坏已发布的桌面壳。"""
    assert protocol.SERVE_READY_MARKER == "jarvis-serve-ready"


# ---- 回执信封 ----

def test_build_reply_ok():
    """成功回执带 result，不带 error。"""
    msg = protocol.build_reply("models.list", ok=True, result=[{"name": "m1"}])
    assert msg["event"] == protocol.EVT_REPLY
    assert msg["data"] == {
        "type": "models.list", "ok": True, "result": [{"name": "m1"}],
    }


def test_build_reply_error():
    """失败回执带 error，不带 result；空 error 有兜底文案。"""
    msg = protocol.build_reply("sessions.open", ok=False, error="缺少会话名 name")
    assert msg["data"]["ok"] is False
    assert msg["data"]["error"] == "缺少会话名 name"
    assert "result" not in msg["data"]
    fallback = protocol.build_reply("x", ok=False)
    assert fallback["data"]["error"] == "未知错误"


# ---- 指令注册完整性 ----

def _make_server() -> DesktopBridgeServer:
    settings = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)
    return DesktopBridgeServer(api, settings)


def test_all_desktop_commands_registered():
    """protocol 声明的每个桌面指令都在 WS 处理器表中注册（防漏）。"""
    server = _make_server()
    for cmd in protocol.DESKTOP_COMMANDS:
        assert cmd in server._ws_handlers, f"指令未注册: {cmd}"


def test_message_handler_overridden():
    """桌面模式 message 指令覆盖内置手机 PWA 路由（改道引擎队列）。"""
    server = _make_server()
    handler = server._ws_handlers["message"]
    assert handler == server._cmd_message  # bound method 相等即同一实现
    assert handler.__name__ == "_cmd_message"
