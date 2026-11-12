"""端到端：一次 `model` 改动，走完 配置层 → store 分派 → apply_mode=llm → 热切换。

前面的用例都在测单段；这条测的是**接缝**——单段全绿而接缝错位，用户看到的就是
"改了没反应"。所以这里把真实调用链连起来跑，只把两个"外部世界"换成假的：
落盘路径（临时目录）与 provider 重建（假 provider）。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import config_store, models_config
from agent.config.config_ops import apply_config_patch
from agent.config.config_specs import (
    APPLY_LLM,
    clear_apply_hooks,
    register_apply_hook,
)
from agent.config.settings import Settings
from agent.llm.hotswap import rebuild_llm_stack, set_llm_rebinder


class _FakeProvider:
    def __init__(self, default_model: str = "") -> None:
        self.default_model = default_model

    def set_thinking_enabled(self, v):        # switch_model 会调
        pass

    def set_thinking_effort(self, v):
        pass


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch):
    """把 models.toml / settings.toml 都指向临时目录，并装一个记录用的假重建器。"""
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text("[tts]\nvolume = 50\n", encoding="utf-8")
    models_file = tmp_path / "models.toml"
    models_file.write_text('provider = "dashscope"\nmodel = "qwen3.7-plus"\n',
                           encoding="utf-8")

    # 落盘路径：config_store 走 models_path_for(settings_path)
    monkeypatch.setattr(
        "agent.config.models_config.models_path_for",
        lambda settings_path: models_file,
    )
    monkeypatch.setattr(
        "agent.config.config_store._models_path", lambda settings_path: models_file
    )

    calls: list[str] = []

    def fake_rebind() -> tuple[bool, str]:
        calls.append("rebind")
        return True, "已切到 glm-4.6（dashscope），下一轮请求生效"

    set_llm_rebinder(fake_rebind)
    register_apply_hook(APPLY_LLM, rebuild_llm_stack)
    yield settings_file, models_file, calls
    set_llm_rebinder(None)
    clear_apply_hooks()


def test_model_change_reaches_models_toml_and_hotswap(sandbox):
    """改 model：落 models.toml（不是 settings.toml）、触发热切换、回执说生效。"""
    settings_file, models_file, calls = sandbox
    settings = Settings()
    settings.model = "qwen3.7-plus"
    settings.provider = "dashscope"

    out = apply_config_patch(settings, {"model": "glm-4.6"})

    assert out.ok, out.text
    assert "glm-4.6" in models_file.read_text(encoding="utf-8")
    assert "glm-4.6" not in settings_file.read_text(encoding="utf-8")
    assert calls == ["rebind"], "apply_mode=llm 没有触发热切换"
    assert "下一轮" in out.text or "生效" in out.text
    assert settings.model == "glm-4.6"


def test_no_rebinder_receipt_says_restart(sandbox):
    """入口没登记重建器时，回执必须写"重启后生效"，不许假装成功。"""
    settings_file, models_file, _calls = sandbox
    set_llm_rebinder(None)          # 覆盖 fixture 的假重建器
    settings = Settings()
    settings.model = "qwen3.7-plus"

    out = apply_config_patch(settings, {"model": "glm-4.6"})

    assert out.ok                      # 写入本身成功
    assert "重启" in out.text       # 但不假装已生效
    assert "glm-4.6" in models_file.read_text(encoding="utf-8")
