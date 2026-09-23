"""workbench/serve 消息附件（图片/文本文件）回归测试。

背景：桌面壳此前没有上传入口——message 指令只带 text。现 📎 按钮/粘贴
产生图片（base64 → vision 链路）与文本文件（内容拼进消息正文），
经 WS 协议桥接到 ChatEngine._handle_send。

覆盖：
- _handle_send：images → ImageContent 走 loop.run；files 拼正文且超长截断；
  纯图片消息补最小指令文本；非法条目跳过；全空不跑 loop；
- _messages_to_render：图片块折叠为「[图片×N]」标记（历史回放不传 base64）。

@author aceFelix
"""

from __future__ import annotations

import queue

import pytest

from agent.config.settings import Settings
from agent.core.message import ImageContent, Message, TextContent, ToolResultContent
from agent.ui.workbench.engine import ChatEngine, _messages_to_render


def _make_engine() -> tuple[ChatEngine, queue.Queue]:
    """构造不启动线程的引擎实例，直接取事件队列断言。"""
    event_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(Settings(), event_queue, queue.Queue())
    return engine, event_queue


class _FakeLoop:
    """假 QueryLoop：只记录 run 的入参（text / images）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def run(self, text: str, ctx: object, images: object = None) -> None:
        self.calls.append((text, images))


@pytest.fixture
def engine_with_fake_loop(monkeypatch):
    """引擎 + 假 loop：_after_turn 打桩防测试写真实会话存档。"""
    engine, event_queue = _make_engine()
    loop = _FakeLoop()
    engine._query_loop = loop
    engine._ctx = object()
    monkeypatch.setattr(engine, "_after_turn", lambda: None)
    return engine, loop, event_queue


def _drain(event_queue: queue.Queue) -> list[dict]:
    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    return events


async def test_send_images_and_files(engine_with_fake_loop) -> None:
    """images → ImageContent 走 vision；files 以代码块拼进正文。"""
    engine, loop, event_queue = engine_with_fake_loop
    await engine._handle_send(
        "看图和文件",
        images=[{"data": "QUJD", "media_type": "image/png"}],
        files=[{"name": "notes.md", "content": "# 标题"}],
    )
    assert len(loop.calls) == 1
    text, images = loop.calls[0]
    assert text.startswith("看图和文件")
    assert "【附带文件 notes.md】" in text and "# 标题" in text
    assert isinstance(images, list) and len(images) == 1
    assert isinstance(images[0], ImageContent)
    assert images[0].data == "QUJD" and images[0].media_type == "image/png"
    # 用户消息回显事件仍发（前端本地已上屏，dispatcher 跳过防双气泡）
    assert any(e["type"] == "user_message" for e in _drain(event_queue))


async def test_send_long_file_truncated(engine_with_fake_loop) -> None:
    """超 2 万字符的文本文件截断并附提示。"""
    engine, loop, _ = engine_with_fake_loop
    await engine._handle_send("看文件", files=[{"name": "big.txt", "content": "a" * 25_000}])
    text, _ = loop.calls[0]
    assert "仅展示前 20000 字符" in text
    assert text.count("a") == 20_000


async def test_send_images_only_gets_placeholder_text(engine_with_fake_loop) -> None:
    """纯图片消息：补最小指令文本让模型有回应落点。"""
    engine, loop, _ = engine_with_fake_loop
    await engine._handle_send("", images=[{"data": "QUJD"}])
    text, images = loop.calls[0]
    assert text == "请结合附带的图片回答。"
    assert len(images) == 1


async def test_send_malformed_images_skipped(engine_with_fake_loop) -> None:
    """非法条目（缺 data / 非 dict）静默跳过；media_type 缺省 png。"""
    engine, loop, _ = engine_with_fake_loop
    await engine._handle_send(
        "hi", images=[{"data": ""}, "junk", {"data": "QUJD", "media_type": "image/jpeg"}]
    )
    _, images = loop.calls[0]
    assert len(images) == 1 and images[0].media_type == "image/jpeg"


async def test_send_all_empty_no_run(engine_with_fake_loop) -> None:
    """text/images/files 全空：不进 loop.run。"""
    engine, loop, _ = engine_with_fake_loop
    await engine._handle_send("   ", images=[], files=[])
    assert loop.calls == []


# ---- 历史回放标记 ----

def test_render_image_marker_appended() -> None:
    """带图片块的用户消息：text 尾部追加 [图片×N] 标记。"""
    msg = Message(role="user", content=[
        TextContent(text="看图"),
        ImageContent(data="QUJD", media_type="image/png"),
    ])
    out = _messages_to_render([msg])
    assert len(out) == 1
    assert out[0]["text"].endswith("[图片×1]")


def test_render_image_only_message_kept() -> None:
    """纯图片用户消息（无文本）也进历史回放（此前会被当空消息丢弃）。"""
    msg = Message(role="user", content=[ImageContent(data="QUJD", media_type="image/png")])
    out = _messages_to_render([msg])
    assert out == [{"role": "user", "text": "[图片×1]"}]


def test_render_tool_result_only_still_skipped() -> None:
    """纯工具结果回填的 user 消息仍不进历史回放（回归保护）。"""
    msg = Message(role="user", content=[ToolResultContent(tool_use_id="t1", content="ok")])
    assert _messages_to_render([msg]) == []
