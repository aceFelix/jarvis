"""磁盘扫描器：体积统计、三道性能闸、protect/include、目录排行。

核心风险：扫描是只读的，但**只读也可能把机器拖死**（Windows.old 十几万文件）。
所以这里重点钉住「超时 / 体积上限 / 不跟随符号链接」这三道闸真的生效。

@author aceFelix
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.core.maintenance import scanner as sc
from agent.core.maintenance.rules import CleanerRule


def _rule(paths: list[str], **over) -> CleanerRule:
    data = {
        "id": "r1", "name": "R", "kind": "paths", "paths": paths,
        "category": "temp", "risk": "low", "requires_admin": False, "desc": "d",
    }
    data.update(over)
    from agent.core.maintenance.rules import parse_rule

    return parse_rule(data)


def _make_tree(root: Path) -> Path:
    """造一棵小树：root/a.txt(1KB) root/sub/b.txt(2KB) root/skip.log(4KB)"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.txt").write_bytes(b"a" * 1024)
    (root / "skip.log").write_bytes(b"l" * 4096)
    sub = root / "sub"
    sub.mkdir()
    (sub / "b.txt").write_bytes(b"b" * 2048)
    return root


def test_human_size():
    assert sc.human_size(0) == "0 B"
    assert sc.human_size(1024) == "1.0 KB"
    assert sc.human_size(1024 ** 3 * 2) == "2.0 GB"


def test_scan_rule_counts_recursively(tmp_path):
    tree = _make_tree(tmp_path / "junk")
    rule = _rule([str(tree)])
    item = sc.scan_rule(rule, deadline=sc._Deadline(30), max_bytes=10 ** 9)
    assert item.size_bytes == 1024 + 4096 + 2048
    assert item.file_count == 3
    assert item.unaddressable == ""
    assert item.truncated == ""


def test_protect_hits_are_counted_not_sized(tmp_path):
    """命中 protect 的文件不计入体积，但要单独计数——报告得说清「没删干净是故意的」。"""
    tree = _make_tree(tmp_path / "junk")
    rule = _rule([str(tree)], protect=["*.log"])
    item = sc.scan_rule(rule, deadline=sc._Deadline(30), max_bytes=10 ** 9)
    assert item.size_bytes == 1024 + 2048
    assert item.file_count == 2
    assert item.protected_count == 1


def test_include_filters_files(tmp_path):
    tree = _make_tree(tmp_path / "junk")
    rule = _rule([str(tree)], include=["*.txt"])
    item = sc.scan_rule(rule, deadline=sc._Deadline(30), max_bytes=10 ** 9)
    assert item.file_count == 2
    assert item.size_bytes == 1024 + 2048


def test_missing_path_marks_unaddressable(tmp_path):
    """路径不存在不是错误，是「这台机器没有」——要在报告里如实列出来。"""
    rule = _rule([str(tmp_path / "does-not-exist")])
    item = sc.scan_rule(rule, deadline=sc._Deadline(30), max_bytes=10 ** 9)
    assert item.unaddressable == sc.REASON_ABSENT
    assert item.size_bytes == 0


def test_max_bytes_gate_truncates(tmp_path):
    tree = _make_tree(tmp_path / "junk")
    rule = _rule([str(tree)])
    item = sc.scan_rule(rule, deadline=sc._Deadline(30), max_bytes=1024)
    assert item.truncated, "超过体积上限必须标截断"
    assert item.size_bytes >= 1024


def test_deadline_gate_truncates(tmp_path):
    """超时闸：deadline 已过期时立刻停，并标记原因。"""
    tree = _make_tree(tmp_path / "junk")
    deadline = sc._Deadline(0)
    rule = _rule([str(tree)])
    item = sc.scan_rule(rule, deadline=deadline, max_bytes=10 ** 9)
    assert item.truncated


@pytest.mark.skipif(os.name != "nt", reason="符号链接创建需要 Windows 特权或开发者模式")
def test_symlink_is_not_followed(tmp_path):
    """跟进去会重复计数，更糟的是可能绕到 C:\\Windows 上把 1 MB 报成 20 GB。"""
    target = tmp_path / "outside"
    target.mkdir()
    (target / "big.bin").write_bytes(b"x" * 8192)
    root = tmp_path / "root"
    root.mkdir()
    (root / "small.txt").write_bytes(b"s" * 100)
    try:
        os.symlink(target, root / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("本机不允许创建符号链接")
    rule = _rule([str(root)])
    item = sc.scan_rule(rule, deadline=sc._Deadline(30), max_bytes=10 ** 9)
    assert item.size_bytes == 100, "符号链接目标不该被计入"


def test_scan_all_min_size_filters_but_keeps_unaddressable(tmp_path):
    tree = _make_tree(tmp_path / "junk")
    book = _book([
        _rule([str(tree)], id="small"),
        _rule([str(tmp_path / "absent")], id="absent"),
    ])
    result = sc.scan_all(book, min_size_mb=100.0, timeout_s=30)
    ids = {i.id for i in result.items}
    assert "small" not in ids, "低于阈值的小项应被过滤"
    assert "absent" in ids, "路径不存在必须保留（否则用户以为规则没生效）"


def _book(rules: list[CleanerRule]):
    from agent.core.maintenance.rules import RuleBook

    return RuleBook(rules=list(rules), rejected=[], source="test")


def test_scan_all_respects_scope(tmp_path):
    tree = _make_tree(tmp_path / "junk")
    book = _book([
        _rule([str(tree)], id="a"),
        _rule([str(tree)], id="b"),
    ])
    result = sc.scan_all(book, scope=["b"], min_size_mb=0, timeout_s=30)
    assert [i.id for i in result.items] == ["b"]


def test_describe_commands_lists_only_command_rules():
    from agent.core.maintenance.rules import load_rulebook

    rows = sc.describe_commands(load_rulebook())
    ids = {r["id"] for r in rows}
    assert "winsxs_cleanup" in ids
    assert "user_temp" not in ids
    # 实测（ 2026-10-31 本机）：这行原本只会词匹配「不可用」，结果 note 里把还原命令
    # 写成 ``/undo`` 也能通过。真正要害的是**命令名不能错**：隔离区的唯一还原入口是
    # ``/clean --restore``，``/undo`` 走 W3 副作用账本，敲了注定落空。
    for r in rows:
        assert "还原" in r["note"]
        assert "/undo" not in r["note"]


def test_describe_commands_carries_exact_command():
    """每行都要带上要敲的命令：只报问题不给路的清单等于没用。"""
    from agent.core.maintenance.rules import load_rulebook

    rows = sc.describe_commands(load_rulebook())
    assert rows
    for row in rows:
        assert row["clean_cmd"].strip(), f"{row['id']} 缺 clean_cmd"
        assert row["clean_cmd"].split()[0].lower() in {
            "dism.exe", "vssadmin.exe", "powercfg.exe", "cleanmgr.exe"
        }


def test_describe_commands_note_reflects_analyze_support():
    """有 analyze_cmd 的说"需执行分析"，没分析模式的说"无分析模式"。"""
    from agent.core.maintenance.rules import load_rulebook

    by_id = {r["id"]: r for r in sc.describe_commands(load_rulebook())}
    assert "分析命令" in by_id["winsxs_cleanup"]["note"]
    assert "无分析模式" in by_id["cleanmgr_profile"]["note"]


def test_top_dirs_sorted_and_filtered(tmp_path):
    big = tmp_path / "big"
    big.mkdir()
    (big / "x.bin").write_bytes(b"x" * 300_000)
    small = tmp_path / "small"
    small.mkdir()
    (small / "y.bin").write_bytes(b"y" * 1000)
    (tmp_path / "loose.txt").write_bytes(b"z" * 500)
    rows = sc.top_dirs(str(tmp_path), limit=10, timeout_s=30, min_size_mb=0.0)
    names = [r.name for r in rows]
    assert names[0] == "big"
    assert "loose.txt" not in names, "只统计子目录，文件不算"
    if len(rows) > 1:
        assert rows[0].size_bytes >= rows[1].size_bytes


def test_top_dirs_missing_root_returns_empty(tmp_path):
    assert sc.top_dirs(str(tmp_path / "nope")) == []


def test_disk_trend_returns_none_when_insufficient(monkeypatch, tmp_path):
    """样本不足必须返回 None——编一个 0 会让人误以为没在涨。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".jarvis").mkdir()
    hist = tmp_path / ".jarvis" / "monitor_history.json"
    hist.write_text('[{"date":"2026-10-30","disk_free_gb":10.0}]', encoding="utf-8")
    assert sc.disk_trend_7d_gb() is None


def test_disk_trend_computes_growth(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".jarvis").mkdir()
    hist = tmp_path / ".jarvis" / "monitor_history.json"
    hist.write_text(
        '[{"date":"2026-10-25","disk_free_gb":20.0},'
        '{"date":"2026-10-27","disk_free_gb":17.0},'
        '{"date":"2026-10-30","disk_free_gb":14.0}]',
        encoding="utf-8",
    )
    # 剩余空间从 20 掉到 14 → 占用涨了 6 GB
    assert sc.disk_trend_7d_gb() == 6.0


def test_trend_text_never_leaves_direction_ambiguous():
    """符号必须翻成人话：实测里 ``-1.58`` 被模型读成「空间又少了」（方向反了）。

    裸数字最大的问题是字段名没说是「已用增」还是「可用减」。这个用例钉死三件事：
    正数=占用增长、负数=占用下降、样本不足不许编。
    """
    up = sc.DriveInfo(mount="C:", total_gb=100.0, free_gb=10.0,
                      used_percent=90.0, trend_7d_gb=1.58)
    down = sc.DriveInfo(mount="C:", total_gb=100.0, free_gb=14.3,
                        used_percent=85.7, trend_7d_gb=-1.58)
    unknown = sc.DriveInfo(mount="C:", total_gb=100.0, free_gb=14.3,
                           used_percent=85.7)

    assert "占用增长" in up.trend_text()
    assert "占用下降" in down.trend_text()
    # 负数还得多说一句结论，否则光看到「下降」仍可能被理解成「可用空间下降」
    assert "可用变多" in down.trend_text()
    assert unknown.trend_text() == ""
    assert up.to_dict()["trend_7d_text"] == up.trend_text()


def test_drive_infos_returns_at_least_one():
    drives = sc.drive_infos()
    assert drives, "至少应有一块盘"
    for d in drives:
        assert d.total_gb > 0
        assert 0 <= d.used_percent <= 100