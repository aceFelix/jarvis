"""S3：`apply_mode = llm` 的生效链。

要钉住的三件事：

1. **没登记的入口不许假装生效**——回执必须写"重启后生效"；
2. **重建失败保留旧栈**——异常只降级回执，不把"配置写成功"变成失败；
3. **切换后下一轮真的用新模型**——用假 provider 验证 `switch_model` 生效。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.config.config_specs import (
    APPLY_LLM,
    clear_apply_hooks,
    register_apply_hook,
    run_apply_hooks,
)
from agent.llm.hotswap import has_rebinder, rebuild_llm_stack, set_llm_rebinder


@pytest.fixture(autouse=True)
def _clean():
    """每个用例前后清干净，避免全局回调串场。"""
    set_llm_rebinder(None)
    clear_apply_hooks()
    yield
    set_llm_rebinder(None)
    clear_apply_hooks()


class TestRebuildBridge:
    def test_without_rebinder_says_restart_not_success(self):
        ok, note = rebuild_llm_stack()
        assert ok is False
        assert "重启" in note          # 不许假装生效
        assert not has_rebinder()

    def test_rebinder_exception_is_downgraded_to_note(self):
        def boom():
            raise RuntimeError("no api key")

        set_llm_rebinder(boom)
        ok, note = rebuild_llm_stack()
        assert ok is False
        assert "旧模型继续可用" in note
        assert "no api key" in note

    def test_rebinder_success_passes_through(self):
        set_llm_rebinder(lambda: (True, "已切到 glm-4.6，下一轮请求生效"))
        ok, note = rebuild_llm_stack()
        assert ok is True
        assert "下一轮" in note

    def test_registered_hook_is_reached_through_config_layer(self):
        """走配置层那条真实路径：_apply_modes 调 run_apply_hooks(APPLY_LLM)。"""
        set_llm_rebinder(lambda: (True, "已切换"))
        register_apply_hook(APPLY_LLM, rebuild_llm_stack)
        ok, notes = run_apply_hooks(APPLY_LLM)
        assert ok is True
        assert notes == ["已切换"]

    def test_hook_present_but_rebinder_failing_reports_not_ok(self):
        set_llm_rebinder(lambda: (False, "缺 key"))
        register_apply_hook(APPLY_LLM, rebuild_llm_stack)
        ok, notes = run_apply_hooks(APPLY_LLM)
        assert ok is False
        assert notes == ["缺 key"]


class _FakeProvider:
    def __init__(self, default_model: str) -> None:
        self.default_model = default_model


class TestQueryLoopSwitch:
    """`switch_model` 是 S3 复用的既有原语，这里确认它真的换掉了绑定。"""

    def test_switch_model_keeps_session_and_swaps_binding(self):
        from agent.core.query_loop import QueryLoop

        old = _FakeProvider("old-model")
        loop = QueryLoop.__new__(QueryLoop)          # 不跑 __init__ 的重活
        loop._provider = old
        loop._model = "old-model"
        loop._failover_tried = False
        loop._thinking_override = None
        loop._thinking_effort_override = None

        new = _FakeProvider("new-model")
        returned = loop.switch_model(new, "new-model")
        assert loop._provider is new
        assert loop._model == "new-model"
        assert returned is old
