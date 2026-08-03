"""视觉监控（agent.core.daemon.vision_watcher）单元测试。

覆盖:
- 模型下载 _ensure_model（存在 / 下载成功 / 下载失败）
- VisionWatcher 依赖可用性判断
- start / stop 生命周期（模拟 mediapipe / cv2 / 摄像头 / 线程）
- 手势防抖 _update_gesture（连续 3 帧触发，None 不触发）
- 人脸出现/消失 _update_face（连续 5 帧确认）
- _process_frame 一帧处理与异常容错
- _fire_auto_stop 空闲自动停止

mediapipe / cv2 / 摄像头 / 网络下载全部用替身模拟，不调用真实硬件。

@author aceFelix
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

import agent.core.daemon.vision_watcher as vw
from agent.core.daemon.vision_watcher import EventType, VisionEvent, VisionWatcher


class _FakeThread:
    """不真正启动线程的替身。"""

    def __init__(self, target=None, daemon=None, name=None):
        self.target = target
        self.name = name
        self.started = False

    def start(self):
        self.started = True

    def join(self, timeout=None):
        pass


def _fake_module(name, **attrs):
    """构造可被 import 的假模块。"""
    m = ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


@pytest.fixture
def mediapipe_env(monkeypatch):
    """注入假 mediapipe 包 + cv2 + 线程，模拟完整依赖环境。"""
    # mediapipe 包结构
    mp_pkg = _fake_module("mediapipe", Image=MagicMock(), ImageFormat=MagicMock(SRGB="SRGB"))
    mp_tasks = _fake_module("mediapipe.tasks")
    mp_tasks.__path__ = []
    mp_python = _fake_module("mediapipe.tasks.python", BaseOptions=MagicMock())
    mp_python.__path__ = []
    vision_mod = _fake_module(
        "mediapipe.tasks.python.vision",
        GestureRecognizerOptions=MagicMock(),
        GestureRecognizer=MagicMock(),
        FaceDetectorOptions=MagicMock(),
        FaceDetector=MagicMock(),
    )
    mp_python.vision = vision_mod
    mp_tasks.python = mp_python
    mp_pkg.tasks = mp_tasks

    cv2_mod = _fake_module("cv2", VideoCapture=MagicMock())

    monkeypatch.setitem(sys.modules, "mediapipe", mp_pkg)
    monkeypatch.setitem(sys.modules, "mediapipe.tasks", mp_tasks)
    monkeypatch.setitem(sys.modules, "mediapipe.tasks.python", mp_python)
    monkeypatch.setitem(sys.modules, "mediapipe.tasks.python.vision", vision_mod)
    monkeypatch.setitem(sys.modules, "cv2", cv2_mod)
    monkeypatch.setattr(vw.threading, "Thread", _FakeThread)
    return vision_mod


@pytest.fixture
def camera_ok(mediapipe_env, monkeypatch):
    """默认让摄像头打开成功。"""
    cap = MagicMock()
    cap.isOpened.return_value = True
    import sys as _sys
    cv2_mod = _sys.modules["cv2"]
    cv2_mod.VideoCapture.return_value = cap
    return cap


# ---------------------------------------------------------------------------
# 模型下载
# ---------------------------------------------------------------------------


class TestEnsureModel:
    """_ensure_model 模型文件管理。"""

    def test_model_exists(self, monkeypatch, tmp_path):
        model_file = tmp_path / "gesture.task"
        model_file.write_bytes(b"model-data")
        monkeypatch.setattr(vw, "_models_dir", lambda: tmp_path)
        path = vw._ensure_model("http://x", "gesture.task")
        assert path == str(model_file)

    def test_model_downloads_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vw, "_models_dir", lambda: tmp_path)
        monkeypatch.setattr(
            vw.urllib.request, "urlretrieve",
            lambda url, path: (tmp_path / "m.task").write_bytes(b"downloaded"),
        )
        path = vw._ensure_model("http://x", "m.task")
        assert path is not None
        assert (tmp_path / "m.task").exists()

    def test_model_download_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vw, "_models_dir", lambda: tmp_path)
        monkeypatch.setattr(
            vw.urllib.request, "urlretrieve",
            lambda url, path: (_ for _ in ()).throw(OSError("网络错误")),
        )
        assert vw._ensure_model("http://x", "m.task") is None

    def test_empty_existing_file_triggers_download(self, monkeypatch, tmp_path):
        # 存在但大小为 0 的文件应重新下载
        (tmp_path / "m.task").write_bytes(b"")
        monkeypatch.setattr(vw, "_models_dir", lambda: tmp_path)
        monkeypatch.setattr(
            vw.urllib.request, "urlretrieve",
            lambda url, path: (tmp_path / "m.task").write_bytes(b"data"),
        )
        assert vw._ensure_model("http://x", "m.task") is not None


# ---------------------------------------------------------------------------
# 依赖可用性
# ---------------------------------------------------------------------------


class TestAvailability:
    """available / running 属性。"""

    def test_available_true_with_deps(self, mediapipe_env):
        watcher = VisionWatcher(on_event=lambda e: None)
        assert watcher.available is True

    def test_available_false_without_deps(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "mediapipe", None)
        monkeypatch.setitem(sys.modules, "cv2", None)
        watcher = VisionWatcher(on_event=lambda e: None)
        assert watcher.available is False
        assert watcher.start() is False

    def test_running_default_false(self, mediapipe_env):
        watcher = VisionWatcher(on_event=lambda e: None)
        assert watcher.running is False


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


class TestLifecycle:
    """start / stop / get_status。"""

    def _watch_env(self, monkeypatch, camera_ok, tmp_path, **kwargs):
        """构造一个可成功 start 的环境。"""
        monkeypatch.setattr(vw, "_ensure_model", lambda url, fname: str(tmp_path / fname))
        watcher = VisionWatcher(on_event=lambda e: None, **kwargs)
        return watcher

    def test_start_success(self, monkeypatch, camera_ok, tmp_path):
        watcher = self._watch_env(monkeypatch, camera_ok, tmp_path)
        assert watcher.start() is True
        assert watcher._started is True
        assert watcher.running is True
        assert watcher._gesture_recognizer is not None
        assert watcher._face_detector is not None
        assert watcher._thread is not None

    def test_start_skips_disabled_features(self, monkeypatch, camera_ok, tmp_path):
        watcher = self._watch_env(
            monkeypatch, camera_ok, tmp_path,
            enable_gesture=False, enable_face=False,
        )
        assert watcher.start() is True
        assert watcher._gesture_recognizer is None
        assert watcher._face_detector is None

    def test_start_model_download_fails(self, monkeypatch, camera_ok, tmp_path):
        monkeypatch.setattr(vw, "_ensure_model", lambda url, fname: None)
        watcher = VisionWatcher(on_event=lambda e: None)
        assert watcher.start() is False

    def test_start_camera_not_opened(self, monkeypatch, camera_ok, tmp_path):
        camera_ok.isOpened.return_value = False
        monkeypatch.setattr(vw, "_ensure_model", lambda url, fname: str(tmp_path / fname))
        watcher = VisionWatcher(on_event=lambda e: None)
        assert watcher.start() is False

    def test_stop_releases_resources(self, monkeypatch, camera_ok, tmp_path):
        watcher = self._watch_env(monkeypatch, camera_ok, tmp_path)
        assert watcher.start() is True
        watcher.stop()
        assert watcher._started is False
        assert watcher.running is False
        # 摄像头与模型被释放
        camera_ok.release.assert_called_once()
        assert watcher._cap is None

    def test_stop_closes_models(self, mediapipe_env):
        watcher = VisionWatcher(on_event=lambda e: None)
        gesture = MagicMock()
        face = MagicMock()
        watcher._gesture_recognizer = gesture
        watcher._face_detector = face
        watcher._cap = MagicMock()
        watcher.stop()
        gesture.close.assert_called_once()
        face.close.assert_called_once()

    def test_stop_without_start(self, mediapipe_env):
        watcher = VisionWatcher(on_event=lambda e: None)
        watcher.stop()  # 不应抛异常

    def test_get_status_fields(self, mediapipe_env):
        watcher = VisionWatcher(on_event=lambda e: None)
        watcher._running = True
        watcher._frame_count = 42
        status = watcher.get_status()
        assert status["running"] is True
        assert status["available"] is True
        assert status["camera_index"] == 0
        assert status["frame_count"] == 42
        assert status["current_gesture"] == "无"
        assert status["face_present"] is False


# ---------------------------------------------------------------------------
# 手势防抖
# ---------------------------------------------------------------------------


class TestGesture:
    """_update_gesture 连续 3 帧防抖。"""

    def test_gesture_fires_after_3_stable_frames(self):
        events = []
        watcher = VisionWatcher(on_event=events.append)
        for _ in range(3):
            watcher._update_gesture("Thumb_Up")

        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == EventType.GESTURE
        assert ev.gesture == "Thumb_Up"
        assert "点赞" in ev.description
        assert watcher._gesture_stable == "Thumb_Up"

    def test_none_gesture_never_fires(self):
        events = []
        watcher = VisionWatcher(on_event=events.append)
        for _ in range(5):
            watcher._update_gesture("None")
        assert events == []

    def test_gesture_switch_resets_counter(self):
        events = []
        watcher = VisionWatcher(on_event=events.append)
        # A A B B B → 只有 B 稳定 3 帧后触发
        for g in ["A", "A", "B", "B", "B"]:
            watcher._update_gesture(g)
        assert len(events) == 1
        assert events[0].gesture == "B"

    def test_same_gesture_no_repeat(self):
        events = []
        watcher = VisionWatcher(on_event=events.append)
        for _ in range(6):  # 稳定 6 帧只触发 1 次
            watcher._update_gesture("Open_Palm")
        assert len(events) == 1

    def test_unknown_gesture_name_passthrough(self):
        events = []
        watcher = VisionWatcher(on_event=events.append)
        for _ in range(3):
            watcher._update_gesture("外星手势")
        assert events[0].description == "检测到手势: 外星手势"

    def test_callback_exception_swallowed(self):
        def boom(event):
            raise RuntimeError("回调失败")

        watcher = VisionWatcher(on_event=boom)
        for _ in range(3):
            watcher._update_gesture("Thumb_Up")  # 不应抛异常

    def test_event_resets_idle_timer(self):
        watcher = VisionWatcher(on_event=lambda e: None)
        watcher._last_event_time = 0.0
        for _ in range(3):
            watcher._update_gesture("Thumb_Up")
        assert watcher._last_event_time > 0.0


# ---------------------------------------------------------------------------
# 人脸出现/消失
# ---------------------------------------------------------------------------


class TestFace:
    """_update_face 连续 5 帧确认。"""

    def test_face_appear_after_5_frames(self):
        events = []
        watcher = VisionWatcher(on_event=events.append)
        for _ in range(5):
            watcher._update_face(1)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == EventType.FACE_APPEAR
        assert "人脸进入画面" in ev.description
        assert ev.face_count == 1
        assert watcher._face_present is True

    def test_multiple_faces_description(self):
        events = []
        watcher = VisionWatcher(on_event=events.append)
        for _ in range(5):
            watcher._update_face(2)
        assert "检测到2张人脸进入画面" in events[0].description

    def test_face_disappear_after_5_frames(self):
        events = []
        watcher = VisionWatcher(on_event=events.append)
        watcher._face_present = True
        for _ in range(5):
            watcher._update_face(0)
        assert len(events) == 1
        assert events[0].event_type == EventType.FACE_DISAPPEAR
        assert watcher._face_present is False

    def test_face_no_repeat_after_present(self):
        events = []
        watcher = VisionWatcher(on_event=events.append)
        for _ in range(8):
            watcher._update_face(1)
        assert len(events) == 1  # 已出现后不重复触发

    def test_mixed_frames_reset_counters(self):
        events = []
        watcher = VisionWatcher(on_event=events.append)
        watcher._update_face(1)
        watcher._update_face(1)
        watcher._update_face(0)
        watcher._update_face(0)
        watcher._update_face(1)
        watcher._update_face(1)
        watcher._update_face(1)
        watcher._update_face(1)
        assert events == []  # 从未连续 5 帧
        assert watcher._face_appear_count == 4


# ---------------------------------------------------------------------------
# 一帧处理
# ---------------------------------------------------------------------------


class TestProcessFrame:
    """_process_frame 手势 + 人脸识别。"""

    def test_process_frame_triggers_both(self):
        events = []
        watcher = VisionWatcher(on_event=events.append)
        watcher._gesture_recognizer = MagicMock()
        watcher._gesture_recognizer.recognize.return_value = SimpleNamespace(
            gestures=[[SimpleNamespace(category_name="Thumb_Up")]]
        )
        watcher._face_detector = MagicMock()
        watcher._face_detector.detect.return_value = SimpleNamespace(detections=[1])

        img = SimpleNamespace()
        for _ in range(5):
            watcher._process_frame(img)

        types = {e.event_type for e in events}
        assert EventType.GESTURE in types
        assert EventType.FACE_APPEAR in types

    def test_process_frame_empty_gestures(self):
        events = []
        watcher = VisionWatcher(on_event=events.append)
        watcher._gesture_recognizer = MagicMock()
        watcher._gesture_recognizer.recognize.return_value = SimpleNamespace(gestures=[])
        img = SimpleNamespace()
        for _ in range(3):
            watcher._process_frame(img)
        # 无手势 → 不触发任何事件
        assert events == []

    def test_process_frame_recognizer_raises(self):
        events = []
        watcher = VisionWatcher(on_event=events.append)
        watcher._gesture_recognizer = MagicMock()
        watcher._gesture_recognizer.recognize.side_effect = RuntimeError("推理失败")
        watcher._face_detector = MagicMock()
        watcher._face_detector.detect.side_effect = RuntimeError("检测失败")
        watcher._process_frame(SimpleNamespace())  # 异常被吞
        assert events == []

    def test_process_frame_no_recognizers(self):
        watcher = VisionWatcher(on_event=lambda e: None)
        watcher._process_frame(SimpleNamespace())  # 空识别器不崩溃


# ---------------------------------------------------------------------------
# 空闲自动停止
# ---------------------------------------------------------------------------


class TestAutoStop:
    """_fire_auto_stop。"""

    def test_auto_stop_fires_event_and_cleans(self):
        events = []
        watcher = VisionWatcher(on_event=events.append, auto_stop_seconds=300)
        cap = MagicMock()
        watcher._cap = cap
        gesture = MagicMock()
        watcher._gesture_recognizer = gesture
        watcher._face_detector = None

        watcher._fire_auto_stop()

        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == EventType.AUTO_STOPPED
        assert "监控已空闲5分钟自动关闭" in ev.description
        assert watcher._running is False
        assert watcher._started is False
        assert watcher._cap is None
        cap.release.assert_called_once()
        gesture.close.assert_called_once()

    def test_auto_stop_callback_exception_swallowed(self):
        def boom(event):
            raise RuntimeError("通知失败")

        watcher = VisionWatcher(on_event=boom, auto_stop_seconds=300)
        watcher._cap = MagicMock()
        watcher._fire_auto_stop()  # 不应抛异常


# ---------------------------------------------------------------------------
# 事件模型
# ---------------------------------------------------------------------------


class TestEventModel:
    """EventType / VisionEvent。"""

    def test_event_type_values(self):
        assert EventType.GESTURE.value == "gesture"
        assert EventType.FACE_APPEAR.value == "face_appear"
        assert EventType.FACE_DISAPPEAR.value == "face_disappear"
        assert EventType.AUTO_STOPPED.value == "auto_stopped"

    def test_vision_event_defaults(self):
        ev = VisionEvent(event_type=EventType.GESTURE, description="x", gesture="Thumb_Up")
        assert ev.face_count == 0
        assert ev.timestamp  # 自动生成时间戳
