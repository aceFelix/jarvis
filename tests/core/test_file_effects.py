"""文件级撤销（``UNDO_FILES``）的测试：写前留底 → 记账 → 按文件恢复。

## 为什么这一组要单独存在

用户的原话是「文件变动之后，jarvis 不知道如何恢复为原来的状态」。修之前的
实情有三条断链：``FileWrite``/``FileEdit`` 覆盖即丢原文、已有的 ``FileGuard``
只在 bash 沙箱路径被调用且不进账本、本地写条目一律退到**整轮** shadow git
检查点（改错一个文件要连带退掉同轮所有改动）。

本文件锁的就是补上的这一段，重点是四件容易在后续重构里被悄悄改掉的事：

1. 留底发生在**工具执行之前**（执行后原文已经没了，留底等于没做）；
2. 只读调用与不认识的参数**不**留底（白复制一遍是纯浪费）；
3. 恢复的粒度是**这一条**：不做整轮连带标记，同轮其它条目仍然有效；
4. 快照不在了就如实说撤不动，绝不回报一个假的"已撤销"。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.core.actions import (
    STATE_APPLIED,
    STATE_UNDONE,
    UNDO_CHECKPOINT,
    UNDO_FILES,
    EffectRecord,
    configure_journal,
    get_journal,
    undo_record,
)
from agent.core.actions import file_effects as fe
from agent.core.actions.file_effects import (
    describe_snapshot,
    extract_targets,
    list_entries,
    restore,
    snapshot_before_write,
)
from agent.core.context import ToolContext
from agent.core.message import ToolUseContent
from agent.core.orchestrator import ToolOrchestrator
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import Tool, ToolRegistry


class _AllowAll:
    """恒 ALLOW 的权限桩：本文件不重测权限管线。"""

    def check(self, tool, args, ctx) -> PermissionResult:
        return PermissionResult.allow("测试放行")


def _settings(**over) -> SimpleNamespace:
    base = {
        "actions_journal_enabled": True,
        "actions_undo_window_s": 0,          # 窗口关掉：本地写不走延时，直接执行并记账
        "actions_track_local_writes": True,
        "actions_irreversible_tools": [],
        "actions_file_snapshots": 50,
        "actions_file_snapshot_mb": 64,
        "workdir": ".",
    }
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把留底目录与账本都钉到 tmp：绝不碰用户真实的 ~/.jarvis。

    ``_SNAPSHOT_ROOT`` 支持绝对路径，正是为了这种隔离（见 file_effects 里说明）。
    """
    monkeypatch.setattr(fe, "_SNAPSHOT_ROOT", str(tmp_path / "file_snapshots"))
    configure_journal(path=tmp_path / "actions.jsonl", enabled=True)
    return tmp_path


class _WriteTool(Tool):
    """最小写入工具：走真实的"写文件"路径，让留底与恢复对真实磁盘生效。"""

    name = "FileWrite"
    description = "写文件"
    input_schema = {"type": "object", "properties": {}, "required": []}

    def is_read_only(self, args: dict) -> bool:
        return False

    def is_destructive(self, args: dict) -> bool:
        return True

    async def call(self, args: dict, ctx: ToolContext) -> ToolResult:
        target = Path(ctx.workdir) / args["file_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args["content"], encoding="utf-8")
        return ToolResult.ok(f"已写入 {args['file_path']}")

    def activity_description(self, args: dict | None = None) -> str:
        return f"写入 {(args or {}).get('file_path', '')}"


class TestExtractTargets:
    """参数 → 路径的推导必须**保守**：认不出来就当没有路径。"""

    def test_common_keys(self):
        assert extract_targets({"file_path": "a.txt"}) == ["a.txt"]
        assert extract_targets({"path": "b.txt"}) == ["b.txt"]
        assert extract_targets({"filePath": "c.txt"}) == ["c.txt"]

    def test_multi_path_forms(self):
        assert extract_targets({"paths": ["x", "y"]}) == ["x", "y"]
        assert extract_targets({"files": ("p", "q")}) == ["p", "q"]

    def test_dedupes_and_caps(self):
        assert extract_targets({"file_path": "a", "path": "a"}) == ["a"]
        many = {"paths": [f"f{i}" for i in range(50)]}
        assert len(extract_targets(many)) == fe._MAX_PATHS_PER_CALL

    def test_ignores_text_content(self):
        """``new_string`` 里出现的路径字面量不能被当成目标文件去复制。

        这是保守推导的核心：把正文里的路径当真，会去备份一个跟本次改动无关的
        文件，既拖慢调用也让清单变脏。
        """
        assert extract_targets({"new_string": "改到 /etc/passwd 去"}) == []
        assert extract_targets({"content": "见 C:\\log\\x.txt"}) == []
        assert extract_targets(None) == []
        assert extract_targets({}) == []


class TestSnapshotBeforeWrite:
    """`snapshot_before_write` 的边界：该留的留、不该留的一个字节都不复制。"""

    def test_skips_read_only(self, sandbox):
        target = sandbox / "a.txt"
        target.write_text("原文", encoding="utf-8")
        assert snapshot_before_write(
            "FileRead", {"file_path": str(target)}, read_only=True,
            destructive=False, settings=_settings(),
        ) == ""

    def test_skips_when_no_path(self, sandbox):
        assert snapshot_before_write(
            "Bash", {"command": "ls"}, read_only=False, destructive=True, settings=_settings(),
        ) == ""

    def test_skips_when_not_destructive(self, sandbox):
        """未声明破坏性的工具不留底。

        ``is_read_only`` 默认就是 False，单靠它会让**任何带 file_path 参数的
        工具**都复制一遍文件（包括只查不动的）；两道前置一起用才划算。
        """
        target = sandbox / "a.txt"
        target.write_text("原文", encoding="utf-8")
        assert snapshot_before_write(
            "FileWrite", {"file_path": str(target)}, read_only=False,
            destructive=False, settings=_settings(),
        ) == ""

    def test_skips_when_disabled_by_config(self, sandbox):
        """``[actions].file_snapshots = 0`` 是关闭开关（与 checkpoint 分开）。"""
        target = sandbox / "a.txt"
        target.write_text("原文", encoding="utf-8")
        assert snapshot_before_write(
            "FileWrite", {"file_path": str(target)}, read_only=False, destructive=True,
            settings=_settings(actions_file_snapshots=0),
        ) == ""

    def test_relative_path_resolves_against_workdir(self, sandbox):
        """相对路径必须按工具的工作目录解，不能按进程 cwd。

        这条守的是一个真存在过的缺陷：只按 ``Path(raw)`` 解，相对路径会落到进程
        cwd 上 —— 轻则备份了无关文件，重则把确实存在的文件记成 ``existed=False``，
        于是“恢复”变成把用户文件删掉。
        """
        target = sandbox / "rel.txt"
        target.write_text("原文", encoding="utf-8")
        snap = snapshot_before_write(
            "FileWrite", {"file_path": "rel.txt"}, read_only=False, destructive=True,
            settings=_settings(), workdir=str(sandbox),
        )
        assert snap
        entries = list_entries(snap, _settings())
        assert entries[0]["existed"] is True, "相对路径没解对，会被当成新文件"
        assert Path(entries[0]["path"]).resolve() == target.resolve()

    def test_snapshots_existing_file(self, sandbox):
        target = sandbox / "a.txt"
        target.write_text("原文", encoding="utf-8")
        snap = snapshot_before_write(
            "FileWrite", {"file_path": str(target)}, read_only=False, destructive=True, settings=_settings(),
        )
        assert snap
        entries = list_entries(snap, _settings())
        assert [e["path"] for e in entries][0].endswith("a.txt")
        assert entries[0]["existed"] is True

    def test_snapshots_absent_file_as_not_existed(self, sandbox):
        """新建文件的留底要记成 existed=False，恢复时才会把它删掉而不是写回。"""
        target = sandbox / "not_yet.txt"
        snap = snapshot_before_write(
            "FileWrite", {"file_path": str(target)}, read_only=False, destructive=True, settings=_settings(),
        )
        assert snap
        assert list_entries(snap, _settings())[0]["existed"] is False

    def test_skips_oversized_directory(self, sandbox):
        """超限目录不留底 —— 复制而非硬链接，大目录一次 copytree 能写满磁盘。"""
        big = sandbox / "big"
        big.mkdir()
        (big / "blob.bin").write_bytes(b"x" * (2 * 1024 * 1024))
        snap = snapshot_before_write(
            "FileWrite", {"file_path": str(big)}, read_only=False, destructive=True,
            settings=_settings(actions_file_snapshot_mb=1),
        )
        assert snap == ""

    def test_never_raises(self, sandbox, monkeypatch):
        """留底是辅助能力，任何内部异常都必须降级为"没留底"，不能拖垮写入。"""
        def _boom(*a, **kw):
            raise RuntimeError("模拟备份引擎炸了")

        monkeypatch.setattr(fe, "guard_for", _boom)
        target = sandbox / "a.txt"
        target.write_text("原文", encoding="utf-8")
        assert snapshot_before_write(
            "FileWrite", {"file_path": str(target)}, read_only=False, destructive=True, settings=_settings(),
        ) == ""


class TestRestore:
    """恢复的三种结局：还原原文、删掉新建、如实报告恢复不了。"""

    def test_restores_original_content(self, sandbox):
        target = sandbox / "note.txt"
        target.write_text("原始内容 v1\n", encoding="utf-8")
        snap = snapshot_before_write(
            "FileWrite", {"file_path": str(target)}, read_only=False, destructive=True, settings=_settings(),
        )
        target.write_text("被改坏的内容 v2\n", encoding="utf-8")

        ok, note = restore(snap, _settings())
        assert ok is True
        assert "已恢复 1 个文件" in note
        assert target.read_text(encoding="utf-8") == "原始内容 v1\n"

    def test_new_file_is_deleted(self, sandbox):
        target = sandbox / "created.txt"
        snap = snapshot_before_write(
            "FileWrite", {"file_path": str(target)}, read_only=False, destructive=True, settings=_settings(),
        )
        target.write_text("新建的\n", encoding="utf-8")

        ok, _note = restore(snap, _settings())
        assert ok is True
        assert not target.exists()

    def test_reports_missing_snapshot(self, sandbox):
        """快照被配额清掉后必须说清"恢复不了"，绝不能报成功。"""
        ok, note = restore("20260101_000000_deadbe", _settings())
        assert ok is False
        assert "无法恢复" in note

    def test_reports_double_restore(self, sandbox):
        target = sandbox / "once.txt"
        target.write_text("原文", encoding="utf-8")
        snap = snapshot_before_write(
            "FileWrite", {"file_path": str(target)}, read_only=False, destructive=True, settings=_settings(),
        )
        target.write_text("改了", encoding="utf-8")
        assert restore(snap, _settings())[0] is True
        ok, note = restore(snap, _settings())
        assert ok is False
        assert "恢复过一次" in note

    def test_empty_snap_id(self):
        ok, note = restore("", _settings())
        assert ok is False
        assert "没有留底快照" in note


class TestUndoFilesSpec:
    """账本侧的 ``UNDO_FILES`` 形态：可撤销判定与状态流转。"""

    def _record(self, snap_id: str) -> EffectRecord:
        return EffectRecord(
            tool="FileWrite",
            args={"file_path": "a.txt"},
            preview="写入 a.txt",
            reversible=True,
            undo_spec={"kind": UNDO_FILES, "snap_id": snap_id},
            state=STATE_APPLIED,
            workdir=".",
        )

    def test_undoable_requires_snap_id(self):
        assert self._record("20260101_000000_abcdef").undoable is True
        assert self._record("").undoable is False

    def test_undo_record_marks_undone(self, sandbox):
        target = sandbox / "x.txt"
        target.write_text("原文", encoding="utf-8")
        snap = snapshot_before_write(
            "FileWrite", {"file_path": str(target)}, read_only=False, destructive=True, settings=_settings(),
        )
        target.write_text("改了", encoding="utf-8")

        rec = self._record(snap)
        assert get_journal().record(rec) is True

        ok, note = undo_record(rec, settings=_settings(), journal=get_journal())
        assert ok is True
        assert "已恢复" in note
        assert get_journal().get(rec.id).state == STATE_UNDONE

    def test_undo_record_marks_failed_when_snapshot_gone(self, sandbox):
        rec = self._record("20260101_000000_zzzzzz")
        assert get_journal().record(rec) is True

        ok, note = undo_record(rec, settings=_settings(), journal=get_journal())
        assert ok is False
        assert "无法恢复" in note
        assert get_journal().get(rec.id).state == "failed"

    def test_no_sibling_collateral_marks(self, sandbox):
        """文件级撤销**不得**像整轮回滚那样把同轮其它条目一起标成已撤销。

        这是与 ``UNDO_CHECKPOINT`` 最关键的行为差异：整轮回滚确实会让同轮条目
        一起失效，而这里只动自己被碰过的那几个文件。误标会让用户以为"还有一层
        能撤"却什么都撤不动。
        """
        target = sandbox / "mine.txt"
        target.write_text("原文", encoding="utf-8")
        snap = snapshot_before_write(
            "FileWrite", {"file_path": str(target)}, read_only=False, destructive=True, settings=_settings(),
        )
        mine = self._record(snap)
        other = EffectRecord(
            tool="FileWrite",
            args={"file_path": "other.txt"},
            preview="写入 other.txt",
            reversible=True,
            undo_spec={"kind": UNDO_CHECKPOINT, "cid": "cid1", "workdir": "."},
            state=STATE_APPLIED,
            workdir=".",
        )
        assert get_journal().record(mine) is True
        assert get_journal().record(other) is True

        undo_record(mine, settings=_settings(), journal=get_journal())

        assert get_journal().get(mine.id).state == STATE_UNDONE
        assert get_journal().get(other.id).state == STATE_APPLIED


class TestOrchestratorIntegration:
    """端到端：走真实编排路径，验"留底发生在执行前"与"记账用文件级形态"。"""

    def _orch(self, tool: Tool) -> ToolOrchestrator:
        reg = ToolRegistry()
        reg.register(tool)
        return ToolOrchestrator(reg, _AllowAll())

    async def _write(self, sandbox, path: str, content: str) -> ToolContext:
        ctx = ToolContext(
            workdir=str(sandbox),
            messages=[],
            settings=_settings(),
            extra={},          # 刻意不塞 checkpoint_cid：证明不依赖整轮检查点
        )
        orch = self._orch(_WriteTool())
        await orch.execute_calls(
            [ToolUseContent(id="t1", name="FileWrite",
                            input={"file_path": path, "content": content})],
            ctx,
        )
        return ctx

    async def test_overwrite_is_recoverable_without_any_checkpoint(self, sandbox):
        """没有 git 检查点（终端未开每轮快照）时，文件改动也必须能撤回来。

        修之前这里就是断的：``_turn_checkpoint_id`` 拿不到 cid 直接不记账，
        用户敲 /undo 只得到"没有可撤销的已执行副作用"。
        """
        (sandbox / "note.txt").write_text("原始内容\n", encoding="utf-8")

        await self._write(sandbox, "note.txt", "被改坏的内容\n")
        assert (sandbox / "note.txt").read_text(encoding="utf-8") == "被改坏的内容\n"
        rec = get_journal().last(STATE_APPLIED)
        assert rec is not None, "本地写必须进账本，否则用户无从撤销"
        assert rec.undo_spec.get("kind") == UNDO_FILES, "应走文件级，而不是整轮检查点"
        assert rec.undoable is True

        ok, _note = undo_record(rec, settings=_settings(), journal=get_journal())
        assert ok is True
        assert (sandbox / "note.txt").read_text(encoding="utf-8") == "原始内容\n"

    async def test_receipt_tells_user_how_to_undo(self, sandbox):
        """回执里要带"能反悔"的线索：模型看得见才不至于写错后只会道歉。"""
        (sandbox / "r.txt").write_text("原文\n", encoding="utf-8")
        reg = ToolRegistry()
        reg.register(_WriteTool())
        orch = ToolOrchestrator(reg, _AllowAll())
        ctx = ToolContext(workdir=str(sandbox), messages=[], settings=_settings(), extra={})
        results = await orch.execute_calls(
            [ToolUseContent(id="t1", name="FileWrite",
                            input={"file_path": "r.txt", "content": "改了\n"})],
            ctx,
        )
        assert "/undo" in results[0].content
        assert "留底" in results[0].content

    async def test_new_file_effect_undo_deletes_it(self, sandbox):
        await self._write(sandbox, "brand_new.txt", "新建\n")
        assert (sandbox / "brand_new.txt").exists()

        rec = get_journal().last(STATE_APPLIED)
        ok, _note = undo_record(rec, settings=_settings(), journal=get_journal())
        assert ok is True
        assert not (sandbox / "brand_new.txt").exists()

    async def test_read_only_call_records_nothing(self, sandbox):
        """只读工具即便带了 file_path 也不记账、不留底。"""
        class _ReadTool(_WriteTool):
            name = "FileRead"

            def is_read_only(self, args: dict) -> bool:
                return True

            def is_destructive(self, args: dict) -> bool:
                return False

        (sandbox / "x.txt").write_text("原文", encoding="utf-8")
        reg = ToolRegistry()
        reg.register(_ReadTool())
        orch = ToolOrchestrator(reg, _AllowAll())
        ctx = ToolContext(workdir=str(sandbox), messages=[], settings=_settings(), extra={})
        await orch.execute_calls(
            [ToolUseContent(id="t1", name="FileRead", input={"file_path": "x.txt"})],
            ctx,
        )
        assert get_journal().last(STATE_APPLIED) is None


class TestDescribeSnapshot:
    """回执文案：只描述事实，取不到就不说。"""

    def test_mentions_file_and_command(self, sandbox):
        target = sandbox / "d.txt"
        target.write_text("x", encoding="utf-8")
        snap = snapshot_before_write(
            "FileWrite", {"file_path": str(target)}, read_only=False, destructive=True, settings=_settings(),
        )
        text = describe_snapshot(snap, _settings())
        assert "d.txt" in text
        assert "/undo" in text

    def test_empty_for_missing_snapshot(self):
        assert describe_snapshot("", _settings()) == ""
        assert describe_snapshot("20260101_000000_nope00", _settings()) == ""
