"""workbench engine 模型热切换回归测试。

背景：桌面壳左栏点选模型 → serve ``models.select`` 只写 last_model 就回执，
引擎既不换 provider 也不换 QueryLoop，而 ``models.list`` 的 current 又取自
进程启动快照 —— 于是出现「已选 qwen3.8-2.4t-a95b，列表仍标 qwen3.8-flash
为当前」。修复后：写盘成功即入队 ``{"cmd": "switch_model"}``，引擎线程内
串行热切换（provider 重建 + QueryLoop 就地换 provider/模型），落地推
``model_switched`` 让两端刷新「当前」标记。

覆盖：
- 未装配：只记账 ``_model_override``，不提前触发重型装配，current_model 已跟上；
- 已装配：换 ``_provider``/``_model``/``_provider_settings``、关旧 provider、推事件；
- 装配期间到达的切换：``_ensure_session`` 末尾补落地；
- 重复点选（已是当前模型）：不重建、只回执；
- 配置更新（force=True，来自 models.edit 改当前模型）：同名也重建 provider，
  提示文案改「模型配置已更新」；
- 构造失败：warn 且不推 model_switched（前端「待生效」保留，可重试）；
- 空名：完全静默。

@author aceFelix
"""

from __future__ import annotations

import queue
from unittest import mock

from agent.config.settings import Settings
from agent.core.tool import ToolRegistry
from agent.ui.workbench.engine import ChatEngine


class _FakeProvider:
    """假 LLMProvider：可写 _model，记录 close 次数。"""

    def __init__(self, model: str = "old") -> None:
        self._model = model
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class _FakeLoop:
    """假 QueryLoop：switch_model 语义与真实现一致（复用同实例返回 None）。"""

    def __init__(self, provider) -> None:
        self.provider = provider
        self.swapped: list[tuple] = []

    def switch_model(self, provider, model):
        old = self.provider
        self.provider = provider
        self.swapped.append((provider, model))
        return None if old is provider else old


def _make_engine() -> tuple[ChatEngine, queue.Queue]:
    """构造不启动线程的引擎实例（启动模型 qwen3.8-flash）。"""
    event_queue: queue.Queue = queue.Queue()
    settings = Settings(model="qwen3.8-flash", provider="dashscope")
    engine = ChatEngine(settings, event_queue, queue.Queue())
    return engine, event_queue


def _drain(event_queue: queue.Queue) -> list[dict]:
    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    return events


def _assembled(engine: ChatEngine, *, model: str = "qwen3.8-flash"):
    """把引擎置为「已装配」态（正常由 _ensure_session 完成）。"""
    provider = _FakeProvider(model=model)
    loop = _FakeLoop(provider)
    engine._query_loop = loop
    engine._provider = provider
    engine._model = model
    engine._session_ready = True
    return provider, loop


def _patch_build(monkeypatch, *, error: Exception | None = None, desc: str = "通义千问"):
    """mock model_manager._build_switched_provider（延迟导入 → patch 模块属性）。"""
    import agent.model_manager as mm

    used_settings = Settings(model="switched-snapshot")

    def _fake(settings, provider, name):
        if error is not None:
            raise error
        return _FakeProvider(model=name), desc, used_settings

    build = mock.MagicMock(side_effect=_fake)
    monkeypatch.setattr(mm, "_build_switched_provider", build)
    build.used_settings = used_settings
    return build


async def test_unassembled_records_pending_override(monkeypatch) -> None:
    """会话未装配：只记账待落地模型，不触发装配，current_model 立即跟上。"""
    build = _patch_build(monkeypatch)
    engine, event_queue = _make_engine()
    assert getattr(engine, "_session_ready", False) is False

    await engine._handle_switch_model("qwen3.8-2.4t-a95b")

    assert engine._model_override == "qwen3.8-2.4t-a95b"
    assert engine.current_model == "qwen3.8-2.4t-a95b"  # 列表「当前」立刻移动
    build.assert_not_called()  # 不因一次切换提前装修配（MCP + 提示词）
    assert getattr(engine, "_query_loop", None) is None
    events = _drain(event_queue)
    assert {"type": "model_switched", "payload": {"model": "qwen3.8-2.4t-a95b"}} in events
    assert any(e["type"] == "info" and "模型已切换为" in e["payload"] for e in events)


async def test_assembled_switches_provider_and_emits(monkeypatch) -> None:
    """已装配：换 provider/模型/端点快照，关旧 provider，推 model_switched + info。"""
    build = _patch_build(monkeypatch, desc="通义千问 3.8 2.4T")
    engine, event_queue = _make_engine()
    provider, loop = _assembled(engine)
    snapshot = engine._provider_settings

    await engine._handle_switch_model("qwen3.8-2.4t-a95b")

    # 比较基准是「当前 provider 端点快照」，不是启动 settings
    build.assert_called_once()
    assert build.call_args.args[0] is snapshot
    assert build.call_args.args[1] is provider
    assert build.call_args.args[2] == "qwen3.8-2.4t-a95b"
    new_provider = loop.provider
    assert new_provider is not provider
    assert new_provider._model == "qwen3.8-2.4t-a95b"
    assert loop.swapped == [(new_provider, "qwen3.8-2.4t-a95b")]
    assert provider.closed == 1  # 旧 provider 的 HTTP client 已释放
    assert engine._provider is new_provider
    assert engine._model == "qwen3.8-2.4t-a95b"
    assert engine._provider_settings is build.used_settings
    assert engine.current_model == "qwen3.8-2.4t-a95b"
    events = _drain(event_queue)
    assert {"type": "model_switched", "payload": {"model": "qwen3.8-2.4t-a95b"}} in events
    infos = [e["payload"] for e in events if e["type"] == "info"]
    assert any("qwen3.8-2.4t-a95b（通义千问 3.8 2.4T）" in t for t in infos)


async def test_override_applied_after_ensure_session(monkeypatch) -> None:
    """装配期间到达的切换：_ensure_session 末尾补落地，首条消息即用新模型。"""
    import agent.bootstrap as boot_mod
    import agent.core.orchestrator as orch_mod
    import agent.core.query_loop as ql_mod
    import agent.prompts.system as sp_mod

    build = _patch_build(monkeypatch)
    engine, event_queue = _make_engine()
    engine._registry = ToolRegistry()
    engine._registry_ready.set()
    monkeypatch.setattr(boot_mod, "_build_provider", lambda s, model_type: _FakeProvider())
    monkeypatch.setattr(boot_mod, "_model_type_for", lambda s: "text")
    monkeypatch.setattr(boot_mod, "_build_checker", lambda s: None)
    monkeypatch.setattr(boot_mod, "_build_recovery_executor", lambda s: None)
    monkeypatch.setattr(boot_mod, "_build_context", lambda s, ui, msgs: None)
    monkeypatch.setattr(orch_mod, "ToolOrchestrator", lambda **kw: None)
    monkeypatch.setattr(sp_mod, "build_system_prompt", lambda *a, **k: "sys")
    loop = _FakeLoop(_FakeProvider())
    monkeypatch.setattr(ql_mod, "QueryLoop", lambda **kw: loop)
    monkeypatch.setattr(ChatEngine, "_register_harness", lambda self_, r, w: None)

    # 点选早于首条消息：先记账
    await engine._handle_switch_model("qwen3.8-2.4t-a95b")
    assert engine._model_override == "qwen3.8-2.4t-a95b"
    _drain(event_queue)

    await engine._ensure_session()

    assert engine._session_ready is True
    assert engine._model_override == ""  # 已落地，不残留
    build.assert_called_once()
    assert engine._model == "qwen3.8-2.4t-a95b"
    assert engine.current_model == "qwen3.8-2.4t-a95b"
    events = _drain(event_queue)
    assert {"type": "model_switched", "payload": {"model": "qwen3.8-2.4t-a95b"}} in events


async def test_same_model_only_emits(monkeypatch) -> None:
    """已是当前模型（重复点选/竞态）：不重建 provider，只回执收敛前端。"""
    build = _patch_build(monkeypatch)
    engine, event_queue = _make_engine()
    provider, _loop = _assembled(engine)

    await engine._handle_switch_model("qwen3.8-flash")

    build.assert_not_called()
    assert provider.closed == 0
    assert engine._model == "qwen3.8-flash"
    assert [e["type"] for e in _drain(event_queue)] == ["model_switched"]  # 不重复弹 info


async def test_force_same_model_rebuilds_provider(monkeypatch) -> None:
    """force=True（models.edit 改了当前模型配置）：同名也重建 provider。

    不 force 的口径是「同名即当前」短路 —— 用户改了端点/接口类型却仍跑旧
    provider；force 让引擎按新配置重建，提示文案也随之改为「配置已更新」。
    """
    build = _patch_build(monkeypatch)
    engine, event_queue = _make_engine()
    provider, loop = _assembled(engine)

    await engine._handle_switch_model("qwen3.8-flash", True)

    build.assert_called_once()
    assert loop.provider is not provider  # 同名但已按新配置换过
    assert loop.swapped == [(loop.provider, "qwen3.8-flash")]
    assert provider.closed == 1
    assert engine._model == "qwen3.8-flash"
    events = _drain(event_queue)
    assert {"type": "model_switched", "payload": {"model": "qwen3.8-flash"}} in events
    infos = [e["payload"] for e in events if e["type"] == "info"]
    assert any("模型配置已更新" in t for t in infos)
    assert not any("已切换为" in t for t in infos)


async def test_force_unassembled_emits_config_updated(monkeypatch) -> None:
    """未装配 + force：同样只记账不提前装修配，文案为「配置已更新」。"""
    build = _patch_build(monkeypatch)
    engine, event_queue = _make_engine()

    await engine._handle_switch_model("qwen3.8-flash", True)

    build.assert_not_called()
    assert engine._model_override == "qwen3.8-flash"
    events = _drain(event_queue)
    assert {"type": "model_switched", "payload": {"model": "qwen3.8-flash"}} in events
    assert any(e["type"] == "info" and "模型配置已更新" in e["payload"] for e in events)


async def test_dispatch_passes_force_flag(monkeypatch) -> None:
    """_dispatch 把指令里的 force 透传（models.edit → serve → 引擎队列的链路）。"""
    build = _patch_build(monkeypatch)
    engine, _ = _make_engine()
    provider, loop = _assembled(engine)

    await engine._dispatch(
        {"cmd": "switch_model", "name": "qwen3.8-flash", "force": True}
    )

    build.assert_called_once()
    assert loop.provider is not provider


async def test_build_failure_warns_without_switch(monkeypatch) -> None:
    """构造失败：warn 且不推 model_switched —— 前端「待生效」保留，用户可重试。"""
    build = _patch_build(monkeypatch, error=RuntimeError("unknown provider"))
    engine, event_queue = _make_engine()
    provider, loop = _assembled(engine)

    await engine._handle_switch_model("bad-model")

    build.assert_called_once()
    assert engine._model == "qwen3.8-flash"  # 未落地，仍是原模型
    assert loop.swapped == []
    assert provider.closed == 0
    events = _drain(event_queue)
    types = [e["type"] for e in events]
    assert "model_switched" not in types
    assert "warn" in types
    warns = [e["payload"] for e in events if e["type"] == "warn"]
    assert any("模型切换失败" in w and "unknown provider" in w for w in warns)


async def test_close_failure_keeps_switch(monkeypatch) -> None:
    """旧 provider 关闭失败不影响切换结果（连接释放是尽力而为）。"""
    _patch_build(monkeypatch)
    engine, event_queue = _make_engine()
    provider, loop = _assembled(engine)

    async def boom() -> None:
        raise RuntimeError("client already closed")

    monkeypatch.setattr(provider, "close", boom)

    await engine._handle_switch_model("qwen3.8-2.4t-a95b")

    assert engine._model == "qwen3.8-2.4t-a95b"
    assert loop.provider is engine._provider
    assert "model_switched" in [e["type"] for e in _drain(event_queue)]


async def test_empty_name_silent(monkeypatch) -> None:
    """空名/纯空白：完全静默（不入队语义落到引擎层也不报错）。"""
    build = _patch_build(monkeypatch)
    engine, event_queue = _make_engine()
    _assembled(engine)

    await engine._handle_switch_model("   ")

    build.assert_not_called()
    assert engine._model == "qwen3.8-flash"
    assert _drain(event_queue) == []


async def test_dispatch_routes_switch_model_without_assembly(monkeypatch) -> None:
    """_dispatch 认得 switch_model 指令，且不顺手触发 _ensure_session。"""
    _patch_build(monkeypatch)
    engine, event_queue = _make_engine()

    def boom() -> None:
        raise AssertionError("switch_model 不应触发会话装配")

    monkeypatch.setattr(engine, "_ensure_session", boom)

    await engine._dispatch({"cmd": "switch_model", "name": "qwen3.8-2.4t-a95b"})

    assert engine._model_override == "qwen3.8-2.4t-a95b"
    assert {"type": "model_switched", "payload": {"model": "qwen3.8-2.4t-a95b"}} in _drain(
        event_queue
    )


def test_current_model_falls_back_to_boot_settings() -> None:
    """全新引擎（未点选未装配）：current_model / current_vendor 取启动配置。"""
    engine, _ = _make_engine()
    assert engine.current_model == "qwen3.8-flash"
    assert engine.current_vendor == "dashscope"
