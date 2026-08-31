"""工作台指标采集与 UI 桥接测试。

覆盖：
- collect_metrics 返回结构与取值范围
- MetricsCollector 周期推事件
- WorkbenchUI 事件映射（含 ask_user 跨线程握手）
- WorkbenchRealtimeUI 实时事件扩展

@author aceFelix
"""

from __future__ import annotations

import queue
import threading
import time

from agent.ui.workbench.bridge import (
    WorkbenchRealtimeUI,
    WorkbenchUI,
    _EventEmitter,
)
from agent.ui.workbench.metrics import MetricsCollector, collect_metrics


# ---- 指标采集 ----

def test_collect_metrics_structure():
    """采集结果含 cpu/memory/disk 三键，百分比在合理区间。"""
    m = collect_metrics()
    assert set(m.keys()) == {"cpu", "memory", "disk"}
    assert 0 <= m["cpu"] <= 100
    # 磁盘信息应可用（标准库实现，不依赖 psutil）
    assert m["disk"].get("total_gb", 0) > 0
    assert 0 <= m["disk"]["percent"] <= 100


def test_metrics_collector_pushes_events():
    """采集线程按间隔推送 metrics 事件。"""
    q: queue.Queue = queue.Queue()
    collector = MetricsCollector(q, interval=0.05)
    collector.start()
    try:
        events = []
        deadline = time.time() + 3
        while len(events) < 2 and time.time() < deadline:
            try:
                events.append(q.get(timeout=0.5))
            except queue.Empty:
                continue
        assert len(events) >= 2
        assert all(e["type"] == "metrics" for e in events)
    finally:
        collector.stop()


# ---- UI 桥接 ----

def _drain(q: queue.Queue) -> list[dict]:
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            return items


def test_workbench_ui_maps_protocol_to_events():
    """UIProtocol 各方法逐一映射为事件。"""
    q: queue.Queue = queue.Queue()
    ui = WorkbenchUI(_EventEmitter(q))
    ui.assistant_text("你好")
    ui.assistant_thinking("思考中")
    ui.tool_use("Bash", {"command": "ls"}, "tool-1")
    ui.tool_result("Bash", "tool-1", "file.txt", is_error=False)
    ui.info("提示")
    ui.warn("警告")
    ui.error("错误")
    ui.assistant_done()

    types = [e["type"] for e in _drain(q)]
    assert types == [
        "assistant_text", "assistant_thinking", "tool_use", "tool_result",
        "info", "warn", "error", "assistant_done",
    ]


def test_workbench_ui_tool_input_fallback_to_str():
    """tool_input 含不可序列化对象时降级为字符串，不抛异常。"""
    q: queue.Queue = queue.Queue()
    ui = WorkbenchUI(_EventEmitter(q))

    class _Weird:
        pass

    ui.tool_use("Tool", {"obj": _Weird()}, "tool-2")
    event = _drain(q)[0]
    assert event["type"] == "tool_use"
    assert isinstance(event["payload"]["input"]["obj"], str)


def test_ask_user_handshake_across_threads():
    """ask_user 阻塞等待，answer_user 从另一线程回填。"""
    q: queue.Queue = queue.Queue()
    ui = WorkbenchUI(_EventEmitter(q))
    result: dict = {}

    def _ask() -> None:
        result["v"] = ui.ask_user("是否继续？")

    t = threading.Thread(target=_ask, daemon=True)
    t.start()
    # 等 ask_user 事件入队后回填答案
    event = q.get(timeout=2)
    assert event["type"] == "ask_user"
    ui.answer_user("y")
    t.join(timeout=2)
    assert result["v"] == "y"


def test_realtime_ui_extends_events_and_filters_info():
    """实时适配器追加状态/音量/转录事件，并过滤无效 info。"""
    q: queue.Queue = queue.Queue()
    ui = WorkbenchRealtimeUI(_EventEmitter(q))
    ui.on_status("listening")
    ui.on_volume(0.5)
    ui.on_user_speaking(True)
    ui.on_ai_speaking(True)
    ui.on_user_transcript("你好")
    ui.on_ai_transcript_delta("贾")
    ui.on_ai_transcript("贾维斯在此")
    ui.info("🎙️ 语音模式已开启")  # 应被过滤
    ui.info("有效提示")

    events = _drain(q)
    types = [e["type"] for e in events]
    assert types == [
        "status", "volume", "user_speaking", "ai_speaking",
        "user_transcript", "ai_transcript_delta", "ai_transcript", "info",
    ]
    assert ui.is_running() is True
