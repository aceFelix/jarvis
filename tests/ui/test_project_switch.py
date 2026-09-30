"""桌面项目工作区 workdir 运行时切换（project_switch.handle_set_workdir）单测。

覆盖四条落地路径：非法路径 warn 不切换、同路径收敛、会话未装配只记账、已装配
就地重建（换提示词 + 重挂 harness + 开新会话 + emit project_switched）。重型依赖
（build_system_prompt / harness）打桩，持久化经 Path.home 重定向隔离真实目录。

@author aceFelix
"""

from __future__ import annotations

import queue
from pathlib import Path

import pytest

import agent.prompts.system as sp_mod
from agent.config.settings import Settings
from agent.ui.workbench.engine import ChatEngine
from agent.ui.workbench import project_switch


def _make_engine() -> tuple[ChatEngine, queue.Queue]:
    event_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(Settings(), event_queue, queue.Queue())
    return engine, event_queue


def _drain(event_queue: queue.Queue) -> list[dict]:
    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    return events


@pytest.fixture
def home(tmp_path, monkeypatch):
    """持久化隔离：projects.toml 落在临时 .jarvis 下（touch_project 会自建目录）。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


class _FakeLoop:
    """假 QueryLoop：只提供 project_switch 用到的 _registry 与 update_system_prompt。"""

    def __init__(self) -> None:
        self._registry = object()
        self.system_updates: list[str] = []

    def update_system_prompt(self, text: str) -> None:
        self.system_updates.append(text)


async def test_invalid_path_warns_no_switch(home, monkeypatch) -> None:
    """相对路径 / 不存在目录：只 warn，不推 project_switched、不改 workdir。"""
    engine, event_queue = _make_engine()
    engine._settings.workdir = str(home / "current")
    before = engine._settings.workdir

    await project_switch.handle_set_workdir(engine, "relative/dir")
    await project_switch.handle_set_workdir(engine, str(home / "nope"))

    events = _drain(event_queue)
    assert not [e for e in events if e["type"] == "project_switched"]
    assert any(e["type"] == "warn" for e in events)
    assert engine._settings.workdir == before  # 未改


async def test_same_path_converges(home) -> None:
    """与当前 workdir 相同：仅 emit project_switched 让前端收敛，不重建。"""
    engine, event_queue = _make_engine()
    same = home / "proj"
    same.mkdir()
    engine._settings.workdir = str(same)
    engine._query_loop = None

    await project_switch.handle_set_workdir(engine, str(same))

    events = _drain(event_queue)
    switched = [e for e in events if e["type"] == "project_switched"]
    assert switched == [{"type": "project_switched", "payload": {"workdir": str(same), "name": "proj"}}]
    # 同名收敛不写 last_active（未登记最近项目）
    assert engine._workdir_override == ""


async def test_not_assembled_records_override(home) -> None:
    """会话未装配：写 settings.workdir + 记 _workdir_override + emit，不触发重建。"""
    engine, event_queue = _make_engine()
    engine._settings.workdir = str(home / "old")
    engine._query_loop = None
    target = home / "freshproj"
    target.mkdir()

    await project_switch.handle_set_workdir(engine, str(target))

    assert engine._settings.workdir == str(target)
    assert engine._workdir_override == str(target)
    events = _drain(event_queue)
    assert any(e["type"] == "project_switched" for e in events)
    # 已登记最近项目
    from agent.config import projects_registry

    assert projects_registry.get_last_active() == str(target)


async def test_assembled_rebuilds_prompt_and_new_session(home, monkeypatch) -> None:
    """已装配：换 workdir → 重建提示词 → 就地更新 loop → 重挂 harness → 开新会话。"""
    engine, event_queue = _make_engine()
    engine._settings.workdir = str(home / "old")
    loop = _FakeLoop()
    engine._query_loop = loop
    target = home / "myproj"
    target.mkdir()

    # build_system_prompt 打桩：断言收到新 workdir 且返回串被 loop 采纳
    captured: dict = {}

    def fake_prompt(workdir, registry, *, enable_thinking=True, settings=None):
        captured["workdir"] = workdir
        return f"SYS@{workdir}"

    monkeypatch.setattr(sp_mod, "build_system_prompt", fake_prompt)
    # harness 重挂与开新会话打桩为记录调用（隔离真实线程/会话态）
    harness: dict = {}
    monkeypatch.setattr(
        ChatEngine,
        "_register_harness",
        lambda self_, r, w: harness.update({"registry": r, "workdir": w}),
    )
    new_session = {"called": 0}
    engine._messages = []
    monkeypatch.setattr(
        ChatEngine,
        "_handle_new_session",
        lambda self_: new_session.__setitem__("called", new_session["called"] + 1),
    )

    await project_switch.handle_set_workdir(engine, str(target))

    assert captured["workdir"] == str(target)
    assert loop.system_updates == [f"SYS@{target}"]  # 就地更新了提示词
    # harness 在后台线程重挂，join 前不保证完成——此处只断言切换主链已落地
    assert engine._settings.workdir == str(target)
    assert new_session["called"] == 1
    assert engine._workdir_override == ""
    events = _drain(event_queue)
    assert any(
        e["type"] == "project_switched" and e["payload"] == {"workdir": str(target), "name": "myproj"}
        for e in events
    )


async def test_prompt_rebuild_failure_aborts_switch(home, monkeypatch) -> None:
    """提示词重建抛异常：warn 且不推 project_switched、不开新会话（workdir 已改，
    但 loop 提示词与 harness 不动，前端待生效标记保留可重试）。"""
    engine, event_queue = _make_engine()
    engine._settings.workdir = str(home / "old")
    loop = _FakeLoop()
    engine._query_loop = loop
    target = home / "boomproj"
    target.mkdir()

    def boom(*a, **k):
        raise RuntimeError("prompt 生成失败")

    monkeypatch.setattr(sp_mod, "build_system_prompt", boom)

    await project_switch.handle_set_workdir(engine, str(target))

    events = _drain(event_queue)
    assert not [e for e in events if e["type"] == "project_switched"]
    assert any(e["type"] == "warn" for e in events)
    assert loop.system_updates == []
