"""W3 端到端：后台值守步骤真的改了文件，就必须真的能撤回来。

盯的是 2026-10-22 实机走查的第三条断链：后台步骤的子代理 ctx 里没有
``checkpoint_cid``，而副作用账本的规则是"拿不到本轮检查点就不记"——结果
``actions.jsonl`` 一条没有、台账 ``journal_ids`` 永远为空、``/undo --task``
永远回"没有已执行的副作用记录"。这条链路只在单测里直传 dict 是测不出来的，
所以本文件把**真实编排器 + 真实 FileWrite + 真实 shadow git** 接起来跑。

LLM 那一层用桩替换（``run_subagent`` 换成"通过编排器真调一次 FileWrite"），
其余全部走生产代码：``default_spawn`` 建 extra、``_step_checkpoint`` 打任务级
检查点、``ToolOrchestrator._record_effect`` 入账、驱动器回填 ``journal_ids``、
``undo_task`` 落回工作区。

需要 git（能力自身的前置条件，不是测试环境的额外依赖）：缺失时整组 skip。

@author aceFelix
"""
from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

from agent.core.actions import (
    STATE_APPLIED,
    STATE_UNDONE,
    get_journal,
    undo_task,
)
from agent.core.context import ToolContext
from agent.core.message import ToolUseContent
from agent.core.orchestrator import EXTRA_DENIALS, ToolOrchestrator
from agent.core.result import PermissionResult
from agent.core.tasks.driver import TaskDriver
from agent.core.tasks.spine import (
    STEP_BLOCKED,
    STEP_DONE,
    SpineStore,
    TaskGoal,
    TaskStep,
)
from agent.core.tool import ToolRegistry
from agent.tools.file_ops.file_write import FileWriteTool

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="环境缺少 git，检查点回滚断言跳过"
)

#: 子代理要写的文件（放在目标 workdir 里）
_TARGET = "weekly.txt"


class _AllowChecker:
    """恒放行：本文件测的是"改动有没有入账"，权限判定另有文件覆盖。"""

    def check(self, tool, args, ctx):
        return PermissionResult.allow("测试放行")


def _settings(workdir: str, **over) -> SimpleNamespace:
    base = {
        "tasks_enabled": True,
        "tasks_step_max_attempts": 3,
        "tasks_retry_backoff_s": 60,
        "tasks_blocked_ask_days": 3,
        "tasks_wait_deadline_hours": 72,
        "tasks_checkpoint_max": 8,
        "workdir": workdir,
        "checkpoint_enabled": True,
        "checkpoint_max_per_session": 20,
        "checkpoint_timeout_seconds": 10,
        # 让驱动器认为自己"有可用 provider"（真 provider 由 _resolve_provider 现建，
        # mock provider 不会被调用——run_subagent 已被替换）
        "api_format": "mock",
        "api_key": "test-key",
        "model": "",
        "custom_models": {},
        "permissions_file": "",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _patch_subagent(monkeypatch, rel: str = _TARGET):
    """把子代理换成"通过真实编排器调一次 FileWrite"，其余接线一律不动。"""
    kw_settings: dict = {"got": None}

    async def fake_run(definition, prompt, **kw):
        kw_settings["got"] = kw.get("settings")
        ctx = ToolContext(
            workdir=kw["workdir"],
            messages=[],
            permission_mode=kw.get("permission_mode", "default"),
            ui=None,
            extra=kw["extra"],
            settings=kw.get("settings"),
        )
        reg = ToolRegistry()
        reg.register(FileWriteTool())
        orch = ToolOrchestrator(registry=reg, permission_checker=_AllowChecker())
        calls = [ToolUseContent(
            id="t-step", name="FileWrite",
            input={"file_path": f"{kw['workdir']}/{rel}", "content": "本周一切正常\n"},
        )]
        await orch.execute_calls(calls, ctx)
        return SimpleNamespace(
            success=True, report=f"已写入 {rel}", iterations=1, tool_calls=1,
        )

    monkeypatch.setattr("agent.collaboration.subagent.run_subagent", fake_run)
    # 把捕到的 settings 交回给调用方断言（闭包变量在外层测试里读不到）
    return kw_settings


def _driver(tmp_path, workdir: str, *, settings=None,
            goal=None) -> tuple[TaskDriver, SpineStore]:
    store = SpineStore(tmp_path / "goals")
    driver = TaskDriver(settings=settings or _settings(str(workdir)), store=store)
    if goal is not None:
        store.save(goal)
    return driver, store


def _goal(workdir: str) -> TaskGoal:
    return TaskGoal(
        goal="把周报写到文件里", workdir=workdir,
        steps=[TaskStep(desc=f"在工作目录里创建 {_TARGET}")],
    )


@pytest.fixture()
def workdir(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "keep.txt").write_text("别动我", encoding="utf-8")
    return d


class TestStepSideEffectsJournaled:
    """写成功 → 入账 → 台账回填 → 能撤，四步缺一不可。"""

    def test_step_writes_and_journals(self, tmp_path, monkeypatch, workdir):
        captured = _patch_subagent(monkeypatch)
        journal = get_journal()
        goal = _goal(str(workdir))
        driver, store = _driver(tmp_path, str(workdir), goal=goal)

        driver._tick()

        assert (workdir / _TARGET).read_text(encoding="utf-8") == "本周一切正常\n"
        saved = store.load(goal.id)
        assert saved.steps[0].state == STEP_DONE
        assert saved.steps[0].journal_ids, "台账必须回填账本 id，/undo --task 才查得到"

        recs = journal.task_records(goal.id)
        assert len(recs) == 1, "步骤产生的副作用必须以 task_id 归属入账"
        rec = recs[0]
        assert rec.state == STATE_APPLIED
        assert rec.tool == "FileWrite"
        assert rec.undo_spec.get("kind") in ("files", "checkpoint"), \
            "本地写必须挂上真补偿，不能是假可撤销"
        assert rec.undoable is True
        # 子 ctx 必须拿到宿主 settings：否则编排器读 [actions] 会退成默认值
        # （用户把延时窗口改 90 秒、或关掉记账，对后台步骤无效）
        assert captured["got"] is not None

    def test_undo_task_reverts_step_files(self, tmp_path, monkeypatch, workdir):
        _patch_subagent(monkeypatch)
        goal = _goal(str(workdir))
        driver, store = _driver(tmp_path, str(workdir), goal=goal)
        driver._tick()
        assert (workdir / _TARGET).exists()
        settings = _settings(str(workdir))

        results = undo_task(goal.id, settings=settings)

        assert len(results) == 1
        _rec, ok, note = results[0]
        assert ok is True, f"撤不动却报了成功：{note}"
        assert not (workdir / _TARGET).exists(), "回滚后本步新建的文件应消失"
        assert (workdir / "keep.txt").exists(), "步骤开始前就存在的文件不该被牵连"
        # 从账本重读：返回的 rec 是调用方拿到的快照，状态以落盘的为准
        fresh = get_journal().task_records(goal.id)
        assert fresh and fresh[0].state == STATE_UNDONE

    def test_double_undo_is_refused(self, tmp_path, monkeypatch, workdir):
        _patch_subagent(monkeypatch)
        goal = _goal(str(workdir))
        driver, _ = _driver(tmp_path, str(workdir), goal=goal)
        driver._tick()
        settings = _settings(str(workdir))
        undo_task(goal.id, settings=settings)

        again = undo_task(goal.id, settings=settings)
        assert again and all(ok is False for _rec, ok, _n in again), "同一笔不能撤两次"


class TestNothingRecordedWhenUnreversible:
    """撤不动就不记：一条"看着能撤其实撤不了"的记录比没有更坏。"""

    def test_file_snapshot_alone_is_enough(self, tmp_path, monkeypatch, workdir):
        """整轮检查点关掉也应该能撤：文件级留底是另一条独立的通道（2026-10）。

        修之前用户关掉 ``checkpoint_enabled`` 就同时失去了"改错一个文件能不能撤"，
        因为补偿计划只有整轮检查点一种。现在两条路是分开的：检查点是"退整轮"，
        文件留底是"退这一个文件"，关掉前者不影响后者。
        """
        _patch_subagent(monkeypatch)
        journal = get_journal()
        goal = _goal(str(workdir))
        driver, store = _driver(
            tmp_path, str(workdir), goal=goal,
            settings=_settings(str(workdir), checkpoint_enabled=False),
        )

        driver._tick()

        assert (workdir / _TARGET).exists(), "有没有留底都不该影响执行"
        recs = journal.task_records(goal.id)
        assert len(recs) == 1, "没有轮次检查点也要入账：文件级留底本身就是补偿计划"
        assert recs[0].undo_spec.get("kind") == "files"
        assert store.load(goal.id).steps[0].journal_ids, "台账要能回填这笔 id"

    def test_no_compensation_channel_records_nothing(self, tmp_path, monkeypatch, workdir):
        """两条补偿通道都关掉时就不记 —— 宁可少记，不可假可撤销。"""
        _patch_subagent(monkeypatch)
        journal = get_journal()
        goal = _goal(str(workdir))
        driver, store = _driver(
            tmp_path, str(workdir), goal=goal,
            settings=_settings(
                str(workdir), checkpoint_enabled=False, actions_file_snapshots=0,
            ),
        )

        driver._tick()

        # 文件确实写了（执行不受影响），但没有任何"可撤销"记录被伪造出来
        assert (workdir / _TARGET).exists()
        assert journal.task_records(goal.id) == []
        assert store.load(goal.id).steps[0].journal_ids == []

    def test_blocked_step_leaves_no_journal(self, tmp_path, monkeypatch, workdir):
        """被权限拦下时什么都没写，自然也不该有账：受阻与入账两条口径互不干扰。"""
        goal = _goal(str(workdir))

        async def deny_all(definition, prompt, **kw):
            kw["extra"][EXTRA_DENIALS].append(
                {"tool": "FileWrite", "reason": "需要用户确认但当前环境无 UI"})
            return SimpleNamespace(success=True, report="被拦下", iterations=1, tool_calls=1)

        monkeypatch.setattr("agent.collaboration.subagent.run_subagent", deny_all)
        driver, store = _driver(tmp_path, str(workdir), goal=goal)
        driver._tick()

        saved = store.load(goal.id)
        assert saved.steps[0].state == STEP_BLOCKED
        assert not (workdir / _TARGET).exists()
        assert get_journal().task_records(goal.id) == []

    def test_journal_ids_survive_reload(self, tmp_path, monkeypatch, workdir):
        """归属必须真的落盘：重启后台还能撤，是"跨会话"这条主张的一部分。"""
        _patch_subagent(monkeypatch)
        goal = _goal(str(workdir))
        driver, store = _driver(tmp_path, str(workdir), goal=goal)
        driver._tick()
        first = store.load(goal.id).steps[0].journal_ids
        assert first

        reloaded = SpineStore(tmp_path / "goals").load(goal.id)
        assert reloaded.steps[0].journal_ids == first
