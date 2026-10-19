"""视觉在场源 —— 把休眠的 VisionWatcher 接进总线（W2 第 4 步）。

`agent/core/daemon/vision_watcher.py` 早就写好了（mediapipe 本地跑手势与人脸），
但 2026-09 serve 侧装配主动感知三件套时它仍是休眠件——没有生产接线点。本源就是
它的第一个装配点：**在场事实**（人脸进/出画面）与**手势**（用户对着摄像头比手势）
都变成 `PerceptEvent` 进总线，于是
- 播报侧：过 W1 同一个闸门（低先验分的在场事件天然被压住，不刷屏）；
- 状态侧：宿主可据 `face_disappear` 推断 away（补上 W1 递延的"人脸离开判定"）。

三条刻意的取舍：

1. **默认关，且开了就把成本说清**：摄像头常驻是隐私与 CPU 的双重承诺，所以
   `percept_vision_enabled` 出厂 false；帧率默认 2fps（在场判断不需要流畅，
   mediapipe 人脸检测 ~30ms/帧，2fps 已够 5 帧防抖在 2.5s 内确认）。想更省隐私
   可把 `percept_vision_auto_stop_minutes` 设 >0，代价见配置表说明。
2. **在场事件不参与总线去重**：人脸"进"与"出"是**状态迁移**，不是重复噪声。若沿用
   `dedup_key="presence"`，5 分钟窗口内"回来了"那一条会被当成重复丢掉，away 状态就
   永久卡住。故这两类事件带自增序号（每条键都不同），抖动改由本源自己的
   `presence_min_gap_s`（默认 60s 内同类只算一次）来挡——两层各管各的语义。
3. **依赖缺失不算故障**：mediapipe/cv2 没装或摄像头打不开时 `collect()` 返回空列表
   并把原因写进 `state()`（与"开了但没配齐"同一口径），不抛异常——否则总线的连败
   自动停用会把"本机没装依赖"报成"源坏了"。

线程：VisionWatcher 自己有采集线程，本源不再起第二个线程；`on_event` 只做入队，
`collect()` 由总线节拍排空——与 `source_mail.py` 的 push 型源同一个形状。

@author aceFelix
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable

from agent.core.percept.event import PerceptEvent, SOURCE_VISION

#: VisionWatcher.EventType → 总线事件 kind（一对一，名字换成本项目口径）
KIND_BY_EVENT = {
    "gesture": "gesture",
    "face_appear": "face_appear",
    "face_disappear": "face_disappear",
    "auto_stopped": "watch_stopped",
}

#: 各类事实的先验分：手势=用户在跟贾维斯说话（值得播）；在场=状态迁移（只记账）
HINT_BY_KIND: dict[str, float] = {
    "gesture": 0.6,
    "face_appear": 0.2,
    "face_disappear": 0.25,
    "watch_stopped": 0.1,
}

#: 在场类 kind（参与防抖、且退出去重）
PRESENCE_KINDS = ("face_appear", "face_disappear")

#: 在场源默认帧率：够用即可，常驻成本按帧率线性增长
DEFAULT_FPS = 2


class VisionSource:
    """视觉在场感知源（push 型：watcher 线程采集，`collect()` 排空投递）。

    @author aceFelix
    """

    name = SOURCE_VISION

    def __init__(
        self,
        *,
        watcher: Any = None,
        watcher_factory: Callable[..., Any] | None = None,
        camera_index: int = 0,
        fps: int = DEFAULT_FPS,
        enable_gesture: bool = True,
        enable_face: bool = True,
        auto_stop_minutes: int = 0,
        interval_s: float = 5.0,
        presence_min_gap_s: float = 60.0,
    ) -> None:
        """
        Args:
            watcher / watcher_factory: 注入 VisionWatcher 实例或工厂（测试与定制用）。
                都不传时本源自己 `from agent.core.daemon.vision_watcher import VisionWatcher`
                构造——**延迟到第一次被采集才构造**，源关着时连 import 都不做。
            camera_index: 摄像头索引（默认前置）。
            fps: 帧率。默认 2：在场判断与手势确认都够用，常驻 CPU 才压得住。
            auto_stop_minutes: >0 时空闲这么久自动关摄像头（更省隐私，但关闭期间
                检测不到"回来了"，away 只能靠 TTL 过期或手动 /back 解除）。
            interval_s: 总线排空本源的节拍。
            presence_min_gap_s: 同类在场事件的最小间隔（防人在画面边界来回抖）。
        """
        self.interval_s = float(interval_s)
        self._watcher = watcher
        self._factory = watcher_factory
        self._camera_index = int(camera_index)
        self._fps = max(1, int(fps or DEFAULT_FPS))
        self._enable_gesture = bool(enable_gesture)
        self._enable_face = bool(enable_face)
        self._auto_stop_s = max(0.0, float(auto_stop_minutes or 0) * 60.0)
        self._presence_gap = max(0.0, float(presence_min_gap_s or 0))

        self._queue: deque[Any] = deque(maxlen=50)
        self._lock = threading.Lock()
        self._seq = 0
        self._last_presence: dict[str, float] = {}   # kind → monotonic 上次放行时刻
        self._started = False
        self.unavailable = ""      # 依赖/设备层面的原因（面板可见，非运行时故障）
        self.dropped_flapping = 0  # 被防抖挡掉的在场事件数（"防抖生效中"的证据）
        self.published = 0

        if watcher is not None:
            self._attach(watcher)
            self._started = bool(getattr(watcher, "running", False))

    # ---- 总线契约 ----

    def collect(self) -> list[PerceptEvent]:
        """确保 watcher 已起，然后排空事件队列。"""
        self._ensure_watcher()
        with self._lock:
            pending = list(self._queue)
            self._queue.clear()
        events: list[PerceptEvent] = []
        for raw in pending:
            ev = self._event(raw)
            if ev is not None:
                events.append(ev)
                self.published += 1
        return events

    def close(self) -> None:
        """停掉摄像头与模型（总线 shutdown_sources 会调）。"""
        watcher = self._watcher
        self._started = False
        if watcher is None:
            return
        stopper = getattr(watcher, "stop", None)
        if callable(stopper):
            try:
                stopper()
            except Exception:
                pass  # 释放失败不影响其他源的关闭

    def state(self) -> dict[str, Any]:
        """运行状态（导出/面板用）：在跑没跑、参数、末次不可用原因。"""
        running = False
        if self._watcher is not None:
            running = bool(getattr(self._watcher, "running", False))
        return {
            "started": self._started,
            "running": running,
            "unavailable": self.unavailable,
            "camera_index": self._camera_index,
            "fps": self._fps,
            "gesture_enabled": self._enable_gesture,
            "face_enabled": self._enable_face,
            "auto_stop_minutes": round(self._auto_stop_s / 60.0, 2),
            "presence_min_gap_s": self._presence_gap,
            "dropped_flapping": self.dropped_flapping,
            "published": self.published,
            "queued": len(self._queue),
        }

    # ---- 内部 ----

    def _ensure_watcher(self) -> None:
        """懒构造 + 懒启动（幂等）。依赖缺失或摄像头不可用时记下原因并不再重试。

        本源绝不抛异常（所以总线的连败自动停用不会生效），靠 `unavailable` 本身
        短路：一旦判定不可用就不再调 start，避免每 5 秒重试一次抢不到的摄像头。
        """
        if self._watcher is None:
            if self.unavailable:
                return                      # 已经判定不可用：不再反复尝试
            self._watcher = self._build_watcher()
            if self._watcher is None:
                return
            self._attach(self._watcher)
        if self._started or self.unavailable:
            return
        if not bool(getattr(self._watcher, "available", False)):
            # mediapipe/cv2 未装：环境级不可用，说清原因，不抛（见模块顶部第 3 条）
            self.unavailable = "缺少视觉依赖（pip install \"jarvis-agent[vision]\"）"
            return
        try:
            self._started = bool(self._watcher.start())
        except Exception as e:
            self.unavailable = f"摄像头启动失败：{type(e).__name__}"[:160]
            return
        if not self._started:
            # start() 返回 False：可能是无摄像头/模型下载失败，如实呈现
            self.unavailable = "摄像头不可用（无设备或模型未就绪）"

    def _build_watcher(self) -> Any:
        """按注入的工厂或默认 VisionWatcher 造实例；造不出来返回 None。"""
        try:
            if self._factory is not None:
                return self._factory()
            from agent.core.daemon.vision_watcher import VisionWatcher

            return VisionWatcher(
                self._on_vision_event,
                camera_index=self._camera_index,
                fps=self._fps,
                enable_gesture=self._enable_gesture,
                enable_face=self._enable_face,
                auto_stop_seconds=self._auto_stop_s,
            )
        except Exception as e:
            self.unavailable = f"视觉监控器创建失败：{type(e).__name__}"[:160]
            return None

    def _attach(self, watcher: Any) -> None:
        """把回调改挂到本源（注入的 watcher 也走同一条入队路）。"""
        setter = getattr(watcher, "set_event_handler", None)
        if callable(setter):
            setter(self._on_vision_event)

    def _on_vision_event(self, event: Any) -> None:
        """watcher 线程回调：只入队，不做 IO（采集线程里慢操作会掉帧）。"""
        kind = KIND_BY_EVENT.get(str(getattr(event, "event_type", "") or ""), "")
        if not kind:
            return
        if kind in PRESENCE_KINDS and self._flapping(kind):
            self.dropped_flapping += 1
            return
        with self._lock:
            self._queue.append(event)

    def _flapping(self, kind: str) -> bool:
        """同类在场事件在最小间隔内再来一次 → 视为画面边界抖动，挡掉。"""
        now = time.monotonic()
        last = self._last_presence.get(kind)
        if last is not None and now - last < self._presence_gap:
            return True
        self._last_presence[kind] = now
        return False

    def _event(self, event: Any) -> PerceptEvent | None:
        """VisionEvent → PerceptEvent（payload 只放事实：手势名 / 人脸数）。"""
        kind = KIND_BY_EVENT.get(str(getattr(event, "event_type", "") or ""), "")
        if not kind:
            return None
        gesture = str(getattr(event, "gesture", "") or "")
        face_count = int(getattr(event, "face_count", 0) or 0)
        description = str(getattr(event, "description", "") or "")
        payload: dict[str, Any] = {"summary": description or f"vision.{kind}"}
        if kind == "gesture":
            payload["gesture"] = gesture
            # 手势可以重复比，同名手势在总线窗口内算一件事（不播两遍）
            dedup = f"gesture:{gesture or 'none'}"
        else:
            if kind in PRESENCE_KINDS:
                payload["present"] = kind == "face_appear"
                payload["face_count"] = face_count
            # 带自增序号：状态迁移不能被去重窗口吞掉（见模块顶部第 2 条）
            self._seq += 1
            dedup = f"{kind}:{self._seq}"
        return PerceptEvent(
            source=SOURCE_VISION,
            kind=kind,
            payload=payload,
            salience_hint=HINT_BY_KIND.get(kind, 0.2),
            dedup_key=dedup,
        )
