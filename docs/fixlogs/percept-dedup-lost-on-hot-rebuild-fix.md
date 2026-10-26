# 热重装配丢去重账本：面板改一次配置就等于"再报一遍"

> 2026-10-26 阶段 11 实机走查（收编 `calendar` / `system` 两个源）时人工实测发现。
> 影响面是**整个 `[percept]` 层**，不只新收编的两个源。

## 一、现象

面板上连点几次「保存」（每次写回都触发 `hot_update_percept` 重建总线），
`~/.jarvis/percept/events.jsonl` 就把**同一件事**重复入库几次：

```
20:01:56 | system   | memory_alert
20:01:58 | calendar | event_upcoming
20:01:59 | system   | memory_alert      ← 同一条，又来一遍
20:02:00 | calendar | event_upcoming
20:02:01 | system   | memory_alert
20:02:05 | calendar | event_upcoming
20:02:06 | system   | memory_alert
```

气泡只弹了 2 条——W1 那一层的 300s 去重兜住了，所以**肉眼看不出来**。
但打扰额度被白扣（`notify_state.json` 的 `count` 每多一条就涨一格），
面板「最近事件」与审计留痕也被污染成 4 份重复。

## 二、根因

`hot_update_percept` 的实现是「停旧总线 + 按新配置重建」，这个做法本身是对的：
逐源开关是造实例时的快照，而且只有停掉才能真释放 IMAP / 摄像头。问题在于
去重状态全挂在**实例内存**上，重建等于让所有"这条我已经说过了"一起失忆：

| 状态 | 原本挂在哪 | 重建后 |
|---|---|---|
| 总线去重窗口 `_seen` / 同源节流 `_last_emit` | `PerceptBus` 实例 | 全新实例 → 归零，5 分钟窗口失效 |
| 日历"今天已报过的日程" `_announced` | `CalendarSource` 实例 | 全新实例 → 归零，同一条日程再报 |
| 资源告警冷却 `_last_alert` | `SystemMonitor` 实例 | 全新实例 → 归零，同一条告警再报 |

对照：`source_mail` 早就把已报键写进 `~/.jarvis/percept/mail_seen.json`，
所以邮件源从来没有这个毛病——本次就是按它的模子把另外两个补齐。

## 三、修法（两处，各修一层）

**① 去重账本跟着总线走、跨重建接续** —— `agent/core/percept/bus.py`

新增 `export_dedup_state()` / `import_dedup_state()`，宿主在停旧总线之前导出、
起新总线之后导入：

```python
dedup_state = (self.percept_bus.export_dedup_state()
               if self.percept_bus is not None else None)
self._stop_percept()
self._start_percept()
if dedup_state and self.percept_bus is not None:
    self.percept_bus.import_dedup_state(dedup_state)
```

时间基准是 `time.monotonic()`，**只在同进程内可比**，所以这份账本刻意不落盘——
跨进程重启时它就该归零（新进程的 monotonic 与旧值不可比）。

**② 日历"已报日程"落盘** —— `agent/core/percept/source_calendar.py`

落 `~/.jarvis/percept/calendar_announced.json`，形状 `{"day": "...", "keys": [...]}`；
缺失 / 损坏 / 隔天一律当空账本，上限 200 条环形。这样同一天里无论重启还是改配置，
同一条日程都只报一次。

> 这里踩了一个坑并写进了注释：`_load_state()` 必须同时把 `_announced_day` 对齐到今天。
> 否则 `collect()` 里第一次 `_reset_day()` 会因为 `_announced_day is None` 把刚读回的
> 账本清空，落盘白写。

资源告警刻意**不**落盘：它是"当前状态"型事实，换进程后重新评估是合理的；
面板改配置这种不换进程的场景已被 ① 的 300s 窗口兜住。

## 四、验证

| 用例 | 断言 |
|---|---|
| `test_dedup_state_survives_a_bus_rebuild` | 新总线导入账本后同一事件 `stage == "dedup"`；不导入则 `"emit"` |
| `test_import_dedup_state_ignores_garbage` | 脏账本跳过不抛（`None` / `{}` / 错形状都返回 0） |
| `test_hot_update_keeps_dedup_ledger` | 端到端：`hub.hot_update_percept()` 之后再发同一事件被吞 |
| `test_calendar_announced_ledger_survives_rebuild` | 两实例共用同一账本文件，第二个 `collect() == []` |
| `test_calendar_ledger_from_other_day_is_ignored` | 账本 `day` 是昨天 → 当空账本，今天照报 |

顺带补了一处测试卫生：日历源的用例加了 autouse fixture 隔离账本路径，否则测试会
读写真实的 `~/.jarvis/percept/calendar_announced.json`——既污染用户目录，也让结果
依赖本机的日程历史。

全量 **3258 → 3263 passed**（新增 5 例）；`test_percept_bus` 49 例、
`test_percept_sources_catchup` 22 例、`test_hub_percept` 25 例。

## 五、实测复现路径（下次回归照着点）

1. 开壳 → 管家服务 → 感知源，随便挑一个开关连拨 4 次（每次保存都会重装配）；
2. `Get-Content "$env:USERPROFILE\.jarvis\percept\events.jsonl" -Tail 20`；
3. 判据：同一 `source + kind` 在 `dedup_window_s`（默认 300s）内**只应出现一条**。

@author aceFelix
