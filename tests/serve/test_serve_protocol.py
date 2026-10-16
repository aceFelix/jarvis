"""serve 协议契约测试。

覆盖：
- build_handshake 握手 JSON 结构（Electron 主进程解析契约）
- build_reply 回执信封（ok / 失败两路）
- DESKTOP_COMMANDS 与 DesktopBridgeServer 处理器表双向对应（防漏注册 / 防漏声明）
- proactive.ack RPC：hub 缺失/参数缺失报错、正常路径透传
- models.edit / models.remove RPC：缺 name / 非法枚举 / 名字不存在 → 不入库
- settings.get/set RPC：白名单全键取值、校验、先落盘后生效、调度键热更新、
  落盘失败不动运行时
- email.config.get/set RPC：读不回授权码明文、落盘后运行时生效、授权码留空
  保持原值、端口/类型校验、落盘失败不动运行时

@author aceFelix
"""

from __future__ import annotations

import asyncio
import queue

import pytest

from agent.config.settings import Settings
from agent.serve import protocol
from agent.serve import server as server_mod
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


# ---- reply.abort RPC ----

def test_reply_abort_routes_to_api():
    """reply.abort 已注册且透传 api.abort_reply（无进行中回复时 False）。"""
    server = _make_server()
    assert protocol.CMD_REPLY_ABORT in server._ws_handlers
    assert protocol.CMD_REPLY_ABORT in protocol.DESKTOP_COMMANDS
    # 引擎未启动/无 send 任务：abort_reply 返回 False（前端静默忽略）
    assert server._api.abort_reply() is False


def test_desktop_commands_count():
    """指令总数契约：message + 44 个 rpc + talk.audio（无回执帧通道）= 46。

    2026-09-28 音色-模型适配接入桌面壳：+voices.add / voices.delete（28→30）。
    2026 桌面项目工作区：+project.set / project.get / projects.list /
    projects.forget（30→34）。
    2026-09 桌面输入区工作模式/思考强度：+mode.set / think.set（34→36）。
    2026-10 跨设备协同桌面接入：+phone.connect/disconnect/status +
    wechat.connect/disconnect/status/pairing（36→43）。
    2026-10 消息级回溯（撤回+文件回滚）：+checkpoint.preview / checkpoint.rewind
    （43→45）。
    2026-10 斜杠命令透传：+slash.exec（45→46，实现见 ui/workbench/
    slash_bridge.py，结果走 slash_result 事件）；同月补全目录
    +slash.commands（46→47，只读返回桌面可执行命令列表）。
    2026-10 桌面管家服务：+email.send（47→48，标题栏「管家服务 → 邮件发送」
    表单直连 SendEmailTool，异步处理器 + to_thread，见 server._cmd_email_send）。
    2026-10 管家服务「邮箱服务」改版：+email.config.get / email.config.set
    （48→50，查看/修改当前发件邮箱与 SMTP 配置，读写 settings.toml 的 [email]
    表，授权码不回传明文、留空=保持，见 agent/config/email_settings.py）。
    2026-10 桌面扩展服务：+extensions.exec（50→51，一条通用指令承载五类扩展
    MCP/内置工具/技能/插件/harness 的清单与增删改启停，见 ui/workbench/
    extensions_api.py）。
    增减须同步双仓文档（jarvis-desktop 的 contracts.ts 与本文件口径）。
    """
    assert len(protocol.DESKTOP_COMMANDS) == 51


def test_all_registered_rpcs_declared():
    """反向防漏：WS 处理器表里注册的 RPC 都必须在 protocol 中声明。

    `test_all_desktop_commands_registered` 是单向校验（集合 → 处理器表）；
    反方向同样要防：只写了 `_register_rpc` 却忘记加入 `DESKTOP_COMMANDS`
    时，集合依然「合法」（条数断言也照旧通过），契约却少了一条指令 ——
    `models.add` 就长期漏在集合外，直到补 `models.edit` / `models.remove`
    时才被发现。

    @author aceFelix
    """
    server = _make_server()
    # message 走事件泵（_cmd_message 覆写内置路由）；talk.audio 是无回执的
    # 帧通道（register_ws_handler 直挂，50Hz 小帧不进 RPC 记账）—— 二者
    # 都不在 _rpc_types 内，从期望集合中扣除
    expected = set(protocol.DESKTOP_COMMANDS) - {
        protocol.CMD_MESSAGE,
        protocol.CMD_TALK_AUDIO,
    }
    assert server._rpc_types == expected, f"声明与注册不一致: {server._rpc_types ^ expected}"


def test_talk_audio_handler_registered():
    """talk.audio：已注册帧处理器且声明在指令集合（双仓契约，防单侧漏改）。"""
    server = _make_server()
    assert protocol.CMD_TALK_AUDIO in server._ws_handlers
    assert protocol.CMD_TALK_AUDIO in protocol.DESKTOP_COMMANDS


# ---- email.send RPC（管家服务 → 邮件发送） ----

def test_email_send_registered_and_declared():
    """email.send：已声明（DESKTOP_COMMANDS）且已注册进 WS 处理器表（双仓契约防单侧漏改）。"""
    server = _make_server()
    assert protocol.CMD_EMAIL_SEND in protocol.DESKTOP_COMMANDS
    assert protocol.CMD_EMAIL_SEND in server._ws_handlers


def test_email_send_empty_subject_or_body_rejected():
    """email.send：主题 / 正文留空 → 回执 ok=false（不进 SMTP，快速失败）。"""
    server = _make_server()
    sent: list[dict] = []

    async def _fake_send_json(ws, msg):
        sent.append(msg)

    # 用替身捕获回执（不真连 WS）；_cmd_email_send 校验未过时不会触碰 api/SMTP
    server._send_json = _fake_send_json  # type: ignore[assignment]

    asyncio.run(
        server._cmd_email_send(object(), {"to": "a@b.com", "subject": "  ", "body": "hi"})
    )
    assert sent[-1]["data"]["ok"] is False
    assert "主题" in sent[-1]["data"]["error"]

    asyncio.run(
        server._cmd_email_send(object(), {"to": "a@b.com", "subject": "s", "body": "  "})
    )
    assert sent[-1]["data"]["ok"] is False
    assert "正文" in sent[-1]["data"]["error"]


# ---- email.config.get / email.config.set RPC（管家服务 → 邮箱服务） ----

def test_email_config_handlers_registered_and_declared():
    """email.config.get/set：均声明在 DESKTOP_COMMANDS 且已注册进 WS 处理器表。"""
    server = _make_server()
    for cmd in (protocol.CMD_EMAIL_CONFIG_GET, protocol.CMD_EMAIL_CONFIG_SET):
        assert cmd in protocol.DESKTOP_COMMANDS
        assert cmd in server._ws_handlers


def test_email_config_get_hides_password():
    """email.config.get：返回配置快照但不含授权码明文，只回 has_password。"""
    server = _make_server()
    server._settings.email_smtp_user = "me@163.com"
    server._settings.email_smtp_password = "super-secret-code"
    result = server._rpc_email_config_get({})
    assert "smtp_password" not in result
    assert result["has_password"] is True
    assert result["smtp_user"] == "me@163.com"
    assert result["smtp_port"] == 465


def test_email_config_set_persist_then_runtime(monkeypatch):
    """email.config.set：先落盘成功后运行时 Settings 立即生效，回执回读快照。"""
    calls: list = []
    monkeypatch.setattr(
        server_mod, "save_email_config", lambda patch: calls.append(patch) or True
    )
    server = _make_server()
    result = server._rpc_email_config_set(
        {"enabled": True, "smtp_user": "me@qq.com", "smtp_password": "code123"}
    )
    assert calls == [{"enabled": True, "smtp_user": "me@qq.com", "smtp_password": "code123"}]
    assert server._settings.email_enabled is True
    assert server._settings.email_smtp_user == "me@qq.com"
    assert server._settings.email_smtp_password == "code123"
    assert result["has_password"] is True
    assert "smtp_password" not in result


def test_email_config_set_blank_password_keeps_original(monkeypatch):
    """授权码留空 = 保持不变（不写入补丁、不覆盖原值，明文不回传）。"""
    monkeypatch.setattr(server_mod, "save_email_config", lambda patch: True)
    server = _make_server()
    server._settings.email_smtp_password = "original-code"
    server._rpc_email_config_set({"smtp_password": "", "sender": "me@163.com"})
    assert server._settings.email_smtp_password == "original-code"
    assert server._settings.email_sender == "me@163.com"


def test_email_config_get_hides_imap_password_and_reports_readiness():
    """阶段 9 收件字段：imap_password 同样是 secret，就绪位按 host+账号+凭据判定。"""
    server = _make_server()
    server._settings.email_imap_host = "imap.163.com"
    server._settings.email_imap_user = "me@163.com"
    server._settings.email_imap_password = "imap-secret"
    result = server._rpc_email_config_get({})
    assert "imap_password" not in result
    assert result["imap_host"] == "imap.163.com"
    assert result["imap_port"] == 993
    assert result["has_imap_password"] is True
    assert result["imap_ready"] is True

    # 只配了 host：凭据缺失 → 未就绪（面板据此提示“还差授权码”）
    server._settings.email_imap_password = ""
    server._settings.email_smtp_password = ""
    server._settings.email_smtp_user = ""
    got = server._rpc_email_config_get({})
    assert got["has_imap_password"] is False and got["imap_ready"] is False


def test_email_config_imap_ready_falls_back_to_smtp_credentials():
    """163/QQ 就是一个授权码两边用：imap 账号/凭据留空时回退 SMTP，就绪位跟着算。"""
    server = _make_server()
    server._settings.email_imap_host = "imap.163.com"
    server._settings.email_smtp_user = "me@163.com"
    server._settings.email_smtp_password = "same-code"
    result = server._rpc_email_config_get({})
    assert result["has_imap_password"] is True
    assert result["imap_ready"] is True
    assert "smtp_password" not in result and "imap_password" not in result


def test_email_config_set_imap_password_semantics(monkeypatch):
    """写侧同样守 secret 口径：留空=保持原值，非空才落盘；imap_port 要过区间校验。"""
    calls: list = []
    monkeypatch.setattr(
        server_mod, "save_email_config", lambda patch: calls.append(patch) or True
    )
    server = _make_server()
    server._settings.email_imap_password = "original-imap"
    result = server._rpc_email_config_set({"imap_password": "", "imap_host": "imap.qq.com"})
    assert calls[-1] == {"imap_host": "imap.qq.com"}      # 空授权码不写补丁
    assert server._settings.email_imap_password == "original-imap"
    assert server._settings.email_imap_host == "imap.qq.com"
    assert "imap_password" not in result
    with pytest.raises(ValueError):
        server._rpc_email_config_set({"imap_port": 70000})


def test_email_config_set_validation(monkeypatch):
    """端口超范围 / 类型不符 → ValueError；无有效字段（全空补丁）→ ValueError。"""
    monkeypatch.setattr(server_mod, "save_email_config", lambda patch: True)
    server = _make_server()
    with pytest.raises(ValueError):
        server._rpc_email_config_set({"smtp_port": 0})
    with pytest.raises(ValueError):
        server._rpc_email_config_set({"smtp_port": 70000})
    with pytest.raises(ValueError):
        server._rpc_email_config_set({"enabled": "yes"})
    # 只有空授权码：视为无有效修改
    with pytest.raises(ValueError):
        server._rpc_email_config_set({"smtp_password": ""})


def test_email_config_set_persist_failure_keeps_runtime(monkeypatch):
    """落盘失败 → RuntimeError 且运行时值不变（避免本次生效重启回退分裂）。"""
    monkeypatch.setattr(server_mod, "save_email_config", lambda patch: False)
    server = _make_server()
    with pytest.raises(RuntimeError):
        server._rpc_email_config_set({"smtp_user": "me@163.com"})
    assert server._settings.email_smtp_user == ""


# ---- models.edit / models.remove RPC ----

def test_models_edit_field_validation():
    """models.edit：缺 name / 非法枚举 → ValueError（回执 ok=false，不写盘）。

    与 models.add 同口径，但 api_format / model_type 允许留空（留空 = 沿用
    现值），所以只有非空值才落枚举校验。
    """
    server = _make_server()
    with pytest.raises(ValueError):
        server._rpc_models_edit({})
    with pytest.raises(ValueError):
        server._rpc_models_edit({"name": "   "})
    with pytest.raises(ValueError):
        server._rpc_models_edit({"name": "m1", "api_format": "grpc"})
    with pytest.raises(ValueError):
        server._rpc_models_edit({"name": "m1", "model_type": "audio"})


def test_models_edit_unknown_model_raises():
    """models.edit：名字既不在内置表也不在自定义表 → ValueError（不凭空造模型）。"""
    server = _make_server()
    with pytest.raises(ValueError):
        server._rpc_models_edit({"name": "__no_such_model_probe__"})


def test_models_remove_field_validation():
    """models.remove：缺 name → ValueError；内置/不存在的模型 → ValueError。"""
    server = _make_server()
    with pytest.raises(ValueError):
        server._rpc_models_remove({})
    with pytest.raises(ValueError):
        server._rpc_models_remove({"name": "   "})
    with pytest.raises(ValueError):
        server._rpc_models_remove({"name": "__no_such_model_probe__"})


# ---- proactive.ack RPC ----

class _StubHub:
    """ProactiveHub 替身：记录 acknowledge 调用。"""

    def __init__(self):
        self.acked: list[str] = []

    def acknowledge(self, task_id: str) -> bool:
        self.acked.append(task_id)
        return True


def _make_server_with_hub(hub):
    settings = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)
    return DesktopBridgeServer(api, settings, hub=hub)


def test_proactive_ack_routes_to_hub():
    """正常路径：task_id 透传 hub.acknowledge，返回 True。"""
    hub = _StubHub()
    server = _make_server_with_hub(hub)
    assert server._rpc_proactive_ack({"task_id": "abc123"}) is True
    assert hub.acked == ["abc123"]


def test_proactive_ack_missing_task_id_raises():
    """缺 task_id / 空白 task_id → ValueError（回执 ok=false）。"""
    server = _make_server_with_hub(_StubHub())
    with pytest.raises(ValueError):
        server._rpc_proactive_ack({})
    with pytest.raises(ValueError):
        server._rpc_proactive_ack({"task_id": "   "})


def test_proactive_ack_without_hub_raises():
    """hub 未装配 → RuntimeError（协议向后兼容，连接不中断）。"""
    server = _make_server()  # 无 hub
    with pytest.raises(RuntimeError):
        server._rpc_proactive_ack({"task_id": "abc123"})


# ---- settings.get / settings.set RPC ----

class _StubSettingsHub(_StubHub):
    """带 hot_update_schedule 的 hub 替身：记录调度热更新触发次数。"""

    def __init__(self):
        super().__init__()
        self.hot_updates = 0

    def hot_update_schedule(self):
        self.hot_updates += 1


def test_settings_get_returns_all_whitelist_keys():
    """settings.get 返回全部白名单键的运行时值（Settings 默认值）。"""
    server = _make_server()
    result = server._rpc_settings_get({})
    assert set(result) == {
        "proactive_tts_enabled", "briefing_enabled", "briefing_time",
        "deadline_enabled", "deadline_check_time", "tts_volume", "tts_speech_rate",
    }
    assert result["proactive_tts_enabled"] is True
    assert result["briefing_time"] == "08:30"
    assert result["tts_volume"] == 50
    assert result["tts_speech_rate"] == 1.0


def test_settings_set_persist_then_runtime(monkeypatch):
    """settings.set：先落盘成功后运行时 Settings 立即生效（hub 每次播报现读）。"""
    calls: list = []
    monkeypatch.setattr(
        server_mod, "save_setting", lambda spec, v: calls.append((spec.key, v)) or True
    )
    server = _make_server()
    result = server._rpc_settings_set({"proactive_tts_enabled": False})
    assert result == {"proactive_tts_enabled": False}
    assert calls == [("proactive_tts_enabled", False)]
    assert server._settings.proactive_tts_enabled is False


def test_settings_set_new_keys(monkeypatch):
    """第一批新键：简报时间 / TTS 音量 / 语速——落盘 + 运行时同步。"""
    monkeypatch.setattr(server_mod, "save_setting", lambda spec, v: True)
    server = _make_server()
    assert server._rpc_settings_set({"briefing_time": "07:15"}) == {"briefing_time": "07:15"}
    assert server._settings.briefing_time == "07:15"
    assert server._rpc_settings_set({"tts_volume": 80}) == {"tts_volume": 80}
    assert server._settings.tts_volume == 80
    assert server._rpc_settings_set({"tts_speech_rate": 1.25}) == {"tts_speech_rate": 1.25}
    assert server._settings.tts_speech_rate == 1.25


def test_settings_set_schedule_key_triggers_hot_update(monkeypatch):
    """调度键（briefing/deadline）改动触发 hub.hot_update_schedule 重注册；
    非调度键（tts_volume）不触发。"""
    monkeypatch.setattr(server_mod, "save_setting", lambda spec, v: True)
    hub = _StubSettingsHub()
    server = _make_server_with_hub(hub)
    server._rpc_settings_set({"briefing_enabled": False})
    assert hub.hot_updates == 1
    server._rpc_settings_set({"tts_volume": 60})
    assert hub.hot_updates == 1  # 非调度键不重注册


def test_settings_set_validation(monkeypatch):
    """缺键 / 多键 / 非白名单键 / 类型不符 / 超范围 / 时间格式错 → ValueError。"""
    monkeypatch.setattr(server_mod, "save_setting", lambda spec, v: True)
    server = _make_server()
    with pytest.raises(ValueError):
        server._rpc_settings_set({})
    with pytest.raises(ValueError):
        server._rpc_settings_set({"tts_volume": 10, "briefing_enabled": True})
    with pytest.raises(ValueError):
        server._rpc_settings_set({"not_in_whitelist": 1})
    with pytest.raises(ValueError):
        server._rpc_settings_set({"proactive_tts_enabled": "yes"})
    with pytest.raises(ValueError):
        server._rpc_settings_set({"tts_volume": 101})
    with pytest.raises(ValueError):
        server._rpc_settings_set({"briefing_time": "25:00"})


def test_settings_set_persist_failure_keeps_runtime(monkeypatch):
    """落盘失败 → RuntimeError 且运行时值不变（避免本次生效重启回退分裂）。"""
    monkeypatch.setattr(server_mod, "save_setting", lambda spec, v: False)
    server = _make_server()
    with pytest.raises(RuntimeError):
        server._rpc_settings_set({"proactive_tts_enabled": False})
    assert server._settings.proactive_tts_enabled is True
    with pytest.raises(RuntimeError):
        server._rpc_settings_set({"briefing_time": "07:00"})
    assert server._settings.briefing_time == "08:30"


# ---- 桌面项目工作区 project.* RPC ----

def test_project_rpc_handlers_registered():
    """project.set/get + projects.list/forget 均已注册且声明在指令集合。"""
    server = _make_server()
    for cmd in (
        protocol.CMD_PROJECT_SET,
        protocol.CMD_PROJECT_GET,
        protocol.CMD_PROJECTS_LIST,
        protocol.CMD_PROJECTS_FORGET,
    ):
        assert cmd in server._ws_handlers
        assert cmd in protocol.DESKTOP_COMMANDS


def test_project_set_valid_path_enqueues(tmp_path):
    """合法绝对目录：入队 set_workdir 并回执受理 {ok, workdir, name}。"""
    server = _make_server()
    proj = tmp_path / "demo"
    proj.mkdir()
    result = server._rpc_project_set({"path": str(proj)})
    assert result == {"ok": True, "workdir": str(proj), "name": "demo"}
    # 入队而非同步生效：命令队列尾部应为 set_workdir
    cmd = server._api._command_queue.get_nowait()
    assert cmd == {"cmd": "set_workdir", "path": str(proj)}


def test_project_set_rejects_invalid_paths(tmp_path):
    """缺 path / 空 / 相对路径 / 不存在目录 → ValueError（回执 ok=false，不入队）。"""
    server = _make_server()
    missing = tmp_path / "nope"
    for bad in ({}, {"path": "   "}, {"path": "relative/dir"}, {"path": str(missing)}):
        with pytest.raises(ValueError):
            server._rpc_project_set(bad)
    assert server._api._command_queue.empty()


def test_projects_forget_missing_path_raises():
    """projects.forget：缺 path / 空 path → ValueError。"""
    server = _make_server()
    with pytest.raises(ValueError):
        server._rpc_projects_forget({})
    with pytest.raises(ValueError):
        server._rpc_projects_forget({"path": "   "})
