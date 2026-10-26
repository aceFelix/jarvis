"""``/undo`` 命令的四种参数形态测试（undo_commands.handle_undo）。

覆盖：
- ``/undo list``：空账本、含"撤不动"条目与其原因、统计行
- ``/undo cancel``：撤掉待发项（定时器摘掉 + 状态转 cancelled）、没有待发项时
  如实说没有、给了非待发 id 时报状态不符
- ``/undo <id>`` / ``/undo``：先展示整轮回滚影响面再问用户，y 才落手；
  回答 N/异常都不改动；不可撤销项直接给原因；id 查不到给用法提示
- ``/undo --task <id>``：批量补偿的成功/失败汇总；缺 id 给用法；名下无记录给警告
- 账本被关（journal_enabled=false）时不静默什么也不做，要说清原因

命令侧不碰真实 git：`manager_for` 一律打桩；账本路径由 conftest 钉到 tmp。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent.commands.handlers.undo_commands import handle_undo
from agent.core.actions import (
    STATE_APPLIED,
    STATE_CANCELLED,
    STATE_HELD,
    STATE_UNDONE,
    UNDO_CHECKPOINT,
    UNDO_NONE,
    EffectRecord,
    get_journal,
    get_window,
)


class _FakeUI:
    """记录输出并可预置确认回答的 UI 桩。"""

    def __init__(self, answer: str = "y") -> None:
        self.answer = answer
        self.infos: list[str] = []
        self.warns: list[str] = []
        self.prompts: list[str] = []

    def info(self, text: str = "", **kw) -> None:
        self.infos.append(str(text))

    def warn(self, text: str = "", **kw) -> None:
        self.warns.append(str(text))

    async def read_user_input_async(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if self.answer == "raise":
            raise EOFError("没有输入")
        return self.answer


class _FakeMgr:
    """检查点管理器桩：describe 给影响面，restore 记调用。"""

    def __init__(self, *, available=True, live=True, restore_ok=True) -> None:
        self._available = available
        self._live = live
        self._restore_ok = restore_ok
        self.restored: list[str] = []

    def available(self) -> bool:
        return self._available

    def has_checkpoint(self, cid) -> bool:
        return self._live

    def describe(self, cid):
        if not self._available:
            return None
        return {
            "files": [{"status": "M", "path": "a.txt"}, {"status": "A", "path": "b.py"}],
            "untracked": ["c.log"],
        }

    def restore(self, cid):
        self.restored.append(cid)
        if self._restore_ok:
            return True, "工作区已恢复到该消息发出之前"
        return False, "回滚失败：文件占用"


def _ctx(ui) -> SimpleNamespace:
    return SimpleNamespace(ui=ui, settings=SimpleNamespace(), session_name="session-t")


def _run(ctx, stripped: str) -> None:
    assert asyncio.run(handle_undo(ctx, stripped)) is True


def _applied(**over) -> EffectRecord:
    data = {
        "tool": "FileWrite",
        "args": {"file_path": "a.txt"},
        "preview": "写入 a.txt",
        "reversible": True,
        "undo_spec": {"kind": UNDO_CHECKPOINT, "cid": "cid-1", "workdir": "."},
        "state": STATE_APPLIED,
    }
    data.update(over)
    return EffectRecord(**data)


@pytest.fixture()
def journal():
    """直接取 conftest 已钉到 tmp 的全局账本。"""
    j = get_journal()
    assert j.enabled is True and j.recent(5) == []
    return j


@pytest.fixture()
def patch_manager(monkeypatch):
    """把检查点管理器换成桩，两个导入点一起盖。

    ``undo_commands`` 与 ``journal.undo_record`` 都是函数内局部 import，名字分别
    解析到 ``agent.core.actions.manager_for`` 与 ``agent.core.actions.journal.manager_for``，
    只盖一个会让“展示影响面用假管理器、真回滚跑真 git”。
    @author aceFelix
    """
    def _patch(mgr):
        factory = lambda *a, **k: mgr     # 被盖的是“造管理器”的工厂，不是管理器本身
        monkeypatch.setattr("agent.core.actions.manager_for", factory)
        monkeypatch.setattr("agent.core.actions.journal.manager_for", factory)
        return mgr

    return _patch


class TestDisabledJournal:
    def test_reports_why_instead_of_silence(self, journal, monkeypatch):
        monkeypatch.setattr(journal, "_enabled", False)
        ui = _FakeUI()
        _run(_ctx(ui), "/undo")
        assert any("journal_enabled" in w for w in ui.warns)


class TestList:
    def test_empty_ledger(self, journal):
        ui = _FakeUI()
        _run(_ctx(ui), "/undo list")
        assert any("账本为空" in m for m in ui.infos)

    def test_lists_undoable_and_not(self, journal):
        can = _applied(preview="写入 a.txt")
        cant = _applied(
            tool="SendEmail", reversible=False, preview="发给 a@b.c",
            undo_spec={"kind": UNDO_NONE, "reason": "该动作已离开本机，无法撤回"},
        )
        held = EffectRecord(tool="SendEmail", preview="发给 c@d.e", state=STATE_HELD,
                            due_at=9_999_999_999.0)
        for r in (can, cant, held):
            journal.record(r)

        ui = _FakeUI()
        _run(_ctx(ui), "/undo list")
        blob = "\n".join(ui.infos)
        assert can.id in blob and cant.id in blob and held.id in blob
        assert "可撤销" in blob and "不可撤销" in blob and "窗口内可撤回" in blob
        assert "该动作已离开本机" in blob          # 撤不动的要带原因
        assert "整轮回滚" in blob                  # 影响面口径要写在明面上
        assert "已执行 2" in blob and "待发 1" in blob

    def test_no_square_brackets_in_state_marks(self, journal):
        """状态标签用〈〉：Rich 会把 ``[held]`` 当样式标签把字吃掉。"""
        journal.record(_applied())
        ui = _FakeUI()
        _run(_ctx(ui), "/undo list")
        blob = "\n".join(ui.infos)
        assert "〈已执行〉" in blob
        assert "[held]" not in blob and "[applied]" not in blob


class TestCancel:
    def test_cancel_held_item(self, journal):
        rec = EffectRecord(tool="SendEmail", preview="发给 a@b.c", state=STATE_HELD,
                           due_at=9_999_999_999.0)
        journal.record(rec)
        get_window().schedule(rec.id, 30.0, lambda: None)

        ui = _FakeUI()
        _run(_ctx(ui), f"/undo cancel {rec.id}")

        assert journal.get(rec.id).state == STATE_CANCELLED
        assert get_window().pending() == []       # 定时器真的摘了（邮件不会发出）
        assert any("已撤回，未执行" in m for m in ui.infos)

    def test_cancel_without_id_takes_latest_held(self, journal):
        stale = EffectRecord(tool="SendEmail", preview="早的一条", state=STATE_HELD)
        journal.record(stale)
        fresh = EffectRecord(tool="SendEmail", preview="最新的一条", state=STATE_HELD)
        journal.record(fresh)
        ui = _FakeUI()
        _run(_ctx(ui), "/undo cancel")
        assert journal.get(fresh.id).state == STATE_CANCELLED
        assert journal.get(stale.id).state == STATE_HELD
        get_window().clear()

    def test_cancel_nothing_pending(self, journal):
        ui = _FakeUI()
        _run(_ctx(ui), "/undo cancel")
        assert any("没有还在延时窗口里" in m for m in ui.infos)

    def test_cancel_applied_item_is_rejected(self, journal):
        rec = _applied()
        journal.record(rec)
        ui = _FakeUI()
        _run(_ctx(ui), f"/undo cancel {rec.id}")
        assert any("不是待发动作" in w for w in ui.warns)
        assert journal.get(rec.id).state == STATE_APPLIED

    def test_cancel_unknown_id(self, journal):
        ui = _FakeUI()
        _run(_ctx(ui), "/undo cancel zzzz")
        assert any("zzzz" in w for w in ui.warns)


class TestUndoOne:
    def test_requires_confirmation_then_restores(self, journal, patch_manager):
        rec = _applied()
        journal.record(rec)
        mgr = patch_manager(_FakeMgr())

        ui = _FakeUI(answer="y")
        _run(_ctx(ui), f"/undo {rec.id}")

        assert ui.prompts, "落手前必须问过用户"
        assert any("a.txt" in m for m in ui.infos)
        assert mgr.restored == ["cid-1"]
        assert journal.get(rec.id).state == STATE_UNDONE
        assert any("工作区已恢复" in m for m in ui.infos)

    def test_answer_no_keeps_workdir(self, journal, patch_manager):
        rec = _applied()
        journal.record(rec)
        mgr = patch_manager(_FakeMgr())
        ui = _FakeUI(answer="n")
        _run(_ctx(ui), f"/undo {rec.id}")
        assert mgr.restored == []
        assert any("已放弃撤销" in m for m in ui.infos)
        assert journal.get(rec.id).state == STATE_APPLIED

    def test_input_error_is_treated_as_no(self, journal, patch_manager):
        """读输入抛异常（管道/非交互）按拒绝处理：不得擅自回滚工作区。"""
        rec = _applied()
        journal.record(rec)
        mgr = patch_manager(_FakeMgr())
        ui = _FakeUI(answer="raise")
        _run(_ctx(ui), f"/undo {rec.id}")
        assert mgr.restored == []

    def test_no_diff_skips_confirmation(self, journal, patch_manager):
        """工作区与检查点无差异时不去回滚，也不白问一次。"""
        rec = _applied()
        journal.record(rec)
        mgr = _FakeMgr()
        mgr.describe = lambda cid: {"files": [], "untracked": []}
        patch_manager(mgr)
        ui = _FakeUI(answer="y")
        _run(_ctx(ui), f"/undo {rec.id}")
        assert mgr.restored == []
        assert ui.prompts == []
        assert any("无需回滚" in m for m in ui.infos)

    def test_unundoable_item_reports_reason(self, journal):
        rec = _applied(
            tool="SendEmail", reversible=False, preview="发给 a@b.c",
            undo_spec={"kind": UNDO_NONE, "reason": "该动作已离开本机，无法撤回"},
        )
        journal.record(rec)
        ui = _FakeUI()
        _run(_ctx(ui), f"/undo {rec.id}")
        assert any("无法撤销" in w and "离开本机" in w for w in ui.warns)

    def test_no_checkpoint_no_items(self, journal):
        ui = _FakeUI()
        _run(_ctx(ui), "/undo")
        assert any("没有可撤销的已执行副作用" in m for m in ui.infos)

    def test_bare_undo_takes_last_applied(self, journal, patch_manager):
        older = _applied(preview="老改动")
        newer = _applied(preview="新改动")
        journal.record(older)
        journal.record(newer)
        mgr = patch_manager(_FakeMgr())
        ui = _FakeUI(answer="y")
        _run(_ctx(ui), "/undo")
        assert mgr.restored == ["cid-1"]
        assert any("新改动" in m for m in ui.infos)
        assert journal.get(newer.id).state == STATE_UNDONE

    def test_unknown_id_shows_hint(self, journal):
        journal.record(_applied())
        ui = _FakeUI()
        _run(_ctx(ui), "/undo qqqq")
        assert any("qqqq" in w and "/undo list" in w for w in ui.warns)

    def test_manager_unavailable_warns(self, journal, patch_manager):
        rec = _applied()
        journal.record(rec)
        patch_manager(_FakeMgr(available=False))
        ui = _FakeUI()
        _run(_ctx(ui), f"/undo {rec.id}")
        assert any("检查点已不可用" in w for w in ui.warns)
        assert journal.get(rec.id).state == STATE_APPLIED


class TestUndoByTask:
    def test_batch_summary_counts_both_ways(self, journal, patch_manager):
        write = _applied(tool="FileWrite", preview="写入 a.txt", task_id="goal9",
                         args={"file_path": "a.txt"})
        mail = _applied(
            tool="SendEmail", preview="发给 a@b.c", task_id="goal9", reversible=False,
            undo_spec={"kind": UNDO_NONE, "reason": "该动作已离开本机，无法撤回"},
        )
        for r in (write, mail):
            journal.record(r)
        mgr = patch_manager(_FakeMgr())

        ui = _FakeUI()
        _run(_ctx(ui), "/undo --task goal9")

        blob = "\n".join(ui.infos)
        assert "副作用 2 项" in blob
        assert "撤销成功 1 项" in blob
        assert "✓" in blob and "✗" in blob
        assert "离开本机" in blob
        assert mgr.restored == ["cid-1"]

    def test_missing_task_id(self, journal):
        ui = _FakeUI()
        _run(_ctx(ui), "/undo --task")
        assert any("用法" in w for w in ui.warns)

    def test_task_without_records(self, journal):
        ui = _FakeUI()
        _run(_ctx(ui), "/undo --task nope")
        assert any("没有已执行的副作用记录" in w for w in ui.warns)
