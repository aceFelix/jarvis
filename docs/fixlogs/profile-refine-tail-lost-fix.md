# 画像提炼：尾部增量漏提炼（profile-refine-tail-lost-fix）

> 日期：2026-11-15 ｜ 作者：aceFelix ｜ 影响范围：`profile_refiner.py`、`profile_idle.py`、`/memory refine`、测试 fixture

## 一、问题现象

P0（水位增量）上线后用户实机测试：会话「贾维斯记忆系统与作息」里先被记下了两条
（「最近一直在折腾Jarvis的记忆系统」「习惯凌晨两点左右睡觉」），但**随后说的
「定时清理垃圾 / 杀毒插件」完全没有入库**。

台账 `~/.jarvis/memory/refine_ledger.json` 是关键证据：

```json
"贾维斯记忆系统与作息": {
  "refined_until": 8,
  "last_msg_ts": 1794676665.71,      // 比上次提炼晚约 7 分钟（后面确实又聊了）
  "last_attempt_ts": 1794676249.13,  // 但此后**再没发起过任何尝试**
  "last_refine_ts": 1794676251.26,
  "last_error": ""
}
```

## 二、排查过程

1. **先排除 provider 失败**：`last_error` 为空，且 `last_refine_ts` 有值——第一次提炼是成功的。
2. **看时间轴**：`last_msg_ts` 比 `last_refine_ts` 晚 → 之后有消息；但 `last_attempt_ts`
   停在第一次 —— 说明后续消息**根本没走到"尝试提炼"**。
3. **读代码定位**：`maybe_refine_async` 里
   `if delta < required: return False`（required = `profile_refine_min_delta`，默认 6）。
   尾巴只有 1–5 条 → 直接返回，**连空闲检查都没排**。
4. **再看空闲侧**：`profile_idle._fire` 判定用的是同一个 `min_delta=6` —— 即使排上了，
   "聊两三句走开"同样不触发。两处门槛叠加，尾巴彻底丢失。
5. **附带发现**：用户真实台账里有一条 `"s1"`（`refined_until:16`），是测试写进去的——
   `test_profile_refine_golden` 的后台 worker 可能晚于 fixture teardown 才 `advance()`，
   而 teardown 已把路径还原成真实 `~/.jarvis`。

## 三、根因分析

- **同一个门槛被用在两个不同语义上**：`min_delta` 的本意是"值不值得花一次 LLM"，
  但被同时当成"兜底触发也要满足"的条件。兜底触发已经受时间限流（一次/小时），
  再用条数门槛就变成"短尾巴永远不处理"。
- **提前返回路径漏了调度**：`delta < required` 是"有增量但现在不跑"，正是空闲触发要接住的场景，
  却没调用 `_schedule_idle`。
- **测试隔离不彻底**：只还原了台账路径，没有等后台线程退出；线程名固定的 worker 成了漏网之鱼。

## 四、修复方案

| # | 改动 | 说明 |
|---|---|---|
| 1 | `maybe_refine_async`：`0 < delta < required` 时调 `_schedule_idle` | 尾巴交给空闲兜底 |
| 2 | `profile_idle._fire`：空闲判定改用 `min_delta=1` | 已按小时限流，不必再卡条数 |
| 3 | golden 测试 fixture：teardown 先 `join` 名为 `profile-refiner` 的线程，再 `reset_for_tests()` | 防止线程写回真实路径 |
| 4 | `/memory refine` 改走 `maybe_refine_async(force=True)` | 只提炼增量并推进水位；不再整窗重喂、不再把来源写成 `manual-refine` |

## 五、验证结果

- 新增/调整用例：
  - `test_small_tail_schedules_idle`（2 条增量 < 6 → 不提炼但必须排空闲）；
  - `test_triggers_with_single_message_tail`（1 条尾巴也能被空闲触发）；
  - `test_skips_when_no_new_messages`（delta=0 不触发）。
- 回归：`tests/core/test_profile_*` + `tests/commands` 共 **239 passed**，compile 通过。
- 真实台账：测试跑完后不再新增 `s1`；历史脏数据已手工清除（保留真实会话）。

## 六、涉及文件

| 文件 | 改动 |
|---|---|
| `agent/core/memory/profile_refiner.py` | 增量不足时排空闲兜底 |
| `agent/core/memory/profile_idle.py` | 空闲判定门槛改 `delta ≥ 1` |
| `agent/commands/handlers/session_commands.py` | `/memory refine` 走 force 增量路径 |
| `tests/core/test_profile_refine_golden.py` | fixture join worker + 新用例 |
| `tests/core/test_profile_idle.py` | 尾巴/零增量两例 |
| `docs/architecture/09-记忆与压缩.md`、`docs/log/CHANGELOG.md` | 口径同步 |

## 七、经验总结

1. **"值不值得跑"和"兜底也要跑"必须是两个门槛**。一个参数兼任两职时，短尾巴会被
   两边同时挡死；兜底触发应只受**时间限流**约束。
2. **提前返回的分支要逐个问一遍"这条路径该不该排兜底"**。`return False` 写起来最省事，
   也最容易漏掉后续补偿。
3. **后台线程的测试必须 join**，只还原路径不够——线程晚一步写回，就把测试数据带进用户环境。
4. **台账时间戳是排查利器**：`last_msg_ts` / `last_attempt_ts` / `last_refine_ts` 三者一对比，
   就能区分"没触发 / 触发失败 / 触发了但没内容"，比翻日志快。
5. **用户实机能发现单测发现不了的东西**：单测覆盖了"够 6 条才提炼"，却没覆盖
   "只聊两三条就走开"这个真实使用形态。
