"""偏好账本（阶段 12 P3）：带条件的"以后…"要真的在下次生效。

三条主线各有测试，缺一条这个功能就会变成"记了但没用"：

1. **账本与匹配**：append-only 合并、条件（应用/目录/时间/跨午夜）、"不知道不算命中"；
2. **硬边界**：`confirm` / `forbidden` 项进不来（否则偏好就是绕过确认的后门），
   以及不加条件不许记（无条件偏好 = 偷偷永久改配置，且不入账本、不可 /undo）；
3. **施放**：播报闸门命中后只降 `speak` 不降 `kind`（"别念出来" ≠ "别告诉我"）。

全部用 `patch.object(Path, "home", ...)` 把账本指到 tmp_path，不碰真实 `~/.jarvis`。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.config import prefs
from agent.config.prefs import When
from agent.config.settings import Settings
from agent.core.daemon import proactive


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path):
    """把 `~` 指到临时目录（账本路径在调用时求值，所以 patch home 就够）。"""
    with patch.object(Path, "home", return_value=tmp_path):
        yield tmp_path


def _ledger(home: Path) -> Path:
    return home / ".jarvis" / "prefs.jsonl"


# ---------------------------------------------------------------------------
# 账本：写入 / 合并 / 忘掉
# ---------------------------------------------------------------------------


class TestLedger:
    def test_remember_persists_and_loads(self, _isolated_home):
        pref = prefs.remember(
            "proactive_tts_enabled", False, when={"app": "Code.exe"}, said="写代码时别念"
        )
        assert _ledger(_isolated_home).exists()
        loaded = prefs.load_active()
        assert [p.id for p in loaded] == [pref.id]
        assert loaded[0].value is False
        assert loaded[0].said == "写代码时别念"

    def test_ledger_is_append_only_and_last_wins(self, _isolated_home):
        """同一 id 后写的整条覆盖先写的（与副作用账本同体例）。"""
        pref = prefs.remember("tts_volume", 20, when={"hour_from": 22, "hour_to": 7})
        pref.hits = 5
        prefs._append(pref, _ledger(_isolated_home))
        loaded = prefs.load_active()
        assert len(loaded) == 1
        assert loaded[0].hits == 5

    def test_forget_hides_but_keeps_history(self, _isolated_home):
        pref = prefs.remember("tts_volume", 20, when={"hour_from": 22})
        assert prefs.forget(pref.id) is not None
        assert prefs.load_active() == []
        # 全量里仍在（可追溯"曾经记过什么"），只是状态是 removed
        assert any(p.state == prefs.STATE_REMOVED for p in prefs.load_all())

    def test_forget_accepts_id_prefix(self, _isolated_home):
        pref = prefs.remember("tts_volume", 20, when={"hour_from": 22})
        assert prefs.forget(pref.id[:4]) is not None
        assert prefs.load_active() == []

    def test_forget_unknown_returns_none(self, _isolated_home):
        assert prefs.forget("nosuchid") is None

    def test_clear_all(self, _isolated_home):
        prefs.remember("tts_volume", 20, when={"hour_from": 22})
        prefs.remember("proactive_tts_enabled", False, when={"app": "Code.exe"})
        assert prefs.clear_all() == 2
        assert prefs.load_active() == []

    def test_broken_line_is_skipped_not_fatal(self, _isolated_home):
        """账本是可恢复的观测记录：坏行丢掉，不能让整个偏好系统起不来。"""
        prefs.remember("tts_volume", 20, when={"hour_from": 22})
        with _ledger(_isolated_home).open("a", encoding="utf-8") as fh:
            fh.write("{not json at all\n")
        assert len(prefs.load_active()) == 1


# ---------------------------------------------------------------------------
# 硬边界：只能 auto 域 + 必须带条件
# ---------------------------------------------------------------------------


class TestBoundaries:
    @pytest.mark.parametrize("key", ["daily_max", "percept_enabled", "tasks_enabled"])
    def test_confirm_tier_is_refused(self, _isolated_home, key):
        """带条件的偏好会固化一次本该当场问人的决定——这是本模块最重要的边界。"""
        with pytest.raises(ValueError) as exc:
            prefs.remember(key, 5, when={"app": "Code.exe"})
        assert "改前问人" in str(exc.value)

    @pytest.mark.parametrize("key", ["sandbox_enabled", "sandbox_timeout", "log_events"])
    def test_forbidden_tier_is_refused(self, _isolated_home, key):
        with pytest.raises(ValueError):
            prefs.remember(key, False, when={"app": "Code.exe"})

    def test_unknown_key_is_refused(self, _isolated_home):
        with pytest.raises(ValueError) as exc:
            prefs.remember("no_such_key", 1, when={"app": "Code"})
        assert "不认识配置项" in str(exc.value)

    def test_value_is_validated(self, _isolated_home):
        with pytest.raises(ValueError):
            prefs.remember("tts_volume", 9999, when={"app": "Code.exe"})

    def test_nothing_written_when_refused(self, _isolated_home):
        with pytest.raises(ValueError):
            prefs.remember("daily_max", 50, when={"app": "Code.exe"})
        assert not _ledger(_isolated_home).exists()


# ---------------------------------------------------------------------------
# 条件匹配
# ---------------------------------------------------------------------------


class TestWhenMatching:
    def test_app_is_case_insensitive_substring(self):
        """不分大小写、含即算：用户说的“写代码时”不会精确到哪个安装包。"""
        when = When(app="code")
        assert when.matches({"app": "Code.exe"})
        assert when.matches({"app": "Code - Insiders.exe"})
        assert not when.matches({"app": "chrome.exe"})

    def test_unknown_app_never_matches(self):
        """不知道在用什么程序时不算命中——宁可不动。"""
        assert not When(app="Code.exe").matches({"app": ""})
        assert not When(app="Code.exe").matches({})

    def test_workdir_is_prefix_matched(self):
        when = When(workdir="E:/proj")
        assert when.matches({"workdir": "E:/proj/a/b"})
        assert not when.matches({"workdir": "E:/other"})

    def test_hour_range_same_day(self):
        when = When(hour_from=9, hour_to=18)
        assert when.matches({"hour": 9}) and when.matches({"hour": 18})
        assert not when.matches({"hour": 8}) and not when.matches({"hour": 19})

    def test_hour_range_across_midnight(self):
        when = When(hour_from=22, hour_to=7)
        assert when.matches({"hour": 23}) and when.matches({"hour": 3})
        assert not when.matches({"hour": 14})

    def test_missing_hour_never_matches(self):
        assert not When(hour_from=22, hour_to=7).matches({})

    def test_empty_when_is_always(self):
        assert When().is_empty and When().matches({})
        assert When().describe() == "任何时候"

    def test_all_conditions_must_hold(self):
        when = When(app="Code.exe", hour_from=22, hour_to=7)
        assert when.matches({"app": "Code.exe", "hour": 23})
        assert not when.matches({"app": "Code.exe", "hour": 14})

    def test_describe_is_human_readable(self):
        assert When(app="Code.exe").describe() == "在 Code.exe 时"
        assert "次日" in When(hour_from=22, hour_to=7).describe()
        assert "次日" not in When(hour_from=9, hour_to=18).describe()

    # ---- 多程序："写代码时"很少只有一个编辑器 ----

    def test_multiple_apps_match_any(self):
        """逗号分隔多个：任一命中即算（否则换个编辑器就永远不生效）。"""
        when = When(app="Code.exe, Qoder.exe,idea64.exe")
        assert when.matches({"app": "Code.exe"})
        assert when.matches({"app": "Qoder.exe"})
        assert when.matches({"app": "idea64.exe"})
        assert not when.matches({"app": "chrome.exe"})

    def test_multiple_apps_describe_lists_all(self):
        assert When(app="Code.exe,Qoder.exe").describe() == "在 Code.exe / Qoder.exe 时"

    def test_app_names_ignores_blanks_and_fullwidth_comma(self):
        """中文逗号、多余空格、空段都不该变成一个"永远不命中"的幽灵条件。"""
        assert When(app=" Code.exe , ,Qoder.exe；idea64.exe ").app_names() == [
            "Code.exe", "Qoder.exe", "idea64.exe"
        ]
        # 拆不出任何名字 → 当成空条件，上层"必须带条件"才能拦住它
        assert When(app=" , ").is_empty is True
        assert not When(app=" , ").matches({"app": "Code.exe"})

    def test_single_app_still_works(self):
        """单值路径不能被多值改造弄坏（既有偏好全是单值）。

        顺便钉住“为什么需要多值”：`Code.exe` 匹配不到 `Code - Insiders.exe`
        （子串方向是「条件在上下文里」），只写一个就只能命中那一个。
        """
        assert When(app="Code.exe").matches({"app": "Code.exe"})
        assert When(app="Code.exe").app_names() == ["Code.exe"]
        assert not When(app="Code.exe").matches({"app": "Code - Insiders.exe"})
        # 反过来写宽一点就能盖住（子串匹配的意义）
        assert When(app="code").matches({"app": "Code - Insiders.exe"})


class TestEvaluate:
    def test_no_pref_returns_none(self):
        assert prefs.evaluate({"app": "Code.exe"}) is None

    def test_more_specific_wins(self, _isolated_home):
        """同时命中"22 点后"与"在 Code.exe 时"，取条件更多的那条。"""
        prefs.remember("tts_volume", 10, when={"hour_from": 22, "hour_to": 7})
        specific = prefs.remember(
            "proactive_tts_enabled", False,
            when={"app": "Code.exe", "hour_from": 22, "hour_to": 7},
        )
        hit = prefs.evaluate({"app": "Code.exe", "hour": 23}, count_hit=False)
        assert hit is not None and hit.id == specific.id

    def test_only_enforceable_keys_by_default(self, _isolated_home):
        """没有决策点能施放的键，默认求值不该当答案（可记可查，但不自动生效）。"""
        prefs.remember("briefing_time", "07:15", when={"hour_from": 22, "hour_to": 7})
        assert prefs.evaluate({"hour": 23}, count_hit=False) is None
        hit = prefs.evaluate({"hour": 23}, keys={"briefing_time"}, count_hit=False)
        assert hit is not None

    def test_hit_counter_persists(self, _isolated_home):
        prefs.remember("proactive_tts_enabled", False, when={"app": "Code.exe"})
        prefs.evaluate({"app": "Code.exe"})
        prefs.evaluate({"app": "Code.exe"})
        assert prefs.load_active()[0].hits == 2


# ---------------------------------------------------------------------------
# 施放：播报闸门
# ---------------------------------------------------------------------------


class TestGateEnforcement:
    def _ctx(self, app: str = "Code.exe", hour: int = 14) -> dict:
        return {"app": app, "workdir": "", "hour": hour}

    def test_hit_downgrades_speak_but_not_kind(self, _isolated_home):
        """"别念出来" ≠ "别告诉我"：投递照旧，只是不朗读。"""
        prefs.remember(
            "proactive_tts_enabled", False,
            when={"app": "Code.exe"}, said="写代码时别念出来",
        )
        with patch.object(prefs, "current_context", return_value=self._ctx()):
            action = proactive.notify_gate_action("schedule", "该喝水了")
        assert action.kind == "emit" and action.speak is False
        assert "偏好" in action.reason and "写代码时别念出来" in action.reason

    def test_no_hit_keeps_speaking(self, _isolated_home):
        prefs.remember("proactive_tts_enabled", False, when={"app": "Code.exe"})
        with patch.object(prefs, "current_context", return_value=self._ctx("chrome.exe")):
            action = proactive.notify_gate_action("schedule", "该喝水了")
        assert action.speak is True

    def test_pref_value_true_never_silences(self, _isolated_home):
        prefs.remember("proactive_tts_enabled", True, when={"app": "Code.exe"})
        with patch.object(prefs, "current_context", return_value=self._ctx()):
            action = proactive.notify_gate_action("schedule", "该喝水了")
        assert action.speak is True

    def test_broken_ledger_does_not_break_notifications(self, _isolated_home):
        """偏好求值出错只当没命中，绝不能让播报通道跟着挂。"""
        _ledger(_isolated_home).parent.mkdir(parents=True, exist_ok=True)
        _ledger(_isolated_home).write_text("{broken\n", encoding="utf-8")
        with patch.object(prefs, "current_context", return_value=self._ctx()):
            action = proactive.notify_gate_action("schedule", "该喝水了")
        assert action.kind == "emit" and action.speak is True

    def test_away_still_wins(self, _isolated_home):
        """away 把出声项判成暂缓入库，偏好闸不该把它改回投递。"""
        prefs.remember("proactive_tts_enabled", False, when={"app": "Code.exe"})
        with patch.object(prefs, "current_context", return_value=self._ctx()), \
             patch("agent.core.policy.get_policy_state") as state:
            state.return_value.is_away.return_value = (True, "测试")
            action = proactive.notify_gate_action("schedule", "该喝水了")
        assert action.kind == "queue"

    def test_already_silent_action_untouched(self, _isolated_home):
        """预算闸已经判成不朗读时，不再重复查偏好（也不该覆盖它的原因）。"""
        prefs.remember("proactive_tts_enabled", False, when={"app": "Code.exe"})
        quiet = SimpleNamespace(kind="emit", text="x", reason="静默时段", speak=False,
                                should_emit=True)
        out = proactive._apply_preference_silence(quiet, "schedule", SimpleNamespace(info=lambda *a: None))
        assert out is quiet


# ---------------------------------------------------------------------------
# 工具与命令
# ---------------------------------------------------------------------------


class TestPreferenceTool:
    def _run(self, args: dict):
        import asyncio

        from agent.tools.pref_tool import PreferenceTool

        ctx = SimpleNamespace(settings=Settings(), workdir=".", permission_mode="default")
        return asyncio.run(PreferenceTool().call(args, ctx))

    def test_remember_app_pref(self, _isolated_home):
        res = self._run({
            "action": "remember", "key": "proactive_tts_enabled", "value": "false",
            "when_app": "Code.exe", "said": "以后我写代码时别念出来",
        })
        assert res.is_error is False and "已记住" in res.data
        assert prefs.load_active()[0].when.app == "Code.exe"

    def test_remember_multiple_apps(self, _isolated_home):
        """"写代码"很少只有一个编辑器：一行里写多个，任一命中即算。"""
        res = self._run({
            "action": "remember", "key": "proactive_tts_enabled", "value": "false",
            "when_app": "Code.exe,Qoder.exe", "said": "写代码时别念",
        })
        assert res.is_error is False
        assert "Code.exe / Qoder.exe" in res.data
        assert prefs.effective("proactive_tts_enabled", True, ctx={"app": "Qoder.exe"},
                               count_hit=False)[0] is False

    def test_remember_receipt_shows_current_foreground(self, _isolated_home):
        """回执必须把当前前台程序摊出来，否则条件写错了用户永远查不出原因。"""
        from unittest.mock import patch

        with patch.object(prefs, "current_context",
                          return_value={"app": "Qoder.exe", "workdir": "E:/p", "hour": 15}):
            res = self._run({
                "action": "remember", "key": "proactive_tts_enabled", "value": "false",
                "when_app": "Code.exe", "said": "以后我写代码时别念出来",
            })
        assert "当前前台是 Qoder.exe" in res.data
        assert "条件写的是「在 Code.exe 时」" in res.data

    def test_remember_receipt_says_hit_when_matching(self, _isolated_home):
        from unittest.mock import patch

        with patch.object(prefs, "current_context",
                          return_value={"app": "Code.exe", "workdir": "E:/p", "hour": 15}):
            res = self._run({
                "action": "remember", "key": "proactive_tts_enabled", "value": "false",
                "when_app": "Code.exe", "said": "写代码时别念",
            })
        assert "已立即生效" in res.data

    def test_remember_requires_condition(self, _isolated_home):
        """无条件偏好就是偷偷永久改配置（且不入账本、不可 /undo），必须拒。"""
        from agent.tools.pref_tool import PreferenceTool

        ctx = SimpleNamespace(settings=Settings(), workdir=".", permission_mode="default")
        bad = PreferenceTool().validate_input(
            {"action": "remember", "key": "proactive_tts_enabled", "value": "false"}, ctx,
        )
        assert bad.ok is False and "条件" in bad.message

    def test_remember_refuses_confirm_key(self, _isolated_home):
        res = self._run({"action": "remember", "key": "daily_max", "value": "50",
                         "when_hours": "22-7"})
        assert res.is_error is True and "改前问人" in res.data

    def test_hours_parsing_and_cross_midnight(self, _isolated_home):
        self._run({"action": "remember", "key": "proactive_tts_enabled", "value": "false",
                   "when_hours": "22-7"})
        when = prefs.load_active()[0].when
        assert (when.hour_from, when.hour_to) == (22, 7)
        assert when.matches({"hour": 3})

    def test_bad_hours_reported(self, _isolated_home):
        res = self._run({"action": "remember", "key": "proactive_tts_enabled",
                         "value": "false", "when_hours": "晚上"})
        assert res.is_error is True and "时间段" in res.data

    def test_list_and_forget(self, _isolated_home):
        created = self._run({"action": "remember", "key": "tts_volume", "value": "20",
                             "when_hours": "22-7", "said": "晚上小点声"})
        pref_id = prefs.load_active()[0].id
        listed = self._run({"action": "list"})
        assert pref_id in listed.data and "晚上小点声" in listed.data
        gone = self._run({"action": "forget", "id": pref_id[:4]})
        assert "已忘掉" in gone.data
        assert prefs.load_active() == []
        assert created.is_error is False

    def test_list_empty_explains_the_difference(self, _isolated_home):
        res = self._run({"action": "list"})
        assert "还没有记过" in res.data and "ConfigSet" in res.data


class TestPrefsCommand:
    def _run(self, stripped: str):
        from agent.commands.handlers.prefs_commands import handle_prefs

        class _UI:
            def __init__(self):
                self.infos: list[str] = []
                self.warns: list[str] = []

            def info(self, text):
                self.infos.append(text)

            def warn(self, text):
                self.warns.append(text)

        ui = _UI()
        assert handle_prefs(SimpleNamespace(ui=ui), stripped) is True
        return ui, "\n".join(ui.infos + ui.warns)

    def test_empty_state_explains(self, _isolated_home):
        ui, text = self._run("/prefs")
        assert "还没有记过" in text

    def test_list_shows_condition_and_said(self, _isolated_home):
        prefs.remember("proactive_tts_enabled", False,
                       when={"app": "Code.exe"}, said="写代码时别念")
        ui, text = self._run("/prefs")
        assert "在 Code.exe 时" in text and "写代码时别念" in text and "命中" in text

    def test_forget_unknown_warns(self, _isolated_home):
        ui, text = self._run("/prefs forget nope")
        assert ui.warns and "没找到" in text

    def test_clear(self, _isolated_home):
        prefs.remember("tts_volume", 20, when={"hour_from": 22})
        ui, text = self._run("/prefs clear")
        assert "已忘掉全部 1 条" in text
        assert prefs.load_active() == []

    def test_unknown_subcommand_shows_usage(self, _isolated_home):
        ui, text = self._run("/prefs bogus")
        assert ui.warns and "/prefs forget <id>" in text
