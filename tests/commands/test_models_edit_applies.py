"""`/models → 修改配置` 必须当场生效（aceFelix）。

锁定的用户现象（2026-10 真机实测）：
    > /models
    模型「deepseek-flash」配置已更新
    模型已切换为: deepseek-flash（多模态（deepseek-flash））
    > 贾维斯在吗
    ❌ LLM 调用失败: 原始 error: Connection error.

提示写着「已更新 / 已切换」，下一句话仍打**旧端点**。

根因是两条短路叠加：
1. `handle_models` 里 `picked != ctx.model` 才切 —— 编辑的往往正是当前模型，
   回到列表再选它时 `picked == ctx.model`，**什么都不做**；
2. `_switch_model` 里 `model_name == provider._model` 直接 `return None`。

桌面壳早就用 `force=True` 治好了同一件事
（`model_switch.handle_switch_model` 的注释：「同名也必须重建 provider，
否则『同名即当前』的短理会把改动挡在门外」），终端这条链没跟上。

本文件测的是**链路**（谁该调 force），`_switch_model` 自身的 force 语义
在 `tests/llm/test_model_manager_extra.py::TestSwitchModel` 里测。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent.commands.handlers import model_commands
from agent.config.settings import Settings

CURRENT = "deepseek-flash"


def _settings() -> Settings:
    s = Settings()
    s.models = {"deepseek-flash": "DeepSeek Flash"}
    s.custom_models = {
        CURRENT: {
            "provider": "deepseek", "api_format": "openai",
            "base_url": "https://api.deepseek.com", "api_key": "k",
            "model_type": "multimodal",
        },
    }
    return s


def _run(ctx, monkeypatch, *, picks, action=None, edited=CURRENT):
    """跑一次 handle_models，返回记录盒（switch 记下 _switch_model 的调用参数）。"""
    box = {"switch": [], "edit": [], "picks": list(picks)}

    def _pick(*_a, **_kw):
        return box["picks"].pop(0) if box["picks"] else None

    def _switch(ui, settings, provider, registry, orchestrator, system_prompt,
                model_name, **kwargs):
        box["switch"].append({"model": model_name, "force": kwargs.get("force", False)})
        return (provider, object(), model_name)

    def _pick_action(*_a, **_kw):
        return action

    def _edit(ui, settings, name):
        box["edit"].append(name)
        return edited

    monkeypatch.setattr(model_commands, "pick_from_grouped_list", _pick)
    monkeypatch.setattr(model_commands, "_switch_model", _switch)
    monkeypatch.setattr(model_commands, "_pick_model_action", _pick_action)
    # 内置与自定义两条编辑路径都打桩：目标模型可能同时出现在 models 与
    # custom_models 里，不打桩会真的弹 prompt_toolkit 表单。
    monkeypatch.setattr(model_commands, "_edit_custom_model", _edit)
    monkeypatch.setattr(model_commands, "_edit_builtin_model", _edit)

    asyncio.run(model_commands.handle_models(ctx, "/models"))
    return box


def _ctx() -> SimpleNamespace:
    """UI 桩：把 info/warn/error 收进 calls 列表。"""
    calls: list[tuple[str, str]] = []

    class _UI:
        _console = None

        def info(self, msg, *_a, **_kw):
            calls.append(("info", msg))

        def warn(self, msg, *_a, **_kw):
            calls.append(("warn", msg))

        def error(self, msg, *_a, **_kw):
            calls.append(("error", msg))

    ctx = SimpleNamespace(
        ui=_UI(), settings=_settings(), provider=SimpleNamespace(_model=CURRENT),
        registry=None, orchestrator=None, system_prompt="sys", model=CURRENT,
        loop="old-loop",
    )
    ctx._calls = calls
    return ctx


class TestEditCurrentModelAppliesImmediately:
    def test_edit_current_model_forces_rebuild(self, monkeypatch):
        """**核心回归**：编辑运行中的模型后必须按新配置重建 provider。"""
        ctx = _ctx()
        box = _run(ctx, monkeypatch, picks=[f"__SPACE__{CURRENT}"], action="edit")

        assert box["edit"] == [CURRENT], "没有进入编辑流程"
        assert box["switch"] == [{"model": CURRENT, "force": True}], (
            "编辑当前模型后必须带 force 重建 provider，否则新配置不会生效"
        )

    def test_edit_other_model_does_not_touch_running_provider(self, monkeypatch):
        """改的不是当前模型 → 不该无谓地断一次连接。"""
        ctx = _ctx()
        ctx.settings.custom_models["other"] = {"provider": "deepseek", "api_format": "openai"}
        box = _run(ctx, monkeypatch, picks=["__SPACE__other"], action="edit",
                   edited="other")

        assert box["edit"] == ["other"]
        assert box["switch"] == [], "改别的模型不该切当前模型"

    def test_cancelled_edit_does_nothing(self, monkeypatch):
        """取消编辑（返回 None）→ 不切。"""
        ctx = _ctx()
        box = _run(ctx, monkeypatch, picks=[f"__SPACE__{CURRENT}"], action="edit",
                   edited=None)
        assert box["switch"] == []

    def test_rename_follows_new_name(self, monkeypatch):
        """改名后要切到**新**名字（旧名已不在 custom_models 里）。"""
        ctx = _ctx()
        box = _run(ctx, monkeypatch, picks=[f"__SPACE__{CURRENT}"], action="edit",
                   edited="renamed")
        assert box["switch"] == [{"model": "renamed", "force": True}]
        assert ctx.model == "renamed"


class TestPickCurrentModelIsNotSilent:
    def test_picking_current_model_tells_user_how_to_edit(self, monkeypatch):
        """选中的就是当前模型：不能静默什么都不做（用户会以为“切了”）。"""
        ctx = _ctx()
        box = _run(ctx, monkeypatch, picks=[CURRENT])

        assert box["switch"] == [], "没改配置不该重建连接"
        assert any("已经是当前模型" in m for _lv, m in ctx._calls), (
            "必须告诉用户“已经是当前模型”，并指出怎么改配置"
        )
        assert any("空格" in m for _lv, m in ctx._calls)
