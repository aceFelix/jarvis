"""语音（DashScope）API Key：解析优先级、首启引导、写盘（aceFelix）。

测的不是"有没有这个功能"，而是三处曾经各写一份、并且已经漂移过的口径：

1. **``/voice`` 不读配置文件里的 ``dashscope_api_key``**（只认环境变量）；
2. **``/voice`` 拿别家 LLM 的 key 去连阿里云**（LLM 换成 DeepSeek 就 401，
   而报错只说"TTS 连接建立失败"，用户想不到是 key 混用）；
3. **首启引导根本不问语音 key**（选 DeepSeek/OpenAI 的用户装完"能打字不会说话"）。

三者收敛到 ``agent/config/voice_key.py`` 之后，本文件把口径钉死：
优先级 **显式配置 > 环境变量 > （仅当 LLM 就是 DashScope 时）复用主 key**。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.config import models_config, voice_key
from agent.config.settings import Settings

#: 会干扰"未配置"判定的环境变量，全部清掉（否则测试读到开发机上的真 key）
_ROOT_ENVS = (
    "DASHSCOPE_API_KEY", "JARVIS_API_KEY", "DEEPSEEK_API_KEY", "ZAI_API_KEY",
    "ANTHROPIC_API_KEY", "KIMI_API_KEY", "MINIMAX_API_KEY", "MIMO_API_KEY",
    "OPENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """隔离 HOME 与所有 API Key 环境变量，并拦住真实 keyring 写入。"""
    for name in _ROOT_ENVS:
        monkeypatch.delenv(name, raising=False)
    # 别真的往 Windows 凭据管理器里塞测试 key
    monkeypatch.setattr(
        "agent.config.keyring_store.store_api_key", lambda *a, **k: True,
        raising=False,
    )
    with patch.object(Path, "home", return_value=tmp_path):
        yield tmp_path


# ---------------------------------------------------------------------------
# 解析优先级
# ---------------------------------------------------------------------------


class TestResolve:
    def test_configured_key_wins(self):
        s = Settings(dashscope_api_key="sk-voice", api_key="sk-other")
        assert voice_key.resolve(s) == "sk-voice"
        assert voice_key.source(s) == "configured"

    def test_configured_beats_env(self, monkeypatch):
        """引导里填的 key 必须立刻生效，不能被机器上残留的旧环境变量吃掉。

        注：``env.py`` 只在配置为空时用环境变量灌 ``dashscope_api_key``，
        所以这里的优先级与它一致——手写的永远压过环境变量。
        """
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-from-env")
        s = Settings(dashscope_api_key="sk-from-setup")
        assert voice_key.resolve(s) == "sk-from-setup"

    def test_env_used_when_config_empty(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-from-env")
        s = Settings(dashscope_api_key="")
        assert voice_key.resolve(s) == "sk-from-env"
        assert voice_key.source(s) == "env"

    def test_reuses_llm_key_only_when_llm_is_dashscope(self):
        """**核心回归**：LLM 不是 DashScope 时绝不能借它的 key 硬连阿里云。"""
        deepseek = Settings(provider="deepseek", api_key="sk-deepseek")
        assert voice_key.resolve(deepseek) == ""
        assert voice_key.source(deepseek) == ""

        dashscope = Settings(provider="dashscope", api_key="sk-dashscope")
        assert voice_key.resolve(dashscope) == "sk-dashscope"
        assert voice_key.source(dashscope) == "llm"

    def test_空白字符串算未配置(self):
        # 配置里写了空串或纯空格，都不该被当成"配好了"
        assert voice_key.resolve(Settings(dashscope_api_key="   ")) == ""
        assert voice_key.is_configured(Settings(dashscope_api_key="  ")) is False

    def test_none_settings_是安全的(self):
        assert voice_key.resolve(None) == ""
        assert voice_key.is_configured(None) is False


class TestReuseDefaultKey:
    """第 4 层：配置**顶层**是 DashScope 时用 ``default_api_key``。

    真实场景（2026-10 实测）：``models.toml`` 顶层写 ``provider = "dashscope"``
    + 百炼 key，但 ``last_model`` 切到了 DeepSeek 模型，于是运行时
    ``provider = deepseek``、``api_key`` 是 DeepSeek 的 key。没有这一层，用户
    明明配了百炼 key，语音却说“未配置”。
    """

    def test_uses_top_level_key_when_last_model_switched_vendor(self):
        s = Settings(
            provider="deepseek", api_key="sk-deepseek",
            base_url="https://api.deepseek.com/v1",
            default_provider="dashscope",
            default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            default_api_key="sk-bailian-top",
        )
        assert voice_key.resolve(s) == "sk-bailian-top"
        assert voice_key.source(s) == "default"

    def test_running_dashscope_wins_over_default(self):
        """两层都能命中时优先当前运行中的（更贴近本会话）。"""
        s = Settings(
            provider="dashscope", api_key="sk-current",
            default_provider="dashscope", default_api_key="sk-top",
        )
        assert voice_key.resolve(s) == "sk-current"
        assert voice_key.source(s) == "llm"

    def test_non_dashscope_top_level_is_not_reused(self):
        s = Settings(
            provider="deepseek", api_key="sk-deepseek",
            default_provider="openai", default_api_key="sk-openai",
        )
        assert voice_key.resolve(s) == ""

    def test_default_without_key_is_not_configured(self):
        s = Settings(default_provider="dashscope", default_api_key="")
        assert voice_key.is_configured(s) is False
        # 提示必须说清是“顶层配了百炼但没填 key”，而不是笼统的“未取到 key”
        assert "顶层" in voice_key.missing_hint(s)


class TestLlmIsDashscope:
    def test_by_provider(self):
        assert voice_key.llm_is_dashscope(Settings(provider="dashscope")) is True

    def test_by_api_format(self):
        """旧配置/SDK 自管端点时 ``api_format`` 可能是 ``dashscope``。"""
        assert voice_key.llm_is_dashscope(
            Settings(provider="", api_format="dashscope")
        ) is True

    def test_by_base_url(self):
        """provider 手填成 openai、但地址指向百炼兼容端点——也得认出来。"""
        s = Settings(
            provider="openai", api_format="openai",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        assert voice_key.llm_is_dashscope(s) is True

    def test_other_vendor_is_not(self):
        s = Settings(provider="deepseek", base_url="https://api.deepseek.com/v1")
        assert voice_key.llm_is_dashscope(s) is False

    def test_none_is_not(self):
        assert voice_key.llm_is_dashscope(None) is False


class TestHints:
    def test_describe_covers_all_sources(self, monkeypatch):
        assert voice_key.describe(Settings()) == "未配置"
        assert "dashscope_api_key" in voice_key.describe(
            Settings(dashscope_api_key="sk-x")
        )
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-e")
        assert "DASHSCOPE_API_KEY" in voice_key.describe(Settings())
        monkeypatch.delenv("DASHSCOPE_API_KEY")
        assert "复用主 LLM" in voice_key.describe(
            Settings(provider="dashscope", api_key="sk-dash")
        )
        assert "复用配置顶层" in voice_key.describe(
            Settings(
                provider="deepseek", api_key="sk-ds",
                default_provider="dashscope", default_api_key="sk-top",
            )
        )

    def test_missing_hint_says_why_llm_key_cannot_be_reused(self):
        """LLM 用别家时，提示里必须点明"不能复用主 key"，否则用户会一直试。"""
        hint = voice_key.missing_hint(Settings(provider="deepseek"))
        assert "不是 DashScope" in hint
        # 三条可用配法都要给到，含获取地址
        assert "/init" in hint and "dashscope_api_key" in hint
        assert "DASHSCOPE_API_KEY" in hint
        assert "bailian.console.aliyun.com" in hint

    def test_missing_hint_mentions_dashscope(self):
        """``/talk`` 等入口靠这个字样判断"是配置问题"，别删。"""
        assert "DashScope" in voice_key.missing_hint(Settings())


# ---------------------------------------------------------------------------
# /voice 的读取口径（原先不读 dashscope_api_key）
# ---------------------------------------------------------------------------


class TestVoiceConfigUsesSharedResolver:
    def test_reads_configured_key(self):
        from agent.voice.voice_config import _voice_api_key

        assert _voice_api_key(Settings(dashscope_api_key="sk-cfg")) == "sk-cfg"

    def test_does_not_borrow_other_vendor_key(self):
        """**核心回归**：旧实现无条件回退 ``settings.api_key``。"""
        from agent.voice.voice_config import _voice_api_key

        s = Settings(provider="deepseek", api_key="sk-deepseek")
        assert _voice_api_key(s) == ""

    def test_reuses_when_llm_is_dashscope(self):
        from agent.voice.voice_config import _voice_api_key

        s = Settings(provider="dashscope", api_key="sk-dashscope")
        assert _voice_api_key(s) == "sk-dashscope"


# ---------------------------------------------------------------------------
# 写盘：models.toml 顶层（不能写 settings.toml，会被拆分搬走）
# ---------------------------------------------------------------------------


class TestSaveDashscopeKey:
    def test_writes_to_models_toml_top_level(self, _isolated):
        path = models_config.save_dashscope_api_key("sk-voice-123")
        assert path is not None
        assert path.name == "models.toml"
        lines = path.read_text(encoding="utf-8").splitlines()
        key_line = next(
            i for i, line in enumerate(lines) if line.startswith("dashscope_api_key")
        )
        # TOML 顺序陷阱：顶层键必须落在任何 [section] 之前
        section_line = next((i for i, line in enumerate(lines) if line.startswith("[")),
                            len(lines))
        assert key_line < section_line
        assert 'dashscope_api_key = "sk-voice-123"' == lines[key_line]

    def test_replaces_existing_value(self, _isolated):
        models_config.save_dashscope_api_key("sk-old")
        models_config.save_dashscope_api_key("sk-new")
        text = (_isolated / ".jarvis" / "models.toml").read_text(encoding="utf-8")
        assert "sk-old" not in text and 'sk-new' in text

    def test_preserves_other_content(self, _isolated):
        path = models_config.user_models_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            'provider = "deepseek"\n\n[llm.models]\n'  # noqa: ISC003
            'x = "说明"\n',
            encoding="utf-8",
        )
        models_config.save_dashscope_api_key("sk-voice")
        text = path.read_text(encoding="utf-8")
        assert 'provider = "deepseek"' in text
        assert '[llm.models]' in text and 'x = "说明"' in text
        # 新键插在顶层区（provider 之后、[llm.models] 之前）
        assert text.index("dashscope_api_key") < text.index("[llm.models]")

    def test_empty_key_writes_nothing(self, _isolated):
        assert models_config.save_dashscope_api_key("") is None
        assert models_config.save_dashscope_api_key("   ") is None
        assert not (_isolated / ".jarvis" / "models.toml").exists()

    def test_written_key_is_readable_back(self, _isolated):
        """写盘 → 重新加载 settings，必须能读到（否则引导等于白填）。"""
        models_config.save_dashscope_api_key("sk-roundtrip")
        from agent.config.settings import load_settings

        s = load_settings(workdir=str(_isolated))
        assert s.dashscope_api_key == "sk-roundtrip"


# ---------------------------------------------------------------------------
# 首启引导：条件第四问
# ---------------------------------------------------------------------------


class _UI:
    """ui 替身：记录输出，按队列回答 ask_user。"""

    def __init__(self, answers: list[str] | None = None) -> None:
        self.infos: list[str] = []
        self.printed: list[str] = []
        self.answers = list(answers or [])
        self._console = SimpleNamespace(print=self._record_print)

    def _record_print(self, *args: object, **_kwargs: object) -> None:
        self.printed.append(" ".join(str(a) for a in args))

    def info(self, text: str) -> None:
        self.infos.append(text)

    def error(self, text: str) -> None:
        self.infos.append(f"ERROR: {text}")

    def ask_user(self, prompt: str = "") -> str:      # noqa: ARG002 - 保持一致
        return self.answers.pop(0) if self.answers else ""

    @property
    def text(self) -> str:
        return "\n".join(self.infos + self.printed)


class TestDescribeDetail:
    """带脱敏值 + 具体来源的说明（引导与 /init voice 用）。

    触发原因：首启引导原用 ``describe()`` 输出「dashscope_api_key 已有值（配置文件
    写入或环境变量灌入）」，用户跑完反问“没有第四问啊”——他那句提示既没说清
    是哪把 key，也没说想换该去哪。
    """

    def test_shows_masked_key_never_full(self):
        s = Settings(dashscope_api_key="sk-abcdefghijklmn")
        text = voice_key.describe_detail(s)
        assert "sk-a" in text
        assert "sk-abcdefghijklmn" not in text, "不得回显完整 key"

    def test_detects_env_sourced(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-envabcdefgh")
        s = Settings(dashscope_api_key="sk-envabcdefgh")
        assert "环境变量" in voice_key.describe_detail(s)

    def test_says_it_is_typed_in_file_otherwise(self):
        s = Settings(dashscope_api_key="sk-fileonly12345")
        assert "配置文件" in voice_key.describe_detail(s)

    def test_reuse_explains_why(self):
        s = Settings(provider="dashscope", api_key="sk-llmkey123456")
        assert "复用" in voice_key.describe_detail(s)
        assert "DashScope" in voice_key.describe_detail(s)

    def test_unconfigured(self):
        assert voice_key.describe_detail(Settings()) == "未配置"

    def test_none_settings_is_safe(self):
        assert voice_key.describe_detail(None) == "未配置"


# ---------------------------------------------------------------------------
# /init voice：已配就跳过 -> 想换也能换
# ---------------------------------------------------------------------------


class TestInitVoice:
    """语音 Key 在“已配”时会被首启引导跳过（正确的），但得另有一个换的入口。"""

    @staticmethod
    def _run(ui, settings=None):
        import asyncio

        from agent.commands.handlers import init_command

        return asyncio.run(init_command._handle_voice_init(ui, settings))

    def test_saves_new_key(self, _isolated):
        ui = _UI(answers=["sk-newbailian"])
        assert self._run(ui, Settings()) is True
        text = (_isolated / ".jarvis" / "models.toml").read_text(encoding="utf-8")
        assert 'dashscope_api_key = "sk-newbailian"' in text

    def test_enter_keeps_current(self, _isolated):
        ui = _UI(answers=[""])
        self._run(ui, Settings(dashscope_api_key="sk-keepme"))
        assert "未改动" in ui.text
        assert not (_isolated / ".jarvis" / "models.toml").exists()

    def test_shows_current_state_masked(self, _isolated):
        ui = _UI(answers=[""])
        self._run(ui, Settings(dashscope_api_key="sk-existing-xyz"))
        assert "sk-e" in ui.text
        assert "sk-existing-xyz" not in ui.text, "不得回显完整 key"

    def test_explains_when_llm_is_dashscope(self, _isolated):
        """LLM 就是百炼时语音本来就能用，得说清“为什么还要单独配”。"""
        ui = _UI(answers=[""])
        self._run(ui, Settings(provider="dashscope", api_key="sk-llm"))
        assert "复用" in ui.text

    def test_blank_settings_does_not_crash(self, _isolated):
        ui = _UI(answers=[""])
        assert self._run(ui, None) is True


class TestInitVoiceDispatch:
    """/init voice 必须分流到语音流程，且不能吃掉默认三问或 full。"""

    @staticmethod
    def _dispatch(cmd: str):
        import asyncio

        from agent.commands.handlers import init_command

        hit: list[str] = []

        async def _voice(_ui, _settings=None):
            hit.append("voice")
            return True

        async def _full(_ctx):
            hit.append("full")
            return True

        async def _quick(_ui, _settings=None, **_kw):
            hit.append("quick")
            return True

        with patch.object(init_command, "_handle_voice_init", _voice), \
             patch.object(init_command, "_handle_full_init", _full), \
             patch("agent.config.onboarding.run_quick_setup", _quick):
            asyncio.run(init_command.handle_init(
                SimpleNamespace(ui=_UI(), settings=Settings()), cmd
            ))
        return hit

    @pytest.mark.parametrize("cmd", ["/init voice", "/init VOICE", "/init -v"])
    def test_voice_routes_to_voice_flow(self, cmd):
        assert self._dispatch(cmd) == ["voice"]

    def test_bare_init_still_quick(self):
        assert self._dispatch("/init") == ["quick"]

    def test_full_still_full(self):
        assert self._dispatch("/init full") == ["full"]


class TestAskVoiceKeyBranch:
    """直接测 ``_ask_voice_key``：三种"不问"与一种"问"。"""

    @staticmethod
    def _ask(ui, settings, *, vendor="deepseek", llm_key="sk-llm"):
        from agent.config.onboarding import _ask_voice_key

        return _ask_voice_key(ui, settings, vendor_key=vendor, llm_api_key=llm_key)

    def test_dashscope_vendor_reuses_without_writing(self):
        """LLM 选的就是百炼 → 同一串 key，不问也不写盘。"""
        ui = _UI()
        key, ready = self._ask(ui, Settings(), vendor="dashscope", llm_key="sk-dash")
        assert (key, ready) == ("", True)
        assert ui.answers == []                      # 一个问题都没问
        assert "复用" in ui.text

    def test_dashscope_vendor_without_llm_key_still_asks(self):
        """选了百炼但没填 key —— 没有可复用的东西，还得问。"""
        ui = _UI(answers=[""])
        key, ready = self._ask(ui, Settings(), vendor="dashscope", llm_key="")
        assert (key, ready) == ("", False)

    def test_other_vendor_asks_and_returns_key(self):
        ui = _UI(answers=["sk-voice-new"])
        key, ready = self._ask(ui, Settings(), vendor="deepseek")
        assert (key, ready) == ("sk-voice-new", True)
        assert "阿里云百炼" in ui.text

    def test_other_vendor_skip_marks_not_ready(self):
        ui = _UI(answers=[""])
        key, ready = self._ask(ui, Settings(), vendor="deepseek")
        assert (key, ready) == ("", False)
        assert "已跳过" in ui.text

    def test_already_configured_is_not_asked(self):
        ui = _UI()
        key, ready = self._ask(ui, Settings(dashscope_api_key="sk-has"))
        assert (key, ready) == ("", True)
        assert ui.answers == []
        assert "此项跳过" in ui.text

    def test_env_var_counts_as_configured(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-env")
        ui = _UI()
        key, ready = self._ask(ui, Settings(), vendor="deepseek")
        assert (key, ready) == ("", True)
        assert ui.answers == []


class TestQuickSetupIntegration:
    """跑一次完整首启，断言语音 key 真的写进了 models.toml。"""

    def _run(self, ui, *, vendor: str, model: str = "some-model",
             online: list[str] | None = None):
        import asyncio

        from agent.commands.handlers import init_command
        from agent.config import onboarding

        saved: list[dict] = []

        async def _fake_test(_vendor, _key):
            return True, "ok"

        with patch("agent.ui.terminal_picker.pick_from_list",
                   side_effect=[vendor, model]), \
             patch("agent.llm.model_catalog.fetch_with_reason",
                   return_value=(list(online or []), "测试：断网")), \
             patch.object(init_command, "_save_config",
                          side_effect=lambda v, k, t: saved.append(dict(v, _key=k))
                          or Path("models.toml")), \
             patch.object(init_command, "_test_connection", _fake_test):
            ok = asyncio.run(onboarding.run_quick_setup(ui, Settings()))
        return ok, saved

    def test_non_dashscope_vendor_writes_voice_key(self, _isolated):
        """选 DeepSeek 的用户必须能在这里把语音 key 配好。"""
        ui = _UI(answers=["sk-deepseek", "sk-bailian-voice"])
        ok, saved = self._run(ui, vendor="deepseek", online=["some-model"])
        assert ok is True
        assert saved[0]["_key"] == "sk-deepseek"      # LLM key 没被污染

        text = (_isolated / ".jarvis" / "models.toml").read_text(encoding="utf-8")
        assert 'dashscope_api_key = "sk-bailian-voice"' in text

    def test_dashscope_vendor_write_no_voice_key(self, _isolated):
        """选百炼的用户走复用，不该在配置里出现第二份同名 key。"""
        ui = _UI(answers=["sk-dashscope"])
        ok, _ = self._run(ui, vendor="dashscope", online=["some-model"])
        assert ok is True
        path = _isolated / ".jarvis" / "models.toml"
        assert not path.exists() or "dashscope_api_key" not in path.read_text(
            encoding="utf-8"
        )

    def test_skipped_voice_key_shows_how_to_add_later(self, _isolated):
        """跳过了也要把"怎么补"留在屏幕上，否则用户不知道缺什么。"""
        ui = _UI(answers=["sk-deepseek", ""])
        ok, _ = self._run(ui, vendor="deepseek", online=["some-model"])
        assert ok is True
        assert "已跳过语音配置" in ui.text
        assert "dashscope_api_key" in ui.text
