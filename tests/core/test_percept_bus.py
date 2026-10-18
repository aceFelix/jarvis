"""W2 感知层事件总线测试（阶段 9 第 1 步：总线骨架）。

覆盖总线存在的全部理由——去重窗口、同源节流、重要度地板、订阅方故障隔离、
采集源故障隔离与自动停用、逐源默认关（不启用就一个字节都不采）、
确定性 tick（不依赖真实 sleep）、落盘留痕与裁剪、导出自查。

时间一律注入 monotonic 值（``now=`` / ``tick(now=)``），不使用 sleep，
避免时序抖动导致的假阴/假阳。@author aceFelix
"""

from __future__ import annotations

import json
import time

import pytest

from agent.core.percept import PerceptBus, PollSource
from agent.core.percept.bus import BusConfig, PublishResult
from agent.core.percept.event import PerceptEvent


def make_bus(tmp_path, **cfg):
    """建一台指向 tmp 的总线（日志默认落盘，个别用例再单独关）。"""
    cfg.setdefault("dedup_window_s", 300)
    return PerceptBus(BusConfig(**cfg), log_path=tmp_path / "events.jsonl")


def ev(source="vcs", kind="build_failed", summary="构建失败：tests 2 例红", **kw):
    payload = dict(kw.pop("payload", {}) or {})
    payload.setdefault("summary", summary)
    return PerceptEvent(source=source, kind=kind, payload=payload, **kw)


# ---------------------------------------------------------------------------
# 事件模型
# ---------------------------------------------------------------------------


def test_event_key_is_stable_for_equivalent_payload():
    """键序不同的同一事实必须撞同一个去重键（否则同源会被算成多件事）。"""
    a = PerceptEvent(source="file", kind="dir_grew", payload={"b": 2, "a": 1})
    b = PerceptEvent(source="file", kind="dir_grew", payload={"a": 1, "b": 2})
    assert a.key() == b.key()


def test_event_explicit_dedup_key_wins():
    a = PerceptEvent(source="mail", kind="mail_received", dedup_key="msg-1",
                     payload={"summary": "来自 a@b 的来信"})
    b = PerceptEvent(source="mail", kind="mail_received", dedup_key="msg-1",
                     payload={"summary": "另一条不同的描述"})
    assert a.key() == b.key() == "mail:msg-1"


def test_event_summary_falls_back_to_kind():
    assert PerceptEvent(source="system", kind="disk_full", payload={}).summary() == "system.disk_full"


def test_event_dict_roundtrip_tolerates_bad_salience():
    """坏行不该让整批历史读失败：salience 解析不了回先验中位 0.5。"""
    raw = {"ts": "2026-10-16T10:00:00", "source": "vcs", "kind": "k",
           "payload": {"summary": "s"}, "salience_hint": "not-a-float"}
    got = PerceptEvent.from_dict(raw)
    assert got.salience_hint == 0.5
    assert got.to_dict()["source"] == "vcs"


# ---------------------------------------------------------------------------
# 三道闸：地板分 / 去重 / 节流
# ---------------------------------------------------------------------------


def test_low_salience_is_dropped_before_dedup(tmp_path):
    bus = make_bus(tmp_path, min_salience=0.4)
    out = bus.publish(ev(salience_hint=0.2), now=1000.0)
    assert out.stage == "low_salience"
    assert bus.stats()["counters"]["low_salience"] == 1
    assert bus.recent() == []


def test_same_event_within_window_counts_once(tmp_path):
    """5 分钟内重复上报只投递一次，但合并条数要能看见（面板"这事说了 3 遍"）。"""
    bus = make_bus(tmp_path)
    assert bus.publish(ev(), now=1000.0).stage == "emit"
    second = bus.publish(ev(), now=1060.0)
    assert second.stage == "dedup"
    assert "累计合并 2 条" in second.reason
    assert len(bus.recent()) == 1


def test_same_event_after_window_is_emitted_again(tmp_path):
    bus = make_bus(tmp_path, dedup_window_s=300)
    bus.publish(ev(), now=1000.0)
    assert bus.publish(ev(), now=1301.0).stage == "emit"


def test_throttle_blocks_distinct_events_of_same_source(tmp_path):
    """节流按源计数：即便不是同一件事，投得太密一样压掉（vcs 60s 口径）。"""
    bus = make_bus(tmp_path, throttle_s={"vcs": 60})
    bus.publish(ev(summary="构建失败 A"), now=1000.0)
    out = bus.publish(ev(summary="构建失败 B"), now=1030.0)
    assert out.stage == "throttle"
    assert "间隔不足" in out.reason


def test_dedup_and_throttle_are_independent(tmp_path):
    """去重只挡同一件事，节流只挡过密——两条正交，不能互相顶包。"""
    bus = make_bus(tmp_path, dedup_window_s=300, throttle_s={})
    bus.publish(ev(), now=1000.0)
    assert bus.publish(ev(summary="另一件事"), now=1001.0).stage == "emit"


def test_publish_rejects_event_without_source(tmp_path):
    bus = make_bus(tmp_path)
    assert bus.publish(PerceptEvent(source="", kind="k")).stage == "low_salience"


# ---------------------------------------------------------------------------
# 订阅与分发
# ---------------------------------------------------------------------------


def test_subscriber_exception_does_not_break_others(tmp_path):
    """故障隔离第一层：坏订阅方（播报适配器抛错）不能吃掉其他订阅方的事件。"""
    bus = make_bus(tmp_path)
    got: list[str] = []

    def boom(_event):
        raise RuntimeError("播报适配器炸了")

    bus.subscribe(boom)
    bus.subscribe(lambda e: got.append(e.summary()))
    out = bus.publish(ev(), now=1000.0)
    assert out.emitted
    assert got == ["构建失败：tests 2 例红"]
    assert bus.stats()["counters"]["subscriber_errors"] == 1


def test_subscription_filters_and_unsubscribe(tmp_path):
    bus = make_bus(tmp_path)
    only_vcs: list[str] = []
    kinds: list[str] = []
    bus.subscribe(lambda e: only_vcs.append(e.source), sources=["vcs"])
    off = bus.subscribe(lambda e: kinds.append(e.kind), kinds=["dir_grew"])
    bus.publish(ev(source="vcs"), now=1000.0)
    bus.publish(ev(source="file", kind="dir_grew", summary="下载目录多了 12 个文件"), now=1001.0)
    assert only_vcs == ["vcs"] and kinds == ["dir_grew"]
    off()
    bus.publish(ev(source="file", kind="dir_grew", summary="又多了 3 个", payload={"n": 3}), now=2000.0)
    assert kinds == ["dir_grew"]


def test_event_without_subscriber_is_still_recorded(tmp_path):
    """没订阅方也要记账：用户只开采集不听播报时，历史与导出仍要能查。"""
    bus = make_bus(tmp_path)
    out = bus.publish(ev(), now=1000.0)
    assert out.emitted and "无订阅方" in out.reason
    assert bus.stats()["counters"]["no_subscriber"] == 1
    assert len(bus.recent()) == 1


# ---------------------------------------------------------------------------
# 采集源：逐源默认关 + 确定性 tick + 故障隔离
# ---------------------------------------------------------------------------


def test_register_source_requires_name_and_collect(tmp_path):
    bus = make_bus(tmp_path)
    with pytest.raises(ValueError):
        bus.register_source(PollSource("", lambda: None))
    with pytest.raises(ValueError):
        bus.register_source(type("NoCollect", (), {"name": "x"})())


def test_disabled_source_is_never_collected(tmp_path):
    """逐源默认关的硬保证：没点头同意，collect() 一次都不该被调用。"""
    calls = []
    bus = make_bus(tmp_path)
    bus.register_source(PollSource("window", lambda: calls.append(1), interval_s=5))
    assert bus.tick(now=1000.0) == 0
    assert calls == []


def test_tick_only_collects_due_sources(tmp_path):
    """到点判定：首轮全采，之后只采已到间隔的源（单轮询线程能服务不同频率的源）。"""
    bus = make_bus(tmp_path)
    fired: list[str] = []
    bus.register_source(PollSource("vcs", lambda: (fired.append("vcs"), None)[1], interval_s=60), enabled=True)
    bus.register_source(PollSource("window", lambda: (fired.append("window"), None)[1], interval_s=5), enabled=True)

    assert bus.tick(now=1000.0) == 2          # 首次都算到点
    assert sorted(fired) == ["vcs", "window"]
    fired.clear()

    assert bus.tick(now=1003.0) == 0          # 3s：两个源都未到点
    assert bus.tick(now=1006.0) == 1          # 6s：仅 window（间隔 5s）到点
    assert fired == ["window"]
    fired.clear()

    assert bus.tick(now=1060.0) == 2          # window 再次到点，vcs 恰好满 60s
    assert sorted(fired) == ["vcs", "window"]

    assert bus.unregister_source("vcs") is True
    assert bus.tick(now=2000.0) == 1          # 摘除后只剩 window
    assert fired[-1] == "window"


def test_collect_result_is_published_and_counted(tmp_path):
    """源采集出的事件走完整投递链路，并按源记账 collected/events。"""
    bus = make_bus(tmp_path)
    seen: list[str] = []
    bus.subscribe(lambda e: seen.append(e.summary()))
    bus.register_source(PollSource("file", lambda: [ev(source="file", kind="dir_grew", summary="Downloads 多了 9 个文件")],
                                   interval_s=30), enabled=True)
    assert bus.tick(now=1000.0) == 1
    assert seen == ["Downloads 多了 9 个文件"]
    assert bus.stats()["sources"]["file"]["collected"] == 1


def test_source_error_is_isolated_and_auto_disables(tmp_path):
    """故障隔离第二层：坏源连续失败到阈值自动停用，好源照常工作。"""
    bus = make_bus(tmp_path, auto_disable_after_errors=2)
    ok: list[str] = []

    def broken():
        raise OSError("pywin32 句柄失效")

    bus.register_source(PollSource("window", broken, interval_s=5))
    bus.register_source(PollSource("vcs", lambda: ok.append("vcs"), interval_s=5), enabled=True)
    bus.set_source_enabled("window", True)

    bus.tick(now=1000.0)
    bus.tick(now=1006.0)
    st = bus.stats()["sources"]["window"]
    assert st["enabled"] is False and st["auto_disabled"] is True
    assert "句柄失效" in st["last_error"]
    assert ok == ["vcs", "vcs"]  # 另一源完全不受影响


def test_success_resets_consecutive_errors(tmp_path):
    """偶发一次抖动不该往停用计数里累：成功一次即清零。"""
    bus = make_bus(tmp_path, auto_disable_after_errors=2)
    state = {"fail": True}

    def flaky():
        if state["fail"]:
            state["fail"] = False
            raise TimeoutError("一次超时")
        return ev(source="vcs", summary="恢复了")

    bus.register_source(PollSource("vcs", flaky, interval_s=10), enabled=True)
    bus.tick(now=1000.0)
    bus.tick(now=1011.0)
    st = bus.stats()["sources"]["vcs"]
    assert st["enabled"] is True and st["auto_disabled"] is False
    assert st["errors"] == 1 and st["collected"] == 1


def test_set_enabled_rearms_auto_disabled_source(tmp_path):
    bus = make_bus(tmp_path, auto_disable_after_errors=1)
    bus.register_source(PollSource("mail", lambda: (_ for _ in ()).throw(RuntimeError("认证失败")),
                                   interval_s=30), enabled=True)
    bus.tick(now=1000.0)
    assert bus.is_source_enabled("mail") is False
    state = bus.set_source_enabled("mail", True)
    assert state["enabled"] is True and state["auto_disabled"] is False and state["last_error"] == ""


def test_set_enabled_unknown_source(tmp_path):
    bus = make_bus(tmp_path)
    with pytest.raises(KeyError):
        bus.set_source_enabled("ghost", True)


def test_unpublishable_source_name_still_counts(tmp_path):
    """未登记源直接 publish（push 型场景）：记账但不崩。"""
    bus = make_bus(tmp_path)
    assert bus.publish(ev(source="unknown"), now=1000.0).emitted
    assert bus.stats()["counters"]["emit"] == 1


# ---------------------------------------------------------------------------
# 生命周期：无线程空载 / 有源才起线程
# ---------------------------------------------------------------------------


def test_start_without_enabled_source_launches_no_thread(tmp_path):
    """验收口径"无源开启时 CPU 增量为 0"的实现依据：根本不起线程。"""
    bus = make_bus(tmp_path)
    bus.register_source(PollSource("window", lambda: None, interval_s=5))
    assert bus.start() is False
    assert bus.running is False and bus._thread is None
    bus.stop()  # 幂等


def test_start_with_enabled_source_polls_and_stops(tmp_path):
    """真线程用例：唯一需要等真实时间的一条，上限 2s（远超 0.05s 节拍）。"""
    bus = make_bus(tmp_path)
    hits = []
    bus.register_source(PollSource("vcs", lambda: (hits.append(1), None)[1], interval_s=0.05), enabled=True)
    assert bus.start() is True
    try:
        deadline = time.monotonic() + 2.0
        while not hits and time.monotonic() < deadline:
            time.sleep(0.02)
        assert hits, "轮询线程应已至少采集一次"
    finally:
        bus.stop()
    assert bus.running is False


def test_thread_exits_when_last_source_is_disabled(tmp_path):
    """最后一个轮询源被关掉→线程自行退出置 running=False→重开后 start 能再接上。"""
    bus = make_bus(tmp_path)
    bus.register_source(PollSource("vcs", lambda: None, interval_s=0.05), enabled=True)
    assert bus.start() is True
    bus.set_source_enabled("vcs", False)
    deadline = time.monotonic() + 2.0
    while bus.running and time.monotonic() < deadline:
        time.sleep(0.02)
    assert bus.running is False
    assert bus.start() is False          # 仍无启用源：不该起线程
    bus.set_source_enabled("vcs", True)
    assert bus.start() is True
    bus.stop()


def test_stop_is_idempotent_and_closes_sources(tmp_path):
    closed = []

    class Src:
        name = "mail"
        interval_s = 0.0

        def collect(self):
            return None

        def close(self):
            closed.append("mail")

    bus = make_bus(tmp_path)
    bus.register_source(Src())
    bus.stop()
    bus.stop()
    bus.shutdown_sources()
    assert closed == ["mail"]


def test_close_failure_is_isolated(tmp_path):
    """一个源析构失败不能挡住其他源释放。"""
    closed = []

    class Bad:
        name = "window"
        interval_s = 0.0

        def collect(self):
            return None

        def close(self):
            raise RuntimeError("句柄已释放")

    class Good:
        name = "vcs"
        interval_s = 0.0

        def collect(self):
            return None

        def close(self):
            closed.append("vcs")

    bus = make_bus(tmp_path)
    bus.register_source(Bad())
    bus.register_source(Good())
    bus.shutdown_sources()
    assert closed == ["vcs"]


# ---------------------------------------------------------------------------
# 留痕、历史与导出
# ---------------------------------------------------------------------------


def test_events_are_logged_as_jsonl(tmp_path):
    bus = make_bus(tmp_path)
    bus.publish(ev(summary="构建失败：pytest 红 2 例"), now=1000.0)
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["source"] == "vcs" and row["payload"]["summary"] == "构建失败：pytest 红 2 例"


def test_no_log_path_means_no_disk_trace(tmp_path):
    """用户关掉"保留采集历史"时的落地：完全不碰磁盘。"""
    log = tmp_path / "events.jsonl"
    bus = PerceptBus(BusConfig(), log_path=None)
    bus.publish(ev(), now=1000.0)
    assert not log.exists()


def test_log_is_trimmed_to_history_limit(tmp_path):
    """常驻运行不能无限写：超上限裁到 history_max*10 行，且保留的是最近的。"""
    bus = make_bus(tmp_path, dedup_window_s=0, history_max=2)  # 上限 20 行
    for i in range(30):
        bus.publish(ev(summary=f"事件 {i}", payload={"summary": f"事件 {i}", "i": i}), now=1000.0 + i)
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 20
    assert json.loads(lines[-1])["payload"]["i"] == 29


def test_history_ring_is_bounded_and_newest_first(tmp_path):
    bus = make_bus(tmp_path, dedup_window_s=0, history_max=3)
    for i in range(5):
        bus.publish(ev(payload={"summary": f"s{i}", "i": i}), now=1000.0 + i)
    recent = bus.recent()
    assert [e.payload["i"] for e in recent] == [4, 3, 2]


# ---- 磁盘为权威的最近事件（2026-10-19 走查：面板“还没有采到事件”）----


def test_recent_from_log_reads_newest_first(tmp_path):
    """磁盘尾部读取与内存历史同序（最新在前），面板才能直接渲染。"""
    bus = make_bus(tmp_path, dedup_window_s=0)
    for i in range(3):
        bus.publish(ev(payload={"summary": f"落盘 {i}", "i": i}), now=1000.0 + i)
    assert [row["payload"]["i"] for row in bus.recent_from_log(10)] == [2, 1, 0]


def test_recent_from_log_survives_bus_rebuild(tmp_path):
    """改配置会重建总线：新实例内存为空，但磁盘读得到。"""
    old = make_bus(tmp_path)
    old.publish(ev(summary="重建前采到的"), now=1000.0)
    fresh = make_bus(tmp_path)                 # 同一台宿主的下一轮装配
    assert fresh.recent() == []                # 内存确实是空的
    rows = fresh.recent_from_log(20)
    assert len(rows) == 1 and rows[0]["payload"]["summary"] == "重建前采到的"


def test_recent_from_log_drops_partial_head_after_seek(tmp_path):
    """按字节读尾部会把首行切成半条：残行必须静默丢掉，不能当成解析失败。"""
    bus = make_bus(tmp_path, dedup_window_s=0)
    for i in range(60):   # 每行 >150 字节，总量远超 1 行的 4KB 预算
        bus.publish(ev(payload={"summary": f"填充 {i} " + "x" * 80, "i": i}),
                    now=1000.0 + i)
    rows = bus.recent_from_log(1)     # span=4096 < size → 必然从行中截开
    assert len(rows) == 1 and rows[0]["payload"]["i"] == 59


def test_recent_from_log_skips_broken_lines(tmp_path):
    """单行坏 JSON 只丢自己，不连累其余（读侧永不抛异常）。"""
    log = tmp_path / "events.jsonl"
    good = json.dumps({"source": "file", "payload": {"summary": "完整一条"}},
                      ensure_ascii=False)
    log.write_text(f"坏行不是 JSON\n{good}\n", encoding="utf-8")
    rows = make_bus(tmp_path).recent_from_log(10)
    assert [r["payload"]["summary"] for r in rows] == ["完整一条"]


def test_recent_from_log_empty_without_log_path():
    """关掉留痕（log_path=None）→ 读不到东西，调用方据此回退内存。"""
    assert PerceptBus(BusConfig(), log_path=None).recent_from_log(10) == []


def test_export_reads_disk_after_rebuild(tmp_path):
    """隐私自查导出与面板同源：重建后的新总线也要能报出真实采集量。

    审计口径写的是“可见即可导出”：如果只读内存，刚改过配置的用户导出会得到
    “只采到 0 条”，而磁盘上躺着全部历史——那份自查就是误导性的。
    """
    old = make_bus(tmp_path)
    old.publish(ev(summary="重建前采到的"), now=1000.0)
    fresh = make_bus(tmp_path)
    events = fresh.export()["events"]
    assert [e["payload"]["summary"] for e in events] == ["重建前采到的"]


# ---- 重建总线时继承观测账本 ----


def test_inherit_stats_from_carries_counters_and_history(tmp_path):
    """计数累加、内存历史拼接：改一次配置不再抹一次证据。"""
    old = make_bus(tmp_path, dedup_window_s=0)
    old.publish(ev(payload={"summary": "旧的", "i": 0}), now=1000.0)
    old.publish(ev(source="file", payload={"summary": "旧的 2", "i": 1}), now=2000.0)
    fresh = make_bus(tmp_path, dedup_window_s=0)
    fresh.inherit_stats_from(old)
    fresh.publish(ev(source="mail", payload={"summary": "新的", "i": 2}), now=3000.0)
    assert fresh.stats()["counters"]["emit"] == 3
    assert [e.payload["i"] for e in fresh.recent()] == [2, 1, 0]


def test_inherit_stats_from_ignores_none_and_self(tmp_path):
    """没旧实例/传了自己都等于空操作（宿主首次装配走的就是这条路）。"""
    fresh = make_bus(tmp_path)
    fresh.inherit_stats_from(None)
    fresh.inherit_stats_from(fresh)
    assert fresh.stats()["counters"]["emit"] == 0


def test_inherit_does_not_carry_dedup_window(tmp_path):
    """只接观测，不接行为：去重窗口跨实例延续会把“重启后重新计时”搅浑。"""
    old = make_bus(tmp_path)
    old.publish(ev(summary="同一件事"), now=1000.0)
    fresh = make_bus(tmp_path)
    fresh.inherit_stats_from(old)
    result = fresh.publish(ev(summary="同一件事"), now=1010.0)   # 窗口内但新实例
    assert result.stage == "emit"                     # 去重窗口确实重新计时了
    # 计数是“旧 1 + 新 1”：观测累加、行为不继承，两件事在这条用例里分开成立
    assert fresh.stats()["counters"]["emit"] == 2


def test_payload_values_are_clamped(tmp_path):
    """长标题/长日志尾行必须截断：总线要常驻，payload 不能无界。"""
    bus = make_bus(tmp_path)
    long_text = "窗" * 500
    out = bus.publish(ev(summary=long_text), now=1000.0)
    assert out.emitted
    stored = bus.recent()[0].payload["summary"]
    assert len(stored) == 241 and stored.endswith("…")


def test_export_shows_scope_and_collected_data(tmp_path):
    """隐私自查入口：开关范围 + 采到了什么，一次导出全看见。"""
    bus = make_bus(tmp_path, throttle_s={"vcs": 60})
    bus.register_source(PollSource("vcs", lambda: None, interval_s=60), enabled=True)
    bus.publish(ev(summary="构建失败"), now=1000.0)
    out = bus.export()
    assert out["config"]["throttle_s"] == {"vcs": 60}
    assert out["stats"]["sources"]["vcs"] == {
        "enabled": True, "interval_s": 60.0, "events": 1, "collected": 1,
        "errors": 0, "auto_disabled": False, "last_error": "",
    }
    assert out["events"][0]["dedup_key"].startswith("vcs:build_failed:")
    assert isinstance(out["events"][0]["payload"]["summary"], str)


def test_seen_keys_are_pruned_to_bound_window_memory(tmp_path):
    """去重键过窗即清（防常驻内存无限增长）：但不影响窗口内合并语义。"""
    bus = make_bus(tmp_path, dedup_window_s=1, history_max=1000)
    for i in range(200):
        bus.publish(ev(payload={"summary": f"s{i}", "i": i}), now=1000.0 + i * 5)
    assert len(bus._seen) <= 200
    # 窗口已过 → 同一件事重新投递（剪枝后行为不变）
    assert bus.publish(ev(payload={"summary": "s0", "i": 0}), now=9999.0).stage == "emit"


def test_publish_result_is_named_tuple_like():
    """PublishResult 的 emitted 属性是播报适配器的判据，语义要稳。"""
    assert PublishResult("emit", "x").emitted is True
    assert PublishResult("dedup", "x").emitted is False


# ---------------------------------------------------------------------------
# 源实例自报细节（W2 第 5 步：面板要能说清“为什么一个字节都没采”）
# ---------------------------------------------------------------------------


class _StatefulSource:
    """带 state() 的源桩（模拟视觉源那种“环境级不可用”的自报）。"""

    name = "vision"
    interval_s = 5.0

    def __init__(self, state=None, boom=False) -> None:
        self._state = state if state is not None else {"unavailable": "缺少视觉依赖"}
        self._boom = boom

    def collect(self):
        return []

    def state(self):
        if self._boom:
            raise RuntimeError("源自报状态炸了")
        return self._state


def test_source_detail_passes_through_instance_state(tmp_path):
    """总线只转手，不加戏：源自报什么就返回什么。"""
    bus = make_bus(tmp_path)
    bus.register_source(_StatefulSource(), enabled=False)
    assert bus.source_detail("vision") == {"unavailable": "缺少视觉依赖"}


def test_source_detail_empty_for_unknown_or_stateless(tmp_path):
    """未登记的源、没有 state() 的源都返回空 dict（面板不能因此白屏）。"""
    bus = make_bus(tmp_path)
    assert bus.source_detail("nope") == {}
    bus.register_source(PollSource("vcs", lambda: None, interval_s=60))
    assert bus.source_detail("vcs") == {}


def test_source_detail_swallows_source_exceptions(tmp_path):
    """源自报状态报错也不能外溢：返回空 dict，不影响其他源。"""
    bus = make_bus(tmp_path)
    bus.register_source(_StatefulSource(boom=True))
    assert bus.source_detail("vision") == {}

