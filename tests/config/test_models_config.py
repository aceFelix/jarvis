"""模型配置文件（agent.config.models_config）单元测试。

覆盖：
- 路径推导：models_path_for（含示例模板）、user_models_path（含旧路径回退）
- 文本读写：CRLF 换行保持、UTF-8 BOM 剥离
- 外科式写入：upsert_top_scalar / upsert_table_block / remove_custom_model
- 模型域写入：upsert_custom_model / upsert_last_model / save_init_model
- 拆分迁移：split_model_config 的幂等性、注释跟随、段内键不误搬、
  带行尾注释的节头、目标已有键不覆盖、备份、拆分后两个文件均可解析
- 端到端：load_settings 分层加载（同层 models 覆盖 settings、用户级覆盖项目级、
  启动期自动迁移后配置语义等价）

落盘测试用 patch Path.home 重定向到临时目录，不触碰真实 ~/.jarvis；
系统 keyring 读写统一替换为 no-op，避免污染真实凭据管理器。

@author aceFelix
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.config.models_config import (
    has_model_content,
    models_path_for,
    remove_custom_model,
    save_init_model,
    split_model_config,
    upsert_custom_model,
    upsert_last_model,
    upsert_table_block,
    upsert_top_scalar,
    user_config_paths,
    user_models_path,
)
from agent.config.settings import load_settings


@pytest.fixture(autouse=True)
def _isolate_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离系统 keyring：读写替换为 no-op（凭据管理器是进程外状态）。"""
    import agent.config.keyring_store as ks

    monkeypatch.setattr(ks, "store_api_key", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ks, "load_api_key", lambda *a, **k: None, raising=False)


# ---------------------------------------------------------------------------
# 路径推导
# ---------------------------------------------------------------------------


class TestModelsPathFor:
    """同层 models.toml 路径推导。"""

    def test_settings_maps_to_models(self):
        assert models_path_for(Path("/x/configs/settings.toml")) == Path("/x/configs/models.toml")

    def test_example_maps_to_example(self):
        """分发模板必须映射到 models.example.toml，不能被当成实际配置加载。"""
        assert models_path_for(Path("/x/agent/configs/settings.example.toml")) == (
            Path("/x/agent/configs/models.example.toml")
        )

    def test_user_models_path_default(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            (tmp_path / ".jarvis").mkdir()
            assert user_models_path() == tmp_path / ".jarvis" / "models.toml"

    def test_user_models_path_legacy_fallback(self, tmp_path):
        """只有 ~/.my-agent/settings.toml 时跟随旧目录布局（与 load_settings 同规则）。"""
        with patch.object(Path, "home", return_value=tmp_path):
            (tmp_path / ".my-agent").mkdir()
            (tmp_path / ".my-agent" / "settings.toml").write_text(
                'model = "m"\n', encoding="utf-8"
            )
            assert user_models_path() == tmp_path / ".my-agent" / "models.toml"

    def test_user_models_path_ignores_empty_legacy_dir(self, tmp_path):
        """旧目录存在但没有 settings.toml 时仍用 ~/.jarvis（避免读写分家）。"""
        with patch.object(Path, "home", return_value=tmp_path):
            (tmp_path / ".my-agent").mkdir()
            assert user_models_path() == tmp_path / ".jarvis" / "models.toml"

    def test_user_config_paths_are_paired(self, tmp_path):
        """settings 与 models 必须同目录，否则写入目标与加载来源不一致。"""
        with patch.object(Path, "home", return_value=tmp_path):
            settings_path, models_path = user_config_paths()
            assert settings_path.parent == models_path.parent
            assert models_path == user_models_path()

            legacy_dir = tmp_path / ".my-agent"
            legacy_dir.mkdir()
            (legacy_dir / "settings.toml").write_text("", encoding="utf-8")
            settings_path, models_path = user_config_paths()
            assert settings_path == legacy_dir / "settings.toml"
            assert models_path == legacy_dir / "models.toml"


# ---------------------------------------------------------------------------
# 外科式写入助手
# ---------------------------------------------------------------------------


class TestUpsertTopScalar:
    """顶层标量写入：必须落在任何 [section] 之前（TOML 顺序陷阱）。"""

    def test_replace_existing_value(self):
        content = 'provider = "a"\nmodel = "m"\n\n[tts]\nvoice = "v"\n'
        out = upsert_top_scalar(content, "provider", '"b"')
        assert 'provider = "b"' in out
        assert out.count("provider") == 1
        assert 'voice = "v"' in out

    def test_insert_follows_model_line(self):
        """无同名键时插在 model = 行后，仍在首个节头之前。"""
        content = 'model = "m"\n\n[tts]\nvoice = "v"\n'
        out = upsert_top_scalar(content, "last_model", '"m2"')
        assert out.index('last_model = "m2"') < out.index("[tts]")
        assert out.index('last_model = "m2"') > out.index('model = "m"')

    def test_insert_when_file_starts_with_section(self):
        content = '[tts]\nvoice = "v"\n'
        out = upsert_top_scalar(content, "last_model", '"m"')
        assert out.startswith('last_model = "m"')
        assert "[tts]" in out

    def test_append_when_no_section(self):
        content = 'provider = "a"\n'
        out = upsert_top_scalar(content, "last_model", '"m"')
        assert out.strip().endswith('last_model = "m"')


class TestUpsertTableBlock:
    """section 段替换/追加。"""

    def test_replace_existing_block_keeps_others(self):
        content = '[tts]\nvoice = "v"\n\n[llm.custom_models."a"]\napi_key = "old"\n\n[ui]\nboot = true\n'
        out = upsert_table_block(content, '[llm.custom_models."a"]', ["api_key = \"new\""])
        assert 'api_key = "new"' in out
        assert "old" not in out
        assert 'voice = "v"' in out
        assert "boot = true" in out

    def test_append_new_block(self):
        content = '[tts]\nvoice = "v"\n'
        out = upsert_table_block(content, '[llm.custom_models."a"]', ['api_key = "k"'])
        assert out.index("[tts]") < out.index('[llm.custom_models."a"]')

    def test_unquoted_header_is_matched(self):
        """已有无引号写法 [llm.custom_models.a]（TOML 等价）也应被命中替换。"""
        content = "[llm.custom_models.a]\napi_key = \"old\"\n"
        out = upsert_table_block(content, '[llm.custom_models."a"]', ['api_key = "new"'])
        assert out.count("api_key") == 1
        assert 'api_key = "new"' in out


class TestRemoveCustomModel:
    """自定义模型段删除。"""

    def test_remove_existing(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            target = tmp_path / ".jarvis" / "models.toml"
            target.parent.mkdir(parents=True)
            target.write_text(
                'provider = "dashscope"\n\n[llm.custom_models."a"]\napi_key = "k"\n'
                '\n[llm.custom_models."b"]\napi_key = "k2"\n',
                encoding="utf-8",
            )
            assert remove_custom_model("a") is True
            content = target.read_text(encoding="utf-8")
            assert '"a"' not in content
            assert '"b"' in content
            assert 'provider = "dashscope"' in content

    def test_missing_returns_false(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            assert remove_custom_model("nope") is False
            target = tmp_path / ".jarvis" / "models.toml"
            target.parent.mkdir(parents=True)
            target.write_text('provider = "x"\n', encoding="utf-8")
            assert remove_custom_model("nope") is False


# ---------------------------------------------------------------------------
# 模型域写入（/models、切换模型、jarvis init）
# ---------------------------------------------------------------------------


class TestModelWrites:
    """写入目标必须是 models.toml，且保留既有注释与其他内容。"""

    def test_upsert_custom_model_creates_file(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            assert upsert_custom_model("my-gpt", {
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-xxx",
                "provider_type": "openai",
            }) is True
            content = (tmp_path / ".jarvis" / "models.toml").read_text(encoding="utf-8")
            assert '[llm.custom_models."my-gpt"]' in content
            assert 'api_key = "sk-xxx"' in content
            assert 'base_url = "https://api.example.com/v1"' in content
            # settings.toml 绝不参与模型写入
            assert not (tmp_path / ".jarvis" / "settings.toml").exists()

    def test_upsert_custom_model_updates_without_duplicating(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            target = tmp_path / ".jarvis" / "models.toml"
            target.parent.mkdir(parents=True)
            target.write_text(
                '# 用户注释\nprovider = "dashscope"\n\n[llm.custom_models."a"]\napi_key = "old"\n',
                encoding="utf-8",
            )
            assert upsert_custom_model("a", {"api_key": "new", "provider_type": "anthropic"}) is True
            content = target.read_text(encoding="utf-8")
            assert content.count('[llm.custom_models."a"]') == 1
            assert 'api_key = "new"' in content
            assert "# 用户注释" in content
            assert 'provider = "dashscope"' in content

    def test_upsert_last_model_keeps_top_position(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            target = tmp_path / ".jarvis" / "models.toml"
            target.parent.mkdir(parents=True)
            target.write_text(
                '[llm.models]\n"m1" = "M1"\n',
                encoding="utf-8",
            )
            assert upsert_last_model("m2") is True
            content = target.read_text(encoding="utf-8")
            assert content.index('last_model = "m2"') < content.index("[llm.models]")

    def test_save_init_model_writes_all_three_parts(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            path = save_init_model(
                vendor_key="deepseek",
                model_name="deepseek-chat",
                api_key="sk-d",
                base_url="https://api.deepseek.com/v1",
                api_format="openai",
                model_type="text",
            )
            assert path == tmp_path / ".jarvis" / "models.toml"
            content = path.read_text(encoding="utf-8")
            assert '[llm.custom_models."deepseek-chat"]' in content
            assert 'last_model = "deepseek-chat"' in content
            assert 'provider = "deepseek"' in content
            assert 'api_format = "openai"' in content
            # 顶层键都在节头之前（锚定行首，避开头部说明里的同名文本）
            assert content.index('provider = "deepseek"') < content.index(
                '\n[llm.custom_models.'
            )
            tomllib.loads(content)  # 结构合法

    def test_save_init_model_does_not_override_existing_provider(self, tmp_path):
        """provider 已有值时不覆盖（用户手动选定的厂商优先）。"""
        with patch.object(Path, "home", return_value=tmp_path):
            target = tmp_path / ".jarvis" / "models.toml"
            target.parent.mkdir(parents=True)
            target.write_text('provider = "zhipu"\n', encoding="utf-8")
            save_init_model(
                vendor_key="deepseek",
                model_name="deepseek-chat",
                api_key="",
                base_url="",
                api_format="openai",
                model_type="text",
            )
            content = target.read_text(encoding="utf-8")
            assert 'provider = "zhipu"' in content
            assert 'provider = "deepseek"' not in content


# ---------------------------------------------------------------------------
# 拆分迁移
# ---------------------------------------------------------------------------


def _split_fixture(tmp_path: Path, settings_text: str) -> tuple[Path, Path]:
    """写入待拆分 settings.toml，返回 (settings_path, models_path)。"""
    settings = tmp_path / "settings.toml"
    settings.write_text(settings_text, encoding="utf-8")
    return settings, tmp_path / "models.toml"


class TestSplitModelConfig:
    """settings.toml → models.toml 的幂等拆分。"""

    def test_moves_model_keys_and_sections(self, tmp_path):
        settings, models = _split_fixture(
            tmp_path,
            "# 头注释\nprovider = \"dashscope\"\nmodel = \"qwen\"\nmax_tokens = 100\n"
            "\n# 可选模型\n[llm.models]\n\"m1\" = \"M1\"\n"
            "\n[tts]\nmodel = \"cosyvoice\"\nvoice = \"v\"\n"
            "\n[llm.custom_models.\"c1\"]\nname = \"c1\"\napi_key = \"sk-1\"\n",
        )
        assert split_model_config(settings) is True

        models_text = models.read_text(encoding="utf-8")
        settings_text = settings.read_text(encoding="utf-8")

        # 模型域搬到 models.toml
        assert 'provider = "dashscope"' in models_text
        assert 'max_tokens = 100' in models_text
        assert "[llm.models]" in models_text
        assert '[llm.custom_models."c1"]' in models_text
        # 非模型域留在 settings.toml
        assert "[tts]" in settings_text
        assert 'voice = "v"' in settings_text
        assert 'provider = "dashscope"' not in settings_text
        # 两个文件均可被 TOML 解析
        tomllib.loads(models_text)
        tomllib.loads(settings_text)

    def test_top_keys_before_sections_in_output(self, tmp_path):
        """输出里顶层键必须排在 [llm.*] 段之前（规避顺序陷阱）。"""
        settings, models = _split_fixture(
            tmp_path,
            'api_key = "sk-x"\nmodel = "m"\n\n[llm.models]\n"m1" = "M1"\n',
        )
        assert split_model_config(settings) is True
        models_text = models.read_text(encoding="utf-8")
        # 锚定行首：头部说明注释里也出现了 [llm.models] 字样
        assert models_text.index('api_key = "sk-x"') < models_text.index("\n[llm.models]")

    def test_section_scoped_keys_are_not_moved(self, tmp_path):
        """[tts] 段内的 model 键属于语音域，绝不能被当成顶层模型键搬走。"""
        settings, _models = _split_fixture(
            tmp_path,
            '[tts]\nmodel = "cosyvoice"\nvoice = "v"\n',
        )
        original = settings.read_text(encoding="utf-8")
        assert split_model_config(settings) is False
        assert settings.read_text(encoding="utf-8") == original
        assert has_model_content(original) is False

    def test_section_head_with_inline_comment(self, tmp_path):
        """节头带行尾注释（[memory.embedding]  # 预留）也必须正确识别边界。"""
        settings, models = _split_fixture(
            tmp_path,
            "[memory.embedding]      # 预留\n"
            'model = "emb"\nprovider = "dashscope"\n'
            '\n[llm.models]\n"m1" = "M1"\n',
        )
        assert split_model_config(settings) is True
        models_text = models.read_text(encoding="utf-8")
        settings_text = settings.read_text(encoding="utf-8")
        # 段内的 model/provider 不得进 models.toml 顶层
        assert 'provider = "dashscope"' not in models_text
        assert 'model = "emb"' not in models_text
        assert "[memory.embedding]" in settings_text
        assert 'model = "emb"' in settings_text
        assert "[llm.models]" in models_text

    def test_idempotent_second_run(self, tmp_path):
        settings, models = _split_fixture(
            tmp_path,
            'provider = "dashscope"\n\n[llm.models]\n"m1" = "M1"\n\n[tts]\nvoice = "v"\n',
        )
        assert split_model_config(settings) is True
        first_models = models.read_text(encoding="utf-8")
        first_settings = settings.read_text(encoding="utf-8")

        assert split_model_config(settings) is False
        assert models.read_text(encoding="utf-8") == first_models
        assert settings.read_text(encoding="utf-8") == first_settings

    def test_comments_follow_their_block(self, tmp_path):
        settings, models = _split_fixture(
            tmp_path,
            "# 模型段说明\n# 第二行\n[llm.models]\n\"m1\" = \"M1\"\n\n# 语音段说明\n[tts]\nvoice = \"v\"\n",
        )
        assert split_model_config(settings) is True
        models_text = models.read_text(encoding="utf-8")
        settings_text = settings.read_text(encoding="utf-8")
        assert "# 模型段说明" in models_text
        assert "# 语音段说明" in settings_text
        assert "# 模型段说明" not in settings_text

    def test_backup_created_and_keeps_original(self, tmp_path):
        original = 'provider = "dashscope"\n\n[tts]\nvoice = "v"\n'
        settings, _models = _split_fixture(tmp_path, original)
        assert split_model_config(settings) is True
        backup = tmp_path / "settings.toml.bak"
        assert backup.read_text(encoding="utf-8") == original

    def test_existing_models_file_is_not_overwritten(self, tmp_path):
        """目标 models.toml 已有同名键 → 保留用户新值，只补缺失内容。"""
        settings, models = _split_fixture(
            tmp_path,
            'provider = "dashscope"\nmodel = "m"\n\n[llm.models]\n"m1" = "M1"\n',
        )
        models.write_text('provider = "zhipu"\n', encoding="utf-8")
        assert split_model_config(settings) is True
        models_text = models.read_text(encoding="utf-8")
        assert 'provider = "zhipu"' in models_text
        assert 'provider = "dashscope"' not in models_text
        assert 'model = "m"' in models_text
        assert "[llm.models]" in models_text

    def test_crlf_preserved(self, tmp_path):
        """CRLF 用户文件拆分后仍保持 CRLF（TOML 换行不一致是已知故障源）。"""
        settings = tmp_path / "settings.toml"
        settings.write_bytes(
            b'provider = "dashscope"\r\n\r\n[tts]\r\nvoice = "v"\r\n'
        )
        assert split_model_config(settings) is True
        raw_models = (tmp_path / "models.toml").read_bytes()
        raw_settings = settings.read_bytes()
        assert b"\r\n" in raw_models
        assert b"\r\r" not in raw_models
        assert b"\r\n" in raw_settings
        assert b"\r\r" not in raw_settings

    def test_fully_model_only_file_leaves_placeholder(self, tmp_path):
        settings, models = _split_fixture(tmp_path, 'provider = "dashscope"\nmodel = "m"\n')
        assert split_model_config(settings) is True
        settings_text = settings.read_text(encoding="utf-8")
        assert settings_text.strip().startswith("#")
        tomllib.loads(settings_text)
        tomllib.loads(models.read_text(encoding="utf-8"))

    def test_missing_source_returns_false(self, tmp_path):
        assert split_model_config(tmp_path / "nope.toml") is False


# ---------------------------------------------------------------------------
# 端到端：分层加载与自动迁移
# ---------------------------------------------------------------------------


class TestLoadSettingsLayering:
    """load_settings 顺序：settings → models（同层覆盖），用户级覆盖项目级。"""

    def test_same_layer_models_overrides_settings(self, tmp_path):
        work = tmp_path / "proj"
        cfgdir = work / "configs"
        cfgdir.mkdir(parents=True)
        (cfgdir / "settings.toml").write_text(
            'model = "from-settings"\nmax_tokens = 100\n', encoding="utf-8"
        )
        (cfgdir / "models.toml").write_text('model = "from-models"\n', encoding="utf-8")
        home = tmp_path / "home"
        (home / ".jarvis").mkdir(parents=True)

        with patch.object(Path, "home", return_value=home), patch.dict(os.environ, {}, clear=True):
            s = load_settings(workdir=str(work))
        assert s.model == "from-models"
        assert s.max_tokens == 100  # models.toml 未覆盖的键仍来自 settings.toml

    def test_user_models_overrides_project_models(self, tmp_path):
        work = tmp_path / "proj"
        cfgdir = work / "configs"
        cfgdir.mkdir(parents=True)
        (cfgdir / "settings.toml").write_text('provider = "project"\n', encoding="utf-8")
        (cfgdir / "models.toml").write_text('model = "project-model"\n', encoding="utf-8")
        home = tmp_path / "home"
        (home / ".jarvis").mkdir(parents=True)
        (home / ".jarvis" / "models.toml").write_text('model = "user-model"\n', encoding="utf-8")

        with patch.object(Path, "home", return_value=home), patch.dict(os.environ, {}, clear=True):
            s = load_settings(workdir=str(work))
        assert s.model == "user-model"
        assert s.provider == "project"

    def test_auto_split_on_load_keeps_semantics(self, tmp_path):
        """启动期自动拆分后，模型域字段与非模型域字段的最终取值都不变。"""
        home = tmp_path / "home"
        jarvis_dir = home / ".jarvis"
        jarvis_dir.mkdir(parents=True)
        work = tmp_path / "work"
        work.mkdir()
        (jarvis_dir / "settings.toml").write_text(
            'provider = "deepseek"\napi_key = "sk-old"\nmodel = "deepseek-chat"\n'
            'max_tokens = 8192\n'
            "\n[tts]\nvoice = \"v1\"\n"
            "\n[llm.models]\n\"m1\" = \"M1\"\n"
            '\n[llm.custom_models."c1"]\nname = "c1"\n'
            'base_url = "https://api.deepseek.com/v1"\napi_key = "sk-custom"\n',
            encoding="utf-8",
        )

        with patch.object(Path, "home", return_value=home), patch.dict(os.environ, {}, clear=True):
            s = load_settings(workdir=str(work))

        assert s.provider == "deepseek"
        assert s.api_key == "sk-old"
        assert s.model == "deepseek-chat"
        assert s.max_tokens == 8192
        assert s.models == {"m1": "M1"}
        assert "c1" in s.custom_models
        assert s.tts_voice == "v1"

        # 自动拆分确实发生：models.toml 生成，settings.toml 不再含模型键
        models_cfg = jarvis_dir / "models.toml"
        assert models_cfg.exists()
        assert 'provider = "deepseek"' in models_cfg.read_text(encoding="utf-8")
        settings_text = (jarvis_dir / "settings.toml").read_text(encoding="utf-8")
        assert 'provider = "deepseek"' not in settings_text
        assert s.tts_voice == "v1"
        assert "[tts]" in settings_text

    def test_last_model_restores_custom_model_from_models_file(self, tmp_path):
        """拆分后 last_model 仍能从 models.toml 恢复自定义模型配置。"""
        home = tmp_path / "home"
        jarvis_dir = home / ".jarvis"
        jarvis_dir.mkdir(parents=True)
        work = tmp_path / "work"
        work.mkdir()
        (jarvis_dir / "models.toml").write_text(
            'provider = "dashscope"\napi_format = "openai"\nmodel = "qwen"\nlast_model = "c1"\n'
            'base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"\n'
            '\n[llm.custom_models."c1"]\nname = "c1"\n'
            'base_url = "https://api.deepseek.com/anthropic"\napi_key = "sk-c1"\n'
            'provider_type = "anthropic"\nvendor = "deepseek"\n',
            encoding="utf-8",
        )
        with patch.object(Path, "home", return_value=home), patch.dict(os.environ, {}, clear=True):
            s = load_settings(workdir=str(work))

        assert s.model == "c1"
        assert s.api_key == "sk-c1"
        assert s.base_url == "https://api.deepseek.com/anthropic"
        assert s.api_format == "anthropic"
        # 历史写入的自定义模型段只有 provider_type/vendor、没有 provider 键，
        # settings._apply_toml 用 api_format 兜底（既有行为，本次拆分不改语义）。
        assert s.provider == "anthropic"
        # 原始值留存，供 _switch_model 切回内置模型
        assert s.default_provider == "dashscope"

    def test_models_file_does_not_wipe_non_model_tables(self, tmp_path):
        """模型域文件（无 [lsp]/[tts] 表）不得清空 settings.toml 读到的非模型字段。

        回归：_apply_toml 曾对 [lsp.servers] 无条件写入，加载 models.toml 时
        会把已读入的 lsp_servers 清空（2026-09 模型域拆分后暴露）。
        """
        home = tmp_path / "home"
        jarvis_dir = home / ".jarvis"
        jarvis_dir.mkdir(parents=True)
        work = tmp_path / "work"
        work.mkdir()
        (jarvis_dir / "settings.toml").write_text(
            '[tts]\nvoice = "v1"\n'
            "\n[lsp]\nenable = true\n"
            '\n[lsp.servers.python]\ncommand = "python-lsp-server"\nargs = []\n'
            'extensions = ["py"]\n',
            encoding="utf-8",
        )
        (jarvis_dir / "models.toml").write_text('model = "user-model"\n', encoding="utf-8")

        with patch.object(Path, "home", return_value=home), patch.dict(os.environ, {}, clear=True):
            s = load_settings(workdir=str(work))

        assert s.model == "user-model"
        assert s.tts_voice == "v1"
        assert s.enable_lsp is True
        assert s.lsp_servers == {
            "python": {
                "command": "python-lsp-server",
                "args": [],
                "extensions": ["py"],
            }
        }
