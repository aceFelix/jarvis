"""画像提炼 provider 构建与 API key 解析链测试。

背景：[memory.refine] 独立提炼模型原实现留空 api_key 时直接回退主
LLM key——主 key 常属于别的厂商（如 DashScope），导致提炼请求拿错
key 报 "Authentication Fails"。

修复后的解析链（留空逐级回退）：
refine 配置 → 按厂商查环境变量 → 同名自定义模型 api_key → 主 LLM key。

@author aceFelix
"""

from __future__ import annotations

from agent.config.settings import Settings
from agent.core.memory import profile_refiner as refiner


# ─────────────────────────────────────────────────────────────
# _resolve_api_key_from_env
# ─────────────────────────────────────────────────────────────

class TestResolveApiKeyFromEnv:
    """按厂商查环境变量。"""

    def test_vendor_specific_env_wins(self, monkeypatch) -> None:
        """已知厂商只认专属变量，且优先于通用变量。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-env-key")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        assert refiner._resolve_api_key_from_env("deepseek") == "ds-env-key"

    def test_known_vendor_without_env_returns_empty(self, monkeypatch) -> None:
        """知道厂商但环境变量没配 → 返回空串（不乱拿通用变量）。"""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        assert refiner._resolve_api_key_from_env("deepseek") == ""

    def test_unknown_vendor_falls_back_generic(self, monkeypatch) -> None:
        """未知厂商走通用变量兜底。"""
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        assert refiner._resolve_api_key_from_env("some-vendor") == "openai-key"

    def test_case_insensitive_vendor(self, monkeypatch) -> None:
        """厂商名大小写不敏感。"""
        monkeypatch.setenv("DASHSCOPE_API_KEY", "dsk")
        assert refiner._resolve_api_key_from_env("DashScope") == "dsk"


# ─────────────────────────────────────────────────────────────
# _build_refine_provider 的 key 解析链
# ─────────────────────────────────────────────────────────────

def _capture_provider(monkeypatch) -> list[Settings]:
    """替换 bootstrap._build_provider，捕获传入的 settings。"""
    captured: list[Settings] = []

    def fake_build(s, model_type="multimodal"):
        captured.append(s)
        return object()

    monkeypatch.setattr("agent.bootstrap._build_provider", fake_build)
    return captured


def _settings_with_refine(**extra) -> Settings:
    """带独立提炼模型配置的 Settings（模拟 deepseek 自定义模型）。

    extra 可覆盖任意默认字段（如 profile_refine_api_key）。
    """
    kwargs: dict = dict(
        profile_refine_model="deepseek-v4-flash",
        profile_refine_api_key="",
        api_format="openai",          # 主 LLM 是别家（模拟 DashScope 场景）
        api_key="main-llm-key",
        custom_models={
            "deepseek-v4-flash": {
                "provider_type": "anthropic",
                "base_url": "https://api.deepseek.com/anthropic",
                "vendor": "deepseek",
                "api_key": "hardcoded-ds-key",
                "model_type": "text",
            },
        },
    )
    kwargs.update(extra)
    return Settings(**kwargs)


class TestRefineKeyChain:
    """api_key 解析链优先级。"""

    def test_env_var_takes_priority(self, monkeypatch) -> None:
        """refine key 留空 → 环境变量优先于自定义模型硬编码 key。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-env-key")
        captured = _capture_provider(monkeypatch)

        refiner._build_refine_provider(_settings_with_refine())

        s = captured[0]
        assert s.api_key == "ds-env-key"
        # 协议 / base_url 从同名自定义模型继承
        assert s.api_format == "anthropic"
        assert s.base_url == "https://api.deepseek.com/anthropic"
        assert s.model == "deepseek-v4-flash"

    def test_refine_explicit_key_wins_over_all(self, monkeypatch) -> None:
        """[memory.refine] 显式配了 api_key 时优先级最高。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-env-key")
        captured = _capture_provider(monkeypatch)

        refiner._build_refine_provider(
            _settings_with_refine(profile_refine_api_key="explicit-key")
        )
        assert captured[0].api_key == "explicit-key"

    def test_custom_model_key_when_no_env(self, monkeypatch) -> None:
        """环境变量没配 → 回退同名自定义模型的 api_key。"""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        captured = _capture_provider(monkeypatch)

        refiner._build_refine_provider(_settings_with_refine())
        assert captured[0].api_key == "hardcoded-ds-key"

    def test_never_falls_back_to_wrong_vendor_main_key(self, monkeypatch) -> None:
        """环境变量 + 自定义模型 key 都有时，绝不错拿主 LLM 的别厂商 key。"""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        captured = _capture_provider(monkeypatch)

        refiner._build_refine_provider(_settings_with_refine())
        assert captured[0].api_key != "main-llm-key"

    def test_no_refine_model_uses_main_llm(self, monkeypatch) -> None:
        """未配置独立提炼模型 → 直接用主 LLM（key 不变）。"""
        captured = _capture_provider(monkeypatch)

        s = Settings(api_format="openai", api_key="main-llm-key")
        refiner._build_refine_provider(s)
        assert captured[0].api_key == "main-llm-key"

    def test_mock_without_refine_model_returns_none(self, monkeypatch) -> None:
        """mock 主模型且无独立配置 → 跳过提炼。"""
        assert refiner._build_refine_provider(Settings(api_format="mock")) is None
