"""桌面「停止」按钮延迟复现测试（真实 AnthropicProvider + 本地 fake SSE）。

背景 bug：桌面壳点停止后，思考流仍在输出、状态栏长期停在"正在停止..."。
本测试把整条生产链路（ChatEngine._command_loop → _handle_send → QueryLoop.run
→ _stream_once → AnthropicProvider.stream → anthropic SDK → httpx）与真实
桌面唯一不同的是 LLM 端点换成本地 fake SSE 服务器（慢速吐 thinking 增量），
从测试线程调 abort_current_reply，量化：
- 发出取消 → 最后一个 assistant_thinking 事件入队的延迟
- 发出取消 → assistant_done 事件入队的延迟

若 anthropic 路径取消即时生效，两个延迟都应在百毫秒级；否则即复现 bug。

@author aceFelix
"""

from __future__ import annotations

import json
import queue
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent.config.settings import Settings
from agent.core.context import ToolContext
from agent.core.query_loop import QueryLoop
from agent.core.tool import ToolRegistry
from agent.llm.anthropic_provider import AnthropicProvider
from agent.ui.workbench.engine import ChatEngine


class _NoopOrchestrator:
    """本复现不发起工具调用，编排器仅占位。"""

    async def execute_calls(self, tool_uses, ctx):
        raise AssertionError("不应走到工具执行")


class _SlowThinkingProvider:
    """纯 asyncio 慢速思考流：每 30ms 一条 ThinkingDelta，300 条后 Stop。"""

    name = "slow-thinking"
    default_model = "slow-1"

    async def stream(self, *, model, system, messages, tools, max_tokens=4096,
                     temperature=None):
        import asyncio

        from agent.llm.base import Stop, ThinkingDelta, Usage

        for i in range(300):
            yield ThinkingDelta(text=f"思考{i} ")
            await asyncio.sleep(0.03)
        yield Stop(reason="stop", usage=Usage())

    def set_thinking_enabled(self, enabled): ...
    def is_thinking_enabled(self): return True
    async def close(self): ...


# ---------------------------------------------------------------------------
# fake Anthropic 兼容 SSE 服务器：慢速持续吐 thinking 增量，永不自然结束
# ---------------------------------------------------------------------------

class _SlowSSEHandler(BaseHTTPRequestHandler):
    """POST /v1/messages → SSE 流：header + 300 个 thinking delta（每个 30ms）。

    总时长约 9s，足够在流中途发起 abort；客户端断开时 write 抛异常自然退出。
    """

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 命名约定
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        events = [
            ("message_start", {"type": "message_start", "message": {
                "id": "msg_fake", "type": "message", "role": "assistant",
                "model": "fake", "content": [], "stop_reason": None,
                "usage": {"input_tokens": 10, "output_tokens": 0}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "thinking", "thinking": ""}}),
        ]
        try:
            for name, obj in events:
                self._send_sse(name, obj)
            for i in range(300):
                self._send_sse("content_block_delta", {
                    "type": "content_block_delta", "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": f"思考{i} "}})
                time.sleep(0.03)
        except (OSError, BrokenPipeError):
            return  # 客户端（abort）已断开，正常退出

    def _send_sse(self, name: str, obj: dict) -> None:
        data = f"event: {name}\ndata: {json.dumps(obj)}\n\n".encode()
        self.wfile.write(data)
        self.wfile.flush()

    def log_message(self, *args):  # 静音访问日志
        pass


def _start_fake_server() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _SlowSSEHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    return srv, f"http://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# 链路搭建：真实引擎 + 真实 QueryLoop + 真实 AnthropicProvider
# ---------------------------------------------------------------------------

def _drain_until(event_q: queue.Queue, predicate, timeout: float):
    """轮询事件队列直到 predicate(events) 为真，返回事件列表（含中途收到的）。"""
    events: list[dict] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            events.append(event_q.get(timeout=0.02))
        except queue.Empty:
            pass
        if predicate(events):
            return events
    return events


def _run_abort_latency_scenario(engine_loop_provider, tmp_path: Path, monkeypatch) -> float:
    """搭真实引擎跑指定 provider，thinking 流中 abort，返回取消延迟（秒）。"""
    from agent.ui.workbench import checkpoint_ops

    class _NoopCkpt:
        available = staticmethod(lambda: False)
        create = lambda self, reason="": None

    monkeypatch.setattr(checkpoint_ops, "make_manager", lambda eng: _NoopCkpt())

    event_q: queue.Queue = queue.Queue()
    command_q: queue.Queue = queue.Queue()
    settings = Settings()
    settings.enable_mcp = False
    settings.workdir = str(tmp_path)
    engine = ChatEngine(settings, event_q, command_q)

    # 用引擎自带的真实 UI 桥接（engine._ui=WorkbenchUI），它会把
    # assistant_thinking / assistant_done 投进 event_q——与生产桌面一致。
    # 不能用自造 UI 桩：那样思考事件不会进事件队列，测不到真实链路。
    ui = engine._ui
    messages: list = []
    ctx = ToolContext(workdir=str(tmp_path), messages=messages, ui=ui)
    # 真实 QueryLoop（无工具注册表 → 纯思考流），挂到预装配会话上
    loop = QueryLoop(
        provider=engine_loop_provider,
        registry=ToolRegistry(),
        orchestrator=_NoopOrchestrator(),
        enable_compaction=False,
        deferred_loading=False,
        chat_detection=False,
    )
    engine._session_ready = True
    engine._query_loop = loop
    engine._ctx = ctx
    engine._messages = messages
    engine._after_turn = lambda: None  # 屏蔽存盘/标题生成

    engine.start()
    try:
        command_q.put_nowait({"cmd": "send", "text": "复现停止延迟"})
        # 等第一轮 thinking 事件真正流起来
        seen = _drain_until(
            event_q,
            lambda evs: any(e["type"] == "assistant_thinking" for e in evs)
            and engine._send_task is not None,
            timeout=10,
        )
        assert any(e["type"] == "assistant_thinking" for e in seen), (
            f"未产生思考流，实际事件：{[(e['type'], str(e['payload'])[:200]) for e in seen]}"
        )

        t_abort = time.monotonic()
        assert engine.abort_current_reply() is True

        done = _drain_until(
            event_q,
            lambda evs: any(e["type"] == "assistant_done" for e in evs),
            timeout=20,
        )
        t_done = time.monotonic()
        types = [e["type"] for e in done]
        assert "assistant_done" in types, f"abort 后未收尾：{types[-5:]}"
        return t_done - t_abort
    finally:
        engine.stop()


def test_abort_stops_fake_async_thinking_stream_promptly(tmp_path: Path, monkeypatch):
    """对照组：纯 asyncio provider，验证引擎接线与取消链路本身。"""
    latency = _run_abort_latency_scenario(_SlowThinkingProvider(), tmp_path, monkeypatch)
    print(f"\n[abort-latency] pure asyncio fake provider: {latency:.3f}s")
    assert latency < 1.0


def test_abort_stops_anthropic_thinking_stream_promptly(tmp_path: Path, monkeypatch):
    """核心复现：thinking 流式输出中 abort → 取消应立即穿透 SDK 流。

    workdir 用空临时目录；检查点管理器抹成 no-op：两者都是本轮刚上的
    同步 git 操作，会卡住引擎 loop 十几秒，干扰对「取消延迟」的测量。
    """
    srv, base_url = _start_fake_server()
    provider = AnthropicProvider(api_key="fake-key", base_url=base_url)
    try:
        latency = _run_abort_latency_scenario(provider, tmp_path, monkeypatch)
        print(f"\n[abort-latency] anthropic provider: {latency:.3f}s")
        assert latency < 1.0, f"取消延迟 {latency:.3f}s，停止功能失效被复现"
    finally:
        srv.shutdown()
