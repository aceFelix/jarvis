"""跨设备协同（手机 / 微信）桌面接入测试 —— 共享锁串行 + 桥接装配 + API 入队。

覆盖三层：
1. remote_bridge：connect_phone / connect_wechat 在引擎 loop 上以**引擎唯一共享
   query 锁**装配桥接、回推 qrcode / remote_state 事件；失败走 error 事件。
2. bridge 串行化：注入的 query_lock 确为所用（终端默认自建）；同一 loop 上两轮
   run_query 借共享锁串行，从不并发写共享 messages（回归 t1/t2 的核心保证）。
3. WorkbenchAPI：connect/disconnect/pairing 指令入队口径；status 读取桥接单例。

全程 hermetic：monkeypatch agent.bridge / agent.wechat 的启动函数，不真起端口。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import queue
import threading
from types import SimpleNamespace

import agent.bridge as bridge_pkg
import agent.wechat as wechat_pkg
from agent.bridge.server import BridgeServer
from agent.bridge.ui import BridgeUI
from agent.config.settings import Settings
from agent.ui.workbench.api import WorkbenchAPI
from agent.ui.workbench.engine import ChatEngine
from agent.ui.workbench import remote_bridge
from agent.wechat.server import WeChatBridge
from agent.wechat.ui import WeChatUI


class _Recorder:
    """事件记录器：把 emitter.emit(name, data) 收进列表。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def emit(self, name: str, data: object = None) -> None:
        self.events.append((name, data))

    def names(self) -> list[str]:
        return [n for n, _ in self.events]

    def find(self, name: str) -> object:
        for n, d in self.events:
            if n == name:
                return d
        return None


class _FakeEngine:
    """remote_bridge 所需的最小引擎替身：loop / 锁 / 会话件 + 事件记录器。"""

    def __init__(self) -> None:
        self._emitter = _Recorder()
        self._settings = Settings()
        self._settings.workdir = "C:/work"
        self._query_loop = SimpleNamespace(name="qloop")
        self._ctx = SimpleNamespace(name="ctx")
        self._query_lock = threading.Lock()
        # 独立哨兵：断言桥接拿到的 main_loop 就是引擎持有的这个（而非桥接自己的）
        self._loop = SimpleNamespace(name="engine-loop")
        self.ensure_called = False
        # 落盘回调哨兵：断言桥接拿到的是引擎的 _after_turn（每轮 query 结束存盘）
        self.after_turn_calls = 0
        # 开跑登记回调哨兵：断言桥接拿到的是引擎 _remote_query_begin（供 reply.abort 取消）
        self.query_begin_calls = 0

    def _after_turn(self) -> None:
        self.after_turn_calls += 1

    def _remote_query_begin(self) -> None:
        self.query_begin_calls += 1

    async def _ensure_session(self) -> None:
        self.ensure_called = True


# ---- 1. remote_bridge：手机 ----

def test_connect_phone_uses_shared_lock_and_engine_loop(monkeypatch):
    """手机桥接以引擎唯一 query 锁 + 引擎 loop 装配，回推 qrcode；连接态待手机真正接入。"""
    captured: dict = {}
    holder: dict = {}

    def _fake_start(**kwargs):
        captured.update(kwargs)
        srv = SimpleNamespace(url="http://192.168.1.5:8765/?token=deadbeef", _clients=set())
        holder["server"] = srv
        return srv

    monkeypatch.setattr(bridge_pkg, "start_bridge_in_thread", _fake_start)
    monkeypatch.setattr(bridge_pkg, "stop_bridge", lambda: None)

    engine = _FakeEngine()
    asyncio.run(remote_bridge.connect_phone(engine))

    assert engine.ensure_called is True
    assert captured["query_lock"] is engine._query_lock  # 共享唯一锁
    assert captured["main_loop"] is engine._loop          # 调度回引擎 loop
    qr = engine._emitter.find("qrcode")
    assert qr == {
        "channel": "phone",
        "url": "http://192.168.1.5:8765/?token=deadbeef",
        "fresh": True,
    }
    # 每轮手机对话结束落盘：注入的是引擎 _after_turn
    assert captured["on_turn_end"] == engine._after_turn
    # 每轮手机 query 开跑登记任务：注入的是引擎 _remote_query_begin（使桌面可停止）
    assert captured["on_query_begin"] == engine._remote_query_begin
    # 关键回归：桥接启动不再抢先推 connected（未扫码不应显「已连接」）
    assert engine._emitter.find("remote_state") is None

    # 手机 WS 客户端真正接入 → 回调推 connected=True；最后客户端离开 → False
    holder["server"].on_client_connected()
    holder["server"].on_client_disconnected()
    states = [d for n, d in engine._emitter.events if n == "remote_state"]
    assert states == [
        {"channel": "phone", "connected": True},
        {"channel": "phone", "connected": False},
    ]


def test_connect_phone_emits_connected_when_client_already_present(monkeypatch):
    """复用已运行桥接且已有手机在线时，connect_phone 立即补推 connected=True（否则错过回调）。"""
    def _fake_start(**kwargs):
        return SimpleNamespace(url="http://x/?token=1", _clients={object()})

    monkeypatch.setattr(bridge_pkg, "start_bridge_in_thread", _fake_start)
    monkeypatch.setattr(bridge_pkg, "stop_bridge", lambda: None)

    engine = _FakeEngine()
    asyncio.run(remote_bridge.connect_phone(engine))

    assert engine._emitter.find("remote_state") == {"channel": "phone", "connected": True}


def test_connect_phone_reports_error_on_failure(monkeypatch):
    """桥接启动异常 → error 事件，不外抛、不发 qrcode。"""
    def _boom(**kwargs):
        raise RuntimeError("端口占用")

    monkeypatch.setattr(bridge_pkg, "start_bridge_in_thread", _boom)
    monkeypatch.setattr(bridge_pkg, "stop_bridge", lambda: None)

    engine = _FakeEngine()
    asyncio.run(remote_bridge.connect_phone(engine))

    assert "qrcode" not in engine._emitter.names()
    assert "error" in engine._emitter.names()


def test_disconnect_phone_emits_disconnected(monkeypatch):
    """断开手机：调 stop_bridge 并推 remote_state connected=False。"""
    stopped: list[bool] = []
    monkeypatch.setattr(bridge_pkg, "stop_bridge", lambda: stopped.append(True))

    engine = _FakeEngine()
    asyncio.run(remote_bridge.disconnect_phone(engine))

    assert stopped == [True]
    assert engine._emitter.find("remote_state") == {"channel": "phone", "connected": False}


# ---- 2. remote_bridge：微信 ----

def test_connect_wechat_injects_shared_lock_and_creates_pairing_queue(monkeypatch):
    """微信桥接同样注入共享锁 + 引擎 loop，并新建配对码回喂队列。"""
    captured: dict = {}

    def _fake_start(**kwargs):
        captured.update(kwargs)
        # login 走独立线程；此处返回一个 login 立即失败的桥接，避免干扰断言
        class _B:
            async def login(self, verify_callback, qrcode_callback):
                return False

        return _B()

    monkeypatch.setattr(wechat_pkg, "start_wechat_in_thread", _fake_start)
    monkeypatch.setattr(wechat_pkg, "stop_wechat", lambda: None)

    engine = _FakeEngine()
    asyncio.run(remote_bridge.connect_wechat(engine))

    assert engine.ensure_called is True
    assert captured["query_lock"] is engine._query_lock
    assert captured["main_loop"] is engine._loop
    assert isinstance(captured["ui"], remote_bridge._StatusUI)
    assert isinstance(engine._wechat_pairing_q, queue.Queue)
    # 每轮微信对话结束落盘：注入引擎 _after_turn
    assert captured["on_turn_end"] is not None
    # 每轮微信 query 开跑登记任务：注入引擎 _remote_query_begin
    assert captured["on_query_begin"] == engine._remote_query_begin


def test_wechat_login_worker_emits_qrcode_and_connected(monkeypatch):
    """login 线程 worker：出二维码→收配对码→成功起消息循环并推 connected。"""
    loop_started: list[bool] = []
    monkeypatch.setattr(wechat_pkg, "start_wechat_loop", lambda: loop_started.append(True))

    class _B:
        # login 成功且拿到 bot_token：connected 属性（bool(bot_token)）为真
        connected = True

        async def login(self, verify_callback, qrcode_callback):
            qrcode_callback("weixin://dl/business/?ticket=abc")
            code = verify_callback(False)
            return code == "123456"

    engine = _FakeEngine()
    pairing_q: queue.Queue[str] = queue.Queue()
    pairing_q.put("123456")

    remote_bridge._wechat_login_worker(_B(), engine, pairing_q)

    assert engine._emitter.find("qrcode") == {
        "channel": "wechat",
        "url": "weixin://dl/business/?ticket=abc",
        "fresh": True,
    }
    assert engine._emitter.find("remote_state") == {"channel": "wechat", "connected": True}
    assert loop_started == [True]


def test_wechat_login_worker_already_connected_without_token_not_reported(monkeypatch):
    """login 返回 True 但无 bot_token（already_connected）：不谎报已连接、不起消息循环。"""
    loop_started: list[bool] = []
    monkeypatch.setattr(wechat_pkg, "start_wechat_loop", lambda: loop_started.append(True))

    class _B:
        # already_connected 分支不写 bot_token → connected 为假
        connected = False

        async def login(self, verify_callback, qrcode_callback):
            qrcode_callback("weixin://dl/business/?ticket=abc")
            return True

    engine = _FakeEngine()
    remote_bridge._wechat_login_worker(_B(), engine, queue.Queue())

    # 不应推 connected=True，不应起轮询，但会 warn 提示重新扫码
    assert engine._emitter.find("remote_state") is None
    assert loop_started == []
    assert "warn" in engine._emitter.names()


def test_wechat_login_worker_warns_when_login_incomplete(monkeypatch):
    """login 未完成（无 bot_token）→ warn，不起消息循环。"""
    loop_started: list[bool] = []
    monkeypatch.setattr(wechat_pkg, "start_wechat_loop", lambda: loop_started.append(True))

    class _B:
        async def login(self, verify_callback, qrcode_callback):
            return False

    engine = _FakeEngine()
    remote_bridge._wechat_login_worker(_B(), engine, queue.Queue())

    assert "warn" in engine._emitter.names()
    assert loop_started == []


# ---- 2b. WeChatUI：入站消息事件 + 轮次收尾 ----

def test_wechat_ui_user_message_uses_remote_user_message():
    """桌面宿主有 remote_user_message → 微信入站消息转专事件（channel=wechat）。"""
    calls: list = []

    class _Desktop:
        def remote_user_message(self, channel, text):
            calls.append(("remote", channel, text))

        def info(self, text):
            calls.append(("info", text))

    WeChatUI(desktop_ui=_Desktop()).user_message("后天天气如何")
    assert calls == [("remote", "wechat", "后天天气如何")]


def test_wechat_ui_user_message_falls_back_to_info():
    """终端宿主无 remote_user_message → 回退 info 前缀。"""
    infos: list = []

    class _Desktop:
        def info(self, text):
            infos.append(text)

    WeChatUI(desktop_ui=_Desktop()).user_message("你好")
    assert infos == ["[微信] 你好"]


def test_wechat_ui_end_turn_calls_assistant_done():
    """end_turn → 调桌面 assistant_done 收尾气泡；无该方法（终端）则 no-op。"""
    done: list = []

    class _Desktop:
        def assistant_done(self):
            done.append(True)

    WeChatUI(desktop_ui=_Desktop()).end_turn()
    assert done == [True]
    # 终端宿主无 assistant_done：不抛异常
    WeChatUI(desktop_ui=object()).end_turn()


def test_wechat_exec_query_ends_turn_after_run():
    """_exec_query 跑完 query 后调 ctx.ui.end_turn（桌面收尾本轮气泡）。"""

    class _QL:
        async def run(self, text, ctx):
            return None

    class _TurnUI:
        def __init__(self) -> None:
            self.ended = False

        def end_turn(self) -> None:
            self.ended = True

    turn_ui = _TurnUI()
    ctx = SimpleNamespace(ui=turn_ui)
    bridge = WeChatBridge(
        query_loop=_QL(),
        ctx=SimpleNamespace(ui=None, messages=[], workdir="C:/work"),
        ui=object(),
    )
    asyncio.run(bridge._exec_query("hi", ctx))
    assert turn_ui.ended is True


def test_wechat_login_worker_qrcode_cb_fresh_first_then_stale(monkeypatch):
    """登录内二维码过期重生成：首帧 fresh=True（新连接），后续帧 fresh=False（就地刷新）。"""
    monkeypatch.setattr(wechat_pkg, "start_wechat_loop", lambda: None)

    class _B:
        connected = True

        async def login(self, verify_callback, qrcode_callback):
            qrcode_callback("weixin://ticket=1")  # 首帧
            qrcode_callback("weixin://ticket=2")  # 过期重生成
            return False  # 不关心连接态，只看二维码帧

    engine = _FakeEngine()
    remote_bridge._wechat_login_worker(_B(), engine, queue.Queue())

    qrs = [d for n, d in engine._emitter.events if n == "qrcode"]
    assert [q["fresh"] for q in qrs] == [True, False]
    assert qrs[0]["url"] == "weixin://ticket=1"
    assert qrs[1]["url"] == "weixin://ticket=2"


def test_wechat_exec_query_calls_on_turn_end():
    """_exec_query 跑完 query 后调注入的 on_turn_end（每轮落盘）。"""

    class _QL:
        async def run(self, text, ctx):
            return None

    ended: list = []
    ctx = SimpleNamespace(ui=object())  # 无 end_turn，getattr 回退 no-op
    bridge = WeChatBridge(
        query_loop=_QL(),
        ctx=SimpleNamespace(ui=None, messages=[], workdir="C:/work"),
        ui=object(),
        on_turn_end=lambda: ended.append(True),
    )
    asyncio.run(bridge._exec_query("hi", ctx))
    assert ended == [True]


def test_phone_run_query_calls_on_turn_end():
    """手机 run_query 跑完一轮后调注入的 on_turn_end（引擎 loop 上落盘）。"""

    class _QL:
        async def run(self, text, ctx, images=None):
            return "ok"

    async def _drive():
        ended: list = []
        srv = BridgeServer(_QL(), None, host="127.0.0.1")
        srv._main_loop = asyncio.get_running_loop()  # 走内联路径
        srv.on_turn_end = lambda: ended.append(True)
        await srv.run_query("hi", None)
        return ended

    assert asyncio.run(_drive()) == [True]


def test_phone_run_query_calls_on_query_begin():
    """手机 run_query 拿到锁、开跑前调 on_query_begin（登记任务供桌面 reply.abort）。"""

    class _QL:
        async def run(self, text, ctx, images=None):
            return "ok"

    async def _drive():
        began: list = []
        srv = BridgeServer(_QL(), None, host="127.0.0.1")
        srv._main_loop = asyncio.get_running_loop()  # 走内联路径
        srv.on_query_begin = lambda: began.append(True)
        await srv.run_query("hi", None)
        return began

    assert asyncio.run(_drive()) == [True]


def test_wechat_exec_query_calls_on_query_begin():
    """微信 _exec_query 拿到锁、开跑前调 on_query_begin（登记任务供桌面 reply.abort）。"""

    class _QL:
        async def run(self, text, ctx):
            return None

    began: list = []
    ctx = SimpleNamespace(ui=object())
    bridge = WeChatBridge(
        query_loop=_QL(),
        ctx=SimpleNamespace(ui=None, messages=[], workdir="C:/work"),
        ui=object(),
        on_query_begin=lambda: began.append(True),
    )
    asyncio.run(bridge._exec_query("hi", ctx))
    assert began == [True]


def test_bridge_ui_finish_emits_desktop_assistant_done():
    """手机轮次收尾：BridgeUI.finish 向桌面补发 assistant_done（防 busy 卡死）。

    与微信 end_turn 对称：桌面靠 assistant_done 撤销 busy、定稿气泡。终端宿主
    无该方法时不报错。
    @author aceFelix
    """
    done: list = []

    class _Desktop:
        def assistant_done(self):
            done.append(True)

    BridgeUI(desktop_ui=_Desktop()).finish()
    assert done == [True]
    # 终端宿主无 assistant_done：不抛异常
    BridgeUI(desktop_ui=object()).finish()


def test_engine_remote_query_begin_registers_current_task():
    """_remote_query_begin 把当前引擎 loop 任务登记为 _send_task，供 abort 取消。"""

    engine = ChatEngine(Settings(), queue.Queue(), queue.Queue())

    async def _drive():
        engine._remote_query_begin()
        return engine._send_task

    task = asyncio.run(_drive())
    # 登记的正是驱动本协程的当前任务（abort 可对其 cancel）
    assert task is not None and asyncio.isfuture(task)


# ---- 3. 共享锁串行化（t1/t2 核心保证）----

def test_bridge_server_uses_injected_shared_lock_or_self():
    """注入即用（桌面共享），未注入则自建（终端本桥接内串行）。"""
    shared = threading.Lock()
    srv = BridgeServer(object(), None, host="127.0.0.1", query_lock=shared)
    assert srv._query_lock is shared
    srv2 = BridgeServer(object(), None, host="127.0.0.1")
    assert isinstance(srv2._query_lock, threading.Lock) and srv2._query_lock is not shared


def test_bridge_server_client_callbacks_fire_on_transitions():
    """接入回调每次连接都触发；断开回调仅在最后一个客户端离开时触发（幂等由前端处理）。"""
    events: list[str] = []
    srv = BridgeServer(object(), None, host="127.0.0.1")
    srv.on_client_connected = lambda: events.append("up")
    srv.on_client_disconnected = lambda: events.append("down")

    # 首个接入：add + _on_client_connected → up
    srv._clients.add("ws1")
    asyncio.run(srv._on_client_connected("ws1"))
    assert events == ["up"]
    # 第二个接入：仍触发 up
    srv._clients.add("ws2")
    asyncio.run(srv._on_client_connected("ws2"))
    assert events == ["up", "up"]
    # 移除一个仍有在线：不触发 down
    srv._remove_client("ws1")
    assert events == ["up", "up"]
    # 最后一个离开：触发 down
    srv._remove_client("ws2")
    assert events == ["up", "up", "down"]


def test_run_query_serializes_two_queries_on_shared_lock():
    """同一 loop 上并发两轮 run_query：借共享锁串行，活跃 query 峰值恒为 1。"""
    class _Tracking:
        def __init__(self):
            self.active = 0
            self.max_active = 0

        async def run(self, text, ctx, images=None):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.02)  # 让另一轮有机会交错（若未串行会并发）
            self.active -= 1
            return "done"

    async def _drive():
        tracker = _Tracking()
        lock = threading.Lock()
        srv = BridgeServer(tracker, None, host="127.0.0.1", query_lock=lock)
        # 让 run_query 走内联路径（不再 run_coroutine_threadsafe 重调度）
        srv._main_loop = asyncio.get_running_loop()
        await asyncio.gather(srv.run_query("a", None), srv.run_query("b", None))
        return tracker.max_active

    assert asyncio.run(_drive()) == 1


# ---- 4. 引擎 _dispatch 配对码路由 ----

def test_engine_dispatch_wechat_pairing_feeds_login_queue():
    """wechat_pairing 指令：把 code 放进 login 线程阻塞等待的配对队列。"""
    engine = ChatEngine(Settings(), queue.Queue(), queue.Queue())
    engine._wechat_pairing_q = queue.Queue()
    asyncio.run(engine._dispatch({"cmd": "wechat_pairing", "code": "987654"}))
    assert engine._wechat_pairing_q.get_nowait() == "987654"


# ---- 5. WorkbenchAPI 入队口径 ----

def _make_api():
    settings = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)
    return api, command_queue


def test_api_connect_disconnect_enqueue_commands():
    """connect/disconnect/pairing 均按 cmd 入队（入队即返回）。"""
    api, cq = _make_api()
    assert api.connect_phone() == {"ok": True, "pending": True}
    assert cq.get_nowait() == {"cmd": "connect_phone"}
    api.connect_wechat()
    assert cq.get_nowait() == {"cmd": "connect_wechat"}
    api.disconnect_phone()
    assert cq.get_nowait() == {"cmd": "disconnect_phone"}
    api.disconnect_wechat()
    assert cq.get_nowait() == {"cmd": "disconnect_wechat"}
    api.wechat_pairing("  1234  ")
    assert cq.get_nowait() == {"cmd": "wechat_pairing", "code": "1234"}


def test_api_status_reads_bridge_singletons(monkeypatch):
    """phone.status / wechat.status 读取桥接单例，无实例时回退未连接。"""
    api, _ = _make_api()
    # 无桥接 → 未连接
    monkeypatch.setattr(bridge_pkg, "get_bridge_server", lambda: None, raising=False)
    monkeypatch.setattr(wechat_pkg, "get_wechat_bridge", lambda: None, raising=False)
    assert api.phone_status() == {"active": False, "url": ""}
    assert api.wechat_status() == {"connected": False}
    # 有桥接 → 已连接，带 url
    monkeypatch.setattr(
        bridge_pkg, "get_bridge_server",
        lambda: SimpleNamespace(url="http://x/?token=1"), raising=False,
    )
    monkeypatch.setattr(
        wechat_pkg, "get_wechat_bridge", lambda: SimpleNamespace(connected=True), raising=False,
    )
    assert api.phone_status() == {"active": True, "url": "http://x/?token=1"}
    assert api.wechat_status() == {"connected": True}
