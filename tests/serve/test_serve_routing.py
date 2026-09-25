"""DesktopBridgeServer WS 指令路由测试（不起真实端口，处理器直调）。

覆盖：
- message：入引擎队列 + ok 回执；空文本 → 失败回执
- request/response 型指令：sessions/models/voices/metrics/state 正常路径
- schedule.list / cost.get：右栏任务中心与用量卡数据源（hub 缺失降级空列表）
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
from agent.core.daemon.deadline import Deadline
from agent.core.daemon.scheduler import ScheduleTask
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


def test_message_with_attachments_passthrough():
    """images/files 随 message 透传入引擎队列（桌面壳 📎 附件链路）。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "message", {
        "text": "看图",
        "images": [{"data": "QUJD", "media_type": "image/png"}],
        "files": [{"name": "a.md", "content": "# x"}],
    })
    assert reply["data"]["ok"] is True
    cmd = command_queue.get_nowait()
    assert cmd["images"] == [{"data": "QUJD", "media_type": "image/png"}]
    assert cmd["files"] == [{"name": "a.md", "content": "# x"}]


def test_message_images_only_ok():
    """纯图片（空文本）也入队：引擎会补最小指令文本。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "message", {"text": "", "images": [{"data": "QUJD"}]})
    assert reply["data"]["ok"] is True
    assert command_queue.get_nowait()["cmd"] == "send"


def test_message_too_many_images_rejected():
    """图片超 8 张：失败回执，不入队。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "message", {"text": "x", "images": [{"data": "QQ"}] * 9})
    assert reply["data"]["ok"] is False
    assert command_queue.empty()


def test_message_oversized_file_rejected():
    """单文件内容超 20 万字符：失败回执，不入队。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "message", {
        "text": "x", "files": [{"name": "b.txt", "content": "y" * 200_001}]
    })
    assert reply["data"]["ok"] is False
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


def test_state_get_includes_mcp_key():
    """state.get 带 mcp 键（右栏运行健康数据源；引擎未装配 MCP 时为 None）。"""
    server, _, _, _ = _make()
    reply = _call(server, "state.get", {})
    assert reply["data"]["ok"] is True
    assert "mcp" in reply["data"]["result"]
    assert reply["data"]["result"]["mcp"] is None


# ---- schedule.list / cost.get（右栏任务中心 + 用量卡） ----


class _StubScheduler:
    """Scheduler 替身：返回固定的待触发任务列表。"""

    def __init__(self, tasks: list[ScheduleTask]) -> None:
        self._tasks = tasks

    def list_pending(self) -> list[ScheduleTask]:
        return list(self._tasks)


class _StubDeadlineTracker:
    """DeadlineTracker 替身：返回固定的活跃截止日期列表。"""

    def __init__(self, items: list[Deadline]) -> None:
        self._items = items

    def list_active(self) -> list[Deadline]:
        return list(self._items)


class _StubHub:
    """ProactiveHub 替身：仅暴露 schedule.list 需要的两个只读属性。"""

    def __init__(self, tasks: list[ScheduleTask], items: list[Deadline]) -> None:
        self.scheduler = _StubScheduler(tasks)
        self.deadline_tracker = _StubDeadlineTracker(items)


def test_schedule_list_no_hub_returns_empty():
    """hub 未装配时返回空列表（ok=true，前端空态而非报错）。"""
    server, _, _, _ = _make()
    reply = _call(server, "schedule.list", {})
    assert reply["data"]["ok"] is True
    assert reply["data"]["result"] == {"reminders": [], "deadlines": []}


def test_schedule_list_with_hub_maps_fields():
    """hub 装配时字段映射正确（reminder 取 content/trigger_at，deadline 取 title/days_left）。"""
    settings = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)
    hub = _StubHub(
        tasks=[ScheduleTask(id="t1", trigger_at="2026-09-23T09:00:00", content="开会", repeat="daily")],
        items=[Deadline(id="d1", title="Q3 交付", due_date="2099-01-01")],
    )
    server = DesktopBridgeServer(api, settings, hub=hub)
    reply = _call(server, "schedule.list", {})
    assert reply["data"]["ok"] is True
    result = reply["data"]["result"]
    assert result["reminders"] == [
        {"id": "t1", "content": "开会", "trigger_at": "2026-09-23T09:00:00", "repeat": "daily"}
    ]
    assert len(result["deadlines"]) == 1
    item = result["deadlines"][0]
    assert item["id"] == "d1"
    assert item["title"] == "Q3 交付"
    assert item["due_date"] == "2099-01-01"
    assert item["status"] == "active"
    # 到期日在未来：days_left 应为正整数
    assert isinstance(item["days_left"], int) and item["days_left"] > 0


def test_cost_get_shape_defaults_zero():
    """cost.get 返回用量统计（引擎未装配时 token/轮数/消息数全 0）。"""
    server, _, _, _ = _make()
    reply = _call(server, "cost.get", {})
    assert reply["data"]["ok"] is True
    result = reply["data"]["result"]
    assert {
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "dialogs",
        "messages",
    } <= set(result.keys())
    assert result["input_tokens"] == 0
    assert result["dialogs"] == 0
    assert result["messages"] == 0


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


# ---- 每连接首帧 ----

def test_init_pushed_per_client_connect():
    """init 首帧按连接推送：新连接立即收到 init 信封（payload 同 get_state）。

    启动期一次性 broadcast 在无客户端时会被丢弃，首帧只能走每连接钩子；
    桌面壳首屏七路刷新（含设置面板 settings.get 回填）全挂在该事件上。

    @author aceFelix
    """
    server, api, _, _ = _make()
    ws = _FakeWS()
    asyncio.run(server._on_client_connected(ws))
    assert len(ws.sent) == 1
    assert ws.sent[0]["event"] == "init"
    assert ws.sent[0]["data"] == api.get_state()


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
