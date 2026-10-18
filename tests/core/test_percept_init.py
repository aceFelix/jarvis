"""W2 感知层的配置注入与单例装配测试（`agent/core/percept/__init__.py`）。

这个模块是"配置 → 运行时"的唯一翻译点，也是最容易悄悄失效的一层：
字段名写错、默认值偏激进、单例不复位，都不会让源本身出错，只会让
面板上有个开关而背后压根没按配置走。所以这里逐条锁死：
安全缺省（全关）、字段翻译、留痕开关、单例重建与退出清理。
@author aceFelix
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.config.settings import Settings
from agent.core.percept import (
    SOURCE_ENABLE_KEYS,
    build_config,
    default_log_path,
    get_bus,
    init_percept,
    shutdown_percept,
    source_enabled_map,
)
from agent.core.percept.bus import BusConfig, PerceptBus, PollSource
from agent.core.percept.event import PerceptEvent


def test_defaults_are_all_off_without_settings():
    """未注入任何配置：总线能建、参数保守、一个源都不开、线程不起。"""
    cfg = build_config()
    assert isinstance(cfg, BusConfig)
    assert (cfg.dedup_window_s, cfg.history_max, cfg.auto_disable_after_errors) == (300, 200, 3)
    assert cfg.min_salience == 0.0                      # 地板分默认不裁，裁决在 W1
    assert cfg.throttle_s == {"vcs": 60, "mail": 0, "file": 0}
    assert source_enabled_map() == {name: False for name in SOURCE_ENABLE_KEYS}
    bus = get_bus()
    assert bus is get_bus()                             # 单例
    assert bus.start() is False and bus.running is False


def test_enable_keys_cover_all_eight_sources():
    """逐源开关的口径必须是八个源全集（面板据此渲染，少一个就是漏配）。"""
    assert sorted(SOURCE_ENABLE_KEYS) == sorted(
        ["window", "file", "vcs", "mail", "calendar", "system", "schedule", "vision"])


def test_total_switch_off_forces_every_source_off():
    """用户关掉总开关后，任何单个源都不该显示"正在观察"。"""
    init_percept(Settings(
        percept_enabled=False,
        percept_window_enabled=True, percept_mail_enabled=True,
    ))
    assert all(v is False for v in source_enabled_map().values())


def test_settings_fields_are_translated_into_bus_config():
    """`percept_*` 必须逐项落到 BusConfig，否则调参面板是摆设。"""
    init_percept(Settings(
        percept_enabled=True,
        percept_dedup_window_s=45,
        percept_min_salience=0.3,
        percept_history_max=10,
        percept_auto_disable_after_errors=5,
        percept_vcs_min_interval_s=70,
        percept_mail_min_interval_s=20,
        percept_file_min_interval_s=15,
        percept_window_enabled=True, percept_vcs_enabled=True,
    ))
    cfg = build_config()
    assert cfg.dedup_window_s == 45 and cfg.min_salience == 0.3
    assert cfg.history_max == 10 and cfg.auto_disable_after_errors == 5
    assert cfg.throttle_s == {"vcs": 70, "mail": 20, "file": 15}
    assert source_enabled_map()["window"] is True
    assert source_enabled_map()["vcs"] is True
    assert source_enabled_map()["mail"] is False         # 未打开的保持关


def test_partial_settings_fall_back_to_safe_defaults():
    """字段没登记（如旧 Settings / 别的宿主对象）也不能报错，取保守缺省。"""
    init_percept(SimpleNamespace(percept_enabled=True, percept_dedup_window_s=90))
    cfg = build_config()
    assert cfg.dedup_window_s == 90 and cfg.history_max == 200
    assert source_enabled_map()["window"] is False


def test_log_events_switch_decides_whether_disk_is_touched(monkeypatch, tmp_path):
    """`log_events=False` → 路径 None（完全不碰磁盘）；开则走 user_jarvis_dir 口径。"""
    import agent.core.memory.store as store

    monkeypatch.setattr(store, "user_jarvis_dir", lambda: tmp_path)
    init_percept(Settings(percept_log_events=False))
    assert default_log_path() is None
    init_percept(Settings(percept_log_events=True))
    path = default_log_path()
    assert path is not None and path.parts[-3:] == (tmp_path.name, "percept", "events.jsonl")
    assert not path.exists()                            # 只算路径，不创建文件


def test_reinit_rebuilds_singleton_with_new_config():
    """改配置后重新注入要重建总线（去重/节流这类参数本就该重启源）。"""
    first = init_percept(Settings(percept_dedup_window_s=100))
    assert first.config.dedup_window_s == 100
    second = init_percept(Settings(percept_dedup_window_s=30))
    assert second is not first and second.config.dedup_window_s == 30
    assert get_bus() is second


def test_explicit_bus_becomes_singleton():
    """宿主自装配（或测试）可直接指定实例，注入配置但不覆盖实例。"""
    mine = PerceptBus(BusConfig(dedup_window_s=7), log_path=None)
    assert init_percept(Settings(percept_enabled=True), bus=mine) is mine
    assert get_bus() is mine


def test_reinit_carries_over_observation_ledger(monkeypatch, tmp_path):
    """重建总线要接上观测账本：面板不能因为“改过一次配置”就空掉。

    2026-10-19 走查现场：用户保存配置后闸门计数全 0、最近事件空，而留痕
    文件已经写了 24 条——因为计数与历史都挂在被替换掉的旧实例上。
    """
    import agent.core.memory.store as store

    monkeypatch.setattr(store, "user_jarvis_dir", lambda: tmp_path)
    first = init_percept(Settings(percept_dedup_window_s=0))
    first.publish(PerceptEvent(source="vcs", kind="build_failed",
                               payload={"summary": "重建前采到的"}))
    second = init_percept(Settings(percept_dedup_window_s=30))
    assert second is not first
    assert second.stats()["counters"]["emit"] == 1
    assert [e.payload["summary"] for e in second.recent()] == ["重建前采到的"]


def test_explicit_bus_does_not_inherit_stale_counters(monkeypatch, tmp_path):
    """显式传 bus 时不继承：测试与自装配入口不该被陈旧计数污染。"""
    import agent.core.memory.store as store

    monkeypatch.setattr(store, "user_jarvis_dir", lambda: tmp_path)
    old = init_percept(Settings(percept_dedup_window_s=0))
    old.publish(PerceptEvent(source="vcs", kind="build_failed",
                             payload={"summary": "旧账本"}))
    mine = PerceptBus(BusConfig(), log_path=None)
    assert init_percept(Settings(percept_enabled=True), bus=mine) is mine
    assert mine.stats()["counters"]["emit"] == 0 and mine.recent() == []


def test_shutdown_stops_thread_and_closes_sources():
    """退出路径：轮询线程停干净、源的 close() 被调（IMAP 登出靠它）。"""
    closed: list[str] = []
    bus = PerceptBus(BusConfig(), log_path=None)
    bus.register_source(PollSource("window", lambda: [], interval_s=1), enabled=True)
    bus.subscribe(lambda ev: None)
    original = bus._sources["window"].source
    monkey_close = lambda: closed.append(original.name)  # noqa: E731 - 测试替身
    original.close = monkey_close
    init_percept(Settings(percept_enabled=True), bus=bus)
    assert bus.start() is True and bus.running is True
    shutdown_percept()
    assert bus.running is False and closed == ["window"]


def test_shutdown_without_bus_is_noop():
    """没装配过就退出（CLI 只跑一条命令的场景）不该抛。"""
    shutdown_percept()
    assert get_bus() is not None
