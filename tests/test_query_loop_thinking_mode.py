"""QueryLoop 运行时思考档位 / 编排器热替换测试（2026-09 桌面模式与思考强度）。

覆盖：
- set_thinking_effort：档位记录 + off 同步开/关覆盖 + 转发 provider
- is_thinking_effort：优先取覆盖，未覆盖回退 provider
- set_orchestrator：就地换编排器（权限模式热切换），不重建 loop
- switch_model：新 provider 继承思考开/关与档位覆盖（跨切模型不丢）

@author aceFelix
"""

from __future__ import annotations

from agent.core.query_loop import QueryLoop
from tests._query_loop_fakes import ScriptedProvider, make_loop, registry


class EffortProvider(ScriptedProvider):
    """在 ScriptedProvider 之上记录 set_thinking_effort 调用与当前档位。"""

    def __init__(self, scripts) -> None:
        super().__init__(scripts)
        self.effort: str | None = None
        self.effort_calls: list[str | None] = []

    def set_thinking_effort(self, level: str | None) -> None:
        self.effort_calls.append(level)
        self.effort = level
        # 与真实 provider 同口径：off 关闭、其余开启
        self._thinking = level != "off"

    def is_thinking_effort(self) -> str | None:
        return self.effort


def test_set_thinking_effort_records_and_forwards(registry):  # noqa: ARG001
    provider = EffortProvider([])
    loop = make_loop(provider, object(), registry)
    loop.set_thinking_effort("low")
    assert provider.effort == "low"
    assert provider.effort_calls == ["low"]
    assert loop._thinking_effort_override == "low"
    assert loop._thinking_override is True  # 非 off → 开
    assert loop.is_thinking_effort() == "low"


def test_set_thinking_effort_off_syncs_disable(registry):  # noqa: ARG001
    provider = EffortProvider([])
    loop = make_loop(provider, object(), registry)
    loop.set_thinking_effort("off")
    assert loop._thinking_override is False
    assert provider.is_thinking_enabled() is False
    assert loop.is_thinking_effort() == "off"


def test_is_thinking_effort_falls_back_to_provider(registry):  # noqa: ARG001
    provider = EffortProvider([])
    provider.effort = "high"  # 直接置 provider 实际态，不经 loop
    loop = make_loop(provider, object(), registry)
    assert loop._thinking_effort_override is None
    assert loop.is_thinking_effort() == "high"  # 未覆盖 → 取 provider


def test_set_orchestrator_replaces_in_place(registry):  # noqa: ARG001
    provider = EffortProvider([])
    old = object()
    loop = make_loop(provider, old, registry)
    new = object()
    loop.set_orchestrator(new)
    assert loop._orchestrator is new


def test_switch_model_preserves_thinking_effort_override(registry):  # noqa: ARG001
    provider = EffortProvider([])
    loop = QueryLoop(
        provider=provider,
        registry=registry,
        orchestrator=object(),
        enable_compaction=False,
        deferred_loading=False,
        chat_detection=False,
    )
    loop.set_thinking_effort("medium")
    new_provider = EffortProvider([])
    loop.switch_model(new_provider, "other-model")
    # 新 provider 继承开/关与档位覆盖
    assert new_provider.effort_calls == ["medium"]
    assert new_provider.is_thinking_enabled() is True
    assert loop.is_thinking_effort() == "medium"
