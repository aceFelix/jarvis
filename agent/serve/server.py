"""DesktopBridgeServer —— 桌面壳（jarvis-desktop）专用的 WS API 服务器。

继承手机协同的 BridgeServer 复用传输层（HTTP + WS、token 认证、broadcast、
客户端管理），但完全接管指令分发：

- ``message`` 不再走手机端"共享 REPL 上下文"路径，而是转发给
  workbench 的 ChatEngine 指令队列（与 pywebview 工作台同一引擎）；
- 注册 request/response 型桌面指令（sessions/models/voices/metrics/state），
  能力口径与 ``agent.ui.workbench.api.WorkbenchAPI`` 一一对齐；
- 事件泵线程消费引擎事件队列，逐条 broadcast 给所有 WS 客户端，
  事件名与 payload 保持 workbench 原样（前端渲染逻辑可对齐 app.js）。

绑定收敛为 127.0.0.1 + 随机端口（桌面壳经 stdout 握手 JSON 获取），
不对局域网暴露——这是与手机协同模式（0.0.0.0 + 固定端口）的关键差异。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Any, Callable

from agent.bridge.server import BridgeServer
from agent.config.settings import Settings
from agent.serve import protocol
from agent.ui.workbench.api import WorkbenchAPI
from agent.ui.workbench.metrics import collect_metrics


class DesktopBridgeServer(BridgeServer):
    """桌面壳 API 服务器：BridgeServer 传输层 + ChatEngine 指令路由。

    生命周期由 ``agent.serve.app.run_serve`` 管理：
    start() → 事件泵启动 → WS 客户端连接 → 指令/事件双向流 → stop()。

    @author aceFelix
    """

    def __init__(
        self,
        api: WorkbenchAPI,
        settings: Settings,
        *,
        http_port: int = 0,
        ws_port: int = 0,
        token: str = "",
        hub: Any | None = None,
    ) -> None:
        """
        Args:
            api: 工作台 API 门面（复用其 send/load/list 等全部能力）。
            settings: 全局配置（取 workdir 等）。
            http_port: HTTP 端口，默认 0（系统分配随机端口）。
            ws_port: WS 端口，默认 0（系统分配随机端口）。
            token: 认证 token，空则自动生成。
            hub: ProactiveHub 实例（可选，主动播报中枢）；为 None 时
                proactive.ack 指令回执 ok=false（协议向后兼容）。
        """
        # query_loop/ctx 传 None：桌面模式不用手机端的共享 REPL 上下文路径，
        # message 指令被本类改路由到 ChatEngine 队列
        super().__init__(
            query_loop=None,
            ctx=None,
            http_port=http_port,
            ws_port=ws_port,
            token=token,
            workdir=settings.workdir,
            host="127.0.0.1",
        )
        self._api = api
        self._settings = settings
        self._hub = hub
        # 事件泵运行时状态
        self._pump_thread: threading.Thread | None = None
        self._pump_stop = threading.Event()
        self._event_queue: queue.Queue | None = None
        self._register_desktop_handlers()

    # ---- 指令注册 ----

    def _register_desktop_handlers(self) -> None:
        """把全部桌面指令注册进 WS 处理器表（覆盖内置 message 路由）。"""
        # message：改道引擎队列（结果经事件泵以流式事件返回，回执仅确认入队）
        self.register_ws_handler(protocol.CMD_MESSAGE, self._cmd_message)
        # request/response 型指令：同步取值 → reply 回执
        self._register_rpc(protocol.CMD_SESSIONS_LIST, lambda data: self._api.list_sessions())
        self._register_rpc(protocol.CMD_SESSIONS_OPEN, self._rpc_sessions_open)
        self._register_rpc(protocol.CMD_SESSIONS_NEW, lambda data: self._api.new_session())
        self._register_rpc(protocol.CMD_MODELS_LIST, lambda data: self._api.list_models())
        self._register_rpc(protocol.CMD_MODELS_SELECT, self._rpc_models_select)
        self._register_rpc(protocol.CMD_VOICES_LIST, lambda data: self._api.list_voices())
        self._register_rpc(protocol.CMD_VOICES_SELECT, self._rpc_voices_select)
        self._register_rpc(protocol.CMD_METRICS_GET, lambda data: collect_metrics())
        self._register_rpc(protocol.CMD_STATE_GET, lambda data: self._api.get_state())
        self._register_rpc(protocol.CMD_ANSWER_USER, self._rpc_answer_user)
        # reply.abort：停止当前回复（线程安全取消引擎 send 任务，不入队列）
        self._register_rpc(protocol.CMD_REPLY_ABORT, lambda data: self._api.abort_reply())
        self._register_rpc(protocol.CMD_TALK_START, lambda data: self._api.start_talk())
        self._register_rpc(protocol.CMD_TALK_STOP, lambda data: self._api.stop_talk())
        self._register_rpc(protocol.CMD_VOICE_START, lambda data: self._api.start_voice())
        self._register_rpc(protocol.CMD_VOICE_STOP, lambda data: self._api.stop_voice())
        self._register_rpc(protocol.CMD_VOICE_INTERRUPT, lambda data: self._api.interrupt_voice())
        self._register_rpc(protocol.CMD_PROACTIVE_ACK, self._rpc_proactive_ack)

    def _register_rpc(self, cmd_type: str, fn: Callable[[dict], Any]) -> None:
        """注册一个同步取值型指令：执行 fn(data) → 结果封 reply 回执发回。

        fn 抛异常时回执 ok=false + 错误描述，不中断连接。

        @author aceFelix
        """

        async def _handler(ws: Any, data: dict) -> None:
            try:
                result = fn(data)
                msg = protocol.build_reply(cmd_type, ok=True, result=result)
            except Exception as e:  # noqa: BLE001 - 回执携带错误，连接不中断
                msg = protocol.build_reply(
                    cmd_type, ok=False, error=f"{type(e).__name__}: {e}"
                )
            await self._send_json(ws, msg)

        self.register_ws_handler(cmd_type, _handler)

    # ---- 需要参数加工/校验的指令 ----

    async def _cmd_message(self, ws: Any, data: dict) -> None:
        """message 指令：文本入引擎队列，回执确认（流式结果走事件泵）。"""
        text = (data.get("text") or "").strip()
        if not text:
            await self._send_json(
                ws, protocol.build_reply(protocol.CMD_MESSAGE, ok=False, error="空消息")
            )
            return
        self._api.send_message(text)
        await self._send_json(ws, protocol.build_reply(protocol.CMD_MESSAGE, ok=True))

    def _rpc_sessions_open(self, data: dict) -> None:
        """sessions.open：校验 name 后入引擎队列（结果走 session_loaded 事件）。"""
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("缺少会话名 name")
        self._api.load_session(name)

    def _rpc_models_select(self, data: dict) -> bool:
        """models.select：切换文本模型并持久化（重启引擎后生效，与工作台一致）。"""
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("缺少模型名 name")
        return self._api.set_model(name)

    def _rpc_voices_select(self, data: dict) -> bool:
        """voices.select：切换 TTS 音色并持久化。"""
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("缺少音色名 name")
        return self._api.set_voice(name)

    def _rpc_answer_user(self, data: dict) -> None:
        """answer_user：回填引擎 ask_user 弹窗（权限确认等）。"""
        self._api.answer_user(data.get("text") or "")

    def _rpc_proactive_ack(self, data: dict) -> bool:
        """proactive.ack：确认提醒任务（停止升级重发）。

        桌面壳收到 reminder 类 proactive_notify 且窗口可见时自动回执，
        视为已读。hub 未装配时报错（回执 ok=false，连接不中断）。

        @author aceFelix
        """
        task_id = (data.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("缺少任务 ID task_id")
        if self._hub is None:
            raise RuntimeError("主动播报中枢未装配")
        return self._hub.acknowledge(task_id)

    # ---- 事件泵 ----

    def start_event_pump(self, event_queue: queue.Queue) -> None:
        """启动事件泵线程：消费引擎事件队列 → broadcast 给所有 WS 客户端。

        引擎事件结构 ``{"type": ..., "payload": ...}`` 原样映射为
        ``{"event": type, "data": payload}``（broadcast 内部完成信封组装）。

        @author aceFelix
        """
        if self._pump_thread is not None and self._pump_thread.is_alive():
            return
        self._event_queue = event_queue
        self._pump_stop.clear()
        self._pump_thread = threading.Thread(
            target=self._pump_loop, name="serve-event-pump", daemon=True
        )
        self._pump_thread.start()

    def stop_event_pump(self) -> None:
        """停止事件泵线程（幂等）。"""
        self._pump_stop.set()
        if self._pump_thread is not None:
            self._pump_thread.join(timeout=2)
            self._pump_thread = None

    def _pump_loop(self) -> None:
        """泵主循环：50ms 空闲轮询，与 workbench 前端 poll 节奏一致。"""
        q = self._event_queue
        while not self._pump_stop.is_set():
            try:
                item = q.get(timeout=0.05)
            except queue.Empty:
                continue
            except Exception:
                break
            if not isinstance(item, dict):
                continue
            event_type = item.get("type")
            if not event_type:
                continue
            try:
                self.broadcast(str(event_type), item.get("payload"))
            except Exception:
                pass

    # ---- 内部工具 ----

    @staticmethod
    async def _send_json(ws: Any, msg: dict) -> None:
        """向单个 WS 客户端发送 JSON 消息（失败静默，连接清理由主循环负责）。"""
        try:
            await ws.send(json.dumps(msg, ensure_ascii=False))
        except Exception:
            pass

    async def stop(self) -> None:
        """停止服务：先停事件泵再关传输层（避免泵向已关闭的 loop 投递）。"""
        self.stop_event_pump()
        await super().stop()
