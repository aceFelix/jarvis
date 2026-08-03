"""agent/model_manager.py 补充单元测试。

覆盖模型管理交互逻辑：自定义模型添加/编辑/删除流程、厂商与 Base URL 推断
（剩余分支）、模型切换（复用/重建 provider）、模型列表展示等。
所有外部依赖（form_input / pick_from_list / save_custom_model / save_last_model /
_build_provider）均通过 monkeypatch mock，~/.jarvis 路径重定向到 tmp_path。

@author aceFelix
"""

from pathlib import Path
from unittest import mock

import pytest

from agent.config.settings import Settings
from agent.core.query_loop import QueryLoop
from agent.model_manager import (
    _add_custom_model_flow,
    _delete_custom_model,
    _edit_builtin_model,
    _edit_custom_model,
    _infer_base_url,
    _infer_model_vendor,
    _list_models,
    _pick_model_action,
    _remove_custom_model_from_toml,
    _switch_model,
)


class StubUI:
    """RichCLI 简化替身。"""

    def __init__(self):
        self.calls: list[tuple] = []
        self._console = None

    def info(self, text):
        self.calls.append(("info", text))

    def warn(self, text):
        self.calls.append(("warn", text))

    def error(self, text):
        self.calls.append(("error", text))


class StubProvider:
    """带 _model 属性与 set_model_type 的 provider 替身。"""

    def __init__(self, model: str = "old-model"):
        self._model = model
        self.model_type = None

    def set_model_type(self, t):
        self.model_type = t


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """把 model_manager.Path.home() 重定向到 tmp_path（用于 TOML 持久化测试）。"""
    import agent.model_manager as mm

    class _FakePath(type(Path())):
        @classmethod
        def home(cls):
            return tmp_path

    monkeypatch.setattr(mm, "Path", _FakePath)
    return tmp_path


def _make_settings(**overrides) -> Settings:
    """构造带覆盖字段的 Settings。"""
    s = Settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _patch_form_input(monkeypatch, result):
    """mock agent.ui.terminal_picker.form_input。"""
    import agent.ui.terminal_picker as tp

    monkeypatch.setattr(tp, "form_input", lambda *a, **k: result)


def _patch_pick_from_list(monkeypatch, result):
    """mock agent.ui.terminal_picker.pick_from_list。"""
    import agent.ui.terminal_picker as tp

    monkeypatch.setattr(tp, "pick_from_list", lambda *a, **k: result)


def _patch_save_custom_model(monkeypatch, result=True) -> mock.MagicMock:
    """mock agent.config.settings.save_custom_model，返回 mock 对象。"""
    import agent.config.settings as cs

    m = mock.MagicMock(return_value=result)
    monkeypatch.setattr(cs, "save_custom_model", m)
    return m


def _patch_save_last_model(monkeypatch, result=True) -> mock.MagicMock:
    """mock agent.config.settings.save_last_model，返回 mock 对象。"""
    import agent.config.settings as cs

    m = mock.MagicMock(return_value=result)
    monkeypatch.setattr(cs, "save_last_model", m)
    return m


def _patch_build_provider(monkeypatch, provider=None) -> mock.MagicMock:
    """mock agent.bootstrap._build_provider，返回 mock 对象。"""
    import agent.bootstrap as bs

    m = mock.MagicMock(return_value=provider or StubProvider())
    monkeypatch.setattr(bs, "_build_provider", m)
    return m


class TestInferModelVendorExtra:
    """厂商推断剩余分支。"""

    def test_siliconflow_prefix(self) -> None:
        assert _infer_model_vendor("siliconflow-qlip") == "siliconflow"

    def test_vendor_name_prefix_match(self) -> None:
        assert _infer_model_vendor("moonshot-v1") == "moonshot"
        assert _infer_model_vendor("openai-gpt-4o") == "openai"
        assert _infer_model_vendor("google-gemini") == "google"
        assert _infer_model_vendor("anthropic-claude") == "anthropic"
        assert _infer_model_vendor("minimax-abab") == "minimax"
        assert _infer_model_vendor("xiaomimimo-pro") == "xiaomimimo"

    def test_cfg_zai_mapped_to_zhipu(self) -> None:
        """zai 是接口类型而非厂商，映射回 zhipu。"""
        assert _infer_model_vendor("anything", {"vendor": "zai"}) == "zhipu"

    def test_cfg_provider_field(self) -> None:
        assert _infer_model_vendor("x-model", {"provider": "deepseek"}) == "deepseek"


class TestInferBaseUrlExtra:
    """Base URL 推断剩余分支。"""

    def test_google(self) -> None:
        assert "generativelanguage" in _infer_base_url("google", "openai")

    def test_anthropic(self) -> None:
        assert "api.anthropic.com" in _infer_base_url("anthropic", "anthropic")

    def test_siliconflow(self) -> None:
        assert "api.siliconflow.cn" in _infer_base_url("siliconflow", "openai")

    def test_moonshot(self) -> None:
        assert "api.moonshot.cn" in _infer_base_url("moonshot", "openai")

    def test_minimax(self) -> None:
        assert "api.minimax.chat" in _infer_base_url("minimax", "openai")

    def test_dashscope_openai_compat(self) -> None:
        assert "dashscope.aliyuncs.com" in _infer_base_url("dashscope", "openai")

    def test_openai(self) -> None:
        assert "api.openai.com" in _infer_base_url("openai", "openai")


class TestAddCustomModelFlow:
    """添加自定义模型流程。"""

    def test_cancel_returns_false(self, monkeypatch) -> None:
        _patch_form_input(monkeypatch, None)
        ui = StubUI()
        settings = _make_settings()
        assert _add_custom_model_flow(ui, settings) is False
        assert ("info", "已取消") in ui.calls

    def test_empty_name_warns(self, monkeypatch) -> None:
        _patch_form_input(monkeypatch, {"模型名": "   "})
        ui = StubUI()
        assert _add_custom_model_flow(ui, _make_settings()) is False
        assert ui.calls[0][0] == "warn"
        assert "模型名不能为空" in ui.calls[0][1]

    def test_success(self, monkeypatch) -> None:
        _patch_form_input(monkeypatch, {
            "模型厂商": "deepseek", "模型名": "deepseek-v4-pro", "API Key": "k1",
            "接口类型": "openai", "Base URL": "", "模型类型": "text",
        })
        save_mock = _patch_save_custom_model(monkeypatch, True)
        ui = StubUI()
        settings = _make_settings()
        assert _add_custom_model_flow(ui, settings) is True
        # 配置写入 settings.custom_models
        cfg = settings.custom_models["deepseek-v4-pro"]
        assert cfg["provider"] == "deepseek"
        assert cfg["api_format"] == "openai"
        assert cfg["model_type"] == "text"
        assert cfg["base_url"] == "https://api.deepseek.com"  # Base URL 空自动推断
        save_mock.assert_called_once()
        assert any("已添加" in c[1] for c in ui.calls if c[0] == "info")

    def test_save_failed_warns(self, monkeypatch) -> None:
        _patch_form_input(monkeypatch, {
            "模型厂商": "deepseek", "模型名": "m", "API Key": "",
            "接口类型": "openai", "Base URL": "", "模型类型": "text",
        })
        _patch_save_custom_model(monkeypatch, False)
        ui = StubUI()
        assert _add_custom_model_flow(ui, _make_settings()) is False
        assert ui.calls[0][0] == "warn"
        assert "保存失败" in ui.calls[0][1]

    def test_save_exception_errors(self, monkeypatch) -> None:
        _patch_form_input(monkeypatch, {
            "模型厂商": "deepseek", "模型名": "m", "API Key": "",
            "接口类型": "openai", "Base URL": "", "模型类型": "text",
        })
        import agent.config.settings as cs

        def _boom(*a, **k):
            raise RuntimeError("toml write fail")

        monkeypatch.setattr(cs, "save_custom_model", _boom)
        ui = StubUI()
        assert _add_custom_model_flow(ui, _make_settings()) is False
        assert ui.calls[0][0] == "error"


class TestPickModelAction:
    """模型操作菜单。"""

    def test_returns_edit(self, monkeypatch) -> None:
        _patch_pick_from_list(monkeypatch, "edit")
        assert _pick_model_action(StubUI(), "m1") == "edit"

    def test_returns_delete(self, monkeypatch) -> None:
        _patch_pick_from_list(monkeypatch, "delete")
        assert _pick_model_action(StubUI(), "m1") == "delete"

    def test_returns_none_when_cancelled(self, monkeypatch) -> None:
        _patch_pick_from_list(monkeypatch, None)
        assert _pick_model_action(StubUI(), "m1") is None

    def test_allow_delete_false(self, monkeypatch) -> None:
        captured = {}

        def fake_pick(items, **kw):
            captured["items"] = items
            return "cancel"

        import agent.ui.terminal_picker as tp

        monkeypatch.setattr(tp, "pick_from_list", fake_pick)
        _pick_model_action(StubUI(), "m1", allow_delete=False)
        values = [v for v, _, _ in captured["items"]]
        assert "delete" not in values


class TestEditCustomModel:
    """编辑自定义模型。"""

    def test_invalid_config_warns(self) -> None:
        ui = StubUI()
        settings = _make_settings()
        settings.custom_models["m1"] = "not-a-dict"
        _edit_custom_model(ui, settings, "m1")
        assert ui.calls[0][0] == "warn"
        assert "配置无效" in ui.calls[0][1]

    def test_cancel(self, monkeypatch) -> None:
        _patch_form_input(monkeypatch, None)
        ui = StubUI()
        settings = _make_settings()
        settings.custom_models["m1"] = {"provider": "deepseek"}
        _edit_custom_model(ui, settings, "m1")
        assert ("info", "已取消") in ui.calls

    def test_update_success(self, monkeypatch) -> None:
        _patch_form_input(monkeypatch, {
            "模型厂商": "deepseek", "模型名": "m1", "API Key": "k2",
            "接口类型": "openai", "Base URL": "", "模型类型": "multimodal",
        })
        _patch_save_custom_model(monkeypatch, True)
        ui = StubUI()
        settings = _make_settings()
        settings.custom_models["m1"] = {
            "provider": "deepseek", "api_format": "openai", "base_url": "",
            "api_key": "k1", "model_type": "text",
        }
        _edit_custom_model(ui, settings, "m1")
        assert settings.custom_models["m1"]["model_type"] == "multimodal"
        assert settings.custom_models["m1"]["api_key"] == "k2"
        assert any("已更新" in c[1] for c in ui.calls if c[0] == "info")

    def test_rename_removes_old(self, monkeypatch) -> None:
        _patch_form_input(monkeypatch, {
            "模型厂商": "deepseek", "模型名": "m2", "API Key": "",
            "接口类型": "openai", "Base URL": "", "模型类型": "text",
        })
        _patch_save_custom_model(monkeypatch, True)
        ui = StubUI()
        settings = _make_settings()
        settings.custom_models["m1"] = {"provider": "deepseek", "api_format": "openai"}
        _edit_custom_model(ui, settings, "m1")
        assert "m1" not in settings.custom_models
        assert "m2" in settings.custom_models


class TestEditBuiltinModel:
    """编辑内置模型覆盖配置。"""

    def test_cancel(self, monkeypatch) -> None:
        _patch_form_input(monkeypatch, None)
        ui = StubUI()
        _edit_builtin_model(ui, _make_settings(), "qwen-max")
        assert ("info", "已取消") in ui.calls

    def test_success_uses_defaults(self, monkeypatch) -> None:
        _patch_form_input(monkeypatch, {
            "模型厂商": "dashscope", "API Key": "k",
            "接口类型": "openai", "Base URL": "", "模型类型": "text",
        })
        save_mock = _patch_save_custom_model(monkeypatch, True)
        ui = StubUI()
        settings = _make_settings(provider="dashscope", api_format="openai")
        _edit_builtin_model(ui, settings, "qwen-max")
        cfg = settings.custom_models["qwen-max"]
        assert cfg["name"] == "qwen-max"
        assert cfg["provider"] == "dashscope"
        save_mock.assert_called_once()
        assert any("已添加自定义覆盖配置" in c[1] for c in ui.calls)

    def test_existing_override_as_default(self, monkeypatch) -> None:
        """已有覆盖配置时，默认值取自覆盖配置。"""
        _patch_form_input(monkeypatch, {
            "模型厂商": "openai", "API Key": "existing-key",
            "接口类型": "openai", "Base URL": "https://example.com", "模型类型": "multimodal",
        })
        _patch_save_custom_model(monkeypatch, True)
        ui = StubUI()
        settings = _make_settings(api_format="dashscope")
        settings.custom_models["qwen-max"] = {
            "provider": "openai", "api_format": "openai",
            "base_url": "https://example.com", "api_key": "existing-key", "model_type": "multimodal",
        }
        _edit_builtin_model(ui, settings, "qwen-max")
        assert settings.custom_models["qwen-max"]["provider"] == "openai"

    def test_save_failed_warns(self, monkeypatch) -> None:
        _patch_form_input(monkeypatch, {
            "模型厂商": "dashscope", "API Key": "", "接口类型": "openai",
            "Base URL": "", "模型类型": "text",
        })
        _patch_save_custom_model(monkeypatch, False)
        ui = StubUI()
        _edit_builtin_model(ui, _make_settings(), "qwen-max")
        assert ui.calls[0][0] == "warn"


class TestDeleteCustomModel:
    """删除自定义模型。"""

    def test_confirm_delete(self, monkeypatch, fake_home) -> None:
        _patch_pick_from_list(monkeypatch, "yes")
        removed = mock.MagicMock()
        import agent.model_manager as mm

        monkeypatch.setattr(mm, "_remove_custom_model_from_toml", removed)
        ui = StubUI()
        settings = _make_settings()
        settings.custom_models["m1"] = {"provider": "deepseek"}
        _delete_custom_model(ui, settings, "m1")
        removed.assert_called_once_with("m1")
        assert "m1" not in settings.custom_models
        assert any("已删除" in c[1] for c in ui.calls)

    def test_cancel(self, monkeypatch, fake_home) -> None:
        _patch_pick_from_list(monkeypatch, "no")
        ui = StubUI()
        settings = _make_settings()
        settings.custom_models["m1"] = {"provider": "deepseek"}
        _delete_custom_model(ui, settings, "m1")
        assert "m1" in settings.custom_models
        assert ("info", "已取消") in ui.calls


class TestRemoveCustomModelFromToml:
    """从 settings.toml 移除自定义模型段。"""

    def test_no_file_no_error(self, fake_home) -> None:
        _remove_custom_model_from_toml("m1")  # 不抛异常

    def test_no_marker_no_change(self, fake_home) -> None:
        toml = fake_home / ".jarvis" / "settings.toml"
        toml.parent.mkdir(parents=True, exist_ok=True)
        toml.write_text('last_model = "x"\n\n[llm]\napi_format = "openai"\n', encoding="utf-8")
        _remove_custom_model_from_toml("ghost")
        content = toml.read_text(encoding="utf-8")
        assert "[llm]\napi_format" in content

    def test_remove_section(self, fake_home) -> None:
        toml = fake_home / ".jarvis" / "settings.toml"
        toml.parent.mkdir(parents=True, exist_ok=True)
        toml.write_text(
            'last_model = "m2"\n\n'
            '# 自定义模型（通过 /models 添加）\n'
            '[llm.custom_models."m1"]\n'
            'name = "m1"\n'
            'api_key = "k1"\n\n'
            '[llm.custom_models."m2"]\n'
            'name = "m2"\n',
            encoding="utf-8",
        )
        _remove_custom_model_from_toml("m1")
        content = toml.read_text(encoding="utf-8")
        assert 'custom_models."m1"' not in content
        assert 'custom_models."m2"' in content

    def test_remove_last_section(self, fake_home) -> None:
        toml = fake_home / ".jarvis" / "settings.toml"
        toml.parent.mkdir(parents=True, exist_ok=True)
        toml.write_text(
            '[llm.custom_models."only"]\nname = "only"\n',
            encoding="utf-8",
        )
        _remove_custom_model_from_toml("only")
        content = toml.read_text(encoding="utf-8")
        assert 'custom_models."only"' not in content


class TestSwitchModel:
    """模型切换。"""

    def _settings_with_custom(self, **extra) -> Settings:
        settings = _make_settings(
            api_format="openai",
            base_url="https://api.deepseek.com",
            api_key="base-key",
            max_iterations=10,
            max_tokens=2000,
            temperature=0.1,
            context_compaction=True,
            compaction_threshold=8000,
            keep_recent_messages=6,
            vendor_fallback="",
            models={"qwen-max": "通义千问", "deepseek-chat": "DeepSeek"},
            **extra,
        )
        settings.custom_models["deepseek-chat"] = {
            "provider": "deepseek", "api_format": "openai",
            "base_url": "", "api_key": "", "model_type": "text",
        }
        return settings

    def test_same_model_returns_none(self) -> None:
        """同模型直接返回 None，不重建 provider。"""
        provider = StubProvider(model="deepseek-chat")
        result = _switch_model(
            StubUI(), self._settings_with_custom(), provider, None, None, "sys", "deepseek-chat"
        )
        assert result is None

    def test_custom_reuse_provider(self, monkeypatch) -> None:
        """配置与当前 settings 一致 → 复用 provider，仅同步 model_type。"""
        build_mock = _patch_build_provider(monkeypatch)
        save_mock = _patch_save_last_model(monkeypatch)
        ui = StubUI()
        provider = StubProvider(model="old")
        settings = self._settings_with_custom()
        result = _switch_model(ui, settings, provider, None, None, "sys", "deepseek-chat")
        assert result is not None
        new_provider, loop, name = result
        assert new_provider is provider  # 复用
        assert provider.model_type == "text"  # set_model_type 已同步
        assert provider._model == "deepseek-chat"
        assert isinstance(loop, QueryLoop)
        build_mock.assert_not_called()
        save_mock.assert_called_once_with("deepseek-chat")
        assert ui.calls[0][0] == "info"

    def test_custom_needs_new_provider(self, monkeypatch) -> None:
        """自定义模型 api_format 与当前不同 → 重建 provider。"""
        build_mock = _patch_build_provider(monkeypatch)
        _patch_save_last_model(monkeypatch)
        ui = StubUI()
        provider = StubProvider(model="old")
        settings = self._settings_with_custom()
        settings.custom_models["deepseek-chat"]["api_format"] = "anthropic"
        new_provider, _loop, _name = _switch_model(
            ui, settings, provider, None, None, "sys", "deepseek-chat"
        )
        build_mock.assert_called_once()
        assert new_provider is not provider

    def test_custom_base_url_differs_rebuilds(self, monkeypatch) -> None:
        build_mock = _patch_build_provider(monkeypatch)
        _patch_save_last_model(monkeypatch)
        ui = StubUI()
        provider = StubProvider(model="old")
        settings = self._settings_with_custom()
        settings.custom_models["deepseek-chat"]["base_url"] = "https://other.com"
        new_provider, _loop, _name = _switch_model(
            ui, settings, provider, None, None, "sys", "deepseek-chat"
        )
        build_mock.assert_called_once()
        assert new_provider is not provider

    def test_builtin_with_defaults_rebuilds(self, monkeypatch) -> None:
        """内置模型且存在 default_* 覆盖值 → 用原始配置重建。"""
        build_mock = _patch_build_provider(monkeypatch)
        _patch_save_last_model(monkeypatch)
        ui = StubUI()
        provider = StubProvider(model="deepseek-chat")
        settings = self._settings_with_custom(
            default_provider="dashscope",
            default_api_format="dashscope",
            default_base_url="",
            default_api_key="orig-key",
        )
        new_provider, _loop, name = _switch_model(
            ui, settings, provider, None, None, "sys", "qwen-max"
        )
        build_mock.assert_called_once()
        assert new_provider is not provider
        assert name == "qwen-max"

    def test_builtin_no_defaults_uses_settings(self, monkeypatch) -> None:
        build_mock = _patch_build_provider(monkeypatch)
        _patch_save_last_model(monkeypatch)
        ui = StubUI()
        provider = StubProvider(model="deepseek-chat")
        settings = self._settings_with_custom()
        _switch_model(ui, settings, provider, None, None, "sys", "qwen-max")
        # default_* 全空 → clean_settings 就是 settings 本身
        build_mock.assert_called_once()

    def test_save_last_model_exception_silent(self, monkeypatch) -> None:
        _patch_build_provider(monkeypatch)
        import agent.config.settings as cs

        def _boom(*a, **k):
            raise OSError("no permission")

        monkeypatch.setattr(cs, "save_last_model", _boom)
        ui = StubUI()
        provider = StubProvider(model="old")
        settings = self._settings_with_custom()
        # 不抛异常即为通过
        result = _switch_model(ui, settings, provider, None, None, "sys", "deepseek-chat")
        assert result is not None


class TestListModels:
    """模型列表展示。"""

    def test_no_models_prints_without_console(self, capsys) -> None:
        ui = StubUI()
        _list_models(ui, _make_settings(models={}), "current")
        out = capsys.readouterr().out
        assert "暂无配置的可选模型" in out

    def test_no_models_prints_with_console(self) -> None:
        printed: list = []
        ui = StubUI()
        ui._console = type("Console", (), {"print": lambda self, *a, **k: printed.append(a)})()
        _list_models(ui, _make_settings(models={}), "current")
        assert printed

    def test_models_printed_without_console(self, capsys) -> None:
        ui = StubUI()
        settings = _make_settings(models={"qwen-max": "通义千问", "deepseek-chat": "DeepSeek"})
        _list_models(ui, settings, "qwen-max")
        out = capsys.readouterr().out
        assert "qwen-max" in out
        assert "★ 当前" in out
        assert "deepseek-chat" in out
        assert "通义千问" in out

    def test_models_table_with_console(self) -> None:
        printed: list = []
        ui = StubUI()
        ui._console = type("Console", (), {"print": lambda self, *a, **k: printed.append(a)})()
        settings = _make_settings(models={"qwen-max": "通义千问"})
        _list_models(ui, settings, "qwen-max")
        assert printed, "应通过 rich Table 打印模型列表"
