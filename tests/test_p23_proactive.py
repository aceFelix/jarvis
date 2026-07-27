"""P2-3 主动提醒系统验证脚本。"""
from datetime import date, timedelta
from agent.core.daemon.scheduler import Scheduler, ScheduleTask
from agent.core.daemon.deadline import DeadlineTracker
from agent.core.daemon.proactive import ProactiveEngine, ProactiveConfig

print("=" * 50)
print("P2-3 主动提醒系统 - 集成验证")
print("=" * 50)

# ---- 1. ProactiveEngine 每日简报 ----
print("\n[1] 每日简报生成")
notifications = []
s = Scheduler(on_fire=lambda t: None)
tracker = DeadlineTracker()

due = (date.today() + timedelta(days=1)).isoformat()
tracker.add(title="明天要交的报告", due_date=due)

engine = ProactiveEngine(
    scheduler=s,
    config=ProactiveConfig(briefing_enabled=True, briefing_time="08:30", deadline_enabled=True),
    deadline_tracker=tracker,
    on_notify=lambda msg: notifications.append(msg),
)

# 测试任务识别
fake_briefing = ScheduleTask(note="__proactive_briefing__")
fake_normal = ScheduleTask(note="")
assert engine.is_proactive_task(fake_briefing) is True
assert engine.is_proactive_task(fake_normal) is False
print("  [OK] is_proactive_task 识别正确")

# 手动触发简报
engine._fire_briefing()
assert len(notifications) == 1
briefing = notifications[0]
assert "简报" in briefing or "先生" in briefing
print(f"  [OK] 简报生成成功（{len(briefing)} 字符）")
print("  ---")
for line in briefing.split("\n"):
    print(f"  {line}")
print("  ---")

# ---- 2. 截止日期检查 ----
print("\n[2] 截止日期分级提醒")
notifications.clear()
engine._fire_deadline_check()
assert len(notifications) == 1
print(f"  [OK] 提醒内容: {notifications[0]}")

# ---- 3. 提醒升级/确认 ----
print("\n[3] 提醒升级/确认机制")
s2 = Scheduler(on_fire=lambda t: None)
from datetime import datetime
trigger = (datetime.now() + timedelta(seconds=999)).strftime("%Y-%m-%dT%H:%M:%S")
task = s2.add_task(content="升级测试", trigger_at=trigger)
assert task.acknowledged is False
assert task.escalate_count == 0
assert task.max_escalate == 3
print(f"  [OK] 新任务: acknowledged={task.acknowledged}, max_escalate={task.max_escalate}")

s2.acknowledge(task.id)
# 验证已确认
for t in s2.list_all():
    if t.id == task.id:
        assert t.acknowledged is True
        break
print("  [OK] acknowledge 后 acknowledged=True")
s2.cancel_task(task.id)

# ---- 4. 监控增强 ----
print("\n[4] 周期巡检增强")
from agent.core.daemon.monitor import SystemMonitor, MonitorConfig
monitor = SystemMonitor(
    config=MonitorConfig(enabled=True, disk_trend_days=7, work_break_interval=7200),
    on_alert=lambda a: None,
)
if monitor.available:
    # 磁盘趋势
    monitor.record_disk_usage()
    prediction = monitor.predict_disk_full()
    print(f"  [OK] 磁盘记录完成，预测结果: {prediction or '数据不足/无趋势'}")

    # 工作时长
    idle = monitor.get_idle_time_seconds()
    print(f"  [OK] 用户空闲时间: {idle:.1f}s" if idle else "  [SKIP] 非Windows，跳过")

    # 异常进程
    procs = monitor.check_high_cpu_processes()
    print(f"  [OK] 高CPU进程: {len(procs)} 个")
else:
    print("  [SKIP] psutil 未安装，跳过监控测试")

# ---- 5. 日历数据源 ----
print("\n[5] 日历数据源")
from agent.core.daemon.calendar_source import CalendarSource, CalendarConfig
cal = CalendarSource(config=CalendarConfig(enabled=False))
assert cal.get_today_events() == []
print("  [OK] 日历禁用时返回空列表")

cal2 = CalendarSource(config=CalendarConfig(enabled=True, backend="ics", ics_path="nonexist.ics"))
events = cal2.get_today_events()
print(f"  [OK] ICS 文件不存在时返回: {events}")

# ---- 清理 ----
for d in tracker.list_active():
    tracker.remove(d.id)

print("\n" + "=" * 50)
print("全部验证通过！P2-3 主动提醒系统工作正常。")
print("=" * 50)
