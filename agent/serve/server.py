"""DesktopBridgeServer —— 桌面壳（jarvis-desktop）专用的 WS API 服务器。

继承手机协同的 BridgeServer 复用传输层（HTTP + WS、token 认证、broadcast、
客户端管理），但完全接管指令分发：

- ``message`` 不再走手机端"共享 REPL 上下文"路径，而是转发给
  workbench 的 ChatEngine 指令队列（与 pywebview 工作台同一引擎）；
- 注册 request/response 型桌面指令（sessions/models/voices/metrics/state/settings），
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
from agent.config.desktop_settings import (
    DESKTOP_SETTING_SPECS,
    SCHEDULE_KEYS,
    SPEC_BY_KEY,
    apply_setting,
    read_setting,
    save_setting,
    validate_setting,
)
from agent.config.settings import Settings
from agent.serve import protocol
from agent.ui.workbench.api import WorkbenchAPI
from agent.ui.workbench.metrics import collect_metrics

# 附件上限（与桌面壳前端同一口径）：防单条消息超大 payload 打爆 WS/上下文。
# 超限在入队前快速失败（reply ok=False），不进引擎队列。@author aceFelix
_MAX_ATTACH_IMAGES = 8
_MAX_IMAGE_B64_CHARS = 10_000_000
_MAX_ATTACH_FILES = 5
_MAX_FILE_CHARS = 200_000


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
        self._register_rpc(protocol.CMD_SESSIONS_RENAME, self._rpc_sessions_rename)
        self._register_rpc(protocol.CMD_SESSIONS_DELETE, self._rpc_sessions_delete)
        self._register_rpc(protocol.CMD_MODELS_LIST, lambda data: self._api.list_models())
        self._register_rpc(protocol.CMD_MODELS_SELECT, self._rpc_models_select)
        self._register_rpc(protocol.CMD_VOICES_LIST, lambda data: self._api.list_voices())
        self._register_rpc(protocol.CMD_VOICES_SELECT, self._rpc_voices_select)
        self._register_rpc(protocol.CMD_METRICS_GET, lambda data: collect_metrics())
        self._register_rpc(protocol.CMD_STATE_GET, lambda data: self._api.get_state())
        self._register_rpc(protocol.CMD_SCHEDULE_LIST, self._rpc_schedule_list)
        self._register_rpc(protocol.CMD_COST_GET, lambda data: self._api.get_cost())
        self._register_rpc(protocol.CMD_ANSWER_USER, self._rpc_answer_user)
        # reply.abort：停止当前回复（线程安全取消引擎 send 任务，不入队列）
        self._register_rpc(protocol.CMD_REPLY_ABORT, lambda data: self._api.abort_reply())
        self._register_rpc(protocol.CMD_TALK_START, lambda data: self._api.start_talk())
        self._register_rpc(protocol.CMD_TALK_STOP, lambda data: self._api.stop_talk())
        self._register_rpc(protocol.CMD_VOICE_START, lambda data: self._api.start_voice())
        self._register_rpc(protocol.CMD_VOICE_STOP, lambda data: self._api.stop_voice())
        self._register_rpc(protocol.CMD_VOICE_INTERRUPT, lambda data: self._api.interrupt_voice())
        self._register_rpc(protocol.CMD_PROACTIVE_ACK, self._rpc_proactive_ack)
        self._register_rpc(protocol.CMD_SETTINGS_GET, self._rpc_settings_get)
        self._register_rpc(protocol.CMD_SETTINGS_SET, self._rpc_settings_set)

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
        """message 指令：文本（可带 images/files 附件）入引擎队列，回执确认（流式结果走事件泵）。

        附件校验（快速失败，垃圾数据不进引擎队列）：
        - images：≤8 张，每条 {data: base64 串（≤10M 字符）, media_type}；
        - files：≤5 个，每条 {name, content（≤20 万字符）}。

        @author aceFelix
        """
        text = (data.get("text") or "").strip()
        images = data.get("images") or []
        files = data.get("files") or []
        if not isinstance(images, list) or len(images) > _MAX_ATTACH_IMAGES:
            await self._send_json(
                ws, protocol.build_reply(
                    protocol.CMD_MESSAGE, ok=False, error=f"图片附件过多（上限 {_MAX_ATTACH_IMAGES} 张）"
                )
            )
            return
        for item in images:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("data"), str)
                or not item["data"]
                or len(item["data"]) > _MAX_IMAGE_B64_CHARS
            ):
                await self._send_json(
                    ws, protocol.build_reply(
                        protocol.CMD_MESSAGE, ok=False, error="图片附件非法或超大（base64 上限 10M 字符）"
                    )
                )
                return
        if not isinstance(files, list) or len(files) > _MAX_ATTACH_FILES:
            await self._send_json(
                ws, protocol.build_reply(
                    protocol.CMD_MESSAGE, ok=False, error=f"文件附件过多（上限 {_MAX_ATTACH_FILES} 个）"
                )
            )
            return
        for item in files:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("content"), str)
                or len(item["content"]) > _MAX_FILE_CHARS
            ):
                await self._send_json(
                    ws, protocol.build_reply(
                        protocol.CMD_MESSAGE, ok=False, error="文本文件附件非法或超大（上限 20 万字符）"
                    )
                )
                return
        if not text and not images and not files:
            await self._send_json(
                ws, protocol.build_reply(protocol.CMD_MESSAGE, ok=False, error="空消息")
            )
            return
        self._api.send_message(text, images=images or None, files=files or None)
        await self._send_json(ws, protocol.build_reply(protocol.CMD_MESSAGE, ok=True))

    def _rpc_sessions_open(self, data: dict) -> None:
        """sessions.open：校验 name 后入引擎队列（结果走 session_loaded 事件）。"""
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("缺少会话名 name")
        self._api.load_session(name)

    def _rpc_sessions_rename(self, data: dict) -> None:
        """sessions.rename：校验新旧名后入引擎队列（结果走 session_renamed 事件）。

        @author aceFelix
        """
        name = (data.get("name") or "").strip()
        new_name = (data.get("new_name") or "").strip()
        if not name or not new_name:
            raise ValueError("缺少会话名 name 或新名 new_name")
        self._api.rename_session(name, new_name)

    def _rpc_sessions_delete(self, data: dict) -> None:
        """sessions.delete：校验 name 后入引擎队列（结果走 session_deleted 事件）。

        @author aceFelix
        """
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("缺少会话名 name")
        self._api.delete_session(name)

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

    def _rpc_schedule_list(self, data: dict) -> dict:
        """schedule.list：右栏任务中心数据源（待触发提醒 + 活跃截止日期）。

        hub 未装配时返回空列表（ok=true，前端显示空态而非报错）。
        字段口径：
        - reminders：{id, content, trigger_at, repeat}（trigger_at 为 ISO 串，按时间升序）；
        - deadlines：{id, title, due_date, days_left, status}（days_left 负数=已逾期）。

        @author aceFelix
        """
        reminders: list[dict] = []
        deadlines: list[dict] = []
        hub = self._hub
        if hub is not None:
            for task in hub.scheduler.list_pending():
                reminders.append(
                    {
                        "id": task.id,
                        "content": task.content,
                        "trigger_at": task.trigger_at,
                        "repeat": task.repeat,
                    }
                )
            for item in hub.deadline_tracker.list_active():
                deadlines.append(
                    {
                        "id": item.id,
                        "title": item.title,
                        "due_date": item.due_date,
                        "days_left": item.days_left,
                        "status": item.status,
                    }
                )
        return {"reminders": reminders, "deadlines": deadlines}

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

    def _rpc_settings_get(self, data: dict) -> dict:
        """settings.get：桌面壳设置面板数据源（白名单可改项的运行时值）。

        白名单见 agent/config/desktop_settings.py（第一批：主动播报 TTS/简报/
        截止日期/TTS 语速音量）；主题/语言等纯前端偏好不入此协议（桌面壳
        localStorage 自治）。属性缺失的项返回 None，桌面壳据此显离线态。

        @author aceFelix
        """
        return {spec.key: read_setting(self._settings, spec) for spec in DESKTOP_SETTING_SPECS}

    def _rpc_settings_set(self, data: dict) -> dict:
        """settings.set：修改单个白名单设置项（校验 → 落盘 → 运行时生效）。

        顺序：先持久化 settings.toml 对应节，再改运行时 Settings 实例；
        briefing/deadline 类额外触发 ProactiveHub 重注册调度任务（调度任务
        是启动快照，不重注册新开关/新时间不生效）；落盘失败则回执报错且
        不动运行时，避免「本次生效、重启回退」的口径分裂。

        @author aceFelix
        """
        keys = [k for k in data if k in SPEC_BY_KEY]
        if len(keys) != 1:
            raise ValueError("settings.set 需且仅需一个白名单设置项")
        key = keys[0]
        spec = SPEC_BY_KEY[key]
        value = validate_setting(key, data[key])
        if not save_setting(spec, value):
            raise RuntimeError("settings.toml 落盘失败，请检查磁盘写权限")
        apply_setting(self._settings, spec, value)
        if key in SCHEDULE_KEYS and self._hub is not None:
            self._hub.hot_update_schedule()
        return {key: value}

    # ---- 每连接首帧 ----

    async def _on_client_connected(self, ws: Any) -> None:
        """连接建立即推 init 事件（payload 与 workbench get_state 同构，前端首屏渲染）。

        协议契约是「连接建立后首推」（见 protocol.py 事件表）：启动期一次性
        broadcast 在无在线客户端时会被丢弃，桌面壳的首屏七路刷新（含设置面板
        settings.get 回填）将永不触发；改为按连接推送，同时覆盖断线重连场景。

        @author aceFelix
        """
        await self._send_json(
            ws, {"event": protocol.EVT_INIT, "data": self._api.get_state()}
        )

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
