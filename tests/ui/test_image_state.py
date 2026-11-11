"""图片窗口的数据面测试：``CockpitState.images``、``VisualOptions`` 与 ``ImageManager``。

窗口渲染不在这里测（CI 无显示环境，见 ``test_cockpit.py`` 的同一条边界）：
``ImageManager`` 用假窗口注入来验证「有图才弹 / ✕ 后本轮不再弹 / 换轮重弹 /
hide_all / set_topmost / detach」这套生命周期。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.ui.cockpit import (
    IMAGE_MAX,
    CockpitState,
    VisualOptions,
    normalize_image_window,
    visual_enabled,
)
from agent.ui.cockpit_image import ImageManager


class _FakeState:
    """只带 ``images`` 的最小状态（ImageManager 只读这一个属性）。"""

    def __init__(self, images: list[dict[str, Any]] | None = None) -> None:
        self.images = images or []


class _FakeWindow:
    """记录调用的假窗口，替代真 Tk Toplevel。"""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.rendered = 0
        self.shown = 0
        self.hidden = 0
        self.resets = 0
        self.destroyed = False
        self.topmost: bool | None = None

    def render(self, items: list[dict[str, Any]]) -> None:
        self.items = list(items)
        self.rendered += 1

    def show(self) -> None:
        self.shown += 1

    def hide(self) -> None:
        self.hidden += 1

    def reset_interaction(self) -> None:
        self.resets += 1

    def set_topmost(self, enabled: bool) -> None:
        self.topmost = enabled

    def destroy(self) -> None:
        self.destroyed = True


def _image(path: str = "C:/shots/a.png", **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"path": path}
    data.update(extra)
    return data


class TestCockpitStateImages:
    def test_collects_path_and_meta_only(self) -> None:
        """只收路径与元信息；base64 之类的东西不该出现在状态里。"""
        state = CockpitState()
        state.update({"type": "run_start", "run_id": "r"})
        state.update(
            {
                "type": "image_show",
                "path": "C:/shots/a.png",
                "source": "ScreenShot",
                "width": 2560,
                "height": 1440,
                "bytes": 2048,
                "data": "BASE64SHOULDNOTSURVIVE",
            }
        )
        assert len(state.images) == 1
        assert state.images[0]["path"] == "C:/shots/a.png"
        assert state.images[0]["source"] == "ScreenShot"
        assert "data" not in state.images[0]

    def test_drops_entries_without_path(self) -> None:
        """没有路径就没有展示位：不入库，免得窗口画一张空图。"""
        state = CockpitState()
        state.update({"type": "run_start", "run_id": "r"})
        state.update({"type": "image_show", "source": "ScreenShot"})
        assert len(state.images) == 0

    def test_keeps_only_latest_image_max(self) -> None:
        """超出 IMAGE_MAX 时只留最新的那些。"""
        state = CockpitState()
        state.update({"type": "run_start", "run_id": "r"})
        for index in range(IMAGE_MAX + 3):
            state.update({"type": "image_show", "path": f"C:/shots/{index}.png"})
        assert len(state.images) == IMAGE_MAX
        assert state.images[0]["path"] == "C:/shots/3.png"
        assert state.images[-1]["path"] == f"C:/shots/{IMAGE_MAX + 2}.png"

    def test_new_run_clears_images(self) -> None:
        """换轮清空：上一轮的图不该留在这一轮的窗里。"""
        state = CockpitState()
        state.update({"type": "run_start", "run_id": "r1"})
        state.update({"type": "image_show", "path": "C:/shots/a.png"})
        state.update({"type": "run_start", "run_id": "r2"})
        assert len(state.images) == 0

    def test_subagent_images_are_kept(self) -> None:
        """子代理截的图照样收下（不进步数统计，但「谁截了什么」要看得见）。"""
        state = CockpitState()
        state.update({"type": "run_start", "run_id": "r"})
        state.update(
            {
                "type": "image_show",
                "path": "C:/shots/sub.png",
                "agent_id": "researcher@team",
                "agent_name": "researcher",
            }
        )
        assert len(state.images) == 1
        assert state.steps == 0


class TestImageOptions:
    def test_normalize_image_window(self) -> None:
        """auto / off 两态；未知值降级 auto，大小写不敏感。"""
        assert normalize_image_window("off") == "off"
        assert normalize_image_window("OFF") == "off"
        assert normalize_image_window("auto") == "auto"
        assert normalize_image_window("") == "auto"
        assert normalize_image_window(None) == "auto"
        assert normalize_image_window("window") == "auto"

    def test_clamps_numeric_fields(self) -> None:
        """image_max 钳到 1–24，image_thumb_px 钳到 48–160。"""
        options = VisualOptions.from_values(image_max=999, image_thumb_px=1)
        assert options.image_max == 24
        assert options.image_thumb_px == 48
        options = VisualOptions.from_values(image_max=0, image_thumb_px=9999)
        assert options.image_max == 1
        assert options.image_thumb_px == 160

    def test_image_on_follows_master_switch(self) -> None:
        """子开关打开但总开关关掉时，图片窗不得显示。"""
        assert VisualOptions().image_on() is True
        assert VisualOptions.from_values(image_window="off").image_on() is False
        assert VisualOptions.from_values(enabled=False).image_on() is False

    def test_any_enabled_counts_image_window_alone(self) -> None:
        """只开图片窗时也要算「有可视化」——否则 feed() 会在入口直接返回。"""
        options = VisualOptions.from_values(
            task_cockpit="off",
            tool_cards="off",
            agent_cards="off",
            crawl_window="off",
            image_window="auto",
        )
        assert options.any_enabled() is True
        assert visual_enabled(options, False) is True


class TestImageManagerLifecycle:
    def _manager(self) -> tuple[ImageManager, _FakeWindow]:
        manager = ImageManager(root=None, options=VisualOptions())
        window = _FakeWindow()
        manager._window = window  # 绕过 Tk：只验证生命周期编排
        return manager, window

    def test_has_data(self) -> None:
        assert ImageManager.has_data(_FakeState()) is False
        assert ImageManager.has_data(_FakeState([_image()])) is True

    def test_sync_renders_and_shows(self) -> None:
        manager, window = self._manager()
        manager.sync(_FakeState([_image()]), "r1")
        assert window.rendered == 1
        assert window.shown == 1
        assert window.resets == 1  # 换轮（首次）重置交互
        assert window.items[0]["path"] == "C:/shots/a.png"

    def test_sync_without_images_does_nothing(self) -> None:
        manager, window = self._manager()
        manager.sync(_FakeState(), "r1")
        assert window.rendered == 0
        assert window.shown == 0

    def test_hidden_run_does_not_report(self) -> None:
        """点 ✕ 后本轮不再自动弹；下一轮有新图才重新出现。"""
        manager, window = self._manager()
        manager.sync(_FakeState([_image()]), "r1")
        manager._on_close()
        manager.sync(_FakeState([_image("C:/shots/b.png")]), "r1")
        assert window.rendered == 1
        manager.sync(_FakeState([_image("C:/shots/c.png")]), "r2")
        assert window.rendered == 2
        assert window.items[0]["path"] == "C:/shots/c.png"

    def test_hide_all_and_show_all(self) -> None:
        manager, window = self._manager()
        state = _FakeState([_image()])
        manager.sync(state, "r1")
        manager.hide_all()
        assert window.hidden == 1
        manager.show_all(state)
        assert window.shown == 2

    def test_set_topmost_propagates(self) -> None:
        manager, window = self._manager()
        manager.set_topmost(False)
        assert window.topmost is False
        manager.set_topmost(True)
        assert window.topmost is True

    def test_detach_destroys_window(self) -> None:
        manager, window = self._manager()
        manager.detach()
        assert window.destroyed is True
        assert manager._window is None

    def test_image_window_off_creates_nothing(self) -> None:
        """``image_window = "off"`` 时整条链路零副作用。"""
        manager = ImageManager(root=None, options=VisualOptions.from_values(image_window="off"))
        window = _FakeWindow()
        manager._window = window
        manager.sync(_FakeState([_image()]), "r1")
        assert window.rendered == 0
