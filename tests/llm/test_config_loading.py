"""配置加载/迁移单元测试 — T-03 改进项。

测试覆盖:
- _apply_toml: TOML dict → Settings 字段映射（顶层、子表、权限模式）
- apply_env_overrides: 环境变量覆盖（JARVIS_* / MY_AGENT_*）
- save_last_model / save_custom_model: 模型 TOML 持久化
- _read_toml: UTF-8 BOM 兼容

@author aceFelix
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.config.settings import Settings, _apply_toml, _read_toml, load_settings
from agent.config.env import apply_env_overrides
from agent.config.model_registry import save_last_model, save_custom_model
from agent.permissions.modes import PermissionMode


# ── _read_toml ──

class TestReadToml:
    """TOML 文件读取测试。"""

    def test_read_nonexistent_file(self) -> None:
        assert _read_toml(Path("/nonexistent/settings.toml")) == {}

    def test_read_bom_encoded_file(self) -> None:
        """UTF-8 BOM 文件应正确解析。"""
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".toml", delete=False
        ) as f:
            f.write(b"\xef\xbb\xbfmodel = 'gpt-4o'\nprovider = 'openai'\n")
            path = f.name
        try:
            data = _read_toml(Path(path))
            assert data == {"model": "gpt-4o", "provider": "openai"}
        finally:
            os.unlink(path)

    def test_read_valid_toml(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False, encoding="utf-8"
        ) as f:
            f.write("provider = 'dashscope'\nmax_tokens = 4096\n")
            path = f.name
        try:
            data = _read_toml(Path(path))
            assert data["provider"] == "dashscope"
            assert data["max_tokens"] == 4096
        finally:
            os.unlink(path)

    def test_read_empty_file(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False, encoding="utf-8"
        ) as f:
            f.write("")
            path = f.name
        try:
            assert _read_toml(Path(path)) == {}
        finally:
            os.unlink(path)


# ── _apply_toml ──

class TestApplyToml:
    """TOML → Settings 字段映射测试。"""

    def test_top_level_fields(self) -> None:
        """顶层字段应直接映射。"""
        s = Settings()
        result = _apply_toml(s, {
            "provider": "dashscope",
            "model": "qwen-plus",
            "max_tokens": 8192,
            "temperature": 0.7,
        })
        assert result.provider == "dashscope"
        assert result.model == "qwen-plus"
        assert result.max_tokens == 8192
        assert result.temperature == 0.7

    def test_permission_mode_parsing(self) -> None:
        """permission_mode 字符串应解析为枚举。"""
        s = Settings()
        result = _apply_toml(s, {"permission_mode": "yolo"})
        assert result.permission_mode == PermissionMode.YOLO

    def test_tts_subtable_mapping(self) -> None:
        """[tts] 表字段应映射到 tts_* 顶层。"""
        s = Settings()
        result = _apply_toml(s, {
            "tts": {
                "model": "cosyvoice-v3-flash",
                "voice": "longanlang_v3",
                "volume": 80,
            }
        })
        assert result.tts_model == "cosyvoice-v3-flash"
        assert result.tts_voice == "longanlang_v3"
        assert result.tts_volume == 80

    def test_stt_subtable_mapping(self) -> None:
        s = Settings()
        result = _apply_toml(s, {
            "stt": {
                "model": "paraformer-realtime-v2",
                "silence_seconds": 2.0,
            }
        })
        assert result.stt_model == "paraformer-realtime-v2"
        assert result.stt_silence_seconds == 2.0

    def test_email_subtable_mapping(self) -> None:
        s = Settings()
        result = _apply_toml(s, {
            "email": {
                "enabled": True,
                "smtp_host": "smtp.163.com",
                "smtp_port": 465,
            }
        })
        assert result.email_enabled is True
        assert result.email_smtp_host == "smtp.163.com"
        assert result.email_smtp_port == 465

    def test_llm_models_subtable(self) -> None:
        """[llm.models] 应映射到 models 字典。"""
        s = Settings()
        result = _apply_toml(s, {
            "llm": {
                "models": {
                    "qwen-plus": "通义千问 Plus",
                    "deepseek-chat": "DeepSeek Chat",
                }
            }
        })
        assert result.models == {
            "qwen-plus": "通义千问 Plus",
            "deepseek-chat": "DeepSeek Chat",
        }

    def test_llm_custom_models_normalization(self) -> None:
        """[llm.custom_models] 应规范化 provider_type → api_format 并补充 provider。"""
        s = Settings()
        result = _apply_toml(s, {
            "llm": {
                "custom_models": {
                    "my-model": {
                        "base_url": "https://api.example.com/v1",
                        "api_key": "sk-xxx",
                        "provider_type": "openai",  # 旧字段名
                    }
                }
            }
        })
        cm = result.custom_models
        assert "my-model" in cm
        assert cm["my-model"]["api_format"] == "openai"  # 从 provider_type 规范化
        assert cm["my-model"]["provider"] == "openai"    # 自动补充

    def test_empty_data_returns_same_instance(self) -> None:
        """空数据应返回原 Settings（无副作用）。"""
        s = Settings()
        result = _apply_toml(s, {})
        assert result is s

    def test_boolean_fields(self) -> None:
        s = Settings(debug=False, verbose=False)
        result = _apply_toml(s, {"debug": True, "verbose": True})
        assert result.debug is True
        assert result.verbose is True

    def test_tools_subtable(self) -> None:
        """[tools] 表应映射到 tools_* 字段。"""
        s = Settings()
        result = _apply_toml(s, {
            "tools": {
                "deferred_loading": False,
                "chat_detection": False,
            }
        })
        assert result.tools_deferred_loading is False
        assert result.tools_chat_detection is False


# ── apply_env_overrides ──

class TestEnvOverrides:
    """环境变量覆盖测试。"""

    def test_provider_env_override(self) -> None:
        with patch.dict(os.environ, {"JARVIS_PROVIDER": "deepseek"}, clear=True):
            s = Settings(provider="dashscope")
            result = apply_env_overrides(s)
            assert result.provider == "deepseek"

    def test_model_env_override(self) -> None:
        with patch.dict(os.environ, {"JARVIS_MODEL": "gpt-4o"}, clear=True):
            s = Settings(model="qwen")
            result = apply_env_overrides(s)
            assert result.model == "gpt-4o"

    def test_permission_mode_env(self) -> None:
        with patch.dict(os.environ, {"JARVIS_PERMISSION_MODE": "yolo"}, clear=True):
            s = Settings(permission_mode=PermissionMode.DEFAULT)
            result = apply_env_overrides(s)
            assert result.permission_mode == PermissionMode.YOLO

    def test_debug_env(self) -> None:
        with patch.dict(os.environ, {"JARVIS_DEBUG": "1"}, clear=True):
            s = Settings(debug=False)
            result = apply_env_overrides(s)
            assert result.debug is True

    def test_api_key_env(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test123"}, clear=True):
            s = Settings(api_key="")
            result = apply_env_overrides(s)
            assert result.api_key == "sk-test123"

    def test_dashscope_api_key_env(self) -> None:
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "sk-dash"}, clear=True):
            s = Settings(dashscope_api_key="", api_key="sk-main")
            result = apply_env_overrides(s)
            assert result.dashscope_api_key == "sk-dash"
            # api_key 已有值，不应被覆盖
            assert result.api_key == "sk-main"

    def test_base_url_env(self) -> None:
        with patch.dict(os.environ, {"JARVIS_BASE_URL": "https://api.example.com/v1"}, clear=True):
            s = Settings(base_url="")
            result = apply_env_overrides(s)
            assert result.base_url == "https://api.example.com/v1"

    def test_no_env_vars_no_change(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            s = Settings(provider="dashscope", model="qwen")
            result = apply_env_overrides(s)
            assert result.provider == "dashscope"
            assert result.model == "qwen"

    def test_my_agent_compat(self) -> None:
        """MY_AGENT_* 环境变量兼容性。"""
        with patch.dict(os.environ, {"MY_AGENT_PROVIDER": "zhipu"}, clear=True):
            s = Settings(provider="dashscope")
            result = apply_env_overrides(s)
            assert result.provider == "zhipu"


# ── Model Persistence ──

class TestModelPersistence:
    """模型 TOML 持久化测试。"""

    def test_save_last_model_new_file(self) -> None:
        """新文件写入 last_model。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with patch.object(Path, "home", return_value=home):
                jarvis_dir = home / ".jarvis"
                jarvis_dir.mkdir(parents=True, exist_ok=True)
                toml_path = jarvis_dir / "settings.toml"

                # 首次保存到不存在的文件
                result = save_last_model("qwen-plus")
                assert result is True
                assert toml_path.exists()

                content = toml_path.read_text(encoding="utf-8")
                assert 'last_model = "qwen-plus"' in content

    def test_save_last_model_update_existing(self) -> None:
        """已有文件应更新 last_model 而非重复插入。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with patch.object(Path, "home", return_value=home):
                jarvis_dir = home / ".jarvis"
                jarvis_dir.mkdir(parents=True, exist_ok=True)
                toml_path = jarvis_dir / "settings.toml"
                toml_path.write_text(
                    'model = "qwen-plus"\nlast_model = "gpt-4o"\n\n[tts]\nmodel = "cosyvoice"\n',
                    encoding="utf-8",
                )

                result = save_last_model("deepseek-chat")
                assert result is True

                content = toml_path.read_text(encoding="utf-8")
                assert 'last_model = "deepseek-chat"' in content
                # 不应有两个 last_model
                assert content.count("last_model") == 1

    def test_save_custom_model_new(self) -> None:
        """新增自定义模型到 [llm.custom_models]。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with patch.object(Path, "home", return_value=home):
                jarvis_dir = home / ".jarvis"
                jarvis_dir.mkdir(parents=True, exist_ok=True)
                toml_path = jarvis_dir / "settings.toml"
                toml_path.write_text(
                    'model = "qwen-plus"\n\n[tts]\nmodel = "cosyvoice"\n',
                    encoding="utf-8",
                )

                result = save_custom_model("my-gpt", {
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-xxx",
                    "provider_type": "openai",
                })
                # save_custom_model 要求文件已存在
                assert result is True

                content = toml_path.read_text(encoding="utf-8")
                assert 'my-gpt' in content
                assert 'api_key = "sk-xxx"' in content
