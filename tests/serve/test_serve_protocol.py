"""serve 协议契约测试。

覆盖：
- build_handshake 握手 JSON 结构（Electron 主进程解析契约）
- build_reply 回执信封（ok / 失败两路）
- DESKTOP_COMMANDS 与 DesktopBridgeServer 处理器表双向对应（防漏注册 / 防漏声明）
- proactive.ack RPC：hub 缺失/参数缺失报错、正常路径透传
- models.edit / models.remove RPC：缺 name / 非法枚举 / 名字不存在 → 不入库
- settings.get/set RPC：白名单全键取值、校验、先落盘后生效、调度键热更新、
  落盘失败不动运行时

@author aceFelix
"""

from __future__ import annotations

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
    """指令总数契约：message + 26 个 rpc + talk.audio（无回执帧通道）= 28。

    增减须同步双仓文档（jarvis-desktop 的 contracts.ts 与本文件口径）。
    """
    assert len(protocol.DESKTOP_COMMANDS) == 28


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
