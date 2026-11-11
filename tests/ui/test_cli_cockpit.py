"""RichCLI 与独立驾驶舱窗口的接线测试（不创建真实 Tk 窗口）。

验证：首次 run_start/tool_start 懒创建窗口、run_end 不关闭、
off/语音不创建、退出时 shutdown、异常不打断主流程。

@author aceFelix
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("rich")

from agent.ui import cli as cli_module
from agent.ui.cli import RichCLI


class _FakeWindow:
    """记录 feed / shutdown 的假窗口控制器。"""

    instances: list["_FakeWindow"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.events: list[dict] = []
        self.shutdowns = 0
        _FakeWindow.instances.append(self)

    def feed(self, event: dict) -> None:
        self.events.append(event)

    def shutdown(self) -> None:
        self.shutdowns += 1


class _BoomWindow(_FakeWindow):
    """feed 必抛异常，验证驾驶舱故障不影响主流程。"""

    def feed(self, event: dict) -> None:
        raise RuntimeError("window boom")


def _make_ui(*, visual_cockpit: str = "window", visual_options=None) -> RichCLI:
    """构造 RichCLI，console 指向 StringIO（避免测试污染真实终端）。"""
    from rich.console import Console
    from agent.ui.cockpit import VisualOptions

    options = visual_options or VisualOptions.from_values(task_cockpit=visual_cockpit)
    ui = RichCLI(boot_animation=False, visual_options=options)
    ui._console = Console(
        file=io.StringIO(),
        force_terminal=False,
        width=100,
        color_system=None,
    )
    return ui


class TestWindowRouting:
    def test_first_run_start_creates_window_and_forwards(self, monkeypatch) -> None:
        """首次 run_start 懒创建窗口，后续事件持续转发。"""
        _FakeWindow.instances = []
        monkeypatch.setattr(cli_module, "CockpitWindow", _FakeWindow)
        ui = _make_ui()

        ui.on_trace_event({"type": "run_start", "run_id": "r"})
        ui.on_trace_event({"type": "tool_start", "tool": "Bash"})
        ui.on_trace_event({"type": "run_end", "steps": 1, "failed": 0, "duration_ms": 100})

        assert len(_FakeWindow.instances) == 1
        window = _FakeWindow.instances[0]
        assert [e["type"] for e in window.events] == ["run_start", "tool_start", "run_end"]
        # run_end 只更新汇总，窗口不关闭
        assert window.shutdowns == 0
        assert ui._cockpit_window is window

    def test_tool_start_without_run_start_also_creates(self, monkeypatch) -> None:
        """防御路径：事件流缺 run_start 时，tool_start 也能拉起窗口。"""
        _FakeWindow.instances = []
        monkeypatch.setattr(cli_module, "CockpitWindow", _FakeWindow)
        ui = _make_ui()

        ui.on_trace_event({"type": "tool_start", "tool": "Bash"})
        assert len(_FakeWindow.instances) == 1

    def test_off_mode_creates_no_window(self, monkeypatch) -> None:
        """主 HUD、浮窗、代理卡、抓取窗与图片窗都 off 时完全不创建窗口。"""
        _FakeWindow.instances = []
        monkeypatch.setattr(cli_module, "CockpitWindow", _FakeWindow)
        from agent.ui.cockpit import VisualOptions

        ui = _make_ui(
            visual_options=VisualOptions.from_values(
                task_cockpit="off",
                tool_cards="off",
                agent_cards="off",
                crawl_window="off",
                image_window="off",
            )
        )

        ui.on_trace_event({"type": "run_start", "run_id": "r"})
        ui.on_trace_event({"type": "tool_start", "tool": "Bash"})
        assert _FakeWindow.instances == []
        assert ui._cockpit_window is None

    def test_voice_mode_still_creates_window(self, monkeypatch) -> None:
        """语音模式同样创建任务窗口（2026-11-09：/voice 也要操作可视化）。"""
        _FakeWindow.instances = []
        monkeypatch.setattr(cli_module, "CockpitWindow", _FakeWindow)
        ui = _make_ui()
        ui._voice_mode = True

        ui.on_trace_event({"type": "run_start", "run_id": "r"})
        assert len(_FakeWindow.instances) == 1
        assert ui._cockpit_window is _FakeWindow.instances[0]

    def test_legacy_modes_become_window(self, monkeypatch) -> None:
        """历史配置 auto/terminal 一律按独立窗口工作。"""
        _FakeWindow.instances = []
        monkeypatch.setattr(cli_module, "CockpitWindow", _FakeWindow)
        for legacy in ("auto", "terminal"):
            ui = _make_ui(visual_cockpit=legacy)
            ui.on_trace_event({"type": "run_start", "run_id": legacy})
            assert ui._cockpit_window is not None
        assert len(_FakeWindow.instances) == 2

    def test_master_switch_off_creates_no_window(self, monkeypatch) -> None:
        """[visualization] enabled=false 时即使子项为 window 也不创建。"""
        _FakeWindow.instances = []
        monkeypatch.setattr(cli_module, "CockpitWindow", _FakeWindow)
        from agent.ui.cockpit import VisualOptions

        ui = _make_ui(visual_options=VisualOptions.from_values(enabled=False))
        ui.on_trace_event({"type": "run_start", "run_id": "r"})
        assert _FakeWindow.instances == []
        assert ui._cockpit_window is None

    def test_cards_only_still_creates_window(self, monkeypatch) -> None:
        """主 HUD off、浮窗 auto 时仍需 Tk 根窗口承载卡片。"""
        _FakeWindow.instances = []
        monkeypatch.setattr(cli_module, "CockpitWindow", _FakeWindow)
        from agent.ui.cockpit import VisualOptions

        ui = _make_ui(
            visual_options=VisualOptions.from_values(
                task_cockpit="off", tool_cards="auto"
            )
        )
        ui.on_trace_event({"type": "run_start", "run_id": "r"})
        assert len(_FakeWindow.instances) == 1

    def test_all_subfeatures_off_creates_no_window(self, monkeypatch) -> None:
        """主 HUD、浮窗、代理卡、抓取窗与图片窗都 off 时不创建任何窗口。"""
        _FakeWindow.instances = []
        monkeypatch.setattr(cli_module, "CockpitWindow", _FakeWindow)
        from agent.ui.cockpit import VisualOptions

        ui = _make_ui(
            visual_options=VisualOptions.from_values(
                task_cockpit="off",
                tool_cards="off",
                agent_cards="off",
                crawl_window="off",
                image_window="off",
            )
        )
        ui.on_trace_event({"type": "run_start", "run_id": "r"})
        assert _FakeWindow.instances == []

    def test_crawl_only_still_creates_window(self, monkeypatch) -> None:
        """只开抓取窗时也要拉起 Tk 根窗口（Toplevel 需要 Tk 线程）。"""
        _FakeWindow.instances = []
        monkeypatch.setattr(cli_module, "CockpitWindow", _FakeWindow)
        from agent.ui.cockpit import VisualOptions

        ui = _make_ui(
            visual_options=VisualOptions.from_values(
                task_cockpit="off",
                tool_cards="off",
                agent_cards="off",
                crawl_window="auto",
            )
        )
        ui.on_trace_event({"type": "run_start", "run_id": "r"})
        assert len(_FakeWindow.instances) == 1

    def test_agent_cards_only_still_creates_window(self, monkeypatch) -> None:
        """只开代理卡时也要拉起 Tk 根窗口（卡片需要父容器的 Tk 线程）。"""
        _FakeWindow.instances = []
        monkeypatch.setattr(cli_module, "CockpitWindow", _FakeWindow)
        from agent.ui.cockpit import VisualOptions

        ui = _make_ui(
            visual_options=VisualOptions.from_values(
                task_cockpit="off", tool_cards="off", agent_cards="auto"
            )
        )
        ui.on_trace_event({"type": "run_start", "run_id": "r"})
        assert len(_FakeWindow.instances) == 1

    def test_image_only_still_creates_window(self, monkeypatch) -> None:
        """只开图片窗时也要拉起 Tk 根窗口（图片窗是需要父容器的 Toplevel）。"""
        _FakeWindow.instances = []
        monkeypatch.setattr(cli_module, "CockpitWindow", _FakeWindow)
        from agent.ui.cockpit import VisualOptions

        ui = _make_ui(
            visual_options=VisualOptions.from_values(
                task_cockpit="off",
                tool_cards="off",
                agent_cards="off",
                crawl_window="off",
                image_window="auto",
            )
        )
        ui.on_trace_event({"type": "run_start", "run_id": "r"})
        assert len(_FakeWindow.instances) == 1

    def test_goodbye_shuts_window_down(self, monkeypatch) -> None:
        """退出时关闭窗口，避免进程退出后残留 Tk 线程。"""
        _FakeWindow.instances = []
        monkeypatch.setattr(cli_module, "CockpitWindow", _FakeWindow)
        ui = _make_ui()
        ui.on_trace_event({"type": "run_start", "run_id": "r"})

        ui.goodbye()
        assert _FakeWindow.instances[0].shutdowns == 1

    def test_window_failure_does_not_break_trace(self, monkeypatch) -> None:
        """窗口 feed 抛异常：主流程不受影响，控制器被置空待重建。"""
        _BoomWindow.instances = []
        monkeypatch.setattr(cli_module, "CockpitWindow", _BoomWindow)
        ui = _make_ui()

        ui.on_trace_event({"type": "run_start", "run_id": "r"})
        assert ui._cockpit_window is None
