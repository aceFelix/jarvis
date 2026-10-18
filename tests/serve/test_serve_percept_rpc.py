"""percept.* 桌面 RPC（W2 第 5 步）—— 感知源面板的两条指令。

覆盖：注册与声明齐备（双仓契约）、无宿主时的降级读数、状态快照透传总线统计、
状态/写侧回执附带的 `config` 可编辑配置段、写侧的白名单校验（未知键报错 /
空补丁报错）、**先落盘后生效**（落盘失败不动运行时也不重装配）、重装配抛异常只
降级不吞掉已写入的配置、列表端到端规范化、总开关表态写进询问记录（第 7 步）。

配置落盘由 tests/serve/conftest.py 重定向到 tmp_path/settings.toml，绝不写真实
用户配置。

@author aceFelix
"""

from __future__ import annotations

import tomllib

import pytest

import agent.core.percept.consent as consent_mod
from agent.config.settings import Settings
from agent.core.percept import PerceptBus, PollSource
from agent.core.percept.bus import BusConfig
from agent.core.percept.event import PerceptEvent
from agent.serve import protocol
from agent.serve import rpc_percept as percept_rpc_mod
from tests.serve.support import make_desktop_server


class _StubHub:
    """ProactiveHub 替身：只暴露 percept.* RPC 用到的两个口子。"""

    def __init__(self, bus=None) -> None:
        self._bus = bus
        self.hot_calls = 0

    @property
    def percept_bus(self):
        return self._bus

    def hot_update_percept(self) -> dict[str, bool]:
        self.hot_calls += 1
        return {}


class _BoomHub(_StubHub):
    """重装配抛异常的宿主（配置已落盘，RPC 必须只降级不炸）。"""

    def hot_update_percept(self) -> dict[str, bool]:
        self.hot_calls += 1
        raise RuntimeError("总线装配失败")


class _MinimalHub:
    """没有 hot_update_percept 的老宿主（协议向后兼容路径）。"""

    percept_bus = None


def _bus(tmp_path, *, with_event=True):
    bus = PerceptBus(BusConfig(dedup_window_s=300), log_path=tmp_path / "events.jsonl")
    bus.register_source(PollSource("window", lambda: None, interval_s=5), enabled=True)
    if with_event:
        bus.publish(
            PerceptEvent(source="window", kind="app_switched",
                         payload={"summary": "切到 Chrome"}, dedup_key="w-1"),
            now=1000.0,
        )
    return bus


# ---- 契约：注册 + 声明 ----

def test_percept_commands_registered_and_declared():
    """两条指令都已声明在 DESKTOP_COMMANDS 且已注册进 WS 处理器表。"""
    server, _eq, _cq = make_desktop_server()
    for cmd in (protocol.CMD_PERCEPT_STATUS_GET, protocol.CMD_PERCEPT_CONFIG_SET):
        assert cmd in protocol.DESKTOP_COMMANDS
        assert cmd in server._ws_handlers


# ---- percept.status.get ----

def test_status_get_without_hub_is_readable_not_error():
    """hub=None（非 serve 场景）时照常返回快照：面板拿到的是"未装配"，不是报错。"""
    server, _eq, _cq = make_desktop_server()
    result = server._rpc_percept_status_get({})
    assert result["assembled"] is False
    assert result["enabled"] is False
    assert result["recent_events"] == []
    assert {s["name"] for s in result["sources"]} == {
        "window", "file", "vcs", "mail", "calendar", "system", "schedule", "vision",
    }


def test_status_get_passes_bus_stats_and_event_limit(tmp_path):
    """有总线时透传计数/注册情况/留痕路径，event_limit 生效。"""
    hub = _StubHub(_bus(tmp_path))
    server, _eq, _cq = make_desktop_server(hub=hub)
    result = server._rpc_percept_status_get({"event_limit": 1})
    assert result["assembled"] is True
    assert result["counters"]["emit"] == 1
    assert result["log_file"].endswith("events.jsonl")
    window = next(s for s in result["sources"] if s["name"] == "window")
    assert window["registered"] is True and window["bus_enabled"] is True
    assert result["recent_events"][0]["payload"]["summary"] == "切到 Chrome"

    zero = server._rpc_percept_status_get({"event_limit": 0})   # 非法值走默认上限
    assert len(zero["recent_events"]) == 1


def test_status_get_reports_config_enabled_even_when_not_assembled():
    """配置开了但宿主没接线：enabled=True 与 registered=False 同时可见（不假装在采）。"""
    settings = Settings(percept_enabled=True, percept_file_enabled=True)
    server, _eq, _cq = make_desktop_server(settings)
    result = server._rpc_percept_status_get({})
    item = next(s for s in result["sources"] if s["name"] == "file")
    assert item["enabled"] is True and item["registered"] is False


def test_status_get_carries_editable_config_segment():
    """状态里必须带 `config` 段：逐源 enabled 只是 AND 后的结论，面板的名单与
    阈值编辑表单要靠原始配置值回填（否则一打开就是空框，用户会重复粘贴）。"""
    settings = Settings(
        percept_enabled=True,
        percept_file_watch_dirs=["E:/Downloads"],
        percept_window_linger_minutes=25,
    )
    server, _eq, _cq = make_desktop_server(settings)
    config = server._rpc_percept_status_get({})["config"]
    assert config["enabled"] is True
    assert config["file_watch_dirs"] == ["E:/Downloads"]
    assert config["window_linger_minutes"] == 25
    assert config["log_events"] is True          # 出厂缺省（没配过也不误显成已关）
    assert config["vcs_repos"] == []


def test_status_get_carries_notify_budget(monkeypatch):
    """状态里带 W1 打扰预算：面板要能回答“采到了为什么不说”。

    读侧永不抛异常是底线：预算拿不到（未启用 / 状态文件坏了 / 单例初始化炸）
    就当没有，不能为了可观测性把整个 status.get 带倒。
    """
    import agent.core.policy as policy_mod

    class _Budget:
        def stats(self):
            return {"count": 3, "daily_max": 10, "pending": 2}

    monkeypatch.setattr(policy_mod, "get_budget", lambda: _Budget())
    server, _eq, _cq = make_desktop_server()
    result = server._rpc_percept_status_get({})
    assert result["notify_budget"] == {"count": 3, "daily_max": 10, "pending": 2}

    def boom():
        raise RuntimeError("预算初始化失败")

    monkeypatch.setattr(policy_mod, "get_budget", boom)
    assert server._rpc_percept_status_get({})["notify_budget"] == {}


def test_config_set_result_carries_updated_config_segment(tmp_path):
    """写侧回读同样带 `config` 段，面板一次调用就能把表单与状态一起刷新。"""
    server, _eq, _cq = make_desktop_server()
    result = server._rpc_percept_config_set({
        "vcs_repos": [" E:/repo/a ", "E:/repo/a"],
        "vision_auto_stop_minutes": 10,
    })
    assert result["config"]["vcs_repos"] == ["E:/repo/a"]
    assert result["config"]["vision_auto_stop_minutes"] == 10
    assert (tmp_path / "settings.toml").exists()


# ---- percept.config.set：校验 ----

def test_config_set_rejects_unknown_key(tmp_path):
    """白名单外的键（极客参数）直接报错，且不碰磁盘。"""
    server, _eq, _cq = make_desktop_server()
    with pytest.raises(ValueError, match="不支持"):
        server._rpc_percept_config_set({"dedup_window_s": 10})
    assert not (tmp_path / "settings.toml").exists()


def test_config_set_accepts_real_transport_frame(tmp_path):
    """真实客户端的平铺请求帧（带协议信封键 `type`）必须写通。

    回归 2026-10 人工走查发现的「感知源面板所有开关点了没反应」：桌面壳
    `sendCommand` 发的是 `{type: cmd, ...params}`，分发时整帧进 handler，信封键
    混进白名单校验会让每一条写回都报错——旧用例直传业务 dict，所以没测出来。
    """
    hub = _StubHub()
    settings = Settings(percept_enabled=False)
    server, _eq, _cq = make_desktop_server(settings, hub=hub)

    result = server._rpc_percept_config_set({
        "type": "percept.config.set", "enabled": True, "window_enabled": True,
    })

    on_disk = tomllib.loads((tmp_path / "settings.toml").read_text(encoding="utf-8"))
    assert on_disk["percept"] == {"enabled": True, "window_enabled": True}
    assert result["applied"] == {"enabled": True, "window_enabled": True}


def test_config_set_envelope_does_not_hide_unknown_key(tmp_path):
    """剥信封不等于放宽白名单：混在帧里的未知业务键照样报错且不落盘。"""
    server, _eq, _cq = make_desktop_server()
    with pytest.raises(ValueError, match="不支持"):
        server._rpc_percept_config_set(
            {"type": "percept.config.set", "dedup_window_s": 10}
        )
    assert not (tmp_path / "settings.toml").exists()


def test_config_set_envelope_only_frame_still_rejected():
    """只带信封、不带任何配置项的帧，仍按「至少需要一个有效配置项」报错。"""
    server, _eq, _cq = make_desktop_server()
    with pytest.raises(ValueError, match="至少"):
        server._rpc_percept_config_set({"type": "percept.config.set"})


def test_config_set_requires_at_least_one_field():
    """空补丁（或全是无效键）报错，避免"点了保存但什么都没改"的静默成功。"""
    server, _eq, _cq = make_desktop_server()
    with pytest.raises(ValueError):
        server._rpc_percept_config_set({})


def test_config_set_rejects_wrong_types():
    """类型不符一律报错：bool 收字符串、int 收浮点都不放行。"""
    server, _eq, _cq = make_desktop_server()
    with pytest.raises(ValueError):
        server._rpc_percept_config_set({"enabled": "yes"})
    with pytest.raises(ValueError):
        server._rpc_percept_config_set({"window_linger_minutes": 1.5})


# ---- percept.config.set：落盘 → 生效 → 重装配 ----

def test_config_set_persist_then_runtime_then_rewire(tmp_path):
    """成功路径的完整顺序：写 TOML → 改运行时 → 让宿主重装配 → 回读快照。"""
    hub = _StubHub()
    settings = Settings(percept_enabled=False)
    server, _eq, _cq = make_desktop_server(settings, hub=hub)

    result = server._rpc_percept_config_set({"enabled": True, "window_enabled": True})

    on_disk = tomllib.loads((tmp_path / "settings.toml").read_text(encoding="utf-8"))
    assert on_disk["percept"] == {"enabled": True, "window_enabled": True}
    assert settings.percept_enabled is True
    assert settings.percept_window_enabled is True
    assert hub.hot_calls == 1                       # 改完立刻重装配，不需重启
    assert result["hot_applied"] is True
    assert result["applied"] == {"enabled": True, "window_enabled": True}
    assert result["enabled"] is True
    window = next(s for s in result["sources"] if s["name"] == "window")
    assert window["enabled"] is True


def test_config_set_normalizes_list_end_to_end(tmp_path):
    """名单类字段：去重、丢空项、strip 后同时进 TOML 与运行时。"""
    server, _eq, _cq = make_desktop_server()
    server._rpc_percept_config_set({
        "file_watch_dirs": [" E:/Downloads ", "E:/Downloads", "", "E:/Desktop"],
    })
    assert server._settings.percept_file_watch_dirs == ["E:/Downloads", "E:/Desktop"]
    on_disk = tomllib.loads((tmp_path / "settings.toml").read_text(encoding="utf-8"))
    assert on_disk["percept"]["file_watch_dirs"] == ["E:/Downloads", "E:/Desktop"]


def test_config_set_persist_failure_keeps_runtime(monkeypatch):
    """落盘失败 → RuntimeError，运行时不动、也不重装配（避免口径分裂）。"""
    hub = _StubHub()
    settings = Settings(percept_enabled=False)
    server, _eq, _cq = make_desktop_server(settings, hub=hub)
    monkeypatch.setattr(percept_rpc_mod, "save_percept_config", lambda patch: False)

    with pytest.raises(RuntimeError):
        server._rpc_percept_config_set({"enabled": True})

    assert settings.percept_enabled is False
    assert hub.hot_calls == 0


def test_config_set_without_hub_reports_not_hot_applied(monkeypatch):
    """老宿主没有 hot_update_percept：配置照样落盘生效，但如实回报"还没重装配"。"""
    server, _eq, _cq = make_desktop_server(hub=_MinimalHub())
    monkeypatch.setattr(percept_rpc_mod, "save_percept_config", lambda patch: True)
    result = server._rpc_percept_config_set({"vcs_enabled": True})
    assert result["hot_applied"] is False
    assert server._settings.percept_vcs_enabled is True


def test_config_set_rewire_failure_still_persists(monkeypatch):
    """重装配抛异常不能反过来说"配置没写进去"：落盘与运行时都完成，只降级回报。"""
    hub = _BoomHub()
    server, _eq, _cq = make_desktop_server(hub=hub)
    monkeypatch.setattr(percept_rpc_mod, "save_percept_config", lambda patch: True)

    result = server._rpc_percept_config_set({"mail_enabled": True})

    assert hub.hot_calls == 1
    assert result["hot_applied"] is False
    assert server._settings.percept_mail_enabled is True


# ---- 总开关表态落进询问记录（W2 第 7 步的首次引导据此不再出现）----

def test_config_set_records_master_choice():
    """拨总开关 = 对感知层表态，写进 consent（on/off 两档都记）。"""
    server, _eq, _cq = make_desktop_server(Settings(percept_enabled=False))
    server._rpc_percept_config_set({"enabled": True})
    assert consent_mod.read_consent()["choice"] == "on"
    # 明天关掉也算表过态：不会因为“现在又关了”就重新弹一次引导
    server._rpc_percept_config_set({"enabled": False})
    assert consent_mod.read_consent()["choice"] == "off"


def test_config_set_without_enabled_records_no_choice():
    """只改名单/逐源开关不写表态（用户没对总开关做过选择）。"""
    server, _eq, _cq = make_desktop_server()
    server._rpc_percept_config_set({"window_enabled": True})
    assert consent_mod.read_consent() is None


def test_choice_record_failure_does_not_break_save(monkeypatch):
    """表态留档失败不影响本次保存（配置已落盘已生效，记账只是附带）。"""
    server, _eq, _cq = make_desktop_server()
    monkeypatch.setattr(
        consent_mod, "record_choice",
        lambda choice: (_ for _ in ()).throw(OSError("磁盘只读")),
    )
    result = server._rpc_percept_config_set({"enabled": True})
    assert result["enabled"] is True
    assert server._settings.percept_enabled is True
