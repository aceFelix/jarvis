"""DesktopBridgeServer WS 指令路由测试（不起真实端口，处理器直调）。

覆盖：
- message：入引擎队列 + ok 回执；空文本 → 失败回执
- request/response 型指令：sessions/models/voices/metrics/state 正常路径
- 参数校验：sessions.open / models.select / voices.select 缺 name → 失败回执
- 事件泵：引擎事件 → broadcast 信封映射；stop 幂等

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
import queue
import time

from agent.config.settings import Settings
from agent.serve.server import DesktopBridgeServer
from agent.ui.workbench.api import WorkbenchAPI
from agent.ui.workbench.engine import ChatEngine


class _FakeWS:
    """记录 send 内容的假 WS 客户端（处理器只依赖 ws.send）。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


def _make() -> tuple[DesktopBridgeServer, WorkbenchAPI, queue.Queue, queue.Queue]:
    settings = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)
    server = DesktopBridgeServer(api, settings)
    return server, api, event_queue, command_queue


def _call(server: DesktopBridgeServer, cmd: str, data: dict) -> dict:
    """直调处理器表中的指令，返回唯一一条回执。"""
    ws = _FakeWS()
    payload = {"type": cmd, **data}
    asyncio.run(server._ws_handlers[cmd](ws, payload))
    assert len(ws.sent) == 1
    return ws.sent[0]


# ---- message 指令 ----

def test_message_enqueues_and_replies_ok():
    """message 文本入引擎队列，回执仅确认入队（结果走事件泵）。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "message", {"text": "你好贾维斯"})
    assert reply["event"] == "reply"
    assert reply["data"] == {"type": "message", "ok": True, "result": None}
    cmd = command_queue.get_nowait()
    assert cmd["cmd"] == "send"
    assert cmd["text"] == "你好贾维斯"


def test_message_empty_text_rejected():
    """空白文本不入队，回执 ok=false。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "message", {"text": "   "})
    assert reply["data"]["ok"] is False
    assert reply["data"]["error"] == "空消息"
    assert command_queue.empty()


# ---- request/response 型指令 ----

def test_sessions_list_reply():
    """sessions.list 返回列表（mock 环境下可为空，但必须是 list 回执）。"""
    server, _, _, _ = _make()
    reply = _call(server, "sessions.list", {})
    assert reply["data"]["ok"] is True
    assert isinstance(reply["data"]["result"], list)


def test_sessions_open_requires_name():
    """sessions.open 缺 name → 失败回执；有 name → 指令入队。"""
    server, _, _, command_queue = _make()
    bad = _call(server, "sessions.open", {})
    assert bad["data"]["ok"] is False
    assert "name" in bad["data"]["error"]
    good = _call(server, "sessions.open", {"name": "s1"})
    assert good["data"]["ok"] is True
    assert command_queue.get_nowait()["cmd"] == "load_session"


def test_models_list_and_select():
    """models.list 返回带 current 标记的列表；select 缺 name 被拒。"""
    server, _, _, _ = _make()
    listing = _call(server, "models.list", {})
    assert listing["data"]["ok"] is True
    assert isinstance(listing["data"]["result"], list)
    bad = _call(server, "models.select", {})
    assert bad["data"]["ok"] is False
    assert "name" in bad["data"]["error"]


def test_voices_list_and_select():
    """voices.list 返回列表；voices.select 缺 name 被拒。"""
    server, _, _, _ = _make()
    listing = _call(server, "voices.list", {})
    assert listing["data"]["ok"] is True
    assert isinstance(listing["data"]["result"], list)
    bad = _call(server, "voices.select", {})
    assert bad["data"]["ok"] is False


def test_metrics_get_reply_shape():
    """metrics.get 返回 cpu/memory/disk 三键（与右栏指标面板同构）。"""
    server, _, _, _ = _make()
    reply = _call(server, "metrics.get", {})
    assert reply["data"]["ok"] is True
    result = reply["data"]["result"]
    assert {"cpu", "memory", "disk"} <= set(result.keys())


def test_state_get_reply():
    """state.get 返回引擎状态 dict（init 事件同构 payload）。"""
    server, _, _, _ = _make()
    reply = _call(server, "state.get", {})
    assert reply["data"]["ok"] is True
    assert isinstance(reply["data"]["result"], dict)


def test_answer_user_enqueues():
    """answer_user 把回填文本入引擎队列（ask_user 弹窗闭环）。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "answer_user", {"text": "同意"})
    assert reply["data"]["ok"] is True
    cmd = command_queue.get_nowait()
    assert cmd["cmd"] == "answer_user"
    assert cmd["text"] == "同意"


def test_unknown_command_not_registered():
    """未注册指令不在处理器表中（reader 主循环会静默忽略）。"""
    server, _, _, _ = _make()
    assert "not.a.command" not in server._ws_handlers


# ---- 事件泵 ----

def test_event_pump_broadcasts_and_stops():
    """事件泵消费引擎队列并 broadcast；stop 幂等。"""
    server, _, event_queue, _ = _make()
    received: list[tuple[str, object]] = []
    server.broadcast = lambda event, data: received.append((event, data))  # type: ignore[method-assign]
    server.start_event_pump(event_queue)
    event_queue.put_nowait({"type": "assistant_text", "payload": "你好"})
    event_queue.put_nowait({"type": "metrics", "payload": {"cpu": 1}})
    event_queue.put_nowait("非 dict 项应被跳过")
    deadline = time.time() + 2
    while len(received) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert received == [("assistant_text", "你好"), ("metrics", {"cpu": 1})]
    server.stop_event_pump()
    server.stop_event_pump()  # 幂等：二次调用不抛异常
    assert server._pump_thread is None
