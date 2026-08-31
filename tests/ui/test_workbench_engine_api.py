"""工作台引擎纯函数与 JSBridge API 测试。

覆盖：
- _messages_to_render 历史消息渲染结构（文本/工具块折叠）
- ChatEngine 启停生命周期（不触发重型装配）
- WorkbenchAPI 事件轮询、状态与模型/音色列表
- main.py --gui/--talk 参数解析

@author aceFelix
"""

from __future__ import annotations

import queue

from agent.config.settings import Settings
from agent.core.message import Message, TextContent, ToolResultContent, ToolUseContent
from agent.ui.workbench.api import WorkbenchAPI
from agent.ui.workbench.engine import ChatEngine, _messages_to_render


# ---- 历史消息渲染 ----

def test_messages_to_render_keeps_text_and_folds_tools():
    """文本块保留，工具块折叠为计数，空消息被过滤。"""
    messages = [
        Message(role="user", content=[TextContent(text="你好")]),
        Message(role="assistant", content=[
            TextContent(text="你好，我是贾维斯"),
            ToolUseContent(id="t1", name="Bash", input={"command": "ls"}),
        ]),
        Message(role="user", content=[
            ToolResultContent(tool_use_id="t1", content="file.txt", is_error=False),
        ]),
        Message(role="user", content=[TextContent(text="   ")]),  # 空白 → 过滤
    ]
    out = _messages_to_render(messages)
    assert len(out) == 2  # 纯工具结果消息 + 空白消息均不单独成项
    assert out[0] == {"role": "user", "text": "你好"}
    assert out[1]["role"] == "assistant"
    assert out[1]["text"] == "你好，我是贾维斯"
    assert out[1]["tool_count"] == 1


def test_messages_to_render_empty():
    """空消息列表返回空渲染列表。"""
    assert _messages_to_render([]) == []


# ---- 引擎生命周期 ----

def test_engine_start_stop_without_heavy_init():
    """引擎可启动并干净退出（未发任何指令时不做重型装配）。"""
    settings = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    engine.start()
    assert engine._thread is not None and engine._thread.is_alive()
    engine.stop()
    assert engine._thread is None


def test_engine_new_session_resets_state():
    """new_session 指令清空轮数并推送 session_new 事件。"""
    settings = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    # 手动预置会话状态（跳过重型装配），验证 new_session 的清理逻辑
    engine._session_ready = True
    engine._messages = [Message(role="user", content=[TextContent(text="旧消息")])]
    engine._dialog_count = 3
    engine._title_generated = True
    engine.start()
    try:
        command_queue.put_nowait({"cmd": "new_session"})
        # 等待 session_new 事件
        events = []
        for _ in range(100):
            try:
                events.append(event_queue.get(timeout=0.1))
            except queue.Empty:
                if any(e["type"] == "session_new" for e in events):
                    break
        assert any(e["type"] == "session_new" for e in events)
        assert engine._messages == []
        assert engine._dialog_count == 0
        assert engine._title_generated is False
    finally:
        engine.stop()


# ---- JSBridge API ----

def _make_api() -> tuple[WorkbenchAPI, queue.Queue, queue.Queue]:
    settings = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)
    return api, event_queue, command_queue


def test_api_poll_events_drains_queue():
    """poll_events 一次性取空事件队列。"""
    api, event_queue, _ = _make_api()
    event_queue.put_nowait({"type": "info", "payload": "a"})
    event_queue.put_nowait({"type": "info", "payload": "b"})
    events = api.poll_events()
    assert [e["payload"] for e in events] == ["a", "b"]
    assert api.poll_events() == []


def test_api_commands_enqueued():
    """前端指令入口把指令放入引擎队列（非阻塞）。"""
    api, _, command_queue = _make_api()
    api.send_message("你好")
    api.new_session()
    api.load_session("s1")
    api.answer_user("y")
    api.start_talk()
    api.stop_talk()
    cmds = []
    while True:
        try:
            cmds.append(command_queue.get_nowait()["cmd"])
        except queue.Empty:
            break
    assert cmds == ["send", "new_session", "load_session", "answer_user", "start_talk", "stop_talk"]


def test_api_get_state_and_lists():
    """get_state 含模型/音色字段；模型/音色列表至少包含当前项。"""
    api, _, _ = _make_api()
    state = api.get_state()
    assert {"provider", "model", "tts_voice", "realtime_model", "realtime_voice", "workdir"} <= set(state.keys())

    voices = api.list_voices()
    assert voices and voices[0]["current"] is True

    models = api.list_models()
    if models:
        assert any(m["current"] for m in models)


def test_api_list_models_includes_builtin_and_custom():
    """list_models 与 /models 对齐：内置表 + 自定义表全覆盖，当前模型置顶标 current。"""
    settings = Settings(
        provider="dashscope",
        model="qwen3.7-plus",
        models={"qwen3.7-plus": "通义千问 3.7 Plus", "qwen3.6-flash": "通义千问 3.6 Flash"},
        custom_models={
            "glm-4.7": {"vendor": "zhipu", "model_type": "text"},
            "qwen3.7-plus": {"vendor": "dashscope"},  # 内置名的自定义覆盖：不重复列出
        },
    )
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)

    models = api.list_models()
    names = [m["name"] for m in models]
    # 三个模型全在且无重复（内置覆盖不重复列出）
    assert sorted(names) == ["glm-4.7", "qwen3.6-flash", "qwen3.7-plus"]
    # 当前模型置顶且唯一标 current，带内置描述；厂商经推断而非空
    assert models[0]["name"] == "qwen3.7-plus" and models[0]["current"] is True
    assert models[0]["desc"] == "通义千问 3.7 Plus"
    assert sum(1 for m in models if m["current"]) == 1
    by_name = {m["name"]: m for m in models}
    assert by_name["glm-4.7"]["vendor"] == "zhipu"
    assert by_name["qwen3.6-flash"]["vendor"] == "dashscope"


def test_api_list_sessions_returns_list():
    """list_sessions 始终返回列表（无会话时为空）。"""
    api, _, _ = _make_api()
    sessions = api.list_sessions()
    assert isinstance(sessions, list)
    for s in sessions:
        assert {"name", "updated_at", "message_count", "model"} <= set(s.keys())


# ---- 命令行参数 ----

def test_parse_args_gui_and_talk():
    """--gui 与 --talk 均被识别（两者等价启动工作台）。"""
    from agent.main import parse_args

    assert parse_args(["--gui"]).gui is True
    assert parse_args(["--talk"]).talk is True
    assert parse_args([]).gui is False and parse_args([]).talk is False
