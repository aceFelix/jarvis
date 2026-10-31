"""无人值守自动清理：许可来源、阈值、可还原性与调度。

这一层的核心风险不是「删错」，而是**悄悄删**。所以每条测试都在钉两件事：

1. 只有显式标了 ``auto_ok`` 的规则才会被自动清——`risk=low` 不算许可
   （`recycle_bin` / `user_temp` 也是 low，见 auto_clean 模块头注释）
2. 自动清掉的东西**必须能还原**。隔离区是本模块唯一的底气，它一旦失效，
   “零感知清理”就变成了“零感知删除”

全部在 tmp_path 里跑，不碰任何真实目录，也不碰真实的 ~/.jarvis 状态文件。

@author aceFelix
"""

from __future__ import annotations

import time
from pathlib import Path

from agent.core.maintenance import auto_clean as ac
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


def _tree(root: Path, size_kb: int = 64) -> Path:
    """造一个含内容的目录（size_kb 控制单文件大小，用于测阈值）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.dmp").write_bytes(b"a" * (size_kb * 1024))
    return root


def _isolate_state(monkeypatch, tmp_path) -> Path:
    """把状态文件改到 tmp：不能让测试改写用户真实的 last_auto_clean_ts。"""
    state = tmp_path / "state" / "maintenance_state.json"
    monkeypatch.setattr(ac, "_state_path", lambda: state)
    return state


# ---------------------------------------------------------------------------
# 许可来源：auto_ok 是独立字段，不复用 risk
# ---------------------------------------------------------------------------


def test_risk_low_alone_is_not_an_auto_permit(tmp_path):
    """``risk=low`` 不等于允许自动清——这是整层最重要的判断。

    真实规则库里 ``recycle_bin`` 和 ``user_temp`` 都是 low，但前者装着用户
    刚删的文件、后者可能正被程序占用。把它们当自动许可，等于拿风险等级
    顶替用户意愿。
    """
    low = _rule(tmp_path / "junk", id="plain_low", risk="low")
    marked = _rule(tmp_path / "other", id="marked", risk="low", auto_ok=True)
    book = _book(low, marked)

    assert ac.auto_rule_ids(book) == ["marked"]


def test_auto_ok_rejected_for_command_rules(tmp_path):
    """命令型永不自动执行：DISM/cleanmgr 不可还原，隔离区救不了它们。"""
    import pytest

    with pytest.raises(ValueError) as ei:
        parse_rule({
            "id": "cmd", "name": "C", "kind": "command",
            "clean_cmd": ["dism.exe", "/Online"], "category": "system",
            "risk": "low", "requires_admin": False, "desc": "d", "auto_ok": True,
        })
    assert "命令型" in str(ei.value)


def test_auto_ok_rejected_for_admin_rules(tmp_path):
    """需管理员的不能自动跑：后台进程提不了权，只会白跑一趟。"""
    import pytest

    with pytest.raises(ValueError) as ei:
        parse_rule({
            "id": "adm", "name": "A", "kind": "paths",
            "paths": [str(tmp_path)], "category": "system",
            "risk": "low", "requires_admin": True, "desc": "d", "auto_ok": True,
        })
    assert "管理员" in str(ei.value)


def test_explain_rules_gives_reason_for_every_rule(tmp_path):
    """每条规则都要有理由：用户问「为什么不自动清这条」时不该让他去读 TOML。"""
    book = _book(
        _rule(tmp_path / "j", id="cmd_like", kind="paths", auto_ok=False),
        _rule(tmp_path / "k", id="admin", requires_admin=True),
        _rule(tmp_path / "m", id="ok_one", auto_ok=True),
    )
    rows = {r["id"]: r for r in ac.explain_rules(book)}
    assert rows["ok_one"]["ok"] is True
    assert "auto_ok" in rows["cmd_like"]["reason"]
    assert "管理员" in rows["admin"]["reason"]
    assert all(r["reason"] for r in rows.values())


# ---------------------------------------------------------------------------
# 阈值：低收益不建批次
# ---------------------------------------------------------------------------


def test_below_threshold_skips_without_batch(tmp_path, monkeypatch):
    """低于阈值不该建隔离区批次——零感知垃圾常只有几 MB。

    天天建一个批次目录、写一份 manifest、再 7 天后删一遍，成本高于收益，
    还会把 /clean --batches 刷满噪音。
    """
    _isolate_state(monkeypatch, tmp_path)
    junk = _tree(tmp_path / "small", size_kb=1)     # ~1 KB
    book = _book(_rule(junk, id="tiny", auto_ok=True))

    result = ac.run_auto_clean(book, min_mb=50.0, keep_days=7)

    assert result.ran is False
    assert "阈值" in result.reason
    assert (junk / "a.dmp").exists(), "跳过时必须一个文件都没动"


def test_above_threshold_cleans_and_reports(tmp_path, monkeypatch):
    """超过阈值就真清，并把批次号带回给调用方。"""
    _isolate_state(monkeypatch, tmp_path)
    junk = _tree(tmp_path / "big", size_kb=2048)    # ~2 MB
    book = _book(_rule(junk, id="big_one", auto_ok=True))

    result = ac.run_auto_clean(
        book, min_mb=0.0, quarantine_root=tmp_path / "q"
    )

    assert result.ran is True
    assert result.reclaimed_mb > 0
    assert result.batch_id
    assert not (junk / "a.dmp").exists()
    # 内容必须在隔离区里，而不是在原地消失（隔离区文件名带序号前缀：<n>_a.dmp）
    assert list((tmp_path / "q" / result.batch_id).rglob("*a.dmp"))


# ---------------------------------------------------------------------------
# 可还原：自动清掉的东西必须拿得回来
# ---------------------------------------------------------------------------


def test_auto_cleaned_content_is_restorable(tmp_path, monkeypatch):
    """**本层最关键的断言**：自动清理走隔离区，不是删除。

    这条失效的话，“零感知清理”就变成了“零感知删除”——用户永远不会发现，
    也永远拿不回来。
    """
    _isolate_state(monkeypatch, tmp_path)
    junk = _tree(tmp_path / "big", size_kb=2048)
    book = _book(_rule(junk, id="big_one", auto_ok=True))
    qroot = tmp_path / "q"

    result = ac.run_auto_clean(book, min_mb=0.0, quarantine_root=qroot)
    assert result.ran is True
    assert not (junk / "a.dmp").exists()

    restored = cl.restore(result.batch_id, quarantine_root=qroot)

    assert restored.error == "", f"还原失败：{restored.error}"
    assert (junk / "a.dmp").exists(), "自动清掉的文件必须能原样还原"


# ---------------------------------------------------------------------------
# 状态与到期判断
# ---------------------------------------------------------------------------


def test_state_written_after_real_run(tmp_path, monkeypatch):
    """跑过才写状态：没跑（低于阈值）不该更新 last_ts，否则会一直以为刚清过。"""
    state = _isolate_state(monkeypatch, tmp_path)
    small = _tree(tmp_path / "small", size_kb=1)
    book = _book(_rule(small, id="tiny", auto_ok=True))

    ac.run_auto_clean(book, min_mb=50.0, quarantine_root=tmp_path / "q")
    assert not state.exists(), "跳过时不该写状态"

    big = _tree(tmp_path / "big", size_kb=2048)
    ac.run_auto_clean(
        _book(_rule(big, id="big_one", auto_ok=True)),
        min_mb=0.0, quarantine_root=tmp_path / "q",
    )
    assert state.exists()
    assert isinstance(ac.read_state().get("last_auto_clean_ts"), float)


def test_is_due_respects_interval(tmp_path, monkeypatch):
    """间隔内不重复触发：否则每次开 jarvis 都清一遍，很突兀。"""
    _isolate_state(monkeypatch, tmp_path)
    now = time.time()

    assert ac.is_due(now=now, interval_h=20) is True, "没跑过 = 到点"

    ac.write_state(last_auto_clean_ts=now - 3600)          # 1 小时前
    assert ac.is_due(now=now, interval_h=20) is False

    ac.write_state(last_auto_clean_ts=now - 21 * 3600)     # 21 小时前
    assert ac.is_due(now=now, interval_h=20) is True


def test_broken_state_file_is_tolerated(tmp_path, monkeypatch):
    """状态文件坏了当作空状态：最多多跑一次清理，不该让启动流程崩掉。"""
    state = _isolate_state(monkeypatch, tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{ this is not json", encoding="utf-8")

    assert ac.read_state() == {}
    assert ac.is_due(interval_h=20) is True


# ---------------------------------------------------------------------------
# 失败不抛
# ---------------------------------------------------------------------------


def test_no_candidates_is_not_an_error():
    """一条 auto_ok 都没有时安静返回，不抛——自动清理在后台线程里跑。"""
    result = ac.run_auto_clean(_book(), min_mb=0.0)
    assert result.ran is False
    assert "auto_ok" in result.reason


def test_scan_failure_is_reported_not_raised(tmp_path, monkeypatch):
    """扫描炸了要把原因写进 reason：后台线程里抛异常只会被当成故障吞掉。"""
    _isolate_state(monkeypatch, tmp_path)
    book = _book(_rule(tmp_path / "j", id="x", auto_ok=True))

    def boom(*a, **k):
        raise RuntimeError("扫描炸了")

    monkeypatch.setattr(ac, "scan_all", boom)
    result = ac.run_auto_clean(book, min_mb=0.0)

    assert result.ran is False
    assert "RuntimeError" in result.reason
    assert "扫描炸了" in result.reason


def test_summary_is_human_readable(tmp_path, monkeypatch):
    """摘要要能直接进日志/播报，不能是一串 repr。"""
    _isolate_state(monkeypatch, tmp_path)
    big = _tree(tmp_path / "big", size_kb=2048)
    result = ac.run_auto_clean(
        _book(_rule(big, id="big_one", auto_ok=True)), min_mb=0.0,
        quarantine_root=tmp_path / "q",
    )
    text = result.summary()
    assert "自动清理" in text
    assert "big_one" in text


# ---------------------------------------------------------------------------
# 调度：注册、判定、另起线程
# ---------------------------------------------------------------------------


def test_is_auto_clean_task_only_matches_own_note():
    """任务判定不能误伤用户自己的提醒（那些要播报，不该被静默执行）。"""
    from agent.core.daemon import auto_clean_schedule as acs
    from agent.core.daemon.scheduler import ScheduleTask

    mine = ScheduleTask(content="自动清理", trigger_at="2026-11-01T04:30:00",
                        note=acs.AUTO_CLEAN_NOTE)
    user = ScheduleTask(content="开会", trigger_at="2026-11-01T09:00:00", note="")

    assert acs.is_auto_clean_task(mine) is True
    assert acs.is_auto_clean_task(user) is False


def test_register_is_idempotent(tmp_path, monkeypatch):
    """重复注册不能积出多个任务——否则一天会被清好几次。

    Scheduler 的持久化文件也改到 tmp：它默认写 ~/.jarvis/schedule.json，
    测试绝不能把假任务写进用户真实的行程表。
    """
    from agent.core.daemon import auto_clean_schedule as acs
    from agent.core.daemon import scheduler as sched_mod

    monkeypatch.setattr(sched_mod, "_schedule_file", lambda: tmp_path / "schedule.json")
    s = sched_mod.Scheduler(on_fire=lambda t: None)

    first = acs.register(s, time_str="04:30")
    second = acs.register(s, time_str="04:30")

    assert first is not None
    assert second is None, "第二次应当识别到已存在并跳过"
    assert len([t for t in s.list_pending() if t.note == acs.AUTO_CLEAN_NOTE]) == 1


def test_register_schedules_in_the_future(tmp_path, monkeypatch):
    """首次触发必须落在未来：落在过去会在注册瞬被立刻触发一次。"""
    from datetime import datetime

    from agent.core.daemon import auto_clean_schedule as acs
    from agent.core.daemon import scheduler as sched_mod

    monkeypatch.setattr(sched_mod, "_schedule_file", lambda: tmp_path / "schedule.json")
    s = sched_mod.Scheduler(on_fire=lambda t: None)
    task = acs.register(s, time_str="04:30")

    assert task is not None
    assert datetime.fromisoformat(task.trigger_at) > datetime.now()


def test_fire_background_false_returns_result(tmp_path, monkeypatch):
    """同步模式要能把结果带出：`/clean --auto` 与测试都靠它。

    刻意传**空规则库**：`fire()` 默认会加载内置库并真清 crash_dumps / wer_reports，
    在测试里那就是真动用户的磁盘。靶子必须限定在 tmp 里。
    """
    from agent.core.daemon import auto_clean_schedule as acs

    _isolate_state(monkeypatch, tmp_path)
    result = acs.fire(
        min_mb=0.0, book=_book(), quarantine_root=tmp_path / "q",
        background=False,
    )

    assert result is not None
    assert result.ran is False
    assert "auto_ok" in result.reason


def test_startup_run_skipped_within_interval(tmp_path, monkeypatch):
    """刚清过就别在下次开机时再清一遍。"""
    from agent.core.daemon import auto_clean_schedule as acs

    _isolate_state(monkeypatch, tmp_path)
    ac.write_state(last_auto_clean_ts=time.time() - 3600)   # 1 小时前

    thread = acs.run_on_startup_if_due(interval_h=20)

    assert thread is None, "间隔内不该起线程"
