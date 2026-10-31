"""隔离区与可撤销清理：移动、跳过原因、还原、过期清理。

核心风险：这是子系统里**唯一真正动用户文件**的地方。所以每条测试都在钉
"什么情况下它必须不动 / 必须如实报告"。

全部在 tmp_path 里跑，不碰任何真实目录。

@author aceFelix
"""

from __future__ import annotations

import time
from pathlib import Path

from agent.core.maintenance import cleaner as cl
from agent.core.maintenance.rules import RuleBook, parse_rule


def _rule(path: Path, **over) -> object:
    data = {
        "id": "r1", "name": "R", "kind": "paths", "paths": [str(path)],
        "category": "temp", "risk": "low", "requires_admin": False, "desc": "d",
    }
    data.update(over)
    return parse_rule(data)


def _book(*rules) -> RuleBook:
    return RuleBook(rules=list(rules), rejected=[], source="test")


def _tree(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.tmp").write_bytes(b"a" * 1024)
    (root / "keep.log").write_bytes(b"k" * 2048)
    sub = root / "sub"
    sub.mkdir()
    (sub / "b.tmp").write_bytes(b"b" * 4096)
    return root


# ---------------------------------------------------------------------------
# 清理
# ---------------------------------------------------------------------------


def test_clean_requires_explicit_ids(tmp_path):
    """不接受「全清」——这是这类软件最容易埋雷的地方。"""
    junk = _tree(tmp_path / "junk")
    result = cl.clean(_book(_rule(junk)), [], quarantine_root=tmp_path / "q")
    assert result.error and "显式" in result.error
    assert (junk / "a.tmp").exists(), "报错时必须一个文件都没动"


def test_clean_moves_content_but_keeps_directory(tmp_path):
    """只清内容、保留目录：Temp 这类目录消失后 Windows 不会重建，只会报怪错。"""
    junk = _tree(tmp_path / "junk")
    q = tmp_path / "q"
    # 带 protect 才能同时验证两件事：目录保留 + 保护规则生效
    result = cl.clean(
        _book(_rule(junk, protect=["*.log"])), ["r1"], quarantine_root=q
    )
    assert result.reclaimed_bytes == 1024 + 4096
    assert junk.is_dir(), "目录本身必须保留"
    assert sorted(p.name for p in junk.iterdir()) == ["keep.log"]
    assert result.undo_hint and "/clean --restore" in result.undo_hint


def test_clean_without_protect_moves_everything(tmp_path):
    """没有 protect 时目录内容全部移走（三个顶层项：a.tmp / keep.log / sub）。"""
    junk = _tree(tmp_path / "junk")
    result = cl.clean(_book(_rule(junk)), ["r1"], quarantine_root=tmp_path / "q")
    assert result.reclaimed_bytes == 1024 + 2048 + 4096
    assert list(junk.iterdir()) == []


def test_clean_writes_manifest(tmp_path):
    junk = _tree(tmp_path / "junk")
    q = tmp_path / "q"
    result = cl.clean(_book(_rule(junk)), ["r1"], quarantine_root=q)
    manifest = q / result.batch_id / "manifest.json"
    assert manifest.exists()
    data = cl.read_manifest(q / result.batch_id)
    # a.tmp / keep.log / sub 三个顶层项各一条
    assert data and len(data["entries"]) == 3
    for entry in data["entries"]:
        assert Path(entry["stored"]).exists(), "清单里的 stored 必须真实存在"


def test_protect_pattern_skips_with_reason(tmp_path):
    """跳过要说原因——只报「成功 xx MB」而不说「还有 300 个没删」，等于骗人。"""
    junk = _tree(tmp_path / "junk")
    result = cl.clean(
        _book(_rule(junk, protect=["*.log"])),
        ["r1"], quarantine_root=tmp_path / "q",
    )
    assert (junk / "keep.log").exists()
    reasons = [s.reason for s in result.skipped]
    assert any("保护规则" in r for r in reasons)


def test_unknown_id_reported_not_raised(tmp_path):
    result = cl.clean(_book(), ["nope"], quarantine_root=tmp_path / "q")
    assert any("未知规则" in str(i.get("status")) for i in result.items)


def test_risk_above_cap_is_refused(tmp_path):
    junk = _tree(tmp_path / "junk")
    result = cl.clean(
        _book(_rule(junk, risk="high")), ["r1"],
        quarantine_root=tmp_path / "q", max_risk="low",
    )
    assert (junk / "a.tmp").exists(), "超档规则绝不能动文件"
    assert any("超过当前允许上限" in str(i.get("status")) for i in result.items)


def test_command_rule_refused_here(tmp_path):
    """命令型不可还原，绝不从这个入口执行。"""
    rule = parse_rule({
        "id": "cmd", "name": "C", "kind": "command", "paths": None,
        "clean_cmd": ["dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"],
        "category": "system", "risk": "medium", "requires_admin": True, "desc": "d",
    })
    result = cl.clean(_book(rule), ["cmd"], quarantine_root=tmp_path / "q")
    assert any("命令型" in str(i.get("status")) for i in result.items)


def test_quarantine_inside_paths_is_refused(tmp_path):
    """隔离区自己绝不能进清理范围，否则撤不回来。"""
    q = tmp_path / "q"
    q.mkdir()
    (q / "already").write_bytes(b"x" * 10)
    result = cl.clean(_book(_rule(q)), ["r1"], quarantine_root=q)
    assert (q / "already").exists()


# ---------------------------------------------------------------------------
# 还原
# ---------------------------------------------------------------------------


def test_restore_roundtrip(tmp_path):
    junk = _tree(tmp_path / "junk")
    q = tmp_path / "q"
    result = cl.clean(_book(_rule(junk)), ["r1"], quarantine_root=q)
    assert not (junk / "a.tmp").exists()

    back = cl.restore(result.batch_id, quarantine_root=q)
    assert back.restored == 3
    assert (junk / "a.tmp").exists()
    assert (junk / "sub" / "b.tmp").exists()
    assert (junk / "keep.log").exists()


def test_restore_does_not_overwrite_existing(tmp_path):
    """目标位置已有同名文件时跳过：覆盖等于用旧内容盖掉新内容。"""
    junk = _tree(tmp_path / "junk")
    q = tmp_path / "q"
    result = cl.clean(_book(_rule(junk)), ["r1"], quarantine_root=q)
    (junk / "a.tmp").write_bytes(b"NEW")
    back = cl.restore(result.batch_id, quarantine_root=q)
    assert (junk / "a.tmp").read_bytes() == b"NEW"
    assert any("未覆盖" in s.reason for s in back.skipped)


def test_restore_unknown_batch(tmp_path):
    back = cl.restore("clean-nope", quarantine_root=tmp_path / "q")
    assert back.error and "不存在" in back.error


def test_restore_broken_manifest(tmp_path):
    q = tmp_path / "q"
    batch = q / "clean-20260101-000000"
    batch.mkdir(parents=True)
    (batch / "manifest.json").write_text("{not json", encoding="utf-8")
    back = cl.restore("clean-20260101-000000", quarantine_root=q)
    assert back.error and "损坏" in back.error


def test_batch_dropped_after_full_restore(tmp_path):
    junk = _tree(tmp_path / "junk")
    q = tmp_path / "q"
    result = cl.clean(_book(_rule(junk)), ["r1"], quarantine_root=q)
    cl.restore(result.batch_id, quarantine_root=q)
    assert not (q / result.batch_id).exists(), "全部还原后空壳批次应清掉"
    assert cl.list_batches(q) == []


# ---------------------------------------------------------------------------
# 批次列表与过期清理
# ---------------------------------------------------------------------------


def test_list_batches_reports_broken(tmp_path):
    q = tmp_path / "q"
    ok = q / "clean-20260102-000000"
    ok.mkdir(parents=True)
    (ok / "manifest.json").write_text(
        '{"batch_id":"clean-20260102-000000","created_human":"x","entries":[]}',
        encoding="utf-8",
    )
    broken = q / "clean-20260101-000000"
    broken.mkdir(parents=True)
    rows = cl.list_batches(q)
    assert len(rows) == 2
    assert rows[0]["batch_id"] == "clean-20260102-000000"   # 新→旧
    assert any(r["broken"] for r in rows)


def test_purge_expired_removes_old_batches(tmp_path):
    q = tmp_path / "q"
    old = q / "clean-20200101-000000"
    old.mkdir(parents=True)
    (old / "f.txt").write_bytes(b"x")
    stale = time.time() - 30 * 86400
    import os

    os.utime(old, (stale, stale))
    removed = cl.purge_expired(7, quarantine_root=q)
    assert removed == 1
    assert not old.exists()


def test_purge_expired_keeps_fresh(tmp_path):
    q = tmp_path / "q"
    fresh = q / "clean-20260101-000000"
    fresh.mkdir(parents=True)
    assert cl.purge_expired(7, quarantine_root=q) == 0
    assert fresh.exists()


def test_purge_expired_zero_days_is_noop(tmp_path):
    q = tmp_path / "q"
    (q / "b").mkdir(parents=True)
    assert cl.purge_expired(0, quarantine_root=q) == 0
    assert (q / "b").exists()


def test_make_batch_id_format():
    bid = cl.make_batch_id()
    assert bid.startswith("clean-")
    assert len(bid) == len("clean-20260101-000000")