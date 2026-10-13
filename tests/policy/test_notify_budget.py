"""打扰预算单测 —— 五步闸门与重要度评分（阶段 8 验收②）。

覆盖：静默时段只入库、时段结束后聚合冲刷成一条、同源窗口合并、
低重要度降级、日预算上限、跨天重置、接受率闭环、跨午夜静默区间。
时间一律显式传 now，测试不依赖真实时钟。

@author aceFelix
"""

from __future__ import annotations

from datetime import datetime

from agent.core.policy.budget import BudgetConfig, NotifyBudget

MORNING = datetime(2026, 8, 3, 9, 0)      # 非静默
NIGHT = datetime(2026, 8, 3, 23, 30)      # 静默（23:00-07:00 内）
LATE_NIGHT = datetime(2026, 8, 4, 2, 0)   # 跨午夜仍在静默
NEXT_MORNING = datetime(2026, 8, 4, 9, 0)


def _budget(policy_paths, **overrides) -> NotifyBudget:
    cfg_kwargs = dict(
        daily_max=3, quiet_hours="23:00-07:00",
        aggregate_window_s=300, min_salience=0.3,
    )
    cfg_kwargs.update(overrides)
    return NotifyBudget(BudgetConfig(**cfg_kwargs), path=policy_paths["budget"])


def test_emit_in_normal_window(policy_paths):
    """非静默、高分、额度内 → 照播并计数。"""
    b = _budget(policy_paths)
    a = b.gate("早上好", "briefing", urgency=0.9, now=MORNING)
    assert a.kind == "emit"
    assert b.stats()["count"] == 1


def test_quiet_hours_queue(policy_paths):
    """静默时段只入库不播。"""
    b = _budget(policy_paths)
    a = b.gate("深夜提醒", "deadline", urgency=0.8, now=NIGHT)
    assert a.kind == "queue"
    assert b.pending_count() == 1
    assert b.stats()["count"] == 0


def test_quiet_cross_midnight(policy_paths):
    """跨午夜区间（23:00-07:00）：凌晨 2 点仍算静默。"""
    b = _budget(policy_paths)
    assert b._in_quiet(LATE_NIGHT) is True


def test_flush_aggregates_pending(policy_paths):
    """静默期积压多条，时段结束后一次聚合冲刷成一条播出。"""
    b = _budget(policy_paths)
    b.gate("提醒一", "deadline", dedup_key="d1", now=NIGHT)
    b.gate("提醒二", "calendar", dedup_key="d2", now=LATE_NIGHT)
    a = b.gate("新提醒", "system", dedup_key="d3", urgency=0.9, now=NEXT_MORNING)
    # 第一条非静默调用先冲刷积压（聚合一条），本条本身暂未播
    assert a.kind == "emit"
    assert "积压 2 条" in a.text
    assert b.pending_count() == 0


def test_aggregate_window_suppress(policy_paths):
    """同源事件窗口内已播 → 合并抑制，不重复打扰。"""
    b = _budget(policy_paths)
    b.gate("CPU 高", "system", dedup_key="sys", urgency=0.9, now=MORNING)
    later = MORNING.replace(minute=2)
    a = b.gate("CPU 高", "system", dedup_key="sys", urgency=0.9, now=later)
    assert a.kind == "suppress"
    assert b.stats()["count"] == 1  # 抑制不计数


def test_low_salience_queued(policy_paths):
    """重要度低于门槛 → 入库暂缓（不占用播报额度）。"""
    b = _budget(policy_paths)
    a = b.gate("随便说说", "default", urgency=0.1, now=MORNING)  # 0.5*0.1=0.05 < 0.3
    assert a.kind == "queue"
    assert b.stats()["count"] == 0


def test_daily_max_suppress(policy_paths):
    """超出每日上限 → suppress。"""
    b = _budget(policy_paths, daily_max=2)
    for i in range(2):
        a = b.gate(f"播{i}", "briefing", dedup_key=f"b{i}", urgency=0.9, now=MORNING)
        assert a.kind == "emit"
    a = b.gate("播3", "briefing", dedup_key="b3", urgency=0.9, now=MORNING)
    assert a.kind == "suppress"
    assert "上限" in a.reason


def test_day_rollover_resets(policy_paths):
    """跨天重置当日计数与 last_emit。"""
    b = _budget(policy_paths, daily_max=1)
    assert b.gate("今", "briefing", urgency=0.9, now=MORNING).kind == "emit"
    tomorrow = datetime(2026, 8, 4, 9, 0)
    a = b.gate("昨同源", "briefing", dedup_key="briefing:昨同源", urgency=0.9, now=tomorrow)
    assert a.kind == "emit"  # 新的一天，额度与窗口都重置
    assert b.stats()["count"] == 1


def test_accept_rate_closes_loop(policy_paths):
    """用户持续忽略 → 接受率下滑 → 同源事件最终降级入库。"""
    b = _budget(policy_paths, min_salience=0.3)
    for _ in range(4):
        b.record_accept("default", accepted=False)
    # default 权重 0.5 × urgency 0.9 = 0.45 → ×接受率 0.0 → 低于门槛
    a = b.gate("系统碎碎念", "default", urgency=0.9, now=MORNING)
    assert a.kind == "queue"


def test_state_file_isolated(policy_paths):
    """预算状态独立落盘，文件不存在（首启）不报错。"""
    b = _budget(policy_paths)
    assert not policy_paths["budget"].exists()
    assert b.stats()["count"] == 0
    b.gate("hi", "briefing", urgency=0.9, now=MORNING)
    assert policy_paths["budget"].exists()
