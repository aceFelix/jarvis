"""视觉监控 —— 贾维斯的"实时眼睛"。

阶段五扩展能力。用 mediapipe 在本地 CPU 实时检测摄像头画面的手势和人脸，
检测到事件时触发回调。这是"实时动态感知"的正解——低延迟、免费、隐私好。

核心概念:
1. **VisionWatcher**: 后台线程持续读摄像头帧 + mediapipe 检测。
   检测到手势变化/人脸出现消失 → 触发 on_event 回调。
2. **事件类型**:
   - gesture: 手势变化（如 None→Thumb_Up 表示比了赞）
   - face_appear: 人脸进入画面
   - face_disappear: 人脸离开画面
3. **防抖**: 手势需连续 N 帧稳定才算事件（防抖动误触发）。
   人脸出现/消失需连续 N 帧确认（防瞬时误判）。

为什么用 mediapipe 而非 LLM 视觉:
- LLM 视觉延迟 1-3s + 每帧 token 成本，无法实时
- mediapipe CPU 30fps、~30ms/帧、零成本、模型仅几 MB
- mediapipe 直接输出结构化结果（手势类别/人脸框），无需文本解析

mediapipe GestureRecognizer 支持的手势:
- None / Closed_Fist(握拳) / Open_Palm(张开) / Pointing_Up(指上)
- Thumb_Down(踩) / Thumb_Up(赞) / Victory(耶) / ILoveYou(爱你)

依赖: pip install mediapipe opencv-python
模型: 自动从 Google Storage 下载缓存到 ~/.jarvis/models/
"""

from __future__ import annotations

import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable


# ---- 模型下载 ----
_GESTURE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task"
)
_FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)


def _models_dir() -> Path:
    """模型缓存目录: ~/.jarvis/models/"""
    d = Path.home() / ".jarvis" / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_model(url: str, filename: str) -> str | None:
    """确保模型文件存在，不存在则下载。返回路径，失败返回 None。"""
    path = _models_dir() / filename
    if path.exists() and path.stat().st_size > 0:
        return str(path)
    try:
        urllib.request.urlretrieve(url, str(path))
        return str(path) if path.exists() else None
    except Exception:
        return None


# ---- 事件类型 ----


class EventType(str, Enum):
    GESTURE = "gesture"              # 手势变化
    FACE_APPEAR = "face_appear"      # 人脸出现
    FACE_DISAPPEAR = "face_disappear"  # 人脸消失
    AUTO_STOPPED = "auto_stopped"    # 空闲超时自动停止


@dataclass
class VisionEvent:
    """一个视觉事件。"""
    event_type: EventType
    description: str                 # 给用户/LLM 的描述
    gesture: str = ""                # 手势名（gesture 事件）
    face_count: int = 0              # 人脸数（face 事件）
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))


# ---- 中文手势映射 ----
_GESTURE_CN = {
    "None": "无",
    "Closed_Fist": "握拳",
    "Open_Palm": "张开手掌",
    "Pointing_Up": "指向上方",
    "Thumb_Down": "踩",
    "Thumb_Up": "点赞",
    "Victory": "比耶",
    "ILoveYou": "爱你",
}


# ---------------------------------------------------------------------------
# 视觉监控器
# ---------------------------------------------------------------------------


class VisionWatcher:
    """实时视觉监控器。

    后台 daemon 线程持续读摄像头帧 + mediapipe 检测手势和人脸。
    检测到事件（手势变化/人脸出现消失）时触发 on_event 回调。

    用法::

        watcher = VisionWatcher(on_event=lambda e: print(e.description))
        if watcher.start():
            # 监控运行中
            watcher.stop()

    设计要点:
    - **CPU 友好**: 帧率可控（默认 15fps），mediapipe CPU 推理 ~30ms
    - **防抖**: 手势需连续 3 帧稳定；人脸出现/消失需连续 5 帧确认
    - **优雅降级**: mediapipe/cv2 未装 → available=False；无摄像头 → start 失败
    - **线程安全**: _lock 保护状态
    - **资源释放**: stop 时释放摄像头 + 模型
    """

    def __init__(
        self,
        on_event: Callable[[VisionEvent], None],
        *,
        camera_index: int = 0,
        fps: int = 15,
        enable_gesture: bool = True,
        enable_face: bool = True,
        auto_stop_seconds: float = 300.0,
    ) -> None:
        """
        Args:
            on_event: 事件回调。接收 VisionEvent 参数。
            camera_index: 摄像头索引（默认0=前置）。
            fps: 帧率（默认15，平衡流畅度与CPU占用）。
            enable_gesture: 启用手势识别。
            enable_face: 启用人脸检测。
            auto_stop_seconds: 空闲自动停止秒数（默认300=5分钟）。
                超过此时间没有任何手势/人脸事件，自动停止监控并释放摄像头。
                每次触发事件会重置计时。设为 0 表示不自动停止。
        """
        self._on_event = on_event
        self._camera_index = camera_index
        self._fps = fps
        self._enable_gesture = enable_gesture
        self._enable_face = enable_face
        self._frame_interval = 1.0 / fps
        self._auto_stop_seconds = auto_stop_seconds

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._available = False
        self._running = False

        # mediapipe 组件（延迟初始化）
        self._gesture_recognizer = None
        self._face_detector = None
        self._cap = None

        # 防抖状态
        self._lock = threading.Lock()
        self._last_gesture = "None"
        self._gesture_stable_count = 0
        self._gesture_stable: str | None = None  # 当前稳定手势
        self._face_present = False
        self._face_count = 0
        self._face_appear_count = 0
        self._face_disappear_count = 0

        # 统计 + 空闲计时
        self._frame_count = 0
        self._last_fps_check = 0.0
        self._actual_fps = 0.0
        self._last_event_time = 0.0  # 最后一次事件时间（用于空闲超时）

        # 检查依赖
        try:
            import mediapipe as mp  # noqa: F401
            import cv2  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False

    @property
    def available(self) -> bool:
        """mediapipe + cv2 是否可用。"""
        return self._available

    @property
    def running(self) -> bool:
        """是否正在监控。"""
        return self._running

    def start(self) -> bool:
        """启动监控。返回是否成功启动。"""
        if not self._available:
            return False
        if self._started:
            return True

        # 下载/加载模型
        if self._enable_gesture:
            model_path = _ensure_model(_GESTURE_MODEL_URL, "gesture_recognizer.task")
            if model_path is None:
                return False
            try:
                import mediapipe as mp
                from mediapipe.tasks import python as mp_python
                from mediapipe.tasks.python import vision

                base_opts = mp_python.BaseOptions(model_asset_path=model_path)
                opts = vision.GestureRecognizerOptions(base_options=base_opts)
                self._gesture_recognizer = vision.GestureRecognizer.create_from_options(opts)
            except Exception:
                self._gesture_recognizer = None

        if self._enable_face:
            model_path = _ensure_model(_FACE_MODEL_URL, "blaze_face_short_range.tflite")
            if model_path is None:
                return False
            try:
                import mediapipe as mp
                from mediapipe.tasks import python as mp_python
                from mediapipe.tasks.python import vision

                base_opts = mp_python.BaseOptions(model_asset_path=model_path)
                opts = vision.FaceDetectorOptions(base_options=base_opts)
                self._face_detector = vision.FaceDetector.create_from_options(opts)
            except Exception:
                self._face_detector = None

        # 打开摄像头
        try:
            import cv2
            self._cap = cv2.VideoCapture(self._camera_index)
            if not self._cap.isOpened():
                return False
        except Exception:
            return False

        self._started = True
        self._running = True
        self._stop_event.clear()
        self._last_event_time = time.time()  # 启动时重置空闲计时
        self._thread = threading.Thread(target=self._run, daemon=True, name="jarvis-vision")
        self._thread.start()
        return True

    def stop(self) -> None:
        """停止监控。"""
        self._stop_event.set()
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

        # 释放模型
        for attr in ("_gesture_recognizer", "_face_detector"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)

        self._started = False

    def get_status(self) -> dict:
        """获取监控状态。"""
        with self._lock:
            return {
                "running": self._running,
                "available": self._available,
                "camera_index": self._camera_index,
                "fps": self._actual_fps,
                "frame_count": self._frame_count,
                "gesture_enabled": self._enable_gesture and self._gesture_recognizer is not None,
                "face_enabled": self._enable_face and self._face_detector is not None,
                "current_gesture": _GESTURE_CN.get(self._gesture_stable or "None", "无"),
                "face_present": self._face_present,
                "face_count": self._face_count,
            }

    # ---- 内部 ----

    def _run(self) -> None:
        """监控主循环。"""
        import cv2
        import mediapipe as mp

        self._last_fps_check = time.time()
        frame_count_for_fps = 0

        while not self._stop_event.is_set():
            t0 = time.time()

            # 空闲超时检查：超过 auto_stop_seconds 没有任何事件 → 自动停止
            if self._auto_stop_seconds > 0:
                idle = time.time() - self._last_event_time
                if idle >= self._auto_stop_seconds:
                    self._fire_auto_stop()
                    break

            try:
                ret, frame = self._cap.read()
                if not ret or frame is None:
                    continue

                # BGR → RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)

                self._process_frame(mp_image)

            except Exception:
                pass

            # 统计 fps
            self._frame_count += 1
            frame_count_for_fps += 1
            now = time.time()
            if now - self._last_fps_check >= 5.0:
                with self._lock:
                    self._actual_fps = round(frame_count_for_fps / (now - self._last_fps_check), 1)
                frame_count_for_fps = 0
                self._last_fps_check = now

            # 帧率控制
            elapsed = time.time() - t0
            sleep_time = self._frame_interval - elapsed
            if sleep_time > 0:
                self._stop_event.wait(timeout=sleep_time)

    def _fire_auto_stop(self) -> None:
        """空闲超时自动停止：触发事件通知 + 停止监控。"""
        event = VisionEvent(
            event_type=EventType.AUTO_STOPPED,
            description=f"监控已空闲{int(self._auto_stop_seconds // 60)}分钟自动关闭",
        )
        try:
            self._on_event(event)
        except Exception:
            pass
        # 停止自身（在当前线程调用 stop 安全）
        self._running = False
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        for attr in ("_gesture_recognizer", "_face_detector"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        self._started = False

    def _process_frame(self, mp_image: Any) -> None:
        """处理一帧：手势 + 人脸检测，触发事件。"""
        # ---- 手势检测 ----
        if self._gesture_recognizer is not None:
            try:
                result = self._gesture_recognizer.recognize(mp_image)
                gestures = result.gestures  # [[Category], ...] 每个手一个列表
                if gestures and len(gestures) > 0 and len(gestures[0]) > 0:
                    current = gestures[0][0].category_name  # 取第一只手的第一候选
                else:
                    current = "None"
                self._update_gesture(current)
            except Exception:
                pass

        # ---- 人脸检测 ----
        if self._face_detector is not None:
            try:
                result = self._face_detector.detect(mp_image)
                face_count = len(result.detections) if result.detections else 0
                self._update_face(face_count)
            except Exception:
                pass

    def _update_gesture(self, current: str) -> None:
        """手势防抖更新。连续 3 帧相同才触发事件。"""
        with self._lock:
            if current == self._last_gesture:
                self._gesture_stable_count += 1
            else:
                self._gesture_stable_count = 1
                self._last_gesture = current

            # 连续 3 帧稳定 → 视为手势变化
            if self._gesture_stable_count == 3 and current != self._gesture_stable:
                old = self._gesture_stable
                self._gesture_stable = current
                should_fire = current != "None"  # None 不触发事件
            else:
                should_fire = False
                old = None

        if should_fire:
            cn = _GESTURE_CN.get(current, current)
            old_cn = _GESTURE_CN.get(old or "None", "无") if old else "无"
            self._last_event_time = time.time()  # 重置空闲计时
            event = VisionEvent(
                event_type=EventType.GESTURE,
                description=f"检测到手势: {cn}",
                gesture=current,
            )
            try:
                self._on_event(event)
            except Exception:
                pass

    def _update_face(self, count: int) -> None:
        """人脸出现/消失检测。连续 5 帧确认。"""
        with self._lock:
            self._face_count = count
            if count > 0:
                self._face_appear_count += 1
                self._face_disappear_count = 0
                if self._face_appear_count >= 5 and not self._face_present:
                    self._face_present = True
                    fire_appear = True
                else:
                    fire_appear = False
                fire_disappear = False
            else:
                self._face_disappear_count += 1
                self._face_appear_count = 0
                if self._face_disappear_count >= 5 and self._face_present:
                    self._face_present = False
                    fire_disappear = True
                else:
                    fire_disappear = False
                fire_appear = False

        if fire_appear:
            self._last_event_time = time.time()  # 重置空闲计时
            event = VisionEvent(
                event_type=EventType.FACE_APPEAR,
                description=f"检测到{self._face_count}张人脸进入画面" if self._face_count > 1 else "检测到人脸进入画面",
                face_count=self._face_count,
            )
            try:
                self._on_event(event)
            except Exception:
                pass

        if fire_disappear:
            self._last_event_time = time.time()  # 重置空闲计时
            event = VisionEvent(
                event_type=EventType.FACE_DISAPPEAR,
                description="人脸已离开画面",
                face_count=0,
            )
            try:
                self._on_event(event)
            except Exception:
                pass
