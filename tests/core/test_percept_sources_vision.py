"""W2 第 4 步：视觉在场源 `source_vision.py` 单测。

不碰摄像头：注入假 watcher（可用 / 不可用 / 启动失败三种），验证的是本源
与它的契约——懒启动、事件映射、在场事件不被去重吞掉、防抖、依赖缺失不抛。
@author aceFelix
"""

from __future__ import annotations

from agent.core.percept.bus import BusConfig, PerceptBus
from agent.core.percept.source_vision import (
    DEFAULT_FPS,
    KIND_BY_EVENT,
    PRESENCE_KINDS,
    VisionSource,
)


class FakeEvent:
    """VisionEvent 替身（只带本源会用到的字段）。"""

    def __init__(self, event_type: str, *, gesture: str = "", face_count: int = 0,
                 description: str = "") -> None:
        self.event_type = event_type
        self.gesture = gesture
        self.face_count = face_count
        self.description = description


class FakeWatcher:
    """VisionWatcher 替身：记录启停、把回调留着供测试手动投事件。"""

    def __init__(self, *, available: bool = True, start_ok: bool = True) -> None:
        self.available = available
        self.running = False
        self.start_ok = start_ok
        self.starts = 0
        self.stops = 0
        self.handler = None

    def set_event_handler(self, handler) -> None:
        self.handler = handler

    def start(self) -> bool:
        self.starts += 1
        self.running = bool(self.start_ok and self.available)
        return self.running

    def stop(self) -> None:
        self.stops += 1
        self.running = False

    def emit(self, event: FakeEvent) -> None:
        """模拟 watcher 采集线程回调一次事件。"""
        if self.handler is not None:
            self.handler(event)


def make_source(**kw) -> tuple[VisionSource, FakeWatcher]:
    """造源：`watcher_kwargs` 传给假 watcher，其余传给 VisionSource。"""
    watcher = kw.pop("watcher", None) or FakeWatcher(**(kw.pop("watcher_kwargs", None) or {}))
    return VisionSource(watcher=watcher, **kw), watcher


# ---- 懒启动与依赖缺失 ----

def test_first_collect_starts_watcher_once():
    src, watcher = make_source()
    assert watcher.starts == 0                      # 构造期不碰摄像头
    assert src.collect() == []
    assert src.collect() == []                      # 第二次不重复 start
    assert watcher.starts == 1 and watcher.running is True
    assert src.state()["running"] is True


def test_missing_deps_is_unavailable_not_error():
    """没装 mediapipe：返回空 + 写明原因，不抛（否则面板误报"源坏了"）。"""
    src, watcher = make_source(watcher_kwargs={"available": False})
    assert src.collect() == []
    assert "缺少视觉依赖" in src.state()["unavailable"]
    assert watcher.starts == 0                      # 不可用就不调 start
    src.collect()                                   # 再来一次也不反复尝试
    assert watcher.starts == 0


def test_camera_start_failure_recorded():
    src, watcher = make_source(watcher_kwargs={"start_ok": False})
    assert src.collect() == []
    assert "摄像头不可用" in src.state()["unavailable"]


def test_injected_factory_is_used_lazily():
    built: list[int] = []

    def factory():
        built.append(1)
        return FakeWatcher()

    src = VisionSource(watcher_factory=factory)
    assert built == []
    src.collect()
    assert built == [1]


def test_close_stops_watcher():
    src, watcher = make_source()
    src.collect()
    src.close()
    assert watcher.stops == 1 and src.state()["running"] is False


# ---- 事件映射 ----

def test_gesture_event_mapping():
    src, watcher = make_source()
    src.collect()
    watcher.emit(FakeEvent("gesture", gesture="Thumb_Up", description="检测到手势: 点赞"))
    events = src.collect()
    assert [e.kind for e in events] == ["gesture"]
    ev = events[0]
    assert ev.source == "vision" and ev.payload["gesture"] == "Thumb_Up"
    assert ev.summary() == "检测到手势: 点赞"
    assert ev.salience_hint == 0.6                  # 手势=用户在跟它说话，先验更高
    assert ev.dedup_key == "gesture:Thumb_Up"


def test_same_gesture_twice_is_one_event_on_bus():
    """同名手势连比两次：总线 5 分钟窗口内只算一件事（不播两遍）。"""
    bus = PerceptBus(BusConfig(dedup_window_s=300), log_path=None)
    seen: list[str] = []
    bus.subscribe(lambda e: seen.append(e.summary()))
    src, watcher = make_source()
    bus.register_source(src, enabled=True, interval_s=10)
    bus.tick(now=100.0)                             # 建线：起 watcher
    watcher.emit(FakeEvent("gesture", gesture="Victory", description="比耶"))
    bus.tick(now=110.0)
    watcher.emit(FakeEvent("gesture", gesture="Victory", description="比耶"))
    bus.tick(now=120.0)
    assert seen == ["比耶"]
    assert bus.stats()["counters"]["dedup"] == 1


def test_unknown_event_type_ignored():
    src, watcher = make_source()
    src.collect()
    watcher.emit(FakeEvent("something_new"))
    assert src.collect() == []


def test_watch_stopped_is_recorded():
    src, watcher = make_source()
    src.collect()
    watcher.emit(FakeEvent("auto_stopped", description="监控已空闲5分钟自动关闭"))
    events = src.collect()
    assert [e.kind for e in events] == ["watch_stopped"]
    assert events[0].salience_hint == 0.1           # 低先验：只留痕不打扰


# ---- 在场事件：不被去重吞掉 + 防抖 ----

def test_presence_events_are_not_deduped():
    """离开再回来必须两条都投出去，否则 away 状态永远解不掉。"""
    bus = PerceptBus(BusConfig(dedup_window_s=300), log_path=None)
    got: list[str] = []
    bus.subscribe(lambda e: got.append(e.kind))
    src, watcher = make_source(presence_min_gap_s=0)
    bus.register_source(src, enabled=True, interval_s=10)
    bus.tick(now=100.0)                             # 这一轮只把 watcher 起来
    watcher.emit(FakeEvent("face_disappear", description="人脸已离开画面"))
    watcher.emit(FakeEvent("face_appear", face_count=1, description="检测到人脸进入画面"))
    bus.tick(now=110.0)
    assert got == ["face_disappear", "face_appear"]  # 去重窗口 300s 内两条都活
    recent = bus.recent()
    assert recent[0].payload["present"] is True      # 最新一条是“回来了”
    assert recent[1].payload["present"] is False
    assert recent[0].dedup_key != recent[1].dedup_key  # 各带序号：总线不会合并


def test_same_presence_within_gap_is_dropped():
    """人在画面边界来回抖：同类在场事件在最小间隔内只算一次。"""
    src, watcher = make_source(presence_min_gap_s=60)
    src.collect()
    watcher.emit(FakeEvent("face_disappear", description="人脸已离开画面"))
    watcher.emit(FakeEvent("face_disappear", description="人脸已离开画面"))
    events = src.collect()
    assert [e.kind for e in events] == ["face_disappear"]
    assert src.state()["dropped_flapping"] == 1


def test_different_presence_kinds_do_not_block_each_other():
    src, watcher = make_source(presence_min_gap_s=60)
    src.collect()
    watcher.emit(FakeEvent("face_disappear", description="人脸已离开画面"))
    watcher.emit(FakeEvent("face_appear", face_count=1, description="回来了"))
    assert [e.kind for e in src.collect()] == ["face_disappear", "face_appear"]


# ---- 参数与状态 ----

def test_state_reports_parameters():
    src, _ = make_source(
        camera_index=1, fps=8, enable_gesture=False, auto_stop_minutes=5,
        presence_min_gap_s=30,
    )
    st = src.state()
    assert st["camera_index"] == 1 and st["fps"] == 8
    assert st["gesture_enabled"] is False and st["face_enabled"] is True
    assert st["auto_stop_minutes"] == 5 and st["presence_min_gap_s"] == 30
    assert st["started"] is False and st["queued"] == 0


def test_fps_floor_and_kind_table():
    """帧率给 0 回到默认、给负数抬到 1（不能变成"0 帧空转"）；kind 表要盖全。"""
    assert make_source(fps=0)[0]._fps == DEFAULT_FPS
    assert make_source(fps=-5)[0]._fps == 1
    assert sorted(KIND_BY_EVENT) == sorted(
        ["gesture", "face_appear", "face_disappear", "auto_stopped"])
    assert list(PRESENCE_KINDS) == ["face_appear", "face_disappear"]
    assert DEFAULT_FPS == 2                          # 常驻成本口径：默认低帧率


def test_queue_is_bounded():
    """watcher 狂推而总线没来排空：队列有上限，不涨内存。"""
    src, watcher = make_source(presence_min_gap_s=0)
    src.collect()
    for i in range(200):
        watcher.emit(FakeEvent("gesture", gesture="Open_Palm", description=f"第{i}次"))
    assert len(src._queue) == 50


# ---- 配置接线（[percept] vision_* → 源实例）----

def test_factory_passes_vision_params():
    """六个 vision_* 字段都得流到源实例，否则面板是摆设。"""
    from agent.config.settings import Settings
    from agent.core.percept import build_sources

    settings = Settings(
        percept_enabled=True, percept_vision_enabled=True,
        percept_vision_camera_index=1, percept_vision_fps=6,
        percept_vision_gesture_enabled=False,
        percept_vision_auto_stop_minutes=3, percept_vision_interval_s=9,
    )
    vision, enabled = next((s, e) for s, e in build_sources(settings) if s.name == "vision")
    st = vision.state()
    assert enabled is True
    assert st["camera_index"] == 1 and st["fps"] == 6
    assert st["gesture_enabled"] is False and st["face_enabled"] is True
    assert st["auto_stop_minutes"] == 3 and vision.interval_s == 9.0
    # 只开逐源、不开总开关 → 仍然不算开（与总开关优先级一致）
    pairs = build_sources(Settings(percept_vision_enabled=True))
    assert dict((s.name, e) for s, e in pairs)["vision"] is False


def test_vision_toml_keys_map_to_settings():
    """`[percept]` 下的 vision_* 子键要能被解析进 `percept_vision_*` 字段。"""
    from agent.config.settings import Settings, _apply_toml

    s = _apply_toml(Settings(), {"percept": {
        "vision_enabled": True, "vision_fps": 4, "vision_gesture_enabled": False,
        "vision_auto_stop_minutes": 10, "vision_camera_index": 2, "vision_interval_s": 7,
    }})
    assert (s.percept_vision_enabled, s.percept_vision_fps,
            s.percept_vision_gesture_enabled, s.percept_vision_auto_stop_minutes,
            s.percept_vision_camera_index, s.percept_vision_interval_s) == (
                True, 4, False, 10, 2, 7)
