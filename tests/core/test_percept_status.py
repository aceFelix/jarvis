"""W2 感知层状态快照（agent/core/percept/status.py）—— 面板数据源的拼装口径。

这里验的是"面板看到的形状"：总开关如何压住逐源、已实现源排在前面、未装配收编位
带理由而不给开关、没有总线时读数段返回空而不抛、事件条数受上限约束、源自报细节
原样透出。总线本身的裁决在 tests/core/test_percept_bus.py，RPC 层在
tests/serve/test_serve_percept_rpc.py。

@author aceFelix
"""

from __future__ import annotations

from agent.config.settings import Settings
from agent.core.percept import PerceptBus, PollSource
from agent.core.percept.bus import BusConfig
from agent.core.percept.event import ALL_SOURCES
from agent.core.percept.factory import IMPLEMENTED_SOURCES, PENDING_SOURCES
from agent.core.percept.status import (
    EVENT_LIMIT,
    SOURCE_HINTS,
    SOURCE_LABELS,
    build_status,
)


class _DetailSource:
    """带 state() 自报的源桩（PollSource 没有 state，用它模拟视觉/邮件源）。"""

    name = "vision"
    interval_s = 5.0

    def collect(self):  # noqa: D102 - 总线不轮询这个源
        return []

    def state(self):
        return {"running": False, "unavailable": "缺少视觉依赖（pip install ...）"}


def _settings(**over) -> Settings:
    base = dict(percept_enabled=False, percept_log_events=True)
    base.update(over)
    return Settings(**base)


def _bus(tmp_path, *, sources=()):
    bus = PerceptBus(BusConfig(dedup_window_s=300), log_path=tmp_path / "events.jsonl")
    for src in sources:
        bus.register_source(src)
    return bus


# ---- 逐源 enabled 的推导 ----

def test_master_switch_off_forces_every_source_off(tmp_path):
    """总开关关着时，任何单源都不该显示"正在观察"（与 factory 的 AND 口径一致）。"""
    settings = _settings(
        percept_enabled=False, percept_window_enabled=True, percept_vcs_enabled=True
    )
    status = build_status(settings, _bus(tmp_path))
    assert status["enabled"] is False
    by_name = {s["name"]: s for s in status["sources"]}
    assert by_name["window"]["enabled"] is False
    assert by_name["vcs"]["enabled"] is False


def test_per_source_enabled_only_when_master_on(tmp_path):
    """总开关开 + 逐源开 → 该源 enabled=True，其余仍 False（逐源默认关）。"""
    settings = _settings(percept_enabled=True, percept_mail_enabled=True)
    status = build_status(settings, _bus(tmp_path))
    by_name = {s["name"]: s for s in status["sources"]}
    assert by_name["mail"]["enabled"] is True
    assert by_name["window"]["enabled"] is False


# ---- 列表形状与排布 ----

def test_all_sources_listed_once_with_labels(tmp_path):
    """逐源一律列全（含未装配的收编位），且都有中文标签与说明。"""
    status = build_status(_settings(), _bus(tmp_path))
    names = [s["name"] for s in status["sources"]]
    assert sorted(names) == sorted(ALL_SOURCES)
    for name in IMPLEMENTED_SOURCES:
        assert SOURCE_LABELS[name] and SOURCE_HINTS[name]


def test_implemented_sources_come_before_pending_ones(tmp_path):
    """已实现的排前面（面板主列表），收编位灰显在后面并带"为什么没接"的理由。"""
    status = build_status(_settings(), _bus(tmp_path))
    first = {s["name"] for s in status["sources"][: len(IMPLEMENTED_SOURCES)]}
    assert first == set(IMPLEMENTED_SOURCES)
    for item in status["sources"]:
        if item["name"] in PENDING_SOURCES:
            assert item["implemented"] is False and item["pending_reason"]
        else:
            assert "pending_reason" not in item


# ---- 没有总线时的降级 ----

def test_status_without_bus_still_shows_config(tmp_path):
    """bus=None（终端 REPL / 总开关关）：配置段照常，统计与事件段为空，绝不抛。"""
    status = build_status(_settings(percept_enabled=True, percept_file_enabled=True), None)
    assert status["assembled"] is False
    assert status["bus_running"] is False
    assert status["counters"] == {}
    assert status["recent_events"] == []
    assert status["log_file"] == ""
    file_item = next(s for s in status["sources"] if s["name"] == "file")
    assert file_item["enabled"] is True          # 配置说开了
    assert file_item["registered"] is False      # 但总线里没有这个源


# ---- 有总线时透出统计、事件与自报细节 ----

def test_status_passes_bus_counters_and_event_limit(tmp_path):
    """计数与最近事件来自总线；event_limit 受 EVENT_LIMIT 上限约束。"""
    bus = _bus(tmp_path, sources=[PollSource("vcs", lambda: None, interval_s=60)])
    for i in range(5):
        bus.publish(
            _event(f"vcs-evt-{i}"), now=1000.0 + i,
        )
    status = build_status(_settings(percept_enabled=True), bus, event_limit=3)
    assert len(status["recent_events"]) == 3
    assert status["counters"]["emit"] == 5
    assert status["log_file"].endswith("events.jsonl")
    assert status["dedup_window_s"] == 300

    clamped = build_status(_settings(percept_enabled=True), bus, event_limit=EVENT_LIMIT + 999)
    assert len(clamped["recent_events"]) == 5      # 实际只有 5 条，上限不硬凑


def _event(tag: str):
    from agent.core.percept.event import PerceptEvent

    return PerceptEvent(source="vcs", kind="build_failed", payload={"summary": tag},
                        dedup_key=tag)


def test_status_carries_source_self_reported_detail(tmp_path):
    """源自报的"为什么没采到"要出现在该源的 detail 里（面板据此解释空结果）。"""
    bus = _bus(tmp_path, sources=[_DetailSource()])
    status = build_status(_settings(percept_enabled=True, percept_vision_enabled=True), bus)
    vision = next(s for s in status["sources"] if s["name"] == "vision")
    assert vision["detail"]["unavailable"].startswith("缺少视觉依赖")
    assert vision["registered"] is True
    assert vision["bus_enabled"] is False      # 没调 set_source_enabled，逐源默认关


def test_status_reports_assembled_from_hub_flag(tmp_path):
    """hub_enabled 显式传入时以其为准（区分"宿主接了线但总线还没起"）。"""
    status = build_status(_settings(), None, hub_enabled=True)
    assert status["assembled"] is True
    assert status["enabled"] is False
    assert status["log_events"] is True
