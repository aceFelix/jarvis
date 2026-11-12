"""S2：按 store 分派落盘（settings.toml vs models.toml）。

钉的核心事实：**模型域字段必须落在 models.toml**。写错文件不会报错，只会表现为
"改了没反应"——那是自配置层最不能出现的一类失败。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config.config_specs import get_spec
from agent.config.config_store import inverse_value, write_spec_value


@pytest.fixture()
def cfg(tmp_path: Path) -> Path:
    """同层的 settings.toml（已存在）+ models.toml（待创建）。"""
    settings = tmp_path / "settings.toml"
    settings.write_text("[tts]\nvolume = 50\n", encoding="utf-8")
    return settings


def test_model_scalar_lands_in_models_toml(cfg: Path):
    models = cfg.parent / "models.toml"
    assert write_spec_value(get_spec("provider"), "deepseek", settings_path=cfg)
    assert "deepseek" in models.read_text(encoding="utf-8")
    # 绝不能落到 settings.toml
    assert "deepseek" not in cfg.read_text(encoding="utf-8")


def test_settings_store_still_lands_in_settings_toml(cfg: Path):
    models = cfg.parent / "models.toml"
    assert write_spec_value(
        get_spec("profile_refine_model"), "glm-4.6", settings_path=cfg
    )
    assert "glm-4.6" in cfg.read_text(encoding="utf-8")
    assert not models.exists() or "glm-4.6" not in models.read_text(encoding="utf-8")


def test_alias_map_writes_table_block(cfg: Path):
    models = cfg.parent / "models.toml"
    ok = write_spec_value(
        get_spec("models"), {"qwen3.8-max": "通义千问"}, settings_path=cfg
    )
    assert ok
    text = models.read_text(encoding="utf-8")
    assert "[llm.models]" in text
    assert "通义千问" in text


def test_model_table_add_writes_custom_model_section(cfg: Path):
    """add 写进 models.toml 的 [llm.custom_models."x"] 段（S2c 接线后）。"""
    models = cfg.parent / "models.toml"
    ok = write_spec_value(
        get_spec("custom_models"),
        {"op": "add", "name": "glm-x",
         "fields": {"provider_type": "openai", "model_type": "text"}},
        settings_path=cfg,
    )
    assert ok is True
    text = models.read_text(encoding="utf-8")
    assert '[llm.custom_models."glm-x"]' in text
    assert "openai" in text


def test_model_table_remove_deletes_section(cfg: Path):
    models = cfg.parent / "models.toml"
    spec = get_spec("custom_models")
    write_spec_value(spec, {"op": "add", "name": "glm-x", "fields": {}}, settings_path=cfg)
    assert write_spec_value(spec, {"op": "remove", "name": "glm-x", "fields": {}},
                            settings_path=cfg) is True
    assert "glm-x" not in models.read_text(encoding="utf-8")


class TestInverseValue:
    """撤销一次写入 = 算出"能抵消它的那次写入"。"""

    def test_scalar_inverse_is_old_value(self):
        spec = get_spec("provider")
        assert inverse_value(spec, "dashscope", "deepseek") == "dashscope"

    def test_add_inverse_is_remove(self):
        spec = get_spec("custom_models")
        got = inverse_value(spec, {}, {"op": "add", "name": "x", "fields": {"name": "x"}})
        assert got == {"op": "remove", "name": "x", "fields": {}}

    def test_remove_inverse_is_add_with_old_fields(self):
        spec = get_spec("custom_models")
        old = {"x": {"provider_type": "openai", "model_type": "text"}}
        got = inverse_value(spec, old, {"op": "remove", "name": "x", "fields": {}})
        assert got["op"] == "add"
        assert got["name"] == "x"
        assert got["fields"]["provider_type"] == "openai"
