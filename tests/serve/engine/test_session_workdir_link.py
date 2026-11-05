"""会话恢复与项目目录的联动（``_handle_load`` → ``_restore_session_workdir``）。

背景（2026-11）：桌面左栏「历史会话」点开一条旧会话时，下方「项目」区原本
纹丝不动——``_handle_load`` 只装了消息/轮数/标题，没碰 workdir，也没推
``project_switched``。用户在 A 项目里打开一条 B 项目的会话，面板仍显示 A；
接着发消息，读写文件与命令就落在 A，而对话内容全是 B 的。

本文件锁定修复后的契约：

- 跨目录恢复 → workdir 换成会话目录 + 推 ``project_switched``，
  且**不开新会话**（刚载入的对话必须保住）；
- 同目录恢复 → 不重建、不推多余事件；
- 会话目录已失效 → 保持当前项目，只给一句 ``info``，消息照常载入；
- 老会话没记 workdir（空串）→ 完全不动。

store 层 ``load_session`` 在 handler 内延迟导入，monkeypatch ``store_mod``
的属性即可拦截（调用时取模块最新值）。

@author aceFelix
"""

from __future__ import annotations

import queue
from pathlib import Path

import pytest

import agent.prompts.system as sp_mod
from agent.config.settings import Settings
from agent.core.memory import store as store_mod
from agent.core.message import Message
from agent.serve.engine.engine import ChatEngine


class _FakeMeta:
    """会话元信息（只提供 _handle_load / 联动路径用到的字段）。"""

    def __init__(
        self,
        name: str,
        workdir: str = "",
        dialog_count: int = 1,
        title_generated: bool = False,
    ) -> None:
        self.name = name
        self.workdir = workdir
        self.dialog_count = dialog_count
        self.title_generated = title_generated


class _FakeSession:
    """load_session 的返回值：消息 + 元信息。"""

    def __init__(self, messages: list[Message], meta: _FakeMeta) -> None:
        self.messages = messages
        self.meta = meta


class _FakeLoop:
    """假 QueryLoop：只提供 project_switch 用到的 _registry 与 update_system_prompt。"""

    def __init__(self) -> None:
        self._registry = object()
        self.system_updates: list[str] = []

    def update_system_prompt(self, text: str) -> None:
        self.system_updates.append(text)


def _make_engine(workdir: str) -> tuple[ChatEngine, queue.Queue]:
    """构造不启动线程的引擎实例，预置会话态（正常由 _ensure_session 设置）。"""
    event_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(Settings(), event_queue, queue.Queue())
    engine._settings.workdir = workdir
    engine._session_name = "session-current"
    engine._messages = []
    engine._dialog_count = 0
    engine._title_generated = False
    return engine, event_queue


def _drain(event_queue: queue.Queue) -> list[dict]:
    events: list[dict] = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    return events


def _patch_prompt(monkeypatch) -> None:
    """build_system_prompt 打桩：返回带 workdir 的串，便于断言重建口径。

    **_kwargs 用于吸收 compose_system_prompt 透传的新参数（如 model_name）；
    写死签名的假桩会把 TypeError 变成“切换静默失败”这类难归因的现象。
    """

    def fake_prompt(workdir, registry, *, enable_thinking=True, settings=None, **_kwargs):
        return f"SYS@{workdir}"

    monkeypatch.setattr(sp_mod, "build_system_prompt", fake_prompt)


def _patch_harness(monkeypatch) -> None:
    """harness 重挂打桩：隔离真实后台线程与工具注册。"""
    monkeypatch.setattr(ChatEngine, "_register_harness", lambda self_, r, w: None)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """持久化隔离：projects.toml 落在临时 .jarvis 下（_persist 会 touch）。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


async def test_load_switches_workdir_to_session_dir(home, monkeypatch) -> None:
    """跨目录恢复：workdir 换成会话目录、推 project_switched、不开新会话。"""
    current = home / "proj-a"
    current.mkdir()
    target = home / "proj-b"
    target.mkdir()
    engine, event_queue = _make_engine(str(current))
    loop = _FakeLoop()
    engine._query_loop = loop
    _patch_prompt(monkeypatch)
    _patch_harness(monkeypatch)
    new_session = {"called": 0}
    monkeypatch.setattr(
        ChatEngine,
        "_handle_new_session",
        lambda self_: new_session.__setitem__("called", new_session["called"] + 1),
    )
    msg = Message.user_text("这条会话是在 B 项目里聊的")
    monkeypatch.setattr(
        store_mod,
        "load_session",
        lambda name: _FakeSession([msg], _FakeMeta(name, workdir=str(target))),
    )

    await engine._handle_load("深夜聊")

    events = _drain(event_queue)
    types = [e["type"] for e in events]
    # session_loaded 必须先出，project_switched 在后（刷新列表时 session_name 已就绪）
    assert types[0] == "session_loaded"
    assert "project_switched" in types
    assert (
        types.index("session_loaded") < types.index("project_switched")
    )
    switched = [e for e in events if e["type"] == "project_switched"]
    assert switched[0]["payload"] == {"workdir": str(target), "name": "proj-b"}
    assert engine._settings.workdir == str(target)
    # 会话本身不受影响：名字、消息都还在（这正是 start_new_session=False 的意义）
    assert engine._session_name == "深夜聊"
    assert engine._messages == [msg]
    assert new_session["called"] == 0
    # 提示词按新目录重建
    assert loop.system_updates == [f"SYS@{target}"]
    # 成功切目录不弹「已切换到项目」气泡（用户动作是打开会话，不是切项目）
    assert "info" not in types
    # 最近项目被置顶（selected 口径）
    from agent.config import projects_registry

    assert projects_registry.get_last_active() == str(target)


async def test_load_same_workdir_pushes_nothing_extra(home, monkeypatch) -> None:
    """同目录恢复：只推 session_loaded，不重建提示词、不推 project_switched。"""
    same = home / "proj-same"
    same.mkdir()
    engine, event_queue = _make_engine(str(same))
    loop = _FakeLoop()
    engine._query_loop = loop
    _patch_prompt(monkeypatch)
    monkeypatch.setattr(
        store_mod,
        "load_session",
        lambda name: _FakeSession([Message.user_text("hi")], _FakeMeta(name, workdir=str(same))),
    )

    await engine._handle_load("s")

    assert [e["type"] for e in _drain(event_queue)] == ["session_loaded"]
    assert loop.system_updates == []  # 没有多余的重建


async def test_load_missing_dir_keeps_current_project(home, monkeypatch) -> None:
    """会话目录已失效：项目区保持当前目录，只给一句 info，消息照常载入。"""
    current = home / "proj-a"
    current.mkdir()
    engine, event_queue = _make_engine(str(current))
    engine._query_loop = _FakeLoop()
    _patch_prompt(monkeypatch)
    gone = str(home / "已被删掉的项目")
    monkeypatch.setattr(
        store_mod,
        "load_session",
        lambda name: _FakeSession([Message.user_text("hi")], _FakeMeta(name, workdir=gone)),
    )

    await engine._handle_load("s")

    events = _drain(event_queue)
    types = [e["type"] for e in events]
    assert engine._settings.workdir == str(current)  # 项目区不动
    assert "project_switched" not in types
    assert engine._session_name == "s"  # 对话照常载入，不回滚
    assert len(engine._messages) == 1
    assert "warn" not in types  # announce=False：失败文案由调用方给，不重复报
    infos = [e for e in events if e["type"] == "info"]
    assert infos and "已不可用" in infos[0]["payload"]
    assert gone in infos[0]["payload"]


async def test_load_legacy_session_without_workdir(home, monkeypatch) -> None:
    """老会话没记 workdir：不推 project_switched，工作目录保持不变。"""
    current = home / "proj-a"
    current.mkdir()
    engine, event_queue = _make_engine(str(current))
    engine._query_loop = _FakeLoop()
    _patch_prompt(monkeypatch)
    monkeypatch.setattr(
        store_mod,
        "load_session",
        lambda name: _FakeSession([Message.user_text("hi")], _FakeMeta(name)),
    )

    await engine._handle_load("s")

    assert [e["type"] for e in _drain(event_queue)] == ["session_loaded"]
    assert engine._settings.workdir == str(current)


async def test_load_missing_session_does_not_touch_workdir(home, monkeypatch) -> None:
    """会话不存在：只报 error，工作目录与会话态都不动。"""
    current = home / "proj-a"
    current.mkdir()
    engine, event_queue = _make_engine(str(current))
    engine._query_loop = _FakeLoop()
    monkeypatch.setattr(store_mod, "load_session", lambda name: None)

    await engine._handle_load("不存在")

    events = _drain(event_queue)
    assert [e["type"] for e in events] == ["error"]
    assert engine._settings.workdir == str(current)
    assert engine._session_name == "session-current"
