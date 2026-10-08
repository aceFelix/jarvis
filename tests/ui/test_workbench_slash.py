"""工作台斜杠命令透传 slash_bridge 测试。

覆盖：
- WorkbenchAPI.exec_slash：形态校验与入队参数
- SlashCaptureUI：info/warn/error/_console 输出捕获；ask_user /
  read_user_input_async / terminal_picker 交互禁令
- _pickers_blocked：terminal_picker 三个交互原语执行期抛错、退出后还原
- run_slash：非斜杠 / 会话未装配 / 白名单外拒绝；白名单命令执行并回推
  slash_result；交互命令转友好报错；未知命令（dispatch 返回 False）报错；
  技能命令持 query 锁 + 挂 _send_task + 轮后 _after_turn
- ChatEngine._dispatch slash_exec 路由
- build_desktop_commands：passthrough/native/skill 三类收录、重名技能跳过、
  技能加载失败宽容；WorkbenchAPI.list_slash_commands 委托桥接层

dispatch_command / _build_command_context / load_skills 全程 monkeypatch，不触真实 LLM 与
终端交互（asyncio.run 驱动，与 checkpoint_ops 测试同风格）。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import queue
import threading
from types import SimpleNamespace

from agent.config.settings import Settings
from agent.ui.workbench import slash_bridge
from agent.ui.workbench.api import WorkbenchAPI
from agent.ui.workbench.engine import ChatEngine


def _make_engine() -> tuple[ChatEngine, queue.Queue, queue.Queue]:
    """真实 ChatEngine 实例 + 队列（不装配会话，字段按需伪造）。"""
    settings = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    engine._session_ready = True
    engine._model = "m1"
    engine._messages = []
    engine._session_name = "s1"
    engine._dialog_count = 0
    engine._title_generated = True
    return engine, event_queue, command_queue


def _drain(eq: queue.Queue) -> list[dict]:
    """取空事件队列。"""
    events = []
    while not eq.empty():
        events.append(eq.get_nowait())
    return events


def _last_slash_result(events: list[dict]) -> dict:
    """取最后一条 slash_result 事件 payload。"""
    hits = [e for e in events if e.get("type") == "slash_result"]
    assert hits, f"无 slash_result 事件: {events}"
    return hits[-1]["payload"]


# ---- api 层 ----

def test_api_exec_slash_validation_and_enqueue():
    """合法斜杠命令入队 slash_exec；非斜杠/过短拒绝。"""
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    api = WorkbenchAPI(event_queue, command_queue, SimpleNamespace(), Settings())
    out = api.exec_slash("/cost")
    assert out == {"ok": True, "command": "/cost"}
    assert command_queue.get_nowait() == {"cmd": "slash_exec", "command": "/cost"}
    # 非斜杠与只有 "/" 两种坏输入
    assert api.exec_slash("hello")["ok"] is False
    assert api.exec_slash("/")["ok"] is False
    assert api.exec_slash("")["ok"] is False
    assert command_queue.empty()


# ---- SlashCaptureUI ----

def test_capture_ui_collects_info_warn_error_console():
    """info/warn/error 与 _console.print 汇入同一捕获缓冲（渲染后纯文本）。"""
    ui = slash_bridge.SlashCaptureUI()
    ui.info("普通一行")
    ui.warn("警告行")
    ui.error("错误行")
    ui._console.print("[bold]富文本[/bold]")
    out = ui.get_output()
    assert "普通一行" in out and "⚠️  警告行" in out and "❌ 错误行" in out
    # markup 被 Rich 解析掉，只剩文字；no_color 无 ANSI 残留
    assert "[bold]" not in out and "\x1b[" not in out


def test_capture_ui_interactive_methods_raise():
    """ask_user / read_user_input_async / terminal_picker 全部抛交互禁令。"""
    ui = slash_bridge.SlashCaptureUI()
    try:
        ui.ask_user("确认吗")
        assert False, "ask_user 应抛 SlashInteractiveError"
    except slash_bridge.SlashInteractiveError:
        pass
    try:
        asyncio.run(ui.read_user_input_async())
        assert False, "read_user_input_async 应抛 SlashInteractiveError"
    except slash_bridge.SlashInteractiveError:
        pass
    try:
        ui.terminal_picker.pick_from_list
        assert False, "terminal_picker 成员访问应抛 SlashInteractiveError"
    except slash_bridge.SlashInteractiveError:
        pass


# ---- 交互原语禁令 ----

def test_pickers_blocked_patches_and_restores():
    """禁令上下文内三个 picker 抛错，退出后还原原函数。"""
    from agent.ui import terminal_picker as tp

    original = tp.pick_from_list
    with slash_bridge._pickers_blocked():
        assert tp.pick_from_list is not original
        try:
            tp.pick_from_list([("a", "a", "")], title="t")
            assert False, "禁令内应抛 SlashInteractiveError"
        except slash_bridge.SlashInteractiveError:
            pass
    assert tp.pick_from_list is original


# ---- run_slash 主流程 ----

def test_run_slash_rejects_bad_shapes(monkeypatch):
    """非斜杠 / 会话未装配 / 白名单外且非技能：三种拒绝各回一条 ok=False。"""
    engine, eq, _ = _make_engine()
    # 非斜杠
    asyncio.run(slash_bridge.run_slash(engine, "plain text"))
    r = _last_slash_result(_drain(eq))
    assert r["ok"] is False and "斜杠" in r["text"]

    # 会话未装配
    engine._session_ready = False
    asyncio.run(slash_bridge.run_slash(engine, "/cost"))
    r = _last_slash_result(_drain(eq))
    assert r["ok"] is False and "会话尚未初始化" in r["text"]
    engine._session_ready = True

    # 白名单外 + 非技能（/init 为交互命令，挡在白名单外）
    monkeypatch.setattr(slash_bridge, "_skill_exists", lambda s, t: False)
    asyncio.run(slash_bridge.run_slash(engine, "/init"))
    r = _last_slash_result(_drain(eq))
    assert r["ok"] is False and "暂不支持在桌面执行" in r["text"]


def test_run_slash_whitelist_executes_and_captures(monkeypatch):
    """白名单命令：组装上下文执行 dispatch_command，捕获输出回推 slash_result。"""
    engine, eq, _ = _make_engine()
    calls: list[str] = []

    async def fake_dispatch(ctx, text):
        calls.append(text)
        ctx.ui.info(f"执行 {text}")
        return True

    fake_ctx = SimpleNamespace(ui=None)
    monkeypatch.setattr(slash_bridge, "_build_command_context",
                        lambda e, ui: setattr(fake_ctx, "ui", ui) or fake_ctx)
    monkeypatch.setattr("agent.commands.router.dispatch_command", fake_dispatch)
    asyncio.run(slash_bridge.run_slash(engine, "/cost"))
    assert calls == ["/cost"]
    r = _last_slash_result(_drain(eq))
    assert r["ok"] is True and r["command"] == "/cost"
    assert "执行 /cost" in r["text"]


def test_run_slash_unknown_command_reported(monkeypatch):
    """dispatch 返回 False（未处理）→ ok=False 并提示未知命令。"""
    engine, eq, _ = _make_engine()

    async def fake_dispatch(ctx, text):
        return False

    monkeypatch.setattr(slash_bridge, "_build_command_context",
                        lambda e, ui: SimpleNamespace(ui=ui))
    monkeypatch.setattr("agent.commands.router.dispatch_command", fake_dispatch)
    asyncio.run(slash_bridge.run_slash(engine, "/context"))
    r = _last_slash_result(_drain(eq))
    assert r["ok"] is False and "未知命令" in r["text"]


def test_run_slash_interactive_error_guard(monkeypatch):
    """命令内部触发交互禁令 → 转「需要终端交互」友好报错，不挂起。"""
    engine, eq, _ = _make_engine()

    async def fake_dispatch(ctx, text):
        ctx.ui.ask_user("选一个")
        return True

    monkeypatch.setattr(slash_bridge, "_build_command_context",
                        lambda e, ui: SimpleNamespace(ui=ui))
    monkeypatch.setattr("agent.commands.router.dispatch_command", fake_dispatch)
    asyncio.run(slash_bridge.run_slash(engine, "/memory"))
    r = _last_slash_result(_drain(eq))
    assert r["ok"] is False and "需要终端交互" in r["text"]


def test_run_slash_skill_mode_locks_and_finalizes(monkeypatch):
    """技能命令：放行 + 持 query 锁执行 + 挂 _send_task 可停止 + 轮后落盘。"""
    engine, eq, _ = _make_engine()
    seen: dict = {}
    turns: list[str] = []

    async def fake_dispatch(ctx, text):
        # 锁应已被 run_slash 持有（非阻塞尝试取锁应失败，切勿 release 他人的锁）；
        # _send_task 应指向当前任务（停止可取消）
        seen["locked"] = not engine._query_lock.acquire(blocking=False)
        seen["task_is_current"] = engine._send_task is asyncio.current_task()
        ctx.ui.info("调用技能: demo")
        return True

    monkeypatch.setattr(slash_bridge, "_skill_exists", lambda s, t: True)
    monkeypatch.setattr(slash_bridge, "_build_command_context",
                        lambda e, ui: SimpleNamespace(ui=ui))
    monkeypatch.setattr("agent.commands.router.dispatch_command", fake_dispatch)
    engine._after_turn = lambda: turns.append("saved")  # 免触真实会话落盘
    engine._query_lock = threading.Lock()
    asyncio.run(slash_bridge.run_slash(engine, "/demo 做个网站"))
    assert seen["locked"] is True
    assert seen["task_is_current"] is True
    assert turns == ["saved"]
    # 锁已释放、_send_task 复位
    assert engine._query_lock.acquire(blocking=False)
    engine._query_lock.release()
    assert engine._send_task is None
    r = _last_slash_result(_drain(eq))
    assert r["ok"] is True and "调用技能" in r["text"]


# ---- 引擎路由 ----

def test_engine_dispatch_routes_slash_exec(monkeypatch):
    """_dispatch slash_exec → slash_bridge.run_slash(engine, command)。"""
    engine, _, _ = _make_engine()
    got: list = []

    async def fake_run(eng, text):
        got.append((eng, text))

    monkeypatch.setattr(slash_bridge, "run_slash", fake_run)
    asyncio.run(engine._dispatch({"cmd": "slash_exec", "command": "/doctor"}))
    assert got == [(engine, "/doctor")]


# ---- 上下文组装 ----

def test_build_command_context_maps_engine_state(monkeypatch):
    """CommandContext 各字段按引擎运行态映射（loop._system/_orchestrator 复用）。"""
    engine, _, _ = _make_engine()
    sentinel_orch = object()
    engine._query_loop = SimpleNamespace(
        _orchestrator=sentinel_orch, _system="SP", set_orchestrator=lambda o: None
    )
    engine._active_registry = SimpleNamespace()
    engine._ctx = SimpleNamespace()
    engine._provider = SimpleNamespace()
    engine._mcp_client = None
    monkeypatch.setattr("agent.bootstrap._build_checker", lambda s: object())
    monkeypatch.setattr("agent.bootstrap._build_recovery_executor", lambda s: object())
    ui = slash_bridge.SlashCaptureUI()
    ctx = slash_bridge._build_command_context(engine, ui)
    assert ctx.ui is ui
    assert ctx.loop is engine._query_loop
    assert ctx.orchestrator is sentinel_orch  # 复用运行中 orchestrator
    assert ctx.system_prompt == "SP"
    assert ctx.messages is engine._messages
    assert ctx.model == "m1"
    assert ctx.team_mgr is None and ctx.task_list is None


# ---- 补全目录（slash.commands 数据源） ----

def test_build_desktop_commands_three_sources(monkeypatch):
    """目录 = 白名单透传 + 原生控件 + 技能；与内置重名的技能不重复收录。"""
    skills = [
        SimpleNamespace(name="demo", description="演示技能"),
        SimpleNamespace(name="context", description="与内置重名"),  # 应跳过
        SimpleNamespace(name="blank", description=""),  # 空描述回退「技能包」
    ]
    monkeypatch.setattr("agent.core.extensions.skills.load_skills",
                        lambda wd: skills)
    items = slash_bridge.build_desktop_commands(SimpleNamespace(workdir="wd"))
    by_name = {i["name"]: i for i in items}
    # 白名单全量收录且标 passthrough
    for token in slash_bridge.ALLOWED_COMMANDS:
        assert by_name[token]["source"] == "passthrough"
    # 原生控件命令全量收录且标 native
    for token in slash_bridge._NATIVE_COMMANDS:
        assert by_name[token]["source"] == "native"
    # 技能：独立命令收录、重名跳过、空描述回退
    assert by_name["/demo"] == {"name": "/demo", "description": "演示技能",
                                "source": "skill"}
    assert "/context" in by_name and by_name["/context"]["source"] == "passthrough"
    assert by_name["/blank"]["description"] == "技能包"
    # 内置命令描述取自终端 SLASH_COMMANDS（/cost 在注册表里有说明）
    assert by_name["/cost"]["description"]
    # 桌面不可执行的终端命令不进目录
    assert "/exit" not in by_name and "/init" not in by_name


def test_build_desktop_commands_tolerates_skill_load_failure(monkeypatch):
    """技能目录加载失败不影响主列表（与 _skill_exists 同口径宽容）。"""
    def boom(wd):
        raise RuntimeError("skills dir broken")

    monkeypatch.setattr("agent.core.extensions.skills.load_skills", boom)
    items = slash_bridge.build_desktop_commands(SimpleNamespace(workdir="wd"))
    sources = {i["source"] for i in items}
    assert sources == {"passthrough", "native"}
    assert len(items) == len(slash_bridge.ALLOWED_COMMANDS) + len(slash_bridge._NATIVE_COMMANDS)


def test_api_list_slash_commands_delegates_to_bridge(monkeypatch):
    """WorkbenchAPI.list_slash_commands 只读委托 slash_bridge，不入队。"""
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    settings = Settings()
    api = WorkbenchAPI(event_queue, command_queue, SimpleNamespace(), settings)
    got: list = []

    def fake_build(s):
        got.append(s)
        return [{"name": "/cost", "description": "d", "source": "passthrough"}]

    monkeypatch.setattr(slash_bridge, "build_desktop_commands", fake_build)
    out = api.list_slash_commands()
    assert got == [settings]  # 传的是自身 settings（workdir 现算技能）
    assert out == [{"name": "/cost", "description": "d", "source": "passthrough"}]
    assert command_queue.empty() and event_queue.empty()  # 只读不入队不回推
