"""自我修复层（S4）的测试。全部 dry-run：不碰网络、不写用户目录。

这个文件守的四条线，都是"权限大"之后才会出现的新风险：

1. **只认必需项的真缺件**：可选缺失不催装，看不清（probe_failed）不动手；
2. **预算门的顺序**：总闸 → 单动作关 → 是否代执行 → 硬上限 → 无人值守 → 询问线；
3. **不假装可回滚**：修复记录必须 `reversible=False` 且写清手工退法（检查点盖不到
   工作区外的写入，这条是 S3 实做时纠正的计划错误）；
4. **一次失败只试一条路**：这是本次 Chrome/Firefox 那 8 条 Bash 的直接反面教材。

另有一张契约网：能力表里登记的 `repair_actions` 必须都在白名单里，写错直接红。

@author aceFelix
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.capabilities import probe as probe_mod
from agent.capabilities import repair as repair_mod
from agent.capabilities.registry import ReqState, RequirementStatus
from agent.core.actions import get_journal
from agent.core.actions.journal import configure_journal

_BROWSER_CAP = "browser.selfcheck"


class _Completed:
    def __init__(self, rc: int = 0, out: str = "", err: str = "") -> None:
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def _req(state: ReqState, *, kind: str, label: str, evidence: str = "实测证据",
         optional: bool = False, fix_hint: str = "") -> RequirementStatus:
    return RequirementStatus(
        kind=kind, label=label, state=state, evidence=evidence,
        fix_hint=fix_hint, optional=optional,
    )


def _settings(**over) -> SimpleNamespace:
    base = {
        "repair_enabled": True,
        "repair_ask_threshold_mb": 200.0,
        "repair_disabled_actions": [],
        "repair_announce_voice": True,
        "repair_background_over_s": 30.0,
        "away_idle_minutes": 0,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _fake_requirements(reqs: list[RequirementStatus]):
    def _probe(meta):
        if meta.name != _BROWSER_CAP:
            return [_req(ReqState.OK, kind="python", label="x", evidence="有")]
        return reqs

    return _probe


def _browser_gap() -> list[RequirementStatus]:
    """本次事故的真实形态：包装得好好的，缺的是内核。"""
    return [
        _req(ReqState.OK, kind="python", label="playwright>=1.40.0", evidence="1.60.0"),
        _req(
            ReqState.MISSING, kind="asset", label="playwright 浏览器内核",
            evidence="需要 chromium-1223，目录里一个都没有",
            fix_hint="python -m playwright install chromium --only-shell",
        ),
    ]


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """账本与审计都改到临时目录：测试不许碰用户真实的 ~/.jarvis。"""
    configure_journal(path=tmp_path / "actions.jsonl", clear_window=True)
    probe_mod.reset_cache()

    class _NoAuditor:
        def __init__(self, *a, **k) -> None:
            pass

        def log_execution(self, **kw) -> None:
            AUDIT_CALLS.append(kw)

    monkeypatch.setattr("agent.core.sandbox.audit.SandboxAuditor", _NoAuditor)
    yield
    AUDIT_CALLS.clear()
    probe_mod.reset_cache()
    configure_journal(clear_window=True)


AUDIT_CALLS: list[dict] = []


# ---------------------------------------------------------------------------
# A. 计划生成：什么缺件该配什么
# ---------------------------------------------------------------------------


class TestPlanGeneration:
    def test_kernel_gap_yields_fetch_browser_plan(self, monkeypatch):
        monkeypatch.setattr(probe_mod, "_probe_all_requirements", _fake_requirements(_browser_gap()))
        plans = repair_mod.plans_for_capability(_BROWSER_CAP)

        assert [p.action for p in plans] == ["fetch-browser"], "缺内核只该补内核，不该顺手跑 pip"
        plan = plans[0]
        assert plan.argv[0] == sys.executable, "只认当前解释器"
        assert plan.argv[-2:] == ("chromium", "--only-shell")
        assert plan.est_mb == pytest.approx(112.4)
        assert "--only-shell" in plan.display

    def test_all_ok_yields_no_plan(self, monkeypatch):
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements",
            _fake_requirements([_req(ReqState.OK, kind="python", label="playwright", evidence="有")]),
        )
        assert repair_mod.plans_for_capability(_BROWSER_CAP) == []

    def test_optional_gap_yields_no_plan(self, monkeypatch):
        """可选项缺失不催装：否则每次自查都要拉几百 MB 的手势识别库。"""
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements",
            _fake_requirements([_req(ReqState.MISSING, kind="python", label="mediapipe", optional=True)]),
        )
        assert repair_mod.plans_for_capability(_BROWSER_CAP) == []

    def test_probe_failed_yields_no_plan(self, monkeypatch):
        """不知道缺什么就动手，那不叫修复，叫猜。"""
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements",
            _fake_requirements([_req(ReqState.PROBE_FAILED, kind="asset", label="playwright 浏览器内核",
                                     evidence="探针超时", fix_hint="pip install playwright")]),
        )
        assert repair_mod.plans_for_capability(_BROWSER_CAP) == []

    def test_extras_plan_avoids_package_name_on_editable(self, monkeypatch):
        """源码树环境里绝不能出现 `jarvis-agent[extras]`：那会用 PyPI 版覆盖未发布的修改。"""
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements",
            _fake_requirements([
                _req(ReqState.MISSING, kind="python", label="playwright", evidence="import 不到"),
            ]),
        )
        monkeypatch.setattr("agent.update.detect_installer", lambda: "editable")
        monkeypatch.setattr(repair_mod._util, "find_spec", lambda name: object() if name == "pip" else None)
        monkeypatch.setattr(
            "agent.capabilities.registry.extras_for_group", lambda g: ["playwright>=1.40.0"],
        )

        plans = repair_mod.plans_for_capability(_BROWSER_CAP)

        pip_plans = [p for p in plans if p.action == "pip-extras"]
        assert pip_plans, "editable 渠道应改为按 extras 清单直装"
        assert "jarvis-agent" not in pip_plans[0].display
        assert "playwright>=1.40.0" in pip_plans[0].display

    def test_extras_plan_uses_pinned_name_on_pip_channel(self, monkeypatch):
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements",
            _fake_requirements([
                _req(ReqState.MISSING, kind="python", label="playwright", evidence="import 不到"),
            ]),
        )
        monkeypatch.setattr("agent.update.detect_installer", lambda: "pip")
        monkeypatch.setattr(repair_mod._util, "find_spec", lambda _name: object())

        plans = repair_mod.plans_for_capability(_BROWSER_CAP)

        pip_plans = [p for p in plans if p.action == "pip-extras"]
        assert pip_plans and "jarvis-agent[browser]" in pip_plans[0].display

    def test_no_pip_in_interpreter_yields_no_plan(self, monkeypatch):
        """当前解释器里连 pip 都没有：不猜路子，退回人工。"""
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements",
            _fake_requirements([_req(ReqState.MISSING, kind="python", label="playwright", evidence="没有")]),
        )
        monkeypatch.setattr(
            repair_mod._util, "find_spec",
            lambda name: object() if name == "playwright" else None,
        )

        assert [p.action for p in repair_mod.plans_for_capability(_BROWSER_CAP)] == []

    def test_unwhitelisted_action_id_is_ignored(self, monkeypatch):
        """能力表写了个白名单外的动作 ID：直接跳过，不因为拼错就多跑一条命令。"""
        from agent.capabilities.registry import CAPABILITY_REGISTRY

        meta = CAPABILITY_REGISTRY[_BROWSER_CAP]
        object.__setattr__(meta, "repair_actions", ("install-anything",))
        try:
            monkeypatch.setattr(
                probe_mod, "_probe_all_requirements", _fake_requirements(_browser_gap()),
            )
            assert repair_mod.plans_for_capability(_BROWSER_CAP) == []
        finally:
            object.__setattr__(meta, "repair_actions", ("fetch-browser", "pip-extras"))


# ---------------------------------------------------------------------------
# B. 预算门
# ---------------------------------------------------------------------------


def _plan(action: str = "fetch-browser", *, est_mb: float = 112.4, est_s: float = 120.0) -> repair_mod.RepairPlan:
    spec = repair_mod.REPAIR_ACTIONS[action]
    return repair_mod.RepairPlan(
        action=action, capability=_BROWSER_CAP,
        argv=(sys.executable, "-m", "playwright", "install", "chromium", "--only-shell"),
        display="python -m playwright install chromium --only-shell",
        est_mb=est_mb, est_seconds=est_s, reason="内核缺失", undo_hint=spec.undo_hint,
    )


class TestBudgetGate:
    def test_within_budget_is_auto_and_background(self):
        d = repair_mod.decide(_plan(), _settings())

        assert d.state == "auto"
        assert d.background, "预计 120s > 30s 必须转后台而不是前台阻塞"

    def test_total_switch_off_refuses(self):
        d = repair_mod.decide(_plan(), _settings(repair_enabled=False))

        assert d.state == "refuse"
        assert "总闸" in d.reason

    def test_single_action_switch_off_refuses_only_that_action(self):
        d = repair_mod.decide(_plan(), _settings(repair_disabled_actions=["fetch-browser"]))

        assert d.state == "refuse"
        assert "单独关掉" in d.reason

    def test_non_auto_action_is_manual_not_refused(self):
        """manual 与 refuse 是两件事：前者照旧给出可复制命令，后者连建议都不该发。"""
        d = repair_mod.decide(_plan("config-fix", est_mb=0.0), _settings())

        assert d.state == "manual"

    def test_over_hard_cap_refuses_even_with_confirmation(self):
        """硬上限之上问也没用：那是「要不要花这个代价」的决定，不是「现在批准吗」。"""
        d = repair_mod.decide(_plan(est_mb=450.0), _settings(), confirmed=True)

        assert d.state == "refuse"
        assert "硬上限" in d.reason

    def test_over_ask_line_asks_unless_confirmed(self):
        plan = _plan(est_mb=250.0)

        assert repair_mod.decide(plan, _settings()).state == "ask"
        assert repair_mod.decide(plan, _settings(), confirmed=True).state == "auto"

    def test_away_escalates_to_pending(self):
        d = repair_mod.decide(_plan(), _settings(), away=True, away_note="手动 /away")

        assert d.state == "needs_approval"
        assert "/away" in d.reason or "手动" in d.reason

    def test_away_beats_ask_line(self):
        """顺序：无人值守优先于询问——没人能回答的问题不该变成一个等待输入的框。"""
        d = repair_mod.decide(_plan(est_mb=250.0), _settings(), away=True)

        assert d.state == "needs_approval"


# ---------------------------------------------------------------------------
# C. 执行、留痕、复测
# ---------------------------------------------------------------------------


class TestExecuteAndRecheck:
    def _stub(self, monkeypatch, *, rc=0, out="ok", recheck_state="ok", recheck_usable=True,
              before_dirs: tuple[str, ...] = (), added_dirs: tuple[str, ...] = ()):
        """把命令、复测、目录扫描三件副作用全换成可控的桩。

        `before_dirs` / `added_dirs` 分开给：本层靠“前后目录差集”算出本次新增了什么，
        两者相等就等于没新增——那是一条不同的分支，不能混用同一个桩。
        """
        monkeypatch.setattr(
            repair_mod.subprocess, "run",
            lambda *a, **k: _Completed(rc, out, ""),
        )
        monkeypatch.setattr(
            probe_mod, "deep_check",
            lambda name, **_k: SimpleNamespace(
                state=recheck_state, usable=recheck_usable,
                summary_line=lambda: f"复测={recheck_state}",
            ),
        )
        seq = [set(before_dirs), set(before_dirs) | set(added_dirs)]
        monkeypatch.setattr(repair_mod, "_revision_dirs", lambda _root: seq.pop(0))

    def test_success_records_unreversible_with_exact_manual_hint(self, monkeypatch):
        self._stub(monkeypatch, added_dirs=("chromium_headless_shell-1223",))
        plan = _plan()

        outcome = repair_mod.execute_plan(plan, repair_mod._JournalMeta(workdir=".", session_name="s"))

        assert outcome.state == "done"
        assert outcome.recheck_usable is True
        assert outcome.journal_id

        rec = get_journal().get(outcome.journal_id)
        assert rec is not None
        assert rec.tool == "Repair:fetch-browser"
        # 检查点盖不到工作区外的写入，所以绝不声明 /undo 能撤
        assert rec.reversible is False
        assert rec.undo_spec["kind"] == "none"
        assert "chromium_headless_shell-1223" in rec.undo_spec["reason"]

    def test_no_new_dir_keeps_the_generic_manual_hint(self, monkeypatch):
        """前=后（没新增）时不编造路径：宁可给一句笼统退法，也不拿不存在的目录当真。"""
        self._stub(monkeypatch, before_dirs=("chromium_headless_shell-1223",))
        plan = _plan()

        outcome = repair_mod.execute_plan(plan, repair_mod._JournalMeta())

        assert outcome.added_paths == ()
        assert "chromium_headless_shell-1223" not in plan.undo_hint

    def test_audit_receives_the_command(self, monkeypatch):
        self._stub(monkeypatch)
        repair_mod.execute_plan(_plan(), repair_mod._JournalMeta())

        assert AUDIT_CALLS and "playwright install" in AUDIT_CALLS[0]["command"]

    def test_exit_zero_but_still_unusable_counts_as_failed(self, monkeypatch):
        """只认退出码就等于把「命令跑完了」当成「能力恢复了」。"""
        self._stub(monkeypatch, rc=0, recheck_state="missing", recheck_usable=False)

        outcome = repair_mod.execute_plan(_plan(), repair_mod._JournalMeta())

        assert outcome.state == "failed"
        assert outcome.exit_code == 0, "退出码如实保留，失败判定来自复测而不是命令"
        rec = get_journal().get(outcome.journal_id)
        assert rec.state == "failed"

    def test_timeout_stops_and_does_not_retry(self, monkeypatch):
        import subprocess as sp

        def _boom(*a, **k):
            raise sp.TimeoutExpired(cmd="x", timeout=1)

        monkeypatch.setattr(repair_mod.subprocess, "run", _boom)
        monkeypatch.setattr(
            probe_mod, "deep_check",
            lambda name, **_k: SimpleNamespace(state="missing", usable=False, summary_line=lambda: "仍缺"),
        )

        outcome = repair_mod.execute_plan(_plan(), repair_mod._JournalMeta())

        assert outcome.state == "failed"
        assert "已放弃本次修复（不重试）" in outcome.detail

    def test_journal_write_failure_does_not_flip_done_to_failed(self, monkeypatch):
        """记账坏了是记账的问题：已完成的修复不该被降级成失败状态。"""
        self._stub(monkeypatch)
        monkeypatch.setattr(
            repair_mod, "get_journal",
            lambda: (_ for _ in ()).throw(RuntimeError("磁盘只读")),
        )

        outcome = repair_mod.execute_plan(_plan(), repair_mod._JournalMeta())

        assert outcome.state == "done"
        assert outcome.journal_id == ""

    def test_only_first_plan_is_tried(self, monkeypatch):
        """一次失败只试一条路（本次 8 条 Bash 换一套编造机制的直接反面）。"""
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements",
            _fake_requirements([
                _req(ReqState.MISSING, kind="python", label="playwright", evidence="import 不到"),
                _req(ReqState.MISSING, kind="asset", label="playwright 浏览器内核", evidence="目录没有"),
            ]),
        )
        monkeypatch.setattr("agent.update.detect_installer", lambda: "pip")
        monkeypatch.setattr(repair_mod._util, "find_spec", lambda _n: object())
        plans = repair_mod.plans_for_capability(_BROWSER_CAP)
        assert len(plans) >= 2, "前置准备：确实有两个候选计划"

        tried: list[str] = []
        monkeypatch.setattr(
            repair_mod, "execute_plan",
            lambda p, m: (tried.append(p.action)
                          or repair_mod.RepairOutcome(plan=p, state="failed", detail="不行", recheck="missing")),
        )

        outcomes = repair_mod.repair_capability(_BROWSER_CAP, _settings())

        assert tried == [plans[0].action]
        assert len(outcomes) == 1


# ---------------------------------------------------------------------------
# D. 回执与播报
# ---------------------------------------------------------------------------


class TestAnnounce:
    def test_done_announce_carries_cost_and_manual_rollback(self):
        plan = _plan()
        outcome = repair_mod.RepairOutcome(
            plan=plan, state="done", recheck="ok", recheck_usable=True,
            added_paths=("chromium_headless_shell-1223",),
        )

        text, voice = repair_mod.announce(outcome)

        assert "已完成" in text and "复测" in text
        assert "怎么退" in text and "chromium_headless_shell-1223" in text
        assert voice, "强制语音播报：动过手就必须出声"

    def test_failed_announce_says_it_stopped(self):
        outcome = repair_mod.RepairOutcome(
            plan=_plan(), state="failed", detail="exit=1", recheck="missing",
        )
        text, voice = repair_mod.announce(outcome)

        assert "已停手" in text
        assert "没能恢复" in voice

    def test_refused_has_no_voice(self):
        """被闸门拦下的动作不该出声：没有代价发生，不值得占用注意力。"""
        outcome = repair_mod.RepairOutcome(plan=_plan(), state="refused", detail="总闸已关")
        text, voice = repair_mod.announce(outcome)

        assert voice == ""
        assert "未执行修复" in text

    def test_history_filters_repair_rows_only(self):
        plan = _plan()
        repair_mod._record(
            plan,
            repair_mod.RepairOutcome(plan=plan, state="done", recheck="ok", recheck_usable=True),
            repair_mod._JournalMeta(),
        )
        rows = repair_mod.history()

        assert len(rows) == 1
        assert rows[0]["action"] == "fetch-browser"
        assert rows[0]["undo_hint"]


# ---------------------------------------------------------------------------
# E. 契约：白名单与能力表不能各说各话
# ---------------------------------------------------------------------------


class TestRepairContract:
    def test_every_declared_repair_action_is_whitelisted(self) -> None:
        from agent.capabilities.registry import CAPABILITY_REGISTRY

        bad = [
            f"{meta.name} -> {a}"
            for meta in CAPABILITY_REGISTRY.values()
            for a in meta.repair_actions
            if a not in repair_mod.REPAIR_ACTIONS
        ]
        assert bad == [], "能力表里引用了白名单外的动作 ID：会被静默跳过，修不了也看不见"

    def test_whitelist_matches_the_plan_document(self) -> None:
        """四个 ID 是对外承诺过的清单，改名等于毁约（计划第 4.1 表）。"""
        assert set(repair_mod.REPAIR_ACTIONS) == {
            "fetch-browser", "fetch-model", "pip-extras", "config-fix",
        }

    def test_advice_only_actions_are_never_auto(self) -> None:
        """fetch-model 没有 URL 清单、config-fix 必须走 ConfigSet：两者都不得代执行。"""
        assert repair_mod.REPAIR_ACTIONS["fetch-model"].auto_executable is False
        assert repair_mod.REPAIR_ACTIONS["config-fix"].auto_executable is False

    def test_hard_caps_are_finite_and_positive_where_needed(self) -> None:
        for name, spec in repair_mod.REPAIR_ACTIONS.items():
            if spec.auto_executable:
                assert spec.hard_cap_mb > 0, f"{name} 可自动执行却没有硬上限"


# ---------------------------------------------------------------------------
# F. 命令接线
# ---------------------------------------------------------------------------


class _UI:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warns: list[str] = []
        self.errors: list[str] = []

    def info(self, text: str) -> None:
        self.infos.append(text)

    def warn(self, text: str) -> None:
        self.warns.append(text)

    def error(self, text: str) -> None:
        self.errors.append(text)


def _cmd_ctx(settings=None):
    return SimpleNamespace(ui=_UI(), settings=settings or _settings(), workdir=".", extra={})


class TestRepairCommands:
    async def test_repair_without_target_lists_candidates_and_runs_nothing(self, monkeypatch):
        from agent.commands.handlers import repair_commands as rc

        executed: list[str] = []
        monkeypatch.setattr(
            repair_mod, "repair_capability",
            lambda *a, **k: executed.append("ran") or [],
        )

        ctx = _cmd_ctx()
        text = await rc.run_repair(ctx, ["--repair"])

        assert executed == []
        assert "需要指名要修哪个能力" in text
        assert _BROWSER_CAP in text

    async def test_repair_unknown_capability_is_rejected(self):
        from agent.commands.handlers import repair_commands as rc

        ctx = _cmd_ctx()
        text = await rc.run_repair(ctx, ["--repair", "no.such.cap"])

        assert "没有登记过能力" in text

    async def test_repair_announces_and_speaks_when_enabled(self, monkeypatch):
        from agent.commands.handlers import repair_commands as rc

        plan = _plan()
        monkeypatch.setattr(
            repair_mod, "repair_capability",
            lambda *a, **k: [repair_mod.RepairOutcome(
                plan=plan, state="done", recheck="ok", recheck_usable=True,
            )],
        )
        spoken: list[str] = []
        monkeypatch.setattr(
            "agent.commands.handlers.voice_commands._say",
            lambda ui, settings, text: spoken.append(text),
        )

        ctx = _cmd_ctx()
        text = await rc.run_repair(ctx, ["--repair", _BROWSER_CAP])

        assert "已完成" in text
        assert spoken and "可以用了" in spoken[0], "默认开之下必须主动播报"

    async def test_voice_off_skips_speech_but_keeps_receipt(self, monkeypatch):
        from agent.commands.handlers import repair_commands as rc

        plan = _plan()
        monkeypatch.setattr(
            repair_mod, "repair_capability",
            lambda *a, **k: [repair_mod.RepairOutcome(
                plan=plan, state="done", recheck="ok", recheck_usable=True)],
        )
        spoken: list[str] = []
        monkeypatch.setattr(
            "agent.commands.handlers.voice_commands._say",
            lambda ui, settings, text: spoken.append(text),
        )

        ctx = _cmd_ctx(settings=_settings(repair_announce_voice=False))
        text = await rc.run_repair(ctx, ["--repair", _BROWSER_CAP])

        assert spoken == [] and "已完成" in text

    async def test_speech_failure_does_not_fake_the_result(self, monkeypatch):
        from agent.commands.handlers import repair_commands as rc

        plan = _plan()
        monkeypatch.setattr(
            repair_mod, "repair_capability",
            lambda *a, **k: [repair_mod.RepairOutcome(
                plan=plan, state="done", recheck="ok", recheck_usable=True)],
        )
        monkeypatch.setattr(
            "agent.commands.handlers.voice_commands._say",
            lambda ui, settings, text: (_ for _ in ()).throw(RuntimeError("没有音频设备")),
        )

        ctx = _cmd_ctx()
        text = await rc.run_repair(ctx, ["--repair", _BROWSER_CAP])

        assert "已完成" in text, "播报失败不能把成功的修复报成失败"
        assert "语音播报没成功" in text, "也不能静默吞掉：说了没出声是用户该知道的"

    async def test_repair_off_persists_and_flips_runtime_value(self, monkeypatch):
        from agent.commands.handlers import repair_commands as rc

        saved: list[tuple[str, object]] = []
        monkeypatch.setattr(
            "agent.config.config_specs.save_value",
            lambda spec, value: saved.append((spec.key, value)) or True,
        )

        settings = _settings()
        ctx = _cmd_ctx(settings=settings)
        assert await rc.handle_repair(ctx, "/repair off") is True

        assert saved == [("repair_enabled", False)]
        assert settings.repair_enabled is False, "只落盘不改运行时，用户会以为改了没反应"

    async def test_repair_off_survives_write_failure(self, monkeypatch):
        from agent.commands.handlers import repair_commands as rc

        monkeypatch.setattr("agent.config.config_specs.save_value", lambda spec, value: False)
        settings = _settings()
        ctx = _cmd_ctx(settings=settings)

        await rc.handle_repair(ctx, "/repair off")

        assert ctx.ui.errors, "落盘失败必须报错"
        assert settings.repair_enabled is True, "写不进去就不该改运行时值"

    async def test_disable_action_rejects_non_whitelisted_id(self):
        from agent.commands.handlers import repair_commands as rc

        ctx = _cmd_ctx()
        await rc.handle_repair(ctx, "/repair disable install-anything")

        assert ctx.ui.errors and "不是白名单动作" in ctx.ui.errors[0]

    async def test_status_line_reports_unknown_disabled_actions(self):
        """写错的动作名不生效，就必须把它说出来——否则用户以为关上了。"""
        from agent.commands.handlers import repair_commands as rc

        ctx = _cmd_ctx(settings=_settings(repair_disabled_actions=["pip-extras", "typo-action"]))
        await rc.handle_repair(ctx, "/repair")

        assert any("typo-action" in w for w in ctx.ui.warns)
        assert any("自我修复闸门" in i for i in ctx.ui.infos)

    async def test_history_flag_reads_journal(self, monkeypatch):
        # /health caps 的分派体留在 system_commands（它才是体检入口），
        # 只有 --repair 的执行体被拆去了 repair_commands
        from agent.commands.handlers import system_commands as sys_cmds

        plan = _plan()
        repair_mod._record(
            plan,
            repair_mod.RepairOutcome(plan=plan, state="done", recheck="ok", recheck_usable=True),
            repair_mod._JournalMeta(),
        )

        ctx = _cmd_ctx()
        assert await sys_cmds.handle_health(ctx, "/health caps --history") is True

        assert "最近修复记录" in ctx.ui.infos[-1]
        assert "fetch-browser" in ctx.ui.infos[-1]
