"""首启三问与「用几天后提议」（阶段 12 P4）。

判据是"全新机器到能用 ≤ 3 个问题"，所以测的不是流程好不好看，而是三条硬边界：

1. **只问三件**：模型 / API Key / 工作目录，问完能直接用（其余全默认）；
2. **不该问的地方绝不多问**：非交互（管道/CI）不问、配过了不问、取消过就不再来烦；
3. **提议不越权**：用够几天才提一次，候选**不生效**，要用户 `/prefs accept`。

台账文件全程指到 tmp_path（`Path.home()` 在调用时求值，patch home 就够）。

@author aceFelix
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.config import onboarding
from agent.config.onboarding import Usage
from agent.config.settings import Settings


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """把 `~` 指到 tmp_path，并且**清掉本机的 API Key 环境变量**。

    后者不是洁癖：`is_unconfigured` 会把"环境里有 key"当成已配置（老用户不该
    被弹首启引导）。开发机上真有 `DASHSCOPE_API_KEY` 时，不清掉就会让"裸 Settings
    算未配置"这类断言无故变红——而那不是逻辑错，是测试读到了别人的机器。
    """
    for name in (
        "JARVIS_API_KEY", "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY", "ZAI_API_KEY",
        "ANTHROPIC_API_KEY", "KIMI_API_KEY", "MINIMAX_API_KEY", "MIMO_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    with patch.object(Path, "home", return_value=tmp_path):
        yield tmp_path


class _UI:
    """ui 替身：记录所有输出，按队列回答 ask_user。"""

    def __init__(self, answers: list[str] | None = None) -> None:
        self.infos: list[str] = []
        self.printed: list[str] = []
        self.answers = list(answers or [])
        self._console = SimpleNamespace(print=self._record_print)

    def _record_print(self, *args: object, **_kwargs: object) -> None:
        self.printed.append(" ".join(str(a) for a in args))

    def info(self, text: str) -> None:
        self.infos.append(text)

    def ask_user(self, prompt: str = "") -> str:      # noqa: ARG002 - 保持签名一致
        return self.answers.pop(0) if self.answers else ""

    @property
    def text(self) -> str:
        return "\n".join(self.infos + self.printed)


# ---------------------------------------------------------------------------
# 该不该问
# ---------------------------------------------------------------------------


class TestShouldOnboard:
    def test_bare_settings_counts_as_unconfigured(self):
        assert onboarding.is_unconfigured(Settings()) is True

    def test_model_configured_means_done(self):
        s = Settings()
        s.model = "qwen3.7-plus"
        assert onboarding.is_unconfigured(s) is False

    def test_custom_models_mean_done(self):
        s = Settings()
        s.custom_models = {"qwen3.7-plus": {"vendor": "dashscope"}}
        assert onboarding.is_unconfigured(s) is False

    def test_api_key_means_done(self):
        s = Settings()
        s.api_key = "sk-xxx"
        assert onboarding.is_unconfigured(s) is False

    def test_env_key_means_done(self, monkeypatch):
        """用环境变量跑起来的老用户不该被弹引导。"""
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-xxx")
        assert onboarding.is_unconfigured(Settings()) is False

    def test_none_settings_is_unconfigured(self):
        assert onboarding.is_unconfigured(None) is True

    def test_non_interactive_never_asks(self):
        with patch.object(onboarding, "can_prompt", return_value=False):
            assert onboarding.should_onboard(Settings()) is False

    def test_can_prompt_reads_isatty(self):
        with patch("sys.stdin", SimpleNamespace(isatty=lambda: True)):
            assert onboarding.can_prompt() is True
        with patch("sys.stdin", SimpleNamespace(isatty=lambda: False)):
            assert onboarding.can_prompt() is False

    def test_can_prompt_survives_broken_stdin(self):
        with patch("sys.stdin", None):
            assert onboarding.can_prompt() is False

    def test_declined_user_is_not_asked_again(self):
        onboarding.mark_declined()
        with patch.object(onboarding, "can_prompt", return_value=True):
            assert onboarding.should_onboard(Settings()) is False

    def test_onboarded_user_is_not_asked_again(self):
        onboarding.mark_onboarded()
        with patch.object(onboarding, "can_prompt", return_value=True):
            assert onboarding.should_onboard(Settings()) is False


# ---------------------------------------------------------------------------
# 台账
# ---------------------------------------------------------------------------


class TestUsageLedger:
    def test_start_counter_persists(self):
        onboarding.note_start()
        onboarding.note_start()
        assert onboarding.load_usage().starts == 2

    def test_marks_persist(self):
        onboarding.note_start()
        onboarding.mark_onboarded()
        onboarding.mark_suggested()
        usage = onboarding.load_usage()
        assert usage.onboarded and usage.suggested and usage.starts == 1

    def test_missing_file_is_fresh_usage(self):
        usage = onboarding.load_usage()
        assert usage.starts == 0 and not usage.onboarded and not usage.declined

    def test_broken_file_does_not_crash(self, _isolated_home):
        path = _isolated_home / ".jarvis" / "usage.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert onboarding.load_usage().starts == 0

    def test_days_since_first_seen(self):
        usage = Usage(first_seen=time.time() - 3 * 86400)
        assert 2.9 < usage.days() < 3.1

    def test_save_failure_is_swallowed(self, _isolated_home):
        """记账失败不该拦住启动。"""
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            assert onboarding.save_usage(Usage()) is False


# ---------------------------------------------------------------------------
# 三问
# ---------------------------------------------------------------------------


class TestQuickSetup:
    """三问 = 厂商 / API Key / 模型（模型来自在线目录，不由我们替用户选）。"""

    def _run(self, ui, settings=None):
        import asyncio

        return asyncio.run(onboarding.run_quick_setup(ui, settings or Settings()))

    def _drive(self, ui, *, vendor="dashscope", model="qwen3.7-plus",
               online=None, connection=True):
        """驱动一次完整三问。

        `pick_from_list` 会被调两次：第一次选厂商、第二次选模型；
        `ask_user` 的答案由调用方按顺序放进 `ui.answers`（key、手动模型名等）。
        返回保存时收到的 vendor dict，便于断言最终选了哪个模型。
        """
        from agent.commands.handlers import init_command

        saved: list[dict] = []

        async def _fake_test(_vendor, _key):
            return connection, "连接成功" if connection else "boom"

        with patch("agent.ui.terminal_picker.pick_from_list",
                   side_effect=[vendor, model]) as picker, \
             patch("agent.llm.model_catalog.fetch_with_reason",
                   return_value=(list(online or []), "" if online else "测试：网络超时")) as fetch, \
             patch.object(init_command, "_save_config",
                          side_effect=lambda v, k, t: saved.append(dict(v, _key=k, _type=t))
                          or Path("models.toml")), \
             patch.object(init_command, "_test_connection", _fake_test):
            ok = self._run(ui)
        return ok, saved, fetch, picker

    def test_model_comes_from_online_catalog(self, _isolated_home):
        """有 key 时，模型必须来自厂商实时返回的列表，而不是内置表。"""
        ui = _UI(answers=["sk-test"])
        ok, saved, fetch, _ = self._drive(
            ui, model="qwen3.7-flash",
            online=["qwen3.7-max", "qwen3.7-flash", "qwen3.7-plus"],
        )
        assert ok is True
        assert saved[0]["default_model"] == "qwen3.7-flash"
        fetch.assert_called_once()
        assert "厂商返回 3 个可用模型" in ui.text
        assert onboarding.load_usage().onboarded is True

    def test_fallback_to_builtin_when_offline(self, _isolated_home):
        """拉不到在线列表时用内置兑底，并**说清具体原因**（没给 key 与拉失败要分得开）。"""
        ui = _UI(answers=["sk-test"])
        ok, saved, _, _ = self._drive(ui, model="qwen3.7-plus", online=[])
        assert ok is True
        assert saved[0]["default_model"] == "qwen3.7-plus"
        assert "测试：网络超时" in ui.text
        assert "先用内置推荐 qwen3.7-plus" in ui.text

    def test_manual_model_entry_always_available(self, _isolated_home):
        """列表再全也不该成为限制：手动输入永远可用。"""
        ui = _UI(answers=["sk-test", "my-own-model-x"])
        ok, saved, _, _ = self._drive(ui, model="__manual__", online=["a", "b"])
        assert ok is True
        assert saved[0]["default_model"] == "my-own-model-x"

    def test_no_key_means_no_online_call_and_no_connection_test(self, _isolated_home):
        from agent.commands.handlers import init_command

        ui = _UI(answers=[""])          # key 回车跳过
        with patch("agent.ui.terminal_picker.pick_from_list",
                   side_effect=["deepseek", "deepseek-flash"]), \
             patch("agent.llm.model_catalog.fetch_with_reason") as fetch, \
             patch.object(init_command, "_save_config", return_value=Path("m.toml")), \
             patch.object(init_command, "_test_connection") as test:
            assert self._run(ui) is True
        fetch.assert_not_called()
        test.assert_not_called()

    def test_workdir_is_not_asked(self, _isolated_home):
        """工作目录有安全默认值，不占用三问名额（问券留给没有默认值的模型）。"""
        ui = _UI(answers=["sk-test"])
        self._drive(ui, online=["m1"], model="m1")
        # 只有 key 一次 ask_user（选模型走选择器，不是文本输入）
        assert ui.answers == []
        assert "工作目录用当前目录" in ui.text

    def test_cancel_at_vendor_marks_declined(self, _isolated_home):
        from agent.commands.handlers import init_command

        with patch("agent.ui.terminal_picker.pick_from_list", return_value=None), \
             patch.object(init_command, "_save_config") as save:
            assert self._run(_UI()) is False
        save.assert_not_called()
        usage = onboarding.load_usage()
        assert usage.declined is True and usage.onboarded is False

    def test_custom_vendor_asks_base_url(self, _isolated_home):
        """自建兼容服务没有默认接入地址，这一项没默认值所以必须问。"""
        ui = _UI(answers=["http://localhost:8000/v1", "sk-test"])
        ok, saved, _, _ = self._drive(ui, vendor="custom", model="my-local",
                                      online=["my-local"])
        assert ok is True
        assert saved[0]["default_base_url"] == "http://localhost:8000/v1"

    def test_vendor_table_is_not_mutated(self, _isolated_home):
        """选完模型不能把模块级 `_VENDORS` 写脏（否则第二次启动带着上次的选择）。"""
        from agent.commands.handlers import init_command

        before = next(v for v in init_command._VENDORS if v["key"] == "dashscope")["default_model"]
        self._drive(_UI(answers=["sk-test"]), model="some-other-model",
                    online=["some-other-model"])
        after = next(v for v in init_command._VENDORS if v["key"] == "dashscope")["default_model"]
        assert before == after


# ---------------------------------------------------------------------------
# /init 保存后必须热生效（真机实测：配完立刻对话却报 Connection error）
# ---------------------------------------------------------------------------


class TestApplyToSession:
    """写完盘不刷新会话，用户会看到"✓ 连接成功"然后下一句报 Connection error。"""

    def _ctx(self, *, provider=object(), settings=None):
        return SimpleNamespace(
            ui=_UI(), settings=settings if settings is not None else Settings(),
            provider=provider, registry=object(), orchestrator=object(),
            system_prompt="sys", loop=None, model="",
        )

    def test_without_session_says_restart(self):
        from agent.commands.handlers.init_command import _apply_to_session

        msg = _apply_to_session(SimpleNamespace(settings=None, provider=None), "m")
        assert "重启" in msg

    def test_reloads_settings_and_rebuilds_provider(self):
        from agent.commands.handlers import init_command

        fresh = Settings()
        fresh.provider = "deepseek"
        fresh.model = "deepseek-flash"
        fresh.custom_models = {"deepseek-flash": {"vendor": "deepseek"}}
        ctx = self._ctx()

        with patch("agent.config.settings.load_settings", return_value=fresh), \
             patch("agent.model_manager._switch_model",
                   return_value=("NEW_PROVIDER", "NEW_LOOP", "deepseek-flash")) as switch:
            msg = init_command._apply_to_session(ctx, "deepseek-flash")

        # 会话被真正切走，而不是“已保存、请重启”
        assert "已在本会话生效" in msg
        assert (ctx.provider, ctx.loop, ctx.model) == (
            "NEW_PROVIDER", "NEW_LOOP", "deepseek-flash")
        # settings 被原地刷新（其它地方持有同一引用）
        assert ctx.settings.provider == "deepseek"
        assert "deepseek-flash" in ctx.settings.custom_models
        switch.assert_called_once()

    def test_reload_failure_does_not_lose_the_saved_config(self):
        from agent.commands.handlers.init_command import _apply_to_session

        ctx = self._ctx()
        with patch("agent.config.settings.load_settings", side_effect=RuntimeError("boom")):
            msg = _apply_to_session(ctx, "m")
        assert "配置已保存" in msg and "重新加载失败" in msg

    def test_passes_old_endpoint_snapshot_as_baseline(self):
        """回归（真机）：比较基准必须是「运行中 provider 那份」，不能是刚写进去的新值。

        若拿更新后的 settings 当基准，cfg 与 settings 恒等 → 判定无需重建 →
        复用启动时那个旧 provider（旧 base_url / 旧 key），于是用户看到
        “✓ 连接成功”之后下一句话仍报 Connection error。
        """
        from agent.commands.handlers import init_command

        old = Settings()
        old.provider = "deepseek"
        old.base_url = ""                       # 启动时没有可用端点（故障现场）
        old.api_key = "old-key"
        fresh = Settings()
        fresh.provider = "deepseek"
        fresh.base_url = "https://api.deepseek.com/v1"
        fresh.api_key = "new-key"
        fresh.custom_models = {"deepseek-flash": {"vendor": "deepseek"}}
        ctx = self._ctx(settings=old)

        with patch("agent.config.settings.load_settings", return_value=fresh), \
             patch("agent.model_manager._switch_model",
                   return_value=("P", "L", "deepseek-flash")) as switch:
            init_command._apply_to_session(ctx, "deepseek-flash")

        baseline = switch.call_args.kwargs["baseline"]
        assert baseline is not old               # 快照克隆，不是同一对象
        assert (baseline.base_url, baseline.api_key) == ("", "old-key")
        # 而 settings 已经是新值（原地更新，别的模块共享同一引用）
        assert ctx.settings.base_url == "https://api.deepseek.com/v1"

    def test_switch_failure_reports_restart(self):
        from agent.commands.handlers.init_command import _apply_to_session

        ctx = self._ctx()
        with patch("agent.config.settings.load_settings", return_value=Settings()), \
             patch("agent.model_manager._switch_model", side_effect=RuntimeError("nope")):
            msg = _apply_to_session(ctx, "m")
        assert "重启" in msg

    def test_quick_setup_applies_to_session_when_ctx_given(self, _isolated_home):
        """从 /init 进入（带 ctx）时必须热生效；从 --init 进入则提示重启。"""
        from agent.commands.handlers import init_command

        ui = _UI(answers=["sk-test"])
        ctx = SimpleNamespace(
            ui=ui, settings=Settings(), provider=object(), registry=object(),
            orchestrator=object(), system_prompt="sys", loop=None, model="",
        )
        async def _fake_test(_v, _k):
            return True, "ok"

        with patch("agent.ui.terminal_picker.pick_from_list",
                   side_effect=["dashscope", "qwen3.7-plus"]), \
             patch("agent.llm.model_catalog.fetch_with_reason",
                   return_value=(["qwen3.7-plus"], "")), \
             patch.object(init_command, "_save_config", return_value=Path("m.toml")), \
             patch.object(init_command, "_test_connection", _fake_test), \
             patch.object(init_command, "_apply_to_session",
                          return_value="已在本会话生效：x / y") as apply:
            import asyncio
            asyncio.run(onboarding.run_quick_setup(ui, ctx.settings, ctx=ctx))
        apply.assert_called_once()

    def test_quick_setup_without_ctx_says_restart(self, _isolated_home):
        from agent.commands.handlers import init_command

        ui = _UI(answers=["sk-test"])
        async def _fake_test(_v, _k):
            return True, "ok"

        with patch("agent.ui.terminal_picker.pick_from_list",
                   side_effect=["dashscope", "qwen3.7-plus"]), \
             patch("agent.llm.model_catalog.fetch_with_reason",
                   return_value=(["qwen3.7-plus"], "")), \
             patch.object(init_command, "_save_config", return_value=Path("m.toml")), \
             patch.object(init_command, "_test_connection", _fake_test):
            import asyncio
            asyncio.run(onboarding.run_quick_setup(ui, Settings()))
        assert "重启 jarvis 后生效" in ui.text


# ---------------------------------------------------------------------------
# /init 的分流
# ---------------------------------------------------------------------------


class TestInitDispatch:
    def test_plain_init_goes_quick(self):
        import asyncio

        from agent.commands.handlers import init_command

        called: list[str] = []

        async def _quick(_ui, _settings=None, **_kwargs):
            called.append("quick")
            return True

        with patch("agent.config.onboarding.run_quick_setup", _quick):
            asyncio.run(init_command.handle_init(
                SimpleNamespace(ui=_UI(), settings=Settings()), "/init"
            ))
        assert called == ["quick"]

    @pytest.mark.parametrize("cmd", ["/init full", "/init FULL", "/init --full", "/init -f"])
    def test_init_full_goes_full_flow(self, cmd):
        import asyncio

        from agent.commands.handlers import init_command

        called: list[str] = []

        async def _full(_ctx):
            called.append("full")
            return True

        with patch.object(init_command, "_handle_full_init", _full):
            asyncio.run(init_command.handle_init(
                SimpleNamespace(ui=_UI(), settings=Settings()), cmd
            ))
        assert called == ["full"]


# ---------------------------------------------------------------------------
# 用几天后主动提议
# ---------------------------------------------------------------------------


def _seed_config_records(app="Code.exe"):
    """造两条"同一程序里反复改同一项"的账本记录。"""
    now = time.time()
    return [
        {"tool": "ConfigSet", "ts": now,
         "args": {"patch": {"proactive_tts_enabled": False}, "reason": "写代码别念",
                  "context": {"app": app, "hour": 14}}},
        {"tool": "ConfigSet", "ts": now,
         "args": {"patch": {"proactive_tts_enabled": False}, "reason": "写代码别念",
                  "context": {"app": app, "hour": 15}}},
    ]


class TestSuggestAdjustment:
    def _age_usage(self, *, starts: int, days: float) -> Usage:
        usage = Usage(first_seen=time.time() - days * 86400, starts=starts,
                      onboarded=True)
        onboarding.save_usage(usage)
        return usage

    def test_too_few_starts_no_suggest(self, _isolated_home):
        self._age_usage(starts=2, days=5)
        with patch("agent.core.actions.get_journal") as journal:
            assert onboarding.maybe_suggest_adjustment(_UI()) is False
        journal.assert_not_called()

    def test_too_new_no_suggest(self, _isolated_home):
        self._age_usage(starts=9, days=0.5)
        with patch("agent.core.actions.get_journal") as journal:
            assert onboarding.maybe_suggest_adjustment(_UI()) is False
        journal.assert_not_called()

    def test_suggests_once_with_evidence(self, _isolated_home):
        self._age_usage(starts=6, days=4)
        ui = _UI()
        records = [SimpleNamespace(to_dict=lambda r=r: r) for r in _seed_config_records()]
        with patch("agent.core.actions.get_journal",
                   return_value=SimpleNamespace(recent=lambda _n: records)):
            assert onboarding.maybe_suggest_adjustment(ui) is True
        assert "还没生效" in ui.text and "/prefs" in ui.text
        assert "依据" in ui.text
        # 只提一次
        with patch("agent.core.actions.get_journal") as journal:
            assert onboarding.maybe_suggest_adjustment(ui) is False
        journal.assert_not_called()

    def test_no_candidates_marks_suggested_anyway(self, _isolated_home):
        """没候选也算"提过了"：否则每次启动都要扫一遍账本。"""
        self._age_usage(starts=6, days=4)
        with patch("agent.core.actions.get_journal",
                   return_value=SimpleNamespace(recent=lambda _n: [])):
            assert onboarding.maybe_suggest_adjustment(_UI()) is False
        assert onboarding.load_usage().suggested is True

    def test_candidates_are_not_activated(self, _isolated_home):
        """提议只提议：候选留在 suggested，绝不自己生效。"""
        from agent.config import prefs

        self._age_usage(starts=6, days=4)
        records = [SimpleNamespace(to_dict=lambda r=r: r) for r in _seed_config_records()]
        with patch("agent.core.actions.get_journal",
                   return_value=SimpleNamespace(recent=lambda _n: records)):
            onboarding.maybe_suggest_adjustment(_UI())
        assert len(prefs.load_suggested()) == 1
        assert prefs.load_active() == []

    def test_journal_failure_is_silent(self, _isolated_home):
        self._age_usage(starts=6, days=4)
        with patch("agent.core.actions.get_journal", side_effect=RuntimeError("boom")):
            assert onboarding.maybe_suggest_adjustment(_UI()) is False
