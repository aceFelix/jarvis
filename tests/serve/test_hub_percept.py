"""serve 宿主接线（W2 第 4 步）—— ProactiveHub × 感知总线的装配与投递路径。

这里验的是**接线本身**：总开关决定装不装配、感知事件必须过 W1 同一个闸门、
在场信号只改状态不占播报额度、停机要退订并释放源。闸门内部的裁决在
tests/policy/test_notify_budget.py，源自身的采集在 tests/core/test_percept_*。

预算闸门用桩替掉 `engine._budget`：一是避免测试读写真实 `~/.jarvis/notify_state.json`，
二是这里关心的是"hub 有没有把正确的 source/dedup_key/urgency 交给闸门、
按闸门的结论决定投不投事件"，而不是阈值算法本身。

@author aceFelix
"""

from __future__ import annotations

import queue

import pytest

import agent.core.percept as percept_mod
import agent.core.percept.consent as consent_mod
from agent.config.settings import Settings
from agent.core.percept.event import PerceptEvent
from agent.core.policy import get_policy_state
from agent.core.policy.budget import NotifyAction
from agent.serve import protocol
from agent.serve.hub import (
    PERCEPT_ASK_KIND,
    PERCEPT_ASK_TEXT,
    PERCEPT_ASK_TITLE,
    PERCEPT_NOTIFY_KIND,
    PERCEPT_SILENT_KINDS,
    ProactiveHub,
)


class _StubBudget:
    """打扰预算替身：记录每次裁决入参，按脚本返回 emit/queue/suppress。"""

    def __init__(self, kind: str = "emit") -> None:
        self.kind = kind
        self.calls: list[dict] = []

    def gate(self, text: str, source: str, *, dedup_key: str = "",
             urgency: float = 0.5) -> NotifyAction:
        self.calls.append({"text": text, "source": source,
                           "dedup_key": dedup_key, "urgency": urgency})
        return NotifyAction(self.kind, text, "测试桩")


def _settings(**over) -> Settings:
    """感知层测试用配置：总开关开、不留痕（不碰磁盘）、简报与 TTS 全关。"""
    base = dict(
        percept_enabled=True,
        percept_log_events=False,
        briefing_enabled=False,
        deadline_enabled=False,
        proactive_tts_enabled=False,
    )
    base.update(over)
    return Settings(**base)


def _hub(settings: Settings) -> tuple[ProactiveHub, queue.Queue]:
    eq: queue.Queue = queue.Queue()
    return ProactiveHub(settings, eq), eq


@pytest.fixture
def hub_percept():
    """装配好感知的 hub（源全关：不会有轮询线程，也就不会碰真实设备）。"""
    hub, eq = _hub(_settings())
    budget = _StubBudget()
    hub._engine._budget = budget          # 桩替换 W1 闸门
    hub.start()
    try:
        yield hub, eq, budget
    finally:
        hub.stop()


def _drain(eq: queue.Queue) -> list[dict]:
    items = []
    while not eq.empty():
        items.append(eq.get_nowait())
    return items


# ---- 装配与停机 ----

def test_master_switch_off_skips_assembly():
    """总开关关：不登记源、不订阅、不起线程（面板据此显"未启用"而不是"坏了"）。

    仅注入配置快照（`init_percept`）：`source_enabled_map()` / `default_log_path()`
    必须拿到当前配置而不是上次会话的陈旧快照；hub 侧 `percept_bus` 仍为 None。
    """
    hub, eq = _hub(_settings(percept_enabled=False))
    hub._engine._budget = _StubBudget()
    hub.start()
    try:
        assert hub.percept_bus is None
        assert hub.percept_status == {}
    finally:
        hub.stop()


def test_assembly_registers_five_sources_all_off(hub_percept):
    """开总开关但逐源全关：五个源都登记、都不启用，且不起轮询线程。"""
    hub, _eq, _budget = hub_percept
    bus = hub.percept_bus
    assert bus is not None
    assert sorted(bus.source_names()) == ["file", "mail", "vcs", "vision", "window"]
    assert hub.percept_status == {"window": False, "file": False, "vcs": False,
                                  "mail": False, "vision": False}
    assert bus.running is False                     # 没有启用源 → 零线程（空载 CPU 0）


def test_assembly_reflects_per_source_flags():
    """逐源开关要如实反映到总线（开了才 enabled=True，且需要轮询的源会起线程）。"""
    eq: queue.Queue = queue.Queue()
    # 登记仓库为空：vcs 源即使被采也是空转返回，不会真跑 git
    hub = ProactiveHub(_settings(percept_vcs_enabled=True), eq)
    hub._engine._budget = _StubBudget()
    hub.start()
    try:
        assert hub.percept_status["vcs"] is True
        assert hub.percept_bus.is_source_enabled("vcs") is True
        assert hub.percept_bus.is_source_enabled("window") is False
        assert hub.percept_bus.running is True      # 有需轮询的启用源 → 线程起
    finally:
        hub.stop()


def test_stop_unsubscribes_and_releases_sources(hub_percept):
    """停机：退订（不留指向已停 hub 的回调）+ 停线程 + 调每个源的 close()。"""
    hub, _eq, _budget = hub_percept
    closed: list[str] = []

    class _Spy:
        name = "window"
        interval_s = 0.0

        def collect(self):
            return []

        def close(self):
            closed.append(self.name)

    hub.percept_bus.register_source(_Spy(), enabled=True)
    hub.stop()
    assert closed == ["window"]
    assert hub.percept_bus is None                  # 引用已清，不会被下一次 start 复用
    assert hub._bus_unsub is None


def test_assembly_failure_does_not_break_serve(monkeypatch):
    """感知装配失败只能降级为"不感知"：serve 启动路径不得被拖垮。"""
    def boom(*_a, **_kw):
        raise RuntimeError("源装配失败")

    monkeypatch.setattr(percept_mod, "register_sources", boom)
    eq: queue.Queue = queue.Queue()
    hub = ProactiveHub(_settings(), eq)
    hub.start()                                     # 不抛
    try:
        assert hub.percept_bus is None
        assert hub.scheduler is not None            # 主动感知三件套照常工作
    finally:
        hub.stop()


# ---- 热重装配（W2 第 5 步：面板改完开关不必重启桌面壳）----

def test_hot_update_reassembles_with_new_flags():
    """改完逐源开关后热重装配：新开关如实生效，而且换的是一台新总线。"""
    hub, _eq = _hub(_settings())
    hub._engine._budget = _StubBudget()
    hub.start()
    try:
        old_bus = hub.percept_bus
        hub._settings.percept_vcs_enabled = True      # 模拟 percept.config.set 已改运行时
        status = hub.hot_update_percept()
        assert status["vcs"] is True
        assert hub.percept_bus is not old_bus
        assert hub.percept_bus.is_source_enabled("vcs") is True
        assert old_bus.running is False               # 旧总线已停，不会两套轮询
    finally:
        hub.stop()


def test_hot_update_drops_assembly_when_master_off():
    """关掉总开关后重装配：总线引用与启用表都清空（不给面板留陈旧的"在采"）。"""
    hub, _eq = _hub(_settings(percept_window_enabled=True))
    hub._engine._budget = _StubBudget()
    hub.start()
    try:
        assert hub.percept_status["window"] is True
        hub._settings.percept_enabled = False
        status = hub.hot_update_percept()
        assert status == {}
        assert hub.percept_bus is None
        assert hub.percept_status == {}
    finally:
        hub.stop()


def test_hot_update_releases_old_sources(hub_percept):
    """重装配会 close 旧源——“面板上关掉视觉就立即断摄像头”这条承诺的落点。"""
    hub, _eq, _budget = hub_percept
    closed: list[str] = []

    class _Spy:
        name = "window"
        interval_s = 0.0

        def collect(self):
            return []

        def close(self):
            closed.append(self.name)

    hub.percept_bus.register_source(_Spy(), enabled=True)
    hub.hot_update_percept()
    assert closed == ["window"]


# ---- 事件投递路径 ----

def test_percept_event_goes_through_gate_and_queue(hub_percept):
    """感知事件 → 同一个 W1 闸门 → proactive_notify 事件流（kind=percept）。"""
    hub, eq, budget = hub_percept
    hub.percept_bus.publish(PerceptEvent(
        source="window", kind="focus_changed",
        payload={"summary": "切到 Chrome", "process": "chrome"},
        salience_hint=0.5, dedup_key="focus:chrome",
    ))
    calls = budget.calls
    assert len(calls) == 1
    assert calls[0]["source"] == "percept:window"   # 预算侧按感知源分组聚合
    assert calls[0]["urgency"] == 0.5               # 源先验分作为紧迫度
    assert calls[0]["dedup_key"] == "window:focus:chrome"   # 与总线去重键同一口径
    items = _drain(eq)
    assert len(items) == 1 and items[0]["type"] == "proactive_notify"
    payload = items[0]["payload"]
    assert payload["kind"] == PERCEPT_NOTIFY_KIND == "percept"
    assert payload["text"] == "切到 Chrome"
    assert payload["title"] == "贾维斯观察"


def test_low_salience_event_is_not_spoken(hub_percept):
    """闸门判"不播"（低分入库/聚合/超额度）时宿主必须闭嘴。"""
    hub, eq, _budget = hub_percept
    stub = _StubBudget(kind="queue")
    hub._engine._budget = stub
    hub.percept_bus.publish(PerceptEvent(
        source="file", kind="dir_grew", payload={"summary": "下载目录多了 8 个文件"},
        salience_hint=0.2,
    ))
    assert len(stub.calls) == 1                     # 确实过了闸
    assert _drain(eq) == []                         # 但没投事件流


def test_events_without_summary_still_reach_gate(hub_percept):
    """源没写 summary 时按 source.kind 兜底，不会因为空文本被静默丢掉。"""
    hub, eq, budget = hub_percept
    hub.percept_bus.publish(PerceptEvent(source="vcs", kind="repo_unpushed",
                                        payload={"ahead": 3}))
    assert len(budget.calls) == 1
    assert budget.calls[0]["text"] == "vcs.repo_unpushed"
    assert _drain(eq)[0]["payload"]["text"] == "vcs.repo_unpushed"


def test_gate_exception_degrades_to_direct_notify(hub_percept):
    """闸门自身异常不能吞掉提醒：W1 的“宁多播不漏播”降级口径在宿主路径同样成立。"""
    hub, eq, budget = hub_percept

    def explode(text, source, **_kw):
        raise RuntimeError("闸门挂了")

    budget.gate = explode                           # type: ignore[assignment]
    hub.percept_bus.publish(PerceptEvent(source="window", kind="focus_changed",
                                        payload={"summary": "切到 VS Code"}))
    items = _drain(eq)
    assert len(items) == 1 and items[0]["payload"]["text"] == "切到 VS Code"


# ---- 在场信号 → W1 away 推断 ----

def test_face_absent_sets_vision_away_without_notification(hub_percept):
    """人脸离开：只改在场状态并留痕，不播报（否则每次离开都吵一次）。"""
    hub, eq, _budget = hub_percept
    hub.percept_bus.publish(PerceptEvent(
        source="vision", kind="face_disappear",
        payload={"summary": "人脸已离开画面", "present": False},
        salience_hint=0.25, dedup_key="face_disappear:1",
    ))
    assert _drain(eq) == []
    assert hub.percept_bus.stats()["counters"]["emit"] >= 1   # 总线照常记账
    away, note = get_policy_state().is_away()
    assert away is True and note.startswith("视觉：人脸不在画面")


def test_face_return_clears_vision_away(hub_percept):
    """回到工位：视觉推断的 away 由人脸出现解除。"""
    hub, eq, _budget = hub_percept
    hub.percept_bus.publish(PerceptEvent(source="vision", kind="face_disappear",
                                        payload={"summary": "离开了"}))
    assert get_policy_state().is_away()[0] is True
    hub.percept_bus.publish(PerceptEvent(source="vision", kind="face_appear",
                                        payload={"summary": "回来了", "present": True}))
    assert get_policy_state().is_away()[0] is False
    assert _drain(eq) == []


def test_camera_cannot_cancel_manual_away(hub_percept):
    """用户明确 /away 后，摄像头不得替他取消（手动指令优先于推断）。"""
    hub, eq, _budget = hub_percept
    get_policy_state().set_away(True, source="manual")
    hub.percept_bus.publish(PerceptEvent(source="vision", kind="face_appear",
                                        payload={"summary": "回来了"}))
    away, note = get_policy_state().is_away()
    assert away is True and "手动" in note
    assert _drain(eq) == []


def test_silent_kinds_table_covers_presence_signals():
    """在场类 kind 全在静默表里：新增在场事件时这条断言会提醒同步更新。"""
    assert {"face_appear", "face_disappear", "watch_stopped"} <= PERCEPT_SILENT_KINDS


# ---- 首次启动引导（W2 第 7 步：该问一次，但只在有人听得到的时候问）----

def _closed_hub():
    """总开关关着的 hub（正是该被引导的场景），预算桩与队列就位。"""
    hub, eq = _hub(_settings(percept_enabled=False))
    budget = _StubBudget()
    hub._engine._budget = budget
    return hub, eq, budget


def test_first_run_arms_ask_but_does_not_emit_yet():
    """从没做过选择的人：start 后槽里有引导，但**此刻还不投**。

    启动期没有在线 WS 客户端，事件泵广播会被直接丢掉，而“只问一次”的记录
    已经写了——那就变成一次也没问到。投递时机交给 serve 的连接首帧。
    """
    hub, eq, _budget = _closed_hub()
    hub.start()
    try:
        assert hub._percept_ask is not None
        assert _drain(eq) == []
    finally:
        hub.stop()


def test_drain_emits_once_bypasses_budget_and_records_asked():
    """drain：投一条 kind=percept_ask 的事件、不过预算闸门、只投一次并落记录。"""
    hub, eq, budget = _closed_hub()
    hub.start()
    try:
        assert hub.drain_percept_ask() is True
        items = _drain(eq)
        assert len(items) == 1
        assert items[0]["type"] == protocol.EVT_PROACTIVE_NOTIFY
        payload = items[0]["payload"]
        assert payload["kind"] == PERCEPT_ASK_KIND
        assert payload["title"] == PERCEPT_ASK_TITLE
        assert payload["text"] == PERCEPT_ASK_TEXT
        assert payload["task_id"] == ""
        # 一次性引导不走打扰预算（被静默时段/日额度压掉就永久丢了）也不走 TTS
        assert budget.calls == []
        assert hub.drain_percept_ask() is False
        assert _drain(eq) == []
        assert consent_mod.read_consent()["asked_at"]
    finally:
        hub.stop()


def test_master_on_user_is_not_asked(hub_percept):
    """总开关开着的人已经在用了，不该被科普一遍。"""
    hub, _eq, _budget = hub_percept
    assert hub._percept_ask is None
    assert hub.drain_percept_ask() is False


def test_already_asked_is_not_asked_again():
    """问过就翻篇：下次启动不再装（“只出现一次”的落点在记录文件）。"""
    consent_mod.mark_asked()
    hub, eq, _budget = _closed_hub()
    hub.start()
    try:
        assert hub._percept_ask is None
        assert _drain(eq) == []
    finally:
        hub.stop()


def test_toml_configured_user_is_not_asked(tmp_path):
    """手改过 `[percept]`（哪怕全关）也算做过选择，不再弹引导。"""
    (tmp_path / "settings.toml").write_text(
        "[percept]\nenabled = false\n", encoding="utf-8"
    )
    hub, _eq, _budget = _closed_hub()
    hub.start()
    try:
        assert hub._percept_ask is None
    finally:
        hub.stop()


def test_stop_clears_unsent_ask_and_next_start_rearms():
    """未投递的引导不跨会话堆积：拆完槽空，下次 start 重新判（仍未问过则再装）。"""
    hub, _eq, _budget = _closed_hub()
    hub.start()
    try:
        assert hub._percept_ask is not None
        hub._stop_percept()
        assert hub._percept_ask is None
        hub._start_percept()
        assert hub._percept_ask is not None
    finally:
        hub.stop()
