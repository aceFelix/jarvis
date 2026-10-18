"""打扰预算单测 —— 五步闸门与重要度评分（阶段 8 验收②）。

覆盖：静默时段只入库、时段结束后聚合冲刷成一条、同源窗口合并、
低重要度直接抑制、日预算上限、跨天重置、接受率闭环、跨午夜静默区间，
以及 2026-10 走查后的四条新口径（冲刷不占额度、有冷却、单条不套壳、
不吞当前事件）与感知源权重登记，和 2026-10-19 的闸门换序（静默入库
前必须先过门槛）与冲刷按时间重排。
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
    # 冲刷与当前事件**合一返回**：旧实现直接 return 摘要，把“新提醒”整条丢了
    assert a.kind == "emit"
    assert "补播 2 条提醒" in a.text
    assert "新提醒" in a.text
    assert b.pending_count() == 0
    # 摘要本身不占额度，只有当前事件那一次放行计入 count
    assert b.stats()["count"] == 1


def test_aggregate_window_suppress(policy_paths):
    """同源事件窗口内已播 → 合并抑制，不重复打扰。"""
    b = _budget(policy_paths)
    b.gate("CPU 高", "system", dedup_key="sys", urgency=0.9, now=MORNING)
    later = MORNING.replace(minute=2)
    a = b.gate("CPU 高", "system", dedup_key="sys", urgency=0.9, now=later)
    assert a.kind == "suppress"
    assert b.stats()["count"] == 1  # 抑制不计数


def test_low_salience_suppressed_not_queued(policy_paths):
    """重要度低于门槛 → 直接抑制且不入库（旧版“降级入库”会被下一条冲刷出去）。"""
    b = _budget(policy_paths)
    a = b.gate("随便说说", "default", urgency=0.1, now=MORNING)  # 0.5*0.1=0.05 < 0.3
    assert a.kind == "suppress"
    assert "仅留痕" in a.reason
    assert b.pending_count() == 0        # 不进播报队列
    assert b.stats()["count"] == 0
    # 后续高分请求不会把它冲出来（防“入队→冲刷”乒乓回归）
    later = MORNING.replace(minute=30)
    nxt = b.gate("真提醒", "deadline", dedup_key="ok", urgency=0.9, now=later)
    assert nxt.kind == "emit" and "随便说说" not in nxt.text


def test_daily_max_queues_for_later_delivery(policy_paths):
    """超每日上限：够重要的事入库待补播（而不是丢掉），且补播不占额度。"""
    b = _budget(policy_paths, daily_max=2)
    for i in range(2):
        a = b.gate(f"播{i}", "briefing", dedup_key=f"b{i}", urgency=0.9, now=MORNING)
        assert a.kind == "emit"
    a = b.gate("播3", "briefing", dedup_key="b3", urgency=0.9, now=MORNING)
    assert a.kind == "queue" and "上限" in a.reason
    assert b.pending_count() == 1
    # 过了冷却时段后新请求到达 → 积压被补播，且不再消耗额度
    later = MORNING.replace(hour=11)
    b2 = _budget(policy_paths, daily_max=2)
    a2 = b2.gate("播4", "briefing", dedup_key="b4", urgency=0.9, now=later)
    assert a2.kind == "emit" and "播3" in a2.text
    assert b2.stats()["count"] == 2   # 补播不消耗也不增加今日额度（仍是那 2 次）


def test_day_rollover_resets(policy_paths):
    """跨天重置当日计数与 last_emit。"""
    b = _budget(policy_paths, daily_max=1)
    assert b.gate("今", "briefing", urgency=0.9, now=MORNING).kind == "emit"
    tomorrow = datetime(2026, 8, 4, 9, 0)
    a = b.gate("昨同源", "briefing", dedup_key="briefing:昨同源", urgency=0.9, now=tomorrow)
    assert a.kind == "emit"  # 新的一天，额度与窗口都重置
    assert b.stats()["count"] == 1


def test_accept_rate_closes_loop(policy_paths):
    """用户持续忽略 → 接受率下滑 → 同源事件最终被抑制（不再吵）。"""
    b = _budget(policy_paths, min_salience=0.3)
    for _ in range(4):
        b.record_accept("default", accepted=False)
    # default 权重 0.5 × urgency 0.9 = 0.45 → ×接受率 0.0 → 低于门槛
    a = b.gate("系统碎碎念", "default", urgency=0.9, now=MORNING)
    assert a.kind == "suppress"


def test_state_file_isolated(policy_paths):
    """预算状态独立落盘，文件不存在（首启）不报错。"""
    b = _budget(policy_paths)
    assert not policy_paths["budget"].exists()
    assert b.stats()["count"] == 0
    b.gate("hi", "briefing", urgency=0.9, now=MORNING)
    assert policy_paths["budget"].exists()


# ---- 2026-10 走查后新增的四条口径 ----


def test_flush_has_cooldown_no_relay(policy_paths):
    """冲刷有冷却：一次冲刷之后 `aggregate_window_s` 内不再冲刷。

    旧实现无冷却且④也入库，形成“入队→下一条就冲刷”的乒乓，
    预算与门槛同时被旁路。
    """
    b = _budget(policy_paths, daily_max=1)
    assert b.gate("播一", "briefing", dedup_key="k1", urgency=0.9, now=MORNING).kind == "emit"
    assert b.gate("播二", "deadline", dedup_key="k2", urgency=0.9,
                  now=MORNING.replace(minute=1)).kind == "queue"
    # 首次冲刷（之前没刷过）：播二 补播出去，播三 自身因额度满入库
    a = b.gate("播三", "calendar", dedup_key="k3", urgency=0.9, now=MORNING.replace(minute=2))
    assert a.kind == "emit" and "播二" in a.text
    # 关键：下一条紧跟而来（60s < 300s 冷却）不得再冲刷，否则就是继电器
    a2 = b.gate("播四", "system", dedup_key="k4", urgency=0.9, now=MORNING.replace(minute=3))
    assert a2.kind == "queue" and "播三" not in a2.text
    # 冷却过后才真正补播，且不占今日额度
    a3 = b.gate("播五", "schedule", dedup_key="k5", urgency=0.9,
                now=datetime(2026, 8, 3, 12, 0))
    assert a3.kind == "emit" and "播三" in a3.text
    assert b.stats()["count"] == 1


def test_single_pending_no_digest_shell(policy_paths):
    """单条积压不套「积压 N 条」壳：一条也叫“积压”是文案说谎。"""
    b = _budget(policy_paths)
    b.gate("唯一一条", "deadline", dedup_key="only", now=NIGHT)
    a = b.gate("另一条", "system", dedup_key="next", urgency=0.9, now=NEXT_MORNING)
    assert "条提醒，一并呈上" not in a.text
    assert "唯一一条" in a.text


def test_flush_caps_lines_and_counts_omitted(policy_paths):
    """聚合最多列 5 行，更早的略去但数量要注明（不默默丢信息）。"""
    b = _budget(policy_paths)
    for i in range(8):
        b.gate(f"深夜{i}", "deadline", dedup_key=f"n{i}", now=NIGHT)
    a = b.gate("白天", "system", dedup_key="day", urgency=0.9, now=NEXT_MORNING)
    assert "补播 8 条提醒" in a.text
    assert a.text.count("  • ") == 6        # 5 行积压 + 1 行附带的当前事件
    assert "另有 3 条更早的已略去" in a.text
    assert "深夜0" not in a.text and "深夜7" in a.text


def test_percept_source_weights_registered(policy_paths):
    """感知源必须显式登记权重：落回 default 会让先验乘完全部不过门槛。"""
    b = _budget(policy_paths)
    # 切屏（0.5×0.3=0.15）：只留痕不播报
    a = b.gate("切到 chrome", "percept:window", dedup_key="w1",
               urgency=0.3, now=MORNING)
    assert a.kind == "suppress"
    # 仓库未提交（0.7×0.5=0.35）：该说
    assert b.gate("有 8 项未提交", "percept:vcs", dedup_key="v1",
                  urgency=0.5, now=MORNING).kind == "emit"
    # 白名单来信（0.9×0.75=0.675）：最该说
    assert b.gate("老张来信", "percept:mail", dedup_key="m1",
                  urgency=0.75, now=MORNING).kind == "emit"
    # 停留超时（用户显式开了阈值，0.5×0.65=0.325）：能过闸
    assert b.gate("在浏览器待了 40 分钟", "percept:window", dedup_key="w2",
                  urgency=0.65, now=MORNING.replace(minute=20)).kind == "emit"


# ---- 2026-10-19 走查：静默入库必须先过门槛 ----


def test_quiet_hours_low_salience_suppressed(policy_paths):
    """静默时段不再是门槛的后门：低分事件夜里也不入库。"""
    b = _budget(policy_paths)
    a = b.gate("切到 记事本", "percept:window", dedup_key="w1",
               urgency=0.3, now=NIGHT)              # 0.5×0.3=0.15 < 0.3
    assert a.kind == "suppress" and "仅留痕" in a.reason
    assert b.pending_count() == 0
    # 早上冲刷时它不该冒出来（旧顺序下这类碎事会绕过门槛被念给用户）
    nxt = b.gate("真提醒", "deadline", dedup_key="d1", urgency=0.9, now=NEXT_MORNING)
    assert nxt.kind == "emit" and "记事本" not in nxt.text


def test_quiet_hours_high_salience_still_queued(policy_paths):
    """换序不能把补播功能一起改掉：够重要的事夜里仍入库等补播。"""
    b = _budget(policy_paths)
    a = b.gate("老张来信", "percept:mail", dedup_key="m1", urgency=0.75, now=NIGHT)
    assert a.kind == "queue" and "静默" in a.reason
    assert b.pending_count() == 1


def test_non_percept_sources_unaffected_by_reorder(policy_paths):
    """换序对非感知源零回归：daemon 默认紧迫度 0.6 下最低档 0.5×0.6=0.30 仍过线。

    最后一个是故意拿未登记源探边界：它落 default 0.5，得分刚好等于门槛（不是
    小于），所以仍入库。浮点上 0.5*0.6 与 0.3 是同一个 double，这条用例
    把这个刀尖事实钉住，以后改默认权重时会立刻报警。
    """
    b = _budget(policy_paths)
    for i, src in enumerate(["briefing", "deadline", "calendar", "system",
                             "schedule", "未登记源"]):
        a = b.gate(f"夜间{i}", src, dedup_key=f"k{i}", urgency=0.6, now=NIGHT)
        assert a.kind == "queue", f"{src} 在默认紧迫度 0.6 下不该被门槛拦掉"
    assert b.pending_count() == 6


def test_flush_reorders_pending_by_time(policy_paths):
    """同 key 合并会把 ts 刷成最新，冲刷时必须按时间重排（否则“最近 N 条”挑错）。"""
    b = _budget(policy_paths)
    b.gate("最早发生", "deadline", dedup_key="a", now=NIGHT)                    # 23:30
    b.gate("较晚发生", "calendar", dedup_key="b", now=NIGHT.replace(minute=45))  # 23:45
    # 同 key 再来一次：a 的 ts 被刷到 23:50，但位置仍在 b 前面
    b.gate("最早发生", "deadline", dedup_key="a", now=NIGHT.replace(minute=50))
    a = b.gate("白天", "system", dedup_key="s", urgency=0.9, now=NEXT_MORNING)
    assert a.text.index("较晚发生") < a.text.index("最早发生")
