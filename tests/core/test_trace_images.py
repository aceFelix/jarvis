"""``image_show`` 事件派生测试。

``orchestrator`` 的 ``tool_after`` payload **不含 images**，所以图片必须由工具
通过 ``trace_meta["image"]`` 上报；本文件钉住这条通道的三件事：

1. dict / list 两种形态都能展开成事件；
2. 脏数据（非 dict、缺 path）只跳过这一条，不抛、不写假路径；
3. 事件里只有路径与元信息，**绝不出现 base64**（隐私与体积双重要求）。

@author aceFelix
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.core.trace import TraceRecorder


def _recorder(tmp_path: Path) -> tuple[TraceRecorder, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    recorder = TraceRecorder(
        enabled=True, ui=None, base_dir=tmp_path, on_event=events.append
    )
    recorder.start_run(0)
    return recorder, events


def _tool_after(recorder: TraceRecorder, trace_meta: Any) -> None:
    recorder.on_tool_before(
        {"tool_name": "ScreenShot", "tool_input": {}, "tool_use_id": "tu1"}
    )
    recorder.on_tool_after(
        {
            "tool_name": "ScreenShot",
            "tool_use_id": "tu1",
            "is_error": False,
            "result": "已截图",
            "trace_meta": trace_meta,
        }
    )


def _images(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("type") == "image_show"]


class TestImageEventDerivation:
    def test_single_dict_becomes_one_event(self, tmp_path: Path) -> None:
        recorder, events = _recorder(tmp_path)
        _tool_after(
            recorder,
            {"image": {"path": "C:/shots/a.png", "source": "ScreenShot", "width": 80}},
        )
        images = _images(events)
        assert len(images) == 1
        assert images[0]["path"] == "C:/shots/a.png"
        assert images[0]["source"] == "ScreenShot"
        assert images[0]["width"] == 80

    def test_list_becomes_many_events_in_order(self, tmp_path: Path) -> None:
        recorder, events = _recorder(tmp_path)
        _tool_after(
            recorder,
            {"image": [{"path": "C:/a.png"}, {"path": "C:/b.png"}]},
        )
        assert [event["path"] for event in _images(events)] == ["C:/a.png", "C:/b.png"]

    def test_entry_without_path_is_skipped(self, tmp_path: Path) -> None:
        """没有路径宁可不上报，也不写假路径。"""
        recorder, events = _recorder(tmp_path)
        _tool_after(recorder, {"image": [{"source": "ScreenShot"}, {"path": ""}]})
        assert _images(events) == []

    def test_dirty_payload_does_not_raise(self, tmp_path: Path) -> None:
        """字符串/None/嵌套垃圾都只是不进事件，不能炸工具主流程。"""
        recorder, events = _recorder(tmp_path)
        _tool_after(recorder, {"image": "C:/a.png"})
        _tool_after(recorder, {"image": [None, 3, "x", {"path": "C:/ok.png"}]})
        assert [event["path"] for event in _images(events)] == ["C:/ok.png"]

    def test_no_base64_in_event(self, tmp_path: Path) -> None:
        """即使上游误塞了 data/base64，事件也不该带出去。"""
        recorder, events = _recorder(tmp_path)
        _tool_after(
            recorder,
            {"image": {"path": "C:/a.png", "data": "BASE64SHOULDNOTLEAK"}},
        )
        image = _images(events)[0]
        assert "data" not in image

    def test_event_is_written_to_jsonl(self, tmp_path: Path) -> None:
        """落盘那条也要有：``/trace`` 回看靠它。"""
        recorder, _events = _recorder(tmp_path)
        _tool_after(recorder, {"image": {"path": "C:/a.png"}})
        lines = (tmp_path / f"{recorder.run_id}.jsonl").read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines]
        assert any(record.get("type") == "image_show" for record in records)
        stored = next(record for record in records if record.get("type") == "image_show")
        assert stored["path"] == "C:/a.png"
        assert "data" not in stored

    def test_agent_meta_is_carried(self, tmp_path: Path) -> None:
        """子代理的图带 agent_id，窗口能据此知道是谁截的。"""
        recorder, events = _recorder(tmp_path)

        class _Ctx:
            extra = {"agent_id": "researcher@team", "agent_name": "researcher"}

        recorder.on_tool_before(
            {"tool_name": "ScreenShot", "tool_input": {}, "tool_use_id": "tu2", "ctx": _Ctx()}
        )
        recorder.on_tool_after(
            {
                "tool_name": "ScreenShot",
                "tool_use_id": "tu2",
                "is_error": False,
                "result": "ok",
                "trace_meta": {"image": {"path": "C:/sub.png"}},
            }
        )
        image = _images(events)[0]
        assert image["agent_id"] == "researcher@team"
        assert image["agent_name"] == "researcher"
