"""工作台引擎纯函数与 JSBridge API 测试。

覆盖：
- _messages_to_render 历史消息渲染结构（文本/工具块折叠）
- ChatEngine 启停生命周期（不触发重型装配）
- ChatEngine.is_busy 忙时探针（send 轮次 / 半双工 / 实时双工三路）
- WorkbenchAPI 事件轮询、状态与模型/音色列表
- main.py --gui/--talk 参数解析

@author aceFelix
"""

from __future__ import annotations

import asyncio
import queue
from types import SimpleNamespace

from agent import session_manager as session_manager_mod
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
    """引擎可启动并干净退出（同步重型装配仍懒到首条指令）。

    启动时后台预热会建 registry/连 MCP（首条消息秒进优化），
    测试关 enable_mcp 保 hermetic，不真连用户 MCP server。@author aceFelix
    """
    settings = Settings()
    settings.enable_mcp = False
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    engine.start()
    assert engine._thread is not None and engine._thread.is_alive()
    engine.stop()
    assert engine._thread is None


# ---- 忙时探针（主动播报 TTS「忙时跳过」用） ----

def test_engine_is_busy_idle():
    """无 send 轮次、无语音会话 → is_busy False。"""
    engine = ChatEngine(Settings(), queue.Queue(), queue.Queue())
    assert engine.is_busy is False


def test_engine_is_busy_send_turn():
    """send 任务未结束 → 忙；结束 → 空闲。"""
    engine = ChatEngine(Settings(), queue.Queue(), queue.Queue())
    engine._send_task = SimpleNamespace(done=lambda: False)
    assert engine.is_busy is True
    engine._send_task = SimpleNamespace(done=lambda: True)
    assert engine.is_busy is False


def test_engine_is_busy_voice_sessions():
    """半双工语音 / 实时双工任务未结束 → 同样判忙。"""
    engine = ChatEngine(Settings(), queue.Queue(), queue.Queue())
    engine._voice_task = SimpleNamespace(done=lambda: False)
    assert engine.is_busy is True
    engine._voice_task = None
    engine._talk_task = SimpleNamespace(done=lambda: False)
    assert engine.is_busy is True
    engine._talk_task = SimpleNamespace(done=lambda: True)
    assert engine.is_busy is False


def test_engine_new_session_resets_state():
    """new_session 指令清空轮数并推送 session_new 事件。"""
    settings = Settings()
    settings.enable_mcp = False  # 预热不真连用户 MCP server（hermetic）
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


def test_engine_title_rename_emits_session_renamed(monkeypatch):
    """标题生成改名推 session_renamed，不得复用 session_ready（清屏语义）。

    回归背景：首轮回复后标题任务重发 session_ready，前端按初始化语义
    清空气泡，用户看到"刚开始回答就空白"。改名只应刷新会话列表。

    @author aceFelix
    """
    # 屏蔽落盘与真实标题生成：只验证事件语义
    monkeypatch.setattr(session_manager_mod, "_auto_save", lambda *a, **k: None)

    async def fake_title(ui, messages, old_name):
        return "new-title"

    monkeypatch.setattr(
        session_manager_mod, "_generate_title_from_first_user", fake_title
    )

    settings = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    # 预置会话状态跳过重型装配：首轮结束触发 _gen_first 改名分支
    engine._session_ready = True
    engine._messages = [Message(role="user", content=[TextContent(text="你好")])]
    engine._session_name = "session-x"
    engine._dialog_count = 0
    engine._title_generated = False

    async def drive() -> None:
        engine._after_turn()
        # 等后台标题任务跑完（create_task 不阻塞 _after_turn）
        await asyncio.sleep(0.2)

    asyncio.run(drive())

    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    types = [e["type"] for e in events]
    assert "session_renamed" in types
    assert "session_ready" not in types  # 清屏语义事件不得在改名时出现
    renamed = next(e for e in events if e["type"] == "session_renamed")
    assert renamed["payload"]["name"] == "new-title"
    assert engine._session_name == "new-title"


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


def test_api_list_sessions_marks_current(monkeypatch) -> None:
    """sessions.list 每项带 current 标记：与引擎当前会话名相等的为 True，其余 False。

    左栏选中态数据源：恢复/新建/改名后任一次刷新即自愈，
    前端无需跟踪 session_* 事件序列。@author aceFelix
    """
    from agent.core.memory import store as store_mod
    from agent.core.memory.store import SessionMeta

    monkeypatch.setattr(
        store_mod,
        "list_sessions",
        lambda: [
            SessionMeta(name="opened", workdir="", message_count=2, updated_at=200),
            SessionMeta(name="other", workdir="", message_count=1, updated_at=100),
        ],
    )
    api, _, _ = _make_api()
    api._engine._session_name = "opened"
    by_name = {s["name"]: s for s in api.list_sessions()}
    assert by_name["opened"]["current"] is True
    assert by_name["other"]["current"] is False
    # 引擎未装配/无当前会话时全 False（不误标）
    api._engine._session_name = ""
    assert all(s["current"] is False for s in api.list_sessions())


# ---- 命令行参数 ----

def test_parse_args_gui_and_talk():
    """--gui 与 --talk 均被识别（两者等价启动工作台）。"""
    from agent.main import parse_args

    assert parse_args(["--gui"]).gui is True
    assert parse_args(["--talk"]).talk is True
    assert parse_args([]).gui is False and parse_args([]).talk is False


def test_parse_args_serve():
    """--serve 被识别（headless API 服务，供 jarvis-desktop 接入），默认关闭。"""
    from agent.main import parse_args

    assert parse_args(["--serve"]).serve is True
    assert parse_args([]).serve is False
    # 与 --gui/--talk 共存时 --gui/--talk 优先（分发顺序在前）
    assert parse_args(["--serve", "--gui"]).serve is True
