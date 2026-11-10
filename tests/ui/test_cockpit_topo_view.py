"""拓扑撕出（P3.6）共享组件测试：手势状态机、动画插值、fit、共享状态与绘制。

纯逻辑 + FakeCanvas，不创建真实 Tk 窗口。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.ui.cockpit_crawl_topo import build_topology
from agent.ui.cockpit_topo_view import (
    ARM_PX,
    DROP_PX,
    MAX_SCALE,
    TearOffGesture,
    TopoView,
    TopoViewState,
    alpha_at,
    ease_out,
    fit_view,
    interpolate_rect,
)


class TestTearOffGesture:
    """手势只看区域 + 位移：8px 武装、80px/出区域落点、Esc 取消。"""

    def test_click_without_drag(self) -> None:
        g = TearOffGesture()
        g.press(10, 10)
        assert g.motion(11, 11) is False
        assert g.release(11, 11, inside_topo=True) == "click"

    def test_arm_threshold(self) -> None:
        g = TearOffGesture()
        g.press(0, 0)
        assert g.motion(ARM_PX - 1, 0) is False
        assert g.motion(ARM_PX + 1, 0) is True
        assert g.armed is True

    def test_detach_when_released_outside(self) -> None:
        g = TearOffGesture()
        g.press(0, 0)
        g.motion(30, 0)
        assert g.release(30, 0, inside_topo=False) == "detach"

    def test_detach_when_drop_distance_large_even_inside(self) -> None:
        g = TearOffGesture()
        g.press(0, 0)
        g.motion(DROP_PX + 10, 0)
        assert g.release(DROP_PX + 10, 0, inside_topo=True) == "detach"

    def test_small_drop_inside_cancels(self) -> None:
        """武装了但只拖了一小段就松手：不生成独立窗。"""
        g = TearOffGesture()
        g.press(0, 0)
        g.motion(20, 0)
        assert g.release(20, 0, inside_topo=True) == "cancel"

    def test_escape_cancels(self) -> None:
        g = TearOffGesture()
        g.press(0, 0)
        g.motion(50, 0)
        g.cancel()
        assert g.active is False
        assert g.release(50, 0, inside_topo=False) == "none"

    def test_motion_without_press_is_inert(self) -> None:
        g = TearOffGesture()
        assert g.motion(100, 100) is False
        assert g.release(100, 100, inside_topo=False) == "none"


class TestAnimation:
    def test_ease_out_endpoints_and_monotonic(self) -> None:
        assert ease_out(0) == 0.0
        assert ease_out(1) == 1.0
        values = [ease_out(i / 10) for i in range(11)]
        assert values == sorted(values)
        # ease-out：前半程推进更快
        assert ease_out(0.5) > 0.5

    def test_interpolate_rect_endpoints(self) -> None:
        r0 = (0.0, 0.0, 960.0, 720.0)
        r1 = (100.0, 200.0, 524.0, 160.0)
        assert interpolate_rect(r0, r1, 0) == r0
        assert interpolate_rect(r0, r1, 1) == r1
        mid = interpolate_rect(r0, r1, 0.5)
        assert 0 < mid[0] < 100
        assert 524 < mid[2] < 960  # 尺寸同步收缩

    def test_alpha_fades_to_point_two(self) -> None:
        assert alpha_at(0) == pytest.approx(1.0)
        assert alpha_at(1) == pytest.approx(0.2)
        values = [alpha_at(i / 10) for i in range(11)]
        assert values == sorted(values, reverse=True)


class TestFitView:
    def test_fit_centers_and_scales(self) -> None:
        result = fit_view([(0, 0), (100, 50)], 200, 100, margin=0.05)
        assert result is not None
        scale, pan_x, pan_y = result
        assert scale == pytest.approx(min(180 / 100, 90 / 50))
        # 变换后 bbox 居中：(min*scale+pan) 与 (max*scale+pan) 到两边距离相等
        left = 0 * scale + pan_x
        right = 100 * scale + pan_x
        assert left == pytest.approx(200 - right)

    def test_empty_returns_none(self) -> None:
        assert fit_view([], 200, 100) is None
        assert fit_view([(0, 0)], 0, 100) is None

    def test_huge_graph_scale_has_floor(self) -> None:
        result = fit_view([(0, 0), (100_000, 50)], 500, 200)
        assert result is not None
        assert result[0] >= 0.2


class TestTopoViewState:
    def test_reset(self) -> None:
        state = TopoViewState(scale=2.0, pan_x=10, pan_y=-5,
                              expanded_layers={1: 16}, selected_url="u")
        state.reset()
        assert state.scale == 1.0
        assert state.pan_x == 0.0
        assert state.pan_y == 0.0
        assert state.expanded_layers == {}
        assert state.selected_url is None


class _FakeCanvas:
    """只记录调用、不真的画。"""

    def __getattr__(self, _name):
        return lambda *args, **kwargs: 1


def _layout():
    return build_topology(
        [
            {"url": "https://s/", "status": "ok", "depth": 0},
            {"url": "https://s/a", "status": "ok", "depth": 1, "parent_url": "https://s/"},
        ],
        width=400, height=120, x0=20, y0=40,
    )


class TestTopoView:
    def _view(self) -> tuple[TopoView, list[str]]:
        calls: list[str] = []
        view = TopoView(_FakeCanvas(), TopoViewState(), on_change=lambda: calls.append("redraw"))
        return view, calls

    def test_draw_registers_maps(self) -> None:
        view, _calls = self._view()
        view.draw(_layout(), pad=20, top=40)
        assert view.topo_urls == {0: "https://s/", 1: "https://s/a"}
        assert view.aggregate_depths == {}

    def test_hit_node_and_expand(self) -> None:
        view, calls = self._view()
        view.draw(_layout(), pad=20, top=40)
        assert view.hit(["topo_node", "topo_idx:1"]) == ("node", "https://s/a")
        assert view.hit(["other"]) is None
        view.expand(1)
        view.expand(1)
        assert view.state.expanded_layers == {1: 24}
        assert len(calls) == 2

    def test_zoom_clamps(self) -> None:
        view, _calls = self._view()
        for _ in range(4):
            view.zoom(100, 60, 2.0, 0, 0, 400, 120)
        assert view.state.scale == MAX_SCALE
        for _ in range(4):
            view.zoom(100, 60, 0.01, 0, 0, 400, 120)
        assert view.state.scale == 0.55

    def test_reset_view_clears_view_not_selection(self) -> None:
        view, _calls = self._view()
        view.state.scale = 2.0
        view.state.expanded_layers = {1: 16}
        view.state.selected_url = "https://s/a"
        view.reset_view()
        assert view.state.scale == 1.0
        assert view.state.expanded_layers == {}
        assert view.state.selected_url == "https://s/a"

    def test_shared_state_across_two_views(self) -> None:
        """驾驶舱与独立窗共享同一状态对象：一边改，另一边可见。"""
        state = TopoViewState()
        a = TopoView(_FakeCanvas(), state, on_change=lambda: None)
        b = TopoView(_FakeCanvas(), state, on_change=lambda: None)
        a.state.selected_url = "https://s/a"
        a.expand(1)
        assert b.state.selected_url == "https://s/a"
        assert b.state.expanded_layers == {1: 16}
