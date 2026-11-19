# 改一次配置抹一次证据：感知面板历史空、计数归零，且静默时段绕过重要度门槛

- 发现方式：人工实机走查（用户截图 + `events.jsonl` 与 `notify_state.json` 对账）
- 影响版本：W2 阶段 9（感知面板）/ W1 阶段 8（打扰预算），2026-10-18 走查现场
- 修复分支：`fix/percept-observability`
- 关联复盘：[notify-budget-percept-relay-fix.md](notify-budget-percept-relay-fix.md)（上一轮的
  同族问题，本轮是它没修干净的部分加上一个观测口缺陷）

## 一、现象

用户在面板上保存了一次感知配置，截图同时出现三件互相矛盾的事：

1. 「最近事件」显示**还没有采到事件**；
2. 「闸门计数」七个键**全部为 0**（`dedup 0 · emit 0 · throttle 0 · …`）；
3. 但「打扰预算」那行明明写着 `今日 10/10 · 待补播 1 条` —— 说明预算侧一直在被调用。

而同一时刻直接读磁盘：

```
~/.jarvis/percept/events.jsonl   24 行，最后一行 ts=2026-10-18T23:55:57
~/.jarvis/notify_state.json      count=10, pending=8 条（全是刚才的感知事件）
```

**采集一直在正常工作，面板却在说"什么都没发生"。**

另外两个连带问题：

- `pending` 里 8 条有 6 条是「切到 记事本」「切到 资源管理器」这类低分碎事 —— 它们白天
  会被重要度门槛拦掉，夜里却照样排进了补播队列；
- 队列第 3、4 条的时间戳是 `23:55:57` / `23:55:17`，**顺序反了**。

## 二、排查过程

### 2.1 先排除"没采到"

`events.jsonl` 在写、`notify_state.json` 的 `pending` 在长 —— 说明源在采、总线在投、
订阅方（hub）在过闸。问题只在**面板读到的那份数据**上。

### 2.2 定位面板数据源

```
status.py:119   "recent_events": [ev.to_dict() for ev in bus.recent(limit)]
bus.py:160      self._history = deque(maxlen=...)     # 实例级
bus.py:161      self._counters = {"emit": 0, ...}     # 实例级
```

`recent()` 读的是**内存环形缓冲**，`stats()` 读的是**实例字典**。两者都不落盘。

### 2.3 谁换了实例

```
hub.py:249-250   self._stop_percept(); self._start_percept()   # 配置变更
hub.py:316       bus = init_percept(self._settings)
__init__.py:92   _bus_singleton = None   # 下次 get_bus 按新配置重建
```

`init_percept` 的 docstring 自己写着「按需**重建**总线单例」。于是：

**保存一次配置 → 换一台全新总线 → 历史为空、计数全 0 → 面板显示"还没有采到事件"。**

用户截图上那条绿色的「✓ 已保存并重装配」正是自证：重建刚刚发生。

### 2.4 碎事为什么能进队列

读 `_gate_current` 的判定顺序（修复前）：

```
② 静默时段 → 入库（不评分）
③ 聚合窗口 → 合并抑制
④ 重要度门槛 → 抑制
⑤ 日额度 → 入库
```

**② 排在 ④ 之前，而且静默入库这条路径压根不调用 `salience()`。** 于是
`percept:window` 的 `focus_changed`（0.5 × 0.3 = **0.15**，远低于门槛 0.3）在白天被
④ 拦掉，到了 23:00 之后走 ② 直接入队，早上出静默时被 ① 冲刷出来 —— **绕过门槛**。

我上一轮修的是「非静默时冲刷绕开门槛」，漏了「静默入库绕开门槛」这条同构的路。

### 2.5 顺序反转

```python
# _append_pending：同 key 再来一次时
item["merged"] = item.get("merged", 1) + 1
item["ts"] = now.isoformat(...)     # ← ts 刷新，位置不动
```

合并语义下 `ts` 表示"这件事最后一次发生"，但入队顺序表示"第一次发生"，两者不再一致，
冲刷摘要里"最近 N 条"因此挑错。

## 三、根因

| # | 根因 | 性质 |
|---|---|---|
| 1 | 观测数据（历史、计数）挂在**可被替换的实例**上，而实例替换是常规操作（改配置） | 设计缺陷：观测口与实例生命周期绑死 |
| 2 | 面板"最近事件"只有内存一条来源，**磁盘上那份权威数据没被用** | 实现不完整：留痕文件本就是"让用户看见采了什么" |
| 3 | 闸门顺序把"当时不方便打扰"（静默）排在"不值得打扰"（门槛）之前，且静默路径不评分 | 语义错误：门槛只在白天生效 |
| 4 | 合并时刷新 `ts` 但不重排队列 | 一致性遗漏 |

第 1、2 条是同一个故障的两面：**证据存在，但读证据的口子是易失的。**

## 四、修复方案

### 4.1 最近事件改以磁盘为权威（`bus.py`，面板与导出同源）

新增 `PerceptBus.recent_from_log(limit)`：按字节 seek 读文件尾部（`limit × 4KB` 预算，
大文件也只读几十 KB），倒序返回；首行可能是截开的残行 → 丢弃；单行解析失败 → 只丢该行。

选择规则收在新方法 `recent_dicts()` 里（只写一处）：

```
留痕开着（_log_path 非空）且磁盘读到了 → 用磁盘（跨实例的唯一权威）
读不到 / 没开留痕                  → 回退内存 recent()
```

回退不是冗余：内存有事件而磁盘读不到，说明**写盘失败**（`counters.log_errors` 会非零），
这时观测口仍要能回答"采到了什么"。

两个调用方同时改过去：`status._recent_events()`（面板）与 `bus.export()`（隐私自查）。
后者是同一条根因的另一个出口 —— 审计口径写的是"可见即可导出"，只读内存的话，
刚改过配置的用户会导出“只采到 0 条”而磁盘上躺着全部历史，那份自查就是误导性的。

字段结构一字未变（磁盘上写的就是 `event.to_dict()`），所以**桌面侧零改动**。

### 4.2 重建总线时继承观测账本（`__init__.py` + `bus.py`）

`init_percept` 的重建路径先留住旧单例，新实例构造后调用
`bus.inherit_stats_from(previous)`：计数**累加**、内存历史**拼接**（受 `maxlen` 裁剪）。

刻意**不**继承 `_seen` / `_last_emit`：那是"这台总线的去重与节流窗口"，属于**行为**而不是
**观测**；跨实例延续会把"重启后重新计时"这个可预期语义搅浑。这条边界写进了 docstring
并有专门用例钉住。

显式传入 `bus=` 时不继承 —— 那是测试与宿主自装配入口，拿陈旧计数污染新实例更难排查。

### 4.3 静默入库必须先过门槛（`budget.py`）

闸门顺序改为 **④ → ② → ③ → ⑤**：

```python
# ④ 重要度门槛：低于门槛直接抑制，任何时段都不配占用队列
score = self.salience(source, urgency, relevance, state)
if score < self.config.min_salience:
    return NotifyAction("suppress", ...)
# ② 静默时段：够重要但当时不能说 → 入库待补播
if self._in_quiet(now):
    self._append_pending(...)
```

换序前核过回归面：非感知源在 `daemon` 的默认紧迫度 0.6 下最低档是
`default 0.5 × 0.6 = 0.30`，**刚好不小于门槛 0.3**，仍然入库。这条浮点刀尖事实
由 `test_non_percept_sources_unaffected_by_reorder` 钉住。

### 4.4 冲刷前按时间重排（`budget.py`）

`_flush_pending` 改为 `sorted(pending, key=ts)`，并在 `_append_pending` 的 ts 刷新处
加注释指向这里。保留"ts 表示最后一次发生"的语义，把排序责任放到消费侧。

### 4.5 顺手：先验 0 不再被抬成 0.5（`hub.py`）

```python
# 旧：urgency=float(getattr(event, "salience_hint", 0.5) or 0.5)
urgency=float(event.salience_hint
              if getattr(event, "salience_hint", None) is not None else 0.5)
```

`or 0.5` 会把"这类事件永远别播"（0 分）静默抬成 0.5，反而可能过闸 —— 与意图完全相反。

## 五、验证结果

| 判据 | 结果 |
|---|---|
| 全量单测 | **2827 passed**（基线 2809 + 新增 18） |
| 死链门禁 | 130 份 md / 555 条本地链接 → dead 0 / untracked 0 |
| 隐私自查与面板同源 | `test_export_reads_disk_after_rebuild` |
| 重建后仍能看到历史 | `test_recent_from_log_survives_bus_rebuild`、`test_recent_events_come_from_disk_after_bus_rebuild` |
| 计数跨重建累加 | `test_inherit_stats_from_carries_counters_and_history`、`test_reinit_carries_over_observation_ledger` |
| 行为不跨实例继承 | `test_inherit_does_not_carry_dedup_window` |
| 显式传 bus 不被污染 | `test_explicit_bus_does_not_inherit_stale_counters` |
| 尾部读取的残行/坏行容错 | `test_recent_from_log_drops_partial_head_after_seek`、`test_recent_from_log_skips_broken_lines` |
| 静默时段低分事件不入库 | `test_quiet_hours_low_salience_suppressed` |
| 高分事件补播功能未坏 | `test_quiet_hours_high_salience_still_queued` |
| 非感知源零回归 | `test_non_percept_sources_unaffected_by_reorder` |
| 冲刷按时间排序 | `test_flush_reorders_pending_by_time` |
| 先验 0 原样送到闸门 | `test_zero_salience_hint_is_not_raised_to_default` |

一次全量跑中 `tests/ui/test_serve_stdin_abort.py` 出现过 1 次失败，单跑 6 passed、
重跑全量 2826 passed —— 该用例对 stdin/事件循环时序敏感，与本次改动无关（未触碰该路径）。

## 六、涉及文件

**代码**

- `agent/core/percept/bus.py`：`recent_from_log()`、`recent_dicts()`、
  `inherit_stats_from()`、`_LOG_TAIL_BYTES`，`export()` 事件段改走 `recent_dicts()`
- `agent/core/percept/status.py`：`_recent_events()`（转发 `recent_dicts`）
- `agent/core/percept/__init__.py`：`init_percept()` 重建路径继承观测账本
- `agent/core/policy/budget.py`：`_gate_current()` 换序、`_flush_pending()` 重排
- `agent/serve/hub.py`：`_on_percept_event()` 先验 0 不再被抬升

**测试（新增 18 例）**

- `tests/core/test_percept_bus.py`（+9）、`tests/core/test_percept_status.py`（+2）、
  `tests/core/test_percept_init.py`（+2）、`tests/policy/test_notify_budget.py`（+4）、
  `tests/serve/test_hub_percept.py`（+1）

**文档**

- `docs/architecture/17-W2感知层.md`、`docs/architecture/16-W1判断层.md`、
  `docs/architecture/07-UI层.md`、`docs/guide/desktop.md`、
  `docs/test/percept-w2-testing.md`（新增走查项 D10「改配置不丢证据」，
  并订正 T4/T9/D5/P7 四条旧判据）

## 七、经验总结

1. **观测数据不能挂在可替换的实例上。** 只要"重建"是常规操作（改配置、热更新），
   实例级的计数与历史就等于"随时会被抹掉的证据"。要么落盘，要么显式继承 ——
   关键是**别让它默认消失**。
2. **已经有权威数据源时，别只读缓存。** 留痕文件本来就是"让用户看见采了什么"，
   面板却只读内存环形缓冲 —— 承诺在文档里，实现在另一半。
3. **闸门换序要逐条核对回归面，不能只看目标用例。** 把门槛提到静默之前，顺手算清了
   六个非感知源在新顺序下的得分（最低 0.5×0.6=0.30 刚好过线），才敢说"零回归"。
   浮点刀尖（0.30 vs 0.3）值得用一条用例钉住，而不是靠"看起来相等"。
4. **"绕开门槛"是一条路径家族，不是一个点。** 上一轮修了冲刷绕门槛，这轮才看见静默
   入库也绕门槛 —— 同一条不变量（门槛是绝对判据）的多个旁路，应该在发现第一条时就
   把整条链路按"谁可能绕过它"过一遍。
5. **合并语义会悄悄改变排序假设。** `ts` 刷成最新是对的（它表示"最后一次发生"），
   错的是消费侧默认队列仍按 ts 有序。语义与顺序的契约要显式写在哪一侧负责。
6. **面板"什么都没采到"和后端"采了一堆"同时出现时，先怀疑观测口，别怀疑采集口。**
   这次如果先去查窗口源，会完全查不出来 —— 它工作得好好的。
