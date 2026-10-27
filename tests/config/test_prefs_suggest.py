"""偏好提炼与施放（阶段 12 P3 后半）：看出来、问过人、才生效。

三件事必须一起测，缺一件这个功能就会变成"偷偷替我改行为"：

1. **提炼只在有共同上下文时产出**——两次改动没有共同点就是"用户想要这个新值"，
   不是偏好；凭两次改动编一个条件是用户最不想要的魔法；
2. **候选不生效**——`evaluate` 只看 active，`suggested` 必须等 `accept`；
3. **施放走同一个入口**——出声闸门、播报音量都调 `prefs.effective`，
   新接一个决策点时不该各自重写一份"查表 + 比键名"。

@author aceFelix
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.config import prefs
from agent.config.prefs import When


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path):
    with patch.object(Path, "home", return_value=tmp_path):
        yield tmp_path


def _rec(key, value, *, app="", hour=-1, ts=None, reason="", tool="ConfigSet", patch_multi=None):
    """造一条账本记录（形状与 `EffectRecord.to_dict()` 一致）。"""
    return {
        "tool": tool,
        "ts": time.time() if ts is None else ts,
        "args": {
            "patch": patch_multi if patch_multi is not None else {key: value},
            "reason": reason,
            "entry": "model",
            "context": {"app": app, "hour": hour},
        },
    }


# ---------------------------------------------------------------------------
# 提炼
# ---------------------------------------------------------------------------


class TestSuggestFromRecords:
    def test_common_hour_produces_candidate(self):
        created = prefs.suggest_from_records([
            _rec("tts_volume", 20, hour=22, reason="太吵了"),
            _rec("tts_volume", 20, hour=22, reason="晚上小点声"),
        ])
        assert len(created) == 1
        cand = created[0]
        assert cand.state == prefs.STATE_SUGGESTED
        assert cand.when == When(hour_from=22, hour_to=22)
        # 依据里要有"为什么觉得这是习惯"，用户才能判断要不要接受
        assert "2 次" in cand.said and "太吵了" in cand.said
        # 文案不能出现"你在22 点"这种叠字（describe 的 app 分支已自带"在"）
        assert "在在" not in cand.said

    def test_common_app_produces_candidate(self):
        created = prefs.suggest_from_records([
            _rec("proactive_tts_enabled", False, app="Code.exe"),
            _rec("proactive_tts_enabled", False, app="Code.exe"),
        ])
        assert len(created) == 1
        assert created[0].when.app == "Code.exe"

    def test_no_common_context_produces_nothing(self):
        """9 点改一次、15 点改一次——没有共同点，就不该编出"22 点后"。"""
        assert prefs.suggest_from_records([
            _rec("tts_volume", 30, hour=9),
            _rec("tts_volume", 30, hour=15),
        ]) == []

    def test_single_change_produces_nothing(self):
        assert prefs.suggest_from_records([
            _rec("proactive_tts_enabled", False, app="Code.exe"),
        ]) == []

    def test_confirm_tier_never_suggested(self):
        """提炼也守同一条边界：只能 auto 域。"""
        assert prefs.suggest_from_records([
            _rec("daily_max", 20, hour=22),
            _rec("daily_max", 20, hour=22),
        ]) == []

    def test_multi_key_patch_skipped(self):
        """一次改多项的记录分不清是哪一项让用户不满意，不提炼。"""
        assert prefs.suggest_from_records([
            _rec(None, None, hour=22, patch_multi={"tts_volume": 20, "tts_speech_rate": 1.2}),
            _rec(None, None, hour=22, patch_multi={"tts_volume": 20, "tts_speech_rate": 1.2}),
        ]) == []

    def test_other_tools_ignored(self):
        assert prefs.suggest_from_records([
            _rec("tts_volume", 20, hour=22, tool="SendEmail"),
            _rec("tts_volume", 20, hour=22, tool="SendEmail"),
        ]) == []

    def test_records_outside_window_ignored(self):
        old = time.time() - 30 * 86400
        assert prefs.suggest_from_records([
            _rec("tts_volume", 20, hour=22, ts=old),
            _rec("tts_volume", 20, hour=22, ts=old),
        ]) == []

    def test_no_duplicate_candidates(self):
        records = [_rec("tts_volume", 20, hour=22), _rec("tts_volume", 20, hour=22)]
        assert len(prefs.suggest_from_records(records)) == 1
        assert prefs.suggest_from_records(records) == []

    def test_rejected_candidate_is_not_regenerated(self):
        """用户拒过的别再凑上来——那是最烦人的那种"贴心"。"""
        created = prefs.suggest_from_records([
            _rec("tts_volume", 20, hour=22), _rec("tts_volume", 20, hour=22),
        ])
        prefs.reject(created[0].id)
        again = prefs.suggest_from_records([
            _rec("tts_volume", 20, hour=22), _rec("tts_volume", 20, hour=22),
        ])
        assert again == []


class TestCandidateLifecycle:
    def test_candidate_does_not_take_effect(self):
        created = prefs.suggest_from_records([
            _rec("tts_volume", 20, hour=22), _rec("tts_volume", 20, hour=22),
        ])
        assert created
        assert prefs.load_active() == []
        assert prefs.evaluate({"hour": 22}, keys={"tts_volume"}, count_hit=False) is None
        assert prefs.effective("tts_volume", 50, ctx={"hour": 22}) == (50, "")

    def test_accept_activates(self):
        created = prefs.suggest_from_records([
            _rec("tts_volume", 20, hour=22), _rec("tts_volume", 20, hour=22),
        ])
        accepted = prefs.accept(created[0].id)
        assert accepted is not None and accepted.state == prefs.STATE_ACTIVE
        value, reason = prefs.effective("tts_volume", 50, ctx={"hour": 22})
        assert value == 20 and "上下文偏好" in reason
        # 非命中上下文照旧
        assert prefs.effective("tts_volume", 50, ctx={"hour": 14}) == (50, "")

    def test_accept_accepts_prefix(self):
        created = prefs.suggest_from_records([
            _rec("tts_volume", 20, hour=22), _rec("tts_volume", 20, hour=22),
        ])
        assert prefs.accept(created[0].id[:4]) is not None

    def test_accept_unknown_returns_none(self):
        assert prefs.accept("nosuch") is None

    def test_load_suggested_excludes_active(self):
        created = prefs.suggest_from_records([
            _rec("tts_volume", 20, hour=22), _rec("tts_volume", 20, hour=22),
        ])
        prefs.accept(created[0].id)
        assert prefs.load_suggested() == []
        assert len(prefs.load_active()) == 1


# ---------------------------------------------------------------------------
# 施放：一个入口，两个决策点
# ---------------------------------------------------------------------------


class TestEffective:
    def test_not_in_whitelist_returns_base(self):
        """没有决策点接的键，宁可不施放——别造"能记但不生效"的假象。"""
        prefs.remember("briefing_time", "07:15", when={"hour_from": 22, "hour_to": 7})
        assert prefs.effective("briefing_time", "08:00", ctx={"hour": 23}) == ("08:00", "")

    def test_hit_returns_value_and_reason(self):
        prefs.remember("tts_volume", 20, when={"hour_from": 22, "hour_to": 7},
                       said="晚上小点声")
        value, reason = prefs.effective("tts_volume", 50, ctx={"hour": 23})
        assert value == 20
        assert "晚上小点声" in reason

    def test_no_hit_keeps_base_and_empty_reason(self):
        prefs.remember("tts_volume", 20, when={"hour_from": 22, "hour_to": 7})
        assert prefs.effective("tts_volume", 50, ctx={"hour": 14}) == (50, "")

    def test_speech_rate_also_enforceable(self):
        prefs.remember("tts_speech_rate", 1.2, when={"hour_from": 22, "hour_to": 7})
        assert prefs.effective("tts_speech_rate", 1.0, ctx={"hour": 23})[0] == 1.2

    def test_broken_ledger_falls_back_to_base(self, _isolated_home):
        """账本坏了只当没命中：绝不能让偏好故障把播报弄成静音。"""
        ledger = _isolated_home / ".jarvis" / "prefs.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("{broken\n", encoding="utf-8")
        assert prefs.effective("proactive_tts_enabled", True, ctx={"app": "Code.exe"}) == (True, "")


class TestGateUsesEffective:
    def test_gate_silences_on_hit(self):
        from agent.core.daemon import proactive

        prefs.remember("proactive_tts_enabled", False, when={"app": "Code.exe"},
                       said="写代码时别念")
        with patch.object(prefs, "current_context",
                          return_value={"app": "Code.exe", "workdir": "", "hour": 14}):
            action = proactive.notify_gate_action("schedule", "该喝水了")
        assert action.kind == "emit" and action.speak is False
        assert "写代码时别念" in action.reason


class TestSuggestCommandAndTool:
    def _run_cmd(self, stripped: str):
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
        from types import SimpleNamespace

        handle_prefs(SimpleNamespace(ui=ui), stripped)
        return ui, "\n".join(ui.infos + ui.warns)

    def test_suggest_lists_candidates_and_says_not_active(self, _isolated_home):
        with patch("agent.commands.handlers.prefs_commands._job_records",
                   return_value=[_rec("tts_volume", 20, hour=22),
                                 _rec("tts_volume", 20, hour=22)]):
            ui, text = self._run_cmd("/prefs suggest")
        assert "提炼出 1 条候选" in text and "还没生效" in text
        assert "/prefs accept" in text

    def test_suggest_explains_rule_when_nothing_found(self, _isolated_home):
        with patch("agent.commands.handlers.prefs_commands._job_records", return_value=[]):
            ui, text = self._run_cmd("/prefs suggest")
        assert "没有提炼出新的候选" in text and "共同上下文" in text

    def test_list_shows_candidates_section(self, _isolated_home):
        prefs.suggest_from_records([_rec("tts_volume", 20, hour=22),
                                    _rec("tts_volume", 20, hour=22)])
        ui, text = self._run_cmd("/prefs")
        assert "待确认的偏好" in text and "还没生效" in text

    def test_accept_via_command(self, _isolated_home):
        created = prefs.suggest_from_records([_rec("tts_volume", 20, hour=22),
                                             _rec("tts_volume", 20, hour=22)])
        ui, text = self._run_cmd(f"/prefs accept {created[0].id[:4]}")
        assert "已接受并生效" in text
        assert len(prefs.load_active()) == 1

    def test_forget_rejects_candidate(self, _isolated_home):
        created = prefs.suggest_from_records([_rec("tts_volume", 20, hour=22),
                                             _rec("tts_volume", 20, hour=22)])
        ui, text = self._run_cmd(f"/prefs forget {created[0].id}")
        assert "已取消" in text
        assert prefs.load_suggested() == [] and prefs.load_active() == []

    def test_tool_list_shows_candidates(self, _isolated_home):
        import asyncio
        from types import SimpleNamespace

        from agent.tools.pref_tool import PreferenceTool

        prefs.suggest_from_records([_rec("tts_volume", 20, hour=22),
                                    _rec("tts_volume", 20, hour=22)])
        ctx = SimpleNamespace(settings=None, workdir=".", permission_mode="default")
        res = asyncio.run(PreferenceTool().call({"action": "list"}, ctx))
        assert "待确认候选" in res.data and "accept" in res.data

    def test_tool_accept_requires_id(self):
        from types import SimpleNamespace

        from agent.tools.pref_tool import PreferenceTool

        ctx = SimpleNamespace(settings=None, workdir=".", permission_mode="default")
        bad = PreferenceTool().validate_input({"action": "accept"}, ctx)
        assert bad.ok is False and "id" in bad.message
