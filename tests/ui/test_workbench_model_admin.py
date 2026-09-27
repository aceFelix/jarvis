"""workbench model_admin 单测：模型配置的列表 / 修改 / 删除核心语义。

背景：桌面壳左栏模型面板支持「双击模型项改配置、右键模型项删模型」（交互
对齐会话列表）。后端把这些操作收敛到 agent/ui/workbench/model_admin.py，
与 REPL /models 的「修改配置 / 删除模型」同口径，但有一处刻意差异：
桌面壳不回显密钥，编辑表单 api_key 留空表示「保持原 Key 不变」
（REPL 预填明文，留空即清空）。

覆盖：
- list_models：内置 + 自定义合并去重、当前模型置顶、source/removable/config
  管理元信息、明文密钥绝不回传（只给 has_key 布尔）；
- edit_model：内置名锁定、目标名占用、枚举校验、base_url 留空推断、api_key 留空
  保持、改名删旧段（不留幽灵模型）、写盘失败不动内存、改当前模型入队 force；
- remove_model：内置模型不可删、非自定义拒绝、磁盘段不存在拒绝（防重启复活）、
  成功清内存并回报 was_current；
- add_model：base_url 推断 + 写盘失败抛错。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import agent.config.keyring_store as ks
import agent.config.model_registry as mr
import agent.config.models_config as mc
from agent.config.settings import Settings
from agent.ui.workbench import model_admin


class _FakeEngine:
    """引擎替身：只提供 current_model（model_admin 的唯一依赖）。"""

    def __init__(self, model: str = "", override: str = "") -> None:
        self._model = model
        self._model_override = override

    @property
    def current_model(self) -> str:
        return self._model_override or self._model


def _custom(name: str, **kw) -> dict:
    """构造一条自定义模型配置（字段与 /models 添加口径一致）。"""
    cfg = {
        "name": name,
        "provider": "deepseek",
        "api_format": "openai",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model_type": "text",
    }
    cfg.update(kw)
    return cfg


@pytest.fixture
def isolated_home(tmp_path):
    """隔离用户级配置目录 + keyring（不触碰真实 ~/.jarvis 与系统凭据管理器）。"""
    (tmp_path / ".jarvis").mkdir()
    with patch.object(Path, "home", return_value=tmp_path), patch.object(
        ks, "store_api_key", lambda *a, **k: None
    ):
        yield tmp_path


# ---- list_models ----

def test_list_models_marks_current_and_admin_meta():
    """列表：当前模型置顶标 current，每项带 source/editable/removable/config。"""
    settings = Settings(
        model="custom-a",
        models={"builtin-a": "内置 A", "builtin-b": "内置 B"},
        custom_models={"custom-a": _custom("custom-a", api_key="sk-secret")},
    )
    items = model_admin.list_models(settings, _FakeEngine("custom-a"))

    assert items[0]["name"] == "custom-a" and items[0]["current"] is True
    by_name = {m["name"]: m for m in items}
    assert set(by_name) == {"custom-a", "builtin-a", "builtin-b"}
    assert by_name["custom-a"]["source"] == "custom"
    assert by_name["custom-a"]["removable"] is True
    assert by_name["builtin-a"]["source"] == "builtin"
    assert by_name["builtin-a"]["removable"] is False  # 内置不可删
    assert by_name["builtin-a"]["editable"] is True  # 但可改（写用户级覆盖配置）
    assert by_name["builtin-a"]["desc"] == "内置 A"
    # 明文密钥绝不外传（桌面壳只拿到「有没有 Key」）
    assert "api_key" not in by_name["custom-a"]["config"]
    assert by_name["custom-a"]["config"]["has_key"] is True


def test_list_models_follows_engine_current():
    """「当前」取引擎实时模型：待在 _model_override 落地的切换也立即反映。"""
    settings = Settings(models={"builtin-a": "内置 A"})
    engine = _FakeEngine("builtin-a", override="switched-model")
    items = model_admin.list_models(settings, engine)

    assert items[0]["name"] == "switched-model"  # 不在两张表里也保证可见
    assert [m["name"] for m in items if m["current"]] == ["switched-model"]


def test_list_models_builtin_with_override_is_not_removable():
    """内置模型加过覆盖配置后仍不可删（source/removable 以是否内置为准）。"""
    settings = Settings(
        models={"builtin-a": "内置 A"},
        custom_models={"builtin-a": _custom("builtin-a")},
    )
    by_name = {m["name"]: m for m in model_admin.list_models(settings, _FakeEngine())}
    assert by_name["builtin-a"]["source"] == "builtin"
    assert by_name["builtin-a"]["removable"] is False


# ---- edit_model ----

def test_edit_builtin_writes_override(isolated_home):
    """改内置模型：名字锁定，写的是用户级覆盖配置；非当前模型不入队热切换。"""
    settings = Settings(models={"builtin-a": "内置 A"})
    calls: list = []
    result = model_admin.edit_model(
        settings,
        _FakeEngine("other-model"),
        calls.append,
        "builtin-a",
        base_url="https://override.example/v1",
        api_key="sk-1",
        model_type="text",
    )

    assert result["name"] == "builtin-a"
    assert result["base_url"] == "https://override.example/v1"
    assert result["hot_switched"] is False
    assert calls == []
    assert settings.custom_models["builtin-a"]["api_key"] == "sk-1"
    text = (isolated_home / ".jarvis" / "models.toml").read_text(encoding="utf-8")
    assert '[llm.custom_models."builtin-a"]' in text


def test_edit_builtin_rename_rejected():
    """内置模型改名 → 报错（改不了的内置名换个名字请用「添加模型」）。"""
    settings = Settings(models={"builtin-a": "内置 A"})
    with pytest.raises(ValueError, match="内置模型名不可修改"):
        model_admin.edit_model(
            settings, _FakeEngine(), None, "builtin-a", new_name="my-a"
        )


def test_edit_target_name_taken_rejected():
    """改名撞已有自定义模型名 → 报错（不覆盖别人的配置）。"""
    settings = Settings(
        custom_models={"a-model": _custom("a-model"), "b-model": _custom("b-model")}
    )
    with pytest.raises(ValueError, match="已被占用"):
        model_admin.edit_model(settings, _FakeEngine(), None, "a-model", new_name="b-model")


def test_edit_missing_model_rejected():
    """名字既不在内置表也不在自定义表 → 报错（不凭空造模型）。"""
    with pytest.raises(ValueError, match="模型不存在"):
        model_admin.edit_model(Settings(), _FakeEngine(), None, "nope")


def test_edit_invalid_enum_rejected():
    """接口类型 / 模型类型必须落枚举（写进去引擎也认不了）。"""
    settings = Settings(custom_models={"a-model": _custom("a-model")})
    with pytest.raises(ValueError, match="api_format"):
        model_admin.edit_model(settings, _FakeEngine(), None, "a-model", api_format="grpc")
    with pytest.raises(ValueError, match="model_type"):
        model_admin.edit_model(settings, _FakeEngine(), None, "a-model", model_type="audio")


def test_edit_rename_keeps_blank_api_key(isolated_home):
    """改名 + 留空字段：api_key 保持原值（桌面壳不回显密钥，留空≠清空）。"""
    settings = Settings(custom_models={"old-model": _custom("old-model", api_key="sk-keep")})
    result = model_admin.edit_model(
        settings, _FakeEngine(), None, "old-model", new_name="new-model"
    )

    assert result["name"] == "new-model"
    assert result["base_url"] == "https://api.deepseek.com"  # 留空沿用旧值
    assert "old-model" not in settings.custom_models
    assert settings.custom_models["new-model"]["api_key"] == "sk-keep"
    text = (isolated_home / ".jarvis" / "models.toml").read_text(encoding="utf-8")
    assert '[llm.custom_models."new-model"]' in text
    assert '[llm.custom_models."old-model"]' not in text  # 不留「幽灵模型」


def test_edit_blank_base_url_inferred(isolated_home):
    """base_url 留空 → 按厂商 + 接口类型推断（与 /models 添加同口径）。"""
    settings = Settings(custom_models={"a-model": _custom("a-model", base_url="")})
    result = model_admin.edit_model(settings, _FakeEngine(), None, "a-model")
    assert result["base_url"] == "https://api.deepseek.com"


def test_edit_current_model_enqueues_force_switch(isolated_home):
    """改的是当前运行模型 → 入队 switch_model（带 force 强制按新配置重建）。

    不带 force 时引擎会走「同名即当前」短路，端点/类型的改动永远不生效。
    """
    settings = Settings(custom_models={"a-model": _custom("a-model")})
    calls: list = []
    result = model_admin.edit_model(
        settings, _FakeEngine("a-model"), calls.append, "a-model", model_type="multimodal"
    )

    assert result["hot_switched"] is True
    assert calls == [{"cmd": "switch_model", "name": "a-model", "force": True}]


def test_edit_write_failure_keeps_memory(monkeypatch):
    """写盘失败 → 抛 RuntimeError 且内存配置不动（避免「本次生效、重启回退」）。"""
    monkeypatch.setattr(mr, "save_custom_model", lambda *a, **k: False)
    settings = Settings(custom_models={"a-model": _custom("a-model", base_url="https://old")})
    with pytest.raises(RuntimeError, match="写盘失败"):
        model_admin.edit_model(
            settings, _FakeEngine(), None, "a-model", base_url="https://new"
        )
    assert settings.custom_models["a-model"]["base_url"] == "https://old"


# ---- remove_model ----

def test_remove_custom_ok_and_reports_current(isolated_home):
    """删自定义模型：磁盘段与内存同步清除，回报 was_current 供前端提示。"""
    settings = Settings()
    # 先走添加链路落盘（删除只删磁盘上真实存在的段）
    model_admin.add_model(settings, "a-model", vendor="deepseek", api_format="openai")
    result = model_admin.remove_model(settings, _FakeEngine("a-model"), "a-model")

    assert result == {"name": "a-model", "was_current": True}
    assert "a-model" not in settings.custom_models
    text = (isolated_home / ".jarvis" / "models.toml").read_text(encoding="utf-8")
    assert '[llm.custom_models."a-model"]' not in text
    names = [m["name"] for m in model_admin.list_models(settings, _FakeEngine())]
    assert "a-model" not in names


def test_remove_builtin_rejected():
    """内置模型不可删（删掉用户级覆盖段也只是回退默认端点，列表里仍在）。"""
    settings = Settings(models={"builtin-a": "内置 A"})
    with pytest.raises(ValueError, match="内置模型不可删除"):
        model_admin.remove_model(settings, _FakeEngine(), "builtin-a")


def test_remove_non_custom_rejected():
    """内置表与自定义表都没有该名字 → 报错（不是自定义模型）。"""
    with pytest.raises(ValueError, match="不是自定义模型"):
        model_admin.remove_model(Settings(), _FakeEngine(), "nope")


def test_remove_disk_miss_rejected(monkeypatch):
    """用户级 models.toml 里没有该段（项目级配置的模型）→ 拒绝，不动内存。"""
    monkeypatch.setattr(mc, "remove_custom_model", lambda name: False)
    settings = Settings(custom_models={"a-model": _custom("a-model")})
    with pytest.raises(RuntimeError, match="删除失败"):
        model_admin.remove_model(settings, _FakeEngine(), "a-model")
    assert "a-model" in settings.custom_models


# ---- add_model ----

def test_add_model_infers_base_url_and_syncs_memory(isolated_home):
    """添加自定义模型：写盘 + 内存同步（列表立即可见），base_url 留空按厂商推断。"""
    settings = Settings()
    result = model_admin.add_model(
        settings, "added-model", vendor="deepseek", api_format="openai"
    )

    assert result["base_url"] == "https://api.deepseek.com"
    assert settings.custom_models["added-model"]["api_format"] == "openai"
    text = (isolated_home / ".jarvis" / "models.toml").read_text(encoding="utf-8")
    assert '[llm.custom_models."added-model"]' in text


def test_add_model_write_failure_raises(monkeypatch):
    """写盘失败 → 抛 RuntimeError 且不写内存（列表不出现「实则没存下」的模型）。"""
    monkeypatch.setattr(mr, "save_custom_model", lambda *a, **k: False)
    settings = Settings()
    with pytest.raises(RuntimeError, match="写盘失败"):
        model_admin.add_model(settings, "added-model")
    assert "added-model" not in settings.custom_models
