"""``/tasks`` 两节展示的测试（W3.1 把协作任务与跨会话脊柱并进同一条命令）。

覆盖：
- 第一节沿用 ``TaskList``（无活跃团队时给提示而不是报错）
- 第二节课目：空台账、进行中目标的步骤进度、阻塞原因、等待条件与最迟时刻
- 目标文本里的方括号必须被转义（Rich 会把 ``[y]`` 当样式标签，整行少字）
- 后台驱动线程状态如实反映（在跑 / 未起），口径来自进程级驱动器句柄

命令侧写入子命令（``add``/``show``/``pause``/``resume``/``cancel``/``advance``/``delete``，
阶段 10 收尾切片）只负责参数搬运与呈现，语义正源在 ``spine_ops``（那边单测钉）；
本文件验的是“建得出、看得着、改得动、写错了给得出人话”。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from agent.commands.handlers.collab_commands import handle_tasks
from agent.core.tasks import driver as driver_mod
from agent.core.tasks.spine import (
    BLOCK_APPROVAL,
    BLOCK_TIMEOUT,
    BLOCK_WAIT,
    GOAL_CANCELLED,
    GOAL_DONE,
    GOAL_PAUSED,
    STEP_BLOCKED,
    STEP_DONE,
    STEP_PENDING,
    WAIT_FILE_EXISTS,
    WAIT_TIME,
    TaskGoal,
    TaskStep,
    TaskWait,
    configure_spine_store,
    iso_of,
)


class _FakeUI:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warns: list[str] = []

    def info(self, text: str = "", **kw) -> None:
        self.infos.append(str(text))

    def warn(self, text: str = "", **kw) -> None:
        self.warns.append(str(text))


class _FakeTaskList:
    """协作任务桩：只给 /tasks 第一节要的形状。"""

    def __init__(self, tasks=()) -> None:
        self._tasks = list(tasks)

    def list_all(self):
        return self._tasks


def _ctx(ui, task_list=None):
    """命令上下文：只读两节只要 ui/task_list，写入子命令还要 settings 与 ToolContext。"""
    return SimpleNamespace(
        ui=ui,
        task_list=task_list,
        settings=SimpleNamespace(tasks_wait_deadline_hours=72),
        ctx=SimpleNamespace(workdir="C:/proj"),
        session_name="sess-1",
    )


def _run(ctx, stripped: str = "/tasks") -> str:
    assert asyncio.run(handle_tasks(ctx, stripped)) is True
    return "\n".join(ctx.ui.infos)


@pytest.fixture()
def store(tmp_path):
    """把台账单例钉到 tmp（否则 /tasks 会读到用户真实 ~/.jarvis/goals）。"""
    s = configure_spine_store(tmp_path / "goals")
    yield s
    configure_spine_store(None)


def _goal(steps, **over) -> TaskGoal:
    data = {"goal": "把周报整理好发给团队", "steps": steps}
    data.update(over)
    return TaskGoal(**data)


class TestTwoSections:
    def test_both_sections_present(self, store):
        ui = _FakeUI()
        task = SimpleNamespace(
            id=7, subject="查资料", description="", status="pending",
            owner="", blocked_by=[], blocks=[],
        )
        blob = _run(_ctx(ui, _FakeTaskList([task])))
        assert "共享任务列表" in blob            # 第一节
        assert "跨会话任务脊柱" in blob          # 第二节
        assert "查资料" in blob

    def test_no_task_list_still_shows_spine(self, store):
        ui = _FakeUI()
        blob = _run(_ctx(ui, None))
        assert "任务列表未初始化" in blob
        assert "跨会话任务脊柱" in blob


class TestSpineSection:
    def test_empty_ledger_explains_switch_and_path(self, store):
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        assert "没有进行中的目标" in blob
        assert "settings.tasks_enabled" in blob
        assert ".jarvis/goals" in blob

    def test_terminal_goals_are_not_listed(self, store):
        store.save(_goal([TaskStep(desc="一", state=STEP_DONE)], status=GOAL_DONE))
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        assert "没有进行中的目标" in blob

    def test_paused_goal_is_listed_with_icon(self, store):
        store.save(_goal([TaskStep(desc="等批复", state=STEP_PENDING)], status=GOAL_PAUSED))
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        assert "把周报整理好发给团队" in blob
        assert "⏸" in blob

    def test_id_and_progress_are_readable(self, store):
        g = _goal([TaskStep(desc="一", state=STEP_DONE), TaskStep(desc="二")])
        store.save(g)
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        assert f"〔{g.id}〕1/2" in blob

    def test_blocked_step_shows_reason_and_index(self, store):
        store.save(_goal([
            TaskStep(desc="收集纪要", state=STEP_DONE),
            TaskStep(desc="等张三确认", state=STEP_BLOCKED, block_reason=BLOCK_TIMEOUT),
            TaskStep(desc="发送", state=STEP_PENDING),
        ]))
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        assert "〕1/3" in blob          # 进度写在目标行的〔id〕后面
        assert "卡在第 2 步" in blob
        assert "timeout" in blob

    def test_waiting_step_shows_condition_and_deadline(self, store):
        deadline = time.time() + 3600
        store.save(_goal([TaskStep(
            desc="归档附件", state=STEP_BLOCKED, block_reason=BLOCK_WAIT,
            wait=TaskWait(kind=WAIT_FILE_EXISTS, spec={"path": "x.pdf"}, deadline_at=deadline),
        )]))
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        # 等待条件要用“在等”而不是“卡在”（2026-10-26 实测：重启后驱动会把
        # 等条件的步骤标成 blocked(waiting)，叫“卡住”会让用户以为出了毛病），
        # 但“等什么 + 最迟什么时候”两个要素不能丢。
        assert "第 1 步在等" in blob and "waiting" not in blob
        assert "file_exists" in blob
        assert iso_of(deadline)[:5] in blob

    def test_not_yet_started_wait_shows_condition(self, store):
        """未进入阻塞态但带等待条件的步骤：单独一行说清在等什么。"""
        deadline = time.time() + 7200
        store.save(_goal([TaskStep(
            desc="发送", state=STEP_PENDING,
            wait=TaskWait(kind=WAIT_TIME, spec={"at": iso_of(time.time() + 60)},
                          deadline_at=deadline),
        )]))
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        assert "第 1 步在等：time" in blob

    def test_ready_step_shows_next_action(self, store):
        store.save(_goal([TaskStep(desc="发送"), TaskStep(desc="归档")]))
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        assert "第 1 步待推进：发送" in blob

    def test_goal_text_brackets_are_escaped(self, store):
        """目标里的方括号必须转义，否则 Rich 当样式标签把字吃掉。"""
        pytest.importorskip("rich")
        store.save(_goal([TaskStep(desc="发 [y] 确认")]))
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        assert r"\[y]" in blob          # 开了头的方括号被转义，Rich 不会吞字

    def test_corrupted_ledger_does_not_break_command(self, store):
        store.base_dir.mkdir(parents=True, exist_ok=True)
        (store.base_dir / "bad.json").write_text("{半截", encoding="utf-8")
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        assert "跨会话任务脊柱" in blob


class TestDriverStatusLine:
    def test_reports_running_when_driver_started(self, store, monkeypatch):
        monkeypatch.setattr(driver_mod, "_driver", SimpleNamespace(running=True))
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        assert "在跑" in blob

    def test_reports_not_started_without_driver(self, store, monkeypatch):
        monkeypatch.setattr(driver_mod, "_driver", None)
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        assert "未起" in blob

    def test_reports_not_started_when_stopped(self, store, monkeypatch):
        monkeypatch.setattr(driver_mod, "_driver", SimpleNamespace(running=False))
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        assert "未起" in blob

    def test_status_probe_failure_degrades_to_not_started(self, store, monkeypatch):
        """读驱动器状态本身出错不能把整条 /tasks 带崩。"""
        class _Boom:
            @property
            def running(self):
                raise RuntimeError("探针挂了")

        monkeypatch.setattr(driver_mod, "_driver", _Boom())
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()))
        assert "未起" in blob


class TestWriteSubcommands:
    """``/tasks add|show|pause|...``——脊柱终于有了用户侧入口。"""

    def _run2(self, ui, text: str) -> tuple[str, str]:
        """跑一次命令，返 (全部 info 文本, 全部 warn 文本)。"""
        ctx = _ctx(ui, _FakeTaskList())
        assert asyncio.run(handle_tasks(ctx, text)) is True
        return "\n".join(ui.infos), "\n".join(ui.warns)

    def test_add_creates_goal_visible_in_second_section(self, store):
        ui = _FakeUI()
        blob, warns = self._run2(ui, "/tasks add 收集纪要; 生成周报")
        assert warns == ""
        assert "已建立目标" in blob
        goals = store.list_goals(None, limit=10)
        assert len(goals) == 1
        assert [s.desc for s in goals[0].steps] == ["收集纪要", "生成周报"]
        # 建完直接回显第二节，用户不用再敲一次 /tasks
        assert "跨会话任务脊柱" in blob and "0/2" in blob

    def test_add_stores_workdir_and_session(self, store):
        self._run2(_FakeUI(), "/tasks add 单步目标")
        goal = store.list_goals(None, limit=10)[0]
        assert goal.workdir == "C:/proj" and goal.session_id == "sess-1"

    def test_add_with_time_wait_attaches_to_first_step(self, store):
        self._run2(_FakeUI(), "/tasks add 明早发周报 --wait time:+2h")
        step = store.list_goals(None, limit=10)[0].steps[0]
        assert step.wait is not None and step.wait.kind == WAIT_TIME

    def test_add_accepts_equals_form_switches(self, store):
        """``--wait=`` 连写也要认（用户习惯跟其他命令保持一致）。"""
        self._run2(_FakeUI(), "/tasks add 等文件 --wait=file:C:/t/x.pdf --deadline=6")
        step = store.list_goals(None, limit=10)[0].steps[0]
        assert step.wait.spec["path"] == "C:/t/x.pdf"
        assert step.wait.deadline_at < time.time() + 7 * 3600

    def test_add_bad_wait_is_refused_and_nothing_written(self, store):
        ui = _FakeUI()
        _, warns = self._run2(ui, "/tasks add 目标甲 --wait magic:xyz")
        assert "未知等待类型" in warns
        assert store.list_goals(None, limit=10) == []   # 不能先把目标落下去再说参数错

    def test_add_without_text_shows_usage(self, store):
        ui = _FakeUI()
        _, warns = self._run2(ui, "/tasks add --wait time:+1h")
        assert "/tasks add <目标>" in warns
        assert store.list_goals(None, limit=10) == []

    def test_add_tells_user_when_driver_is_not_running(self, store, monkeypatch):
        """建完必须告知“不会自动推进”，否则用户只会觉得功能坏了。"""
        monkeypatch.setattr(driver_mod, "_driver", None)
        ui = _FakeUI()
        blob, _ = self._run2(ui, "/tasks add 目标乙")
        assert "不会自动推进" in blob

    def test_add_shows_running_driver(self, store, monkeypatch):
        monkeypatch.setattr(driver_mod, "_driver", SimpleNamespace(running=True))
        ui = _FakeUI()
        blob, _ = self._run2(ui, "/tasks add 目标丙")
        assert "条件满足会自己往下推" in blob

    def test_show_expands_steps(self, store):
        ui = _FakeUI()
        self._run2(ui, "/tasks add 甲; 乙; 丙")
        gid = store.list_goals(None, limit=10)[0].id
        ui2 = _FakeUI()
        blob, _ = self._run2(ui2, f"/tasks show {gid[:6]}")
        assert gid in blob
        assert "1." in blob and "3." in blob
        assert "工作目录：C:/proj" in blob

    def test_show_unknown_id_warns(self, store):
        ui = _FakeUI()
        _, warns = self._run2(ui, "/tasks show ffffffffff")
        assert "没找到目标" in warns

    @pytest.mark.parametrize("action,state", [("pause", GOAL_PAUSED), ("cancel", GOAL_CANCELLED)])
    def test_control_actions_persist(self, store, action, state):
        ui = _FakeUI()
        self._run2(ui, "/tasks add 目标丁")
        gid = store.list_goals(None, limit=10)[0].id
        ui2 = _FakeUI()
        blob, _ = self._run2(ui2, f"/tasks {action} {gid[:6]}")
        assert store.load(gid).status == state
        assert gid in blob

    def test_advance_skips_wait_for_testing(self, store):
        """advance 是测试绕行口子：跳掉等待但不谎称条件已成立。"""
        ui = _FakeUI()
        self._run2(ui, "/tasks add 目标戊 --wait file:C:/never/exists.dat")
        gid = store.list_goals(None, limit=10)[0].id
        self._run2(_FakeUI(), f"/tasks advance {gid}")
        assert store.load(gid).steps[0].wait is None

    def test_delete_removes_ledger_entry(self, store):
        ui = _FakeUI()
        self._run2(ui, "/tasks add 目标己")
        gid = store.list_goals(None, limit=10)[0].id
        self._run2(_FakeUI(), f"/tasks delete {gid}")
        assert store.load(gid) is None

    def test_control_without_id_warns_usage(self, store):
        ui = _FakeUI()
        _, warns = self._run2(ui, "/tasks pause")
        assert "用法: /tasks pause <目标id>" in warns

    def test_unknown_subcommand_shows_usage_not_crash(self, store):
        ui = _FakeUI()
        blob, warns = self._run2(ui, "/tasks frobnicate x")
        assert "未知的 /tasks 子命令" in warns
        assert "/tasks add <目标>" in blob

    def test_help_lists_all_subcommands(self, store):
        ui = _FakeUI()
        blob, _ = self._run2(ui, "/tasks help")
        for frag in ("add", "show", "approve", "pause", "advance", "delete"):
            assert frag in blob

    def test_plain_tasks_still_read_only(self, store):
        """无参 ``/tasks`` 依旧只列两节（不能因为加了子命令就改掉老行为）。"""
        ui = _FakeUI()
        blob = _run(_ctx(ui, _FakeTaskList()), "/tasks")
        assert "任务列表为空" in blob and "跨会话任务脊柱" in blob
        assert store.list_goals(None, limit=10) == []   # 只读：一条目标也不建


class TestQuotesAndApprove:
    """全文包围的引号与“批准受阻步骤”（2026-10-26 人工实测后补）。

    两个都来自同一个现场问题：用户照走查清单敲 ``/tasks add "目标"``，播报里
    目标名带着字面引号；接着受阻塞住，播报说“批准后这一步会立刻重试”，
    而终端当时没有任何地方能“批准”——敲个 ``y`` 只会被当成普通对话发给模型。
    """

    def _run2(self, ui, text: str) -> tuple[str, str]:
        ctx = _ctx(ui, _FakeTaskList())
        assert asyncio.run(handle_tasks(ctx, text)) is True
        return "\n".join(ui.infos), "\n".join(ui.warns)

    def test_add_strips_wrapping_quotes(self, store):
        """整体包住的引号要剥：否则目标名与步骤文本都脏着，还会传给子代理。"""
        self._run2(_FakeUI(), '/tasks add "在工作目录里创建文件 a.txt" --wait time:+90s')
        goal = store.list_goals(None, limit=10)[0]
        assert goal.goal == "在工作目录里创建文件 a.txt"
        assert goal.steps[0].desc == "在工作目录里创建文件 a.txt"

    def test_add_keeps_quotes_used_inside_text(self, store):
        """真的在用引号的句子原样保留：只剥“整体包住”的那一层。"""
        self._run2(_FakeUI(), '/tasks add "a.txt" 与 "b.txt" 都改成 c')
        assert store.list_goals(None, limit=10)[0].goal == '"a.txt" 与 "b.txt" 都改成 c'

    def test_approve_refuses_when_step_is_not_waiting_for_approval(self, store):
        self._run2(_FakeUI(), "/tasks add 目标庚")
        gid = store.list_goals(None, limit=10)[0].id
        _, warns = self._run2(_FakeUI(), f"/tasks approve {gid[:6]}")
        assert "没有在等授权的步骤" in warns

    def test_approve_without_driver_does_not_pretend(self, store, monkeypatch):
        """驱动线程未起时批准了也没人重试，必须如实说并给手动路径。"""
        monkeypatch.setattr(driver_mod, "_driver", None)
        store.save(_goal([TaskStep(
            desc="写文件", state=STEP_BLOCKED, block_reason=BLOCK_APPROVAL,
            denied_tools=["FileWrite"],
        )]))
        gid = store.list_goals(None, limit=10)[0].id
        _, warns = self._run2(_FakeUI(), f"/tasks approve {gid[:6]}")
        assert "驱动线程未起" in warns and "advance" in warns
