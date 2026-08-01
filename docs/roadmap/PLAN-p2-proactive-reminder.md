# P2-3 主动提醒系统 — 升级方案

> 目标：让贾维斯从"被动响应"进化为"主动服务"——基于日历、截止日期、系统状态主动提醒用户，无需用户提问。

---

## 一、现有基础分析

| 已有能力 | 位置 | 说明 |
|---|---|---|
| 定时任务调度器 | `agent/core/daemon/scheduler.py` | 后台轮询 + 持久化 `~/.jarvis/schedule.json`，支持 once/daily/weekly |
| 日程提醒工具 | `agent/tools/extensions/schedule_tool.py` | ScheduleReminder/ListSchedule/CancelSchedule 三个 Agent 工具 |
| 触发回调 | `daemon.py` `_on_schedule_fire()` | 托盘通知 + TTS 语音播报 |
| 节假日检测 | `agent/core/daemon/holidays.py` | 中国节假日判断（daemon 启动时检查明天是否放假） |
| 系统监控 | `agent/core/daemon/monitor.py` | CPU/内存/磁盘阈值告警（psutil） |

**缺失的部分**（P2-3 的"主动"含义）：

| 缺失能力 | 说明 |
|---|---|
| 每日简报 | 早上主动播报：今天有什么提醒、是否节假日、系统状态 |
| 截止日期追踪 | 注册 deadline，3天/1天/当天分级提醒 |
| 提醒升级/确认 | 用户未确认时重复提醒，避免遗漏 |
| 日历集成 | 读取 Outlook/ICS 日历事件，提前提醒 |
| 周期巡检增强 | 磁盘趋势预测、异常进程、工作时长提醒 |

---

## 二、架构设计

新增 `agent/core/daemon/proactive.py` 作为 **ProactiveEngine**（主动感知引擎），统一管理所有主动提醒源。daemon 启动时创建并注入 Scheduler。

```
ProactiveEngine
├── DailyBriefing       每日简报（定时触发）
├── DeadlineTracker     截止日期追踪（分级提醒）
├── ReminderEscalation  提醒升级/确认机制
├── PeriodicInspector   周期巡检增强
└── CalendarSource      日历数据源（Outlook COM / ICS）
```

**核心设计决策**：
- **不自起线程**：复用现有 Scheduler 的轮询机制，注册 daily 任务，轻量无额外开销。
- **幂等注册**：通过 task.note 标记识别 ProactiveEngine 注册的任务，start() 多次调用不重复。
- **优雅降级**：任何子模块失败不影响其他模块；psutil/Outlook 不可用时静默跳过。

---

## 三、模块详细设计

### 3.1 每日简报（DailyBriefing）

每天 configurable 时间（默认 08:30）主动播报：
- 今日待触发提醒列表（从 Scheduler.list_pending 筛选今天）
- 今日是否节假日/调休（复用 holidays.py）
- 系统健康摘要（CPU/内存/磁盘当前值）
- 活跃截止日期倒计时
- 日历事件（如果 CalendarSource 可用）

实现：在 Scheduler 中注册一个 daily 任务（note=`__proactive_briefing__`），触发时收集各模块数据，组装为自然语言文本，通过 `_on_proactive_notify` 播报。

### 3.2 截止日期追踪（DeadlineTracker）

数据模型：
```python
@dataclass
class Deadline:
    id: str
    title: str           # "Q3 项目交付"
    due_date: str        # "2026-08-15" (ISO date)
    remind_days: list[int] = [7, 3, 1, 0]  # 提前几天提醒
    status: str = "active"  # active / done / overdue
    reminded_dates: list[str]  # 已提醒过的日期（防重复）
```

持久化：`~/.jarvis/deadlines.json`

分级提醒逻辑：
- ProactiveEngine 每天 09:00 检查所有 active deadline
- 计算距 due_date 的天数差，匹配 remind_days 列表
- 命中则生成提醒（如"距离 Q3 项目交付还有 3 天"）
- due_date 当天: "今天是 Q3 项目交付截止日！"
- 超过 due_date: 标记 overdue，每天提醒"已逾期 N 天"
- 同一天不重复提醒（reminded_dates 去重）

Agent 工具：AddDeadline / ListDeadlines / CompleteDeadline / RemoveDeadline

### 3.3 提醒升级/确认（ReminderEscalation）

扩展现有 ScheduleTask：
- 新增字段：`acknowledged: bool`、`escalate_count: int`、`max_escalate: int = 3`、`last_fired_at: str`
- 提醒触发后，如果用户未确认（未调用 AcknowledgeReminder），自动重复通知
- 每次升级间隔递增：5min → 10min → 20min
- 最多升级 max_escalate 次后停止（避免轰炸）
- 语音模式下，用户说"知道了"/"好的"即视为确认

新增工具：`AcknowledgeReminder`（不指定 task_id 时确认最近的未确认提醒）

### 3.4 周期巡检增强（PeriodicInspector）

扩展现有 SystemMonitor：

1. **磁盘趋势预测**：记录每日磁盘使用率到 `~/.jarvis/monitor_history.json`，线性回归拟合趋势，预测 N 天内将满时提前告警。
2. **异常进程检测**：检测 CPU 占用 > 50% 的进程，通知用户。
3. **工作时长提醒**：通过 Windows `GetLastInputInfo` 检测键鼠活动，连续工作超过 2 小时提醒休息。

### 3.5 日历集成（CalendarSource）

双后端策略：
1. **Outlook COM**（优先，Windows + 已安装 Outlook）：
   - `win32com.client.Dispatch("Outlook.Application")`
   - 读取今日/明日事件，支持重复事件
2. **ICS 文件/URL**（回退，跨平台）：
   - 轻量手写 VEVENT 解析（不依赖 icalendar 库）
   - 支持本地 .ics 文件或远程 URL 订阅
   - 每 30 分钟缓存刷新

配置 `backend = "auto"` 时优先检测 Outlook COM 注册表，不可用则回退 ICS。

---

## 四、具体改动

### 4.1 新增模块

| 文件 | 职责 |
|---|---|
| `agent/core/daemon/proactive.py` | ProactiveEngine 主引擎 + DailyBriefing 组装 |
| `agent/core/daemon/deadline.py` | DeadlineTracker 数据模型 + 分级提醒逻辑 |
| `agent/core/daemon/calendar_source.py` | 日历数据源（Outlook COM / ICS 双后端） |
| `agent/tools/extensions/deadline_tool.py` | AddDeadline/ListDeadlines/CompleteDeadline/RemoveDeadline 工具 |

### 4.2 修改模块

| 文件 | 改动 |
|---|---|
| `agent/daemon/daemon.py` | 启动 ProactiveEngine，注册 deadline 工具，`_on_schedule_fire` 分发 ProactiveEngine 任务，新增 `_on_proactive_notify` |
| `agent/core/daemon/scheduler.py` | ScheduleTask 新增 acknowledged/escalate_count/max_escalate/last_fired_at 字段 + acknowledge()/get_unacknowledged_fired() |
| `agent/core/daemon/monitor.py` | 新增磁盘趋势预测(线性回归)、异常进程检测、工作时长提醒(GetLastInputInfo) |
| `agent/tools/extensions/schedule_tool.py` | 新增 AcknowledgeReminderTool |
| `agent/config/settings.py` | 新增 briefing/deadline/calendar/monitor增强 配置字段 + TOML 解析 |
| `configs/settings.toml` | 新增 [deadline] [calendar] 段 + [daemon] briefing + [monitor] 增强 |
| `configs/settings.example.toml` | 同步示例 |

### 4.3 配置项

```toml
[daemon]
briefing_enabled = true
briefing_time = "08:30"        # 每日简报时间（HH:MM）

[deadline]
enabled = true
check_time = "09:00"           # 每日检查截止日期的时间

[calendar]
enabled = false                # 日历集成（需配置 Outlook 或 ICS）
backend = "auto"               # auto / outlook / ics
ics_path = ""                  # 本地 .ics 文件路径
ics_url = ""                   # 远程 .ics 订阅 URL
remind_minutes_before = 30     # 事件前多少分钟提醒

[monitor]
disk_trend_days = 7            # 磁盘趋势预测：预测几天后将满
high_cpu_duration = 600        # 异常进程：CPU > 50% 持续多少秒通知
work_break_interval = 7200     # 连续工作多少秒提醒休息（2小时）
```

---

## 五、验收标准

- [x] daemon 启动时 ProactiveEngine 自动注册每日简报 + 截止日期检查任务。
- [x] 每天 08:30 自动播报每日简报（提醒/节假日/系统状态/截止日期）。
- [x] 用户对贾维斯说"下周五之前交报告"→ agent 调 AddDeadline 注册截止日期。
- [x] 截止日期分级提醒：提前 7/3/1/0 天 + 逾期每天，同一天不重复。
- [x] 提醒触发后未确认自动升级（5→10→20 分钟，最多 3 次）。
- [x] AcknowledgeReminder 工具可确认提醒，停止升级。
- [x] 磁盘趋势预测：线性回归拟合历史数据，提前 N 天预警。
- [x] 工作时长提醒：连续工作 2 小时后提醒休息（Windows GetLastInputInfo）。
- [x] 日历集成：Outlook COM 优先，回退 ICS 文件/URL（默认关闭，需配置启用）。
- [x] 所有新模块编译通过、导入正常、配置加载正确。
- [x] README.md + roadmap 文档已更新。

---

## 六、风险与回退

| 风险 | 回退方案 |
|---|---|
| Outlook COM 启动慢或未安装 | backend="auto" 自动回退 ICS；ICS 也没配置则日历功能静默不可用 |
| ICS 远程 URL 网络超时 | 10s 超时 + 30 分钟缓存，失败时用上次缓存 |
| 每日简报内容过长导致 TTS 播报体验差 | 语音只播报前 200 字，详细内容走托盘通知 |
| 磁盘历史数据不足（新安装） | 至少 3 天数据才做趋势预测，之前静默 |
| GetLastInputInfo 仅 Windows 可用 | 其他平台返回 None，工作时长提醒静默跳过 |
| 提醒升级可能打扰用户 | 最多 3 次升级后停止；用户可随时 AcknowledgeReminder 确认 |

---

## 七、涉及文件

- `jarvis/agent/core/daemon/proactive.py`（新增）
- `jarvis/agent/core/daemon/deadline.py`（新增）
- `jarvis/agent/core/daemon/calendar_source.py`（新增）
- `jarvis/agent/tools/extensions/deadline_tool.py`（新增）
- `jarvis/agent/core/daemon/scheduler.py`
- `jarvis/agent/core/daemon/monitor.py`
- `jarvis/agent/tools/extensions/schedule_tool.py`
- `jarvis/agent/config/settings.py`
- `jarvis/agent/daemon/daemon.py`
- `jarvis/configs/settings.toml`
- `jarvis/configs/settings.example.toml`
- `jarvis/docs/roadmap/jarvis-upgrade-roadmap.md`
- `jarvis/README.md`
