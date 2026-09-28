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
from agent.ui.workbench.model_admin import VALID_API_FORMATS, VALID_MODEL_TYPES

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
        # 已注册的 request/response 型指令名（契约自检用：注册了就必须在
        # protocol.DESKTOP_COMMANDS 中声明，防「加了指令忘了进集合」）。
        # @author aceFelix
        self._rpc_types: set[str] = set()
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
        self._register_rpc(protocol.CMD_MODELS_ADD, self._rpc_models_add)
        self._register_rpc(protocol.CMD_MODELS_EDIT, self._rpc_models_edit)
        self._register_rpc(protocol.CMD_MODELS_REMOVE, self._rpc_models_remove)
        self._register_rpc(protocol.CMD_VOICES_LIST, lambda data: self._api.list_voices())
        self._register_rpc(protocol.CMD_VOICES_SELECT, self._rpc_voices_select)
        # voices.add / voices.delete（2026-09-28）：桌面壳音色面板的自定义音色
        # 管理，校验与落盘由 api.add_voice/delete_voice 承担（与 /tts-voice 同口径）
        self._register_rpc(protocol.CMD_VOICES_ADD, self._rpc_voices_add)
        self._register_rpc(protocol.CMD_VOICES_DELETE, self._rpc_voices_delete)
        self._register_rpc(protocol.CMD_METRICS_GET, lambda data: collect_metrics())
        self._register_rpc(protocol.CMD_STATE_GET, lambda data: self._api.get_state())
        self._register_rpc(protocol.CMD_SCHEDULE_LIST, self._rpc_schedule_list)
        self._register_rpc(protocol.CMD_COST_GET, lambda data: self._api.get_cost())
        self._register_rpc(protocol.CMD_ANSWER_USER, self._rpc_answer_user)
        # reply.abort：停止当前回复（线程安全取消引擎 send 任务，不入队列）
        self._register_rpc(protocol.CMD_REPLY_ABORT, lambda data: self._api.abort_reply())
        self._register_rpc(protocol.CMD_TALK_START, self._rpc_talk_start)
        self._register_rpc(protocol.CMD_TALK_STOP, lambda data: self._api.stop_talk())
        # talk.audio：桌面全双工麦克风帧（fire-and-forget，无回执——50Hz 级
        # 小帧等回执会白白占满指令队列；帧校验与消费在 api/engine 侧完成）
        self.register_ws_handler(protocol.CMD_TALK_AUDIO, self._cmd_talk_audio)
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
        self._rpc_types.add(cmd_type)

    # ---- 需要参数加工/校验的指令 ----

    def _rpc_talk_start(self, data: dict) -> Any:
        """talk.start：透传 duplex 标记（桌面端全双工桥接，见 _cmd_talk_audio）。"""
        return self._api.start_talk(duplex=bool(data.get("duplex")))

    async def _cmd_talk_audio(self, ws: Any, data: dict) -> None:
        """talk.audio 指令：桌面全双工会话的麦克风帧（fire-and-forget，无回执）。

        base64 帧上限 64KB（100ms @16kHz PCM16 ≈ 3.2KB，余量充足）；非 duplex
        会话或帧非法时静默丢弃，不干扰半双工 PyAudio 路径。
        """
        payload = data.get("data")
        if not isinstance(payload, str) or not payload or len(payload) > 65536:
            return
        self._api.feed_talk_audio(payload)

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
        """models.select：切换文本模型（写盘 + 引擎热切换，与工作台一致）。

        写盘成功即入队 switch_model，引擎线程内串行替换运行中的 provider /
        QueryLoop，切换立即可用；落地推 model_switched、失败推 warn（事件回执）。

        @author aceFelix
        """
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("缺少模型名 name")
        return self._api.set_model(name)

    def _rpc_models_add(self, data: dict) -> dict:
        """models.add：添加/覆盖自定义模型（桌面壳左栏「添加模型」表单提交）。

        入表前完成字段校验：name 必填；api_format / model_type 限枚举值，
        避免写入引擎无法识别的配置（写盘与内存同步由 api.add_model 承担，
        与 REPL /models 添加其他模型同口径）。

        @author aceFelix
        """
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("缺少模型名 name")
        api_format = (data.get("api_format") or "openai").strip() or "openai"
        if api_format not in VALID_API_FORMATS:
            raise ValueError(f"不支持的接口类型 api_format: {api_format}")
        model_type = (data.get("model_type") or "text").strip() or "text"
        if model_type not in VALID_MODEL_TYPES:
            raise ValueError(f"不支持的模型类型 model_type: {model_type}")
        return self._api.add_model(
            name,
            vendor=(data.get("vendor") or "deepseek").strip() or "deepseek",
            api_format=api_format,
            base_url=(data.get("base_url") or "").strip(),
            api_key=(data.get("api_key") or "").strip(),
            model_type=model_type,
        )

    def _rpc_models_edit(self, data: dict) -> dict:
        """models.edit：修改模型配置（桌面壳左栏双击模型项 → 编辑表单 → 提交）。

        与 models.add 同族：同步写盘 + 同步内存 → reply 回执，前端拿到回执后
        刷新模型列表（不新增事件）。字段校验口径同 add：name 必填，
        api_format / model_type 非空时须落枚举；留空表示「沿用现值」由 api 层
        回填，因此这里不填默认值（否则会把用户没改的字段冲成默认）。

        两处语义需前端配合（见 api.edit_model / model_admin 模块说明）：
        - api_key 留空 = **保持原 Key 不变**（桌面壳不回显密钥，不能当清空用）；
        - 若改的是当前运行模型，api 层会入队带 force 的 switch_model，引擎强制
          按新配置重建 provider，端点/模型类型改动立即生效（回执 hot_switched）。

        @author aceFelix
        """
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("缺少模型名 name")
        api_format = (data.get("api_format") or "").strip()
        if api_format and api_format not in VALID_API_FORMATS:
            raise ValueError(f"不支持的接口类型 api_format: {api_format}")
        model_type = (data.get("model_type") or "").strip()
        if model_type and model_type not in VALID_MODEL_TYPES:
            raise ValueError(f"不支持的模型类型 model_type: {model_type}")
        return self._api.edit_model(
            name,
            new_name=(data.get("new_name") or "").strip(),
            vendor=(data.get("vendor") or "").strip(),
            api_format=api_format,
            base_url=(data.get("base_url") or "").strip(),
            api_key=(data.get("api_key") or "").strip(),
            model_type=model_type,
        )

    def _rpc_models_remove(self, data: dict) -> dict:
        """models.remove：删除自定义模型（桌面壳左栏右键模型项 → 删除按钮）。

        仅自定义模型可删（内置模型来自项目级 [llm.models]，删掉用户级覆盖段
        仍会留在列表里，api 层直接拒绝）；删成功即同步内存 custom_models，
        回执 {name, was_current}，was_current=true 时前端提示「当前仍在使用
        该模型，可另选一个」。删除不动运行中的 provider。

        @author aceFelix
        """
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("缺少模型名 name")
        return self._api.remove_model(name)

    def _rpc_voices_select(self, data: dict) -> dict:
        """voices.select：切换 TTS 音色并持久化（含模型联动）。

        回执为 {ok, name, voice_id, linked_model, old_model}：联动发生
        时 linked_model 非空，前端据此提示「已同步切换 TTS 模型」。

        @author aceFelix
        """
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("缺少音色名 name")
        return self._api.set_voice(name)

    def _rpc_voices_add(self, data: dict) -> dict:
        """voices.add：添加/覆盖自定义音色（桌面壳音色面板表单提交）。

        入表前校验 name / voice_id 必填；model 为空时由 api 层按家族
        默认模型兜底。落盘与内存同步由 api.add_voice 承担（同名 upsert
        即编辑复用，与 /tts-voice 新增音色表单同口径）。

        @author aceFelix
        """
        name = (data.get("name") or "").strip()
        voice_id = (data.get("voice_id") or "").strip()
        if not name or not voice_id:
            raise ValueError("音色名 name 与 voice_id 均必填")
        return self._api.add_voice({
            "name": name,
            "voice_id": voice_id,
            "model": (data.get("model") or "").strip(),
            "description": (data.get("description") or "").strip(),
            "vendor": (data.get("vendor") or "").strip(),
        })

    def _rpc_voices_delete(self, data: dict) -> dict:
        """voices.delete：删除自定义音色（桌面壳音色面板右键删除按钮）。

        仅自定义音色可删（内置音色来自 VOICE_CATALOG，api 层直接拒绝）；
        删成功即同步内存 custom_voices 并外科式移除 TOML 段。

        @author aceFelix
        """
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("缺少音色名 name")
        return self._api.delete_voice(name)

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
