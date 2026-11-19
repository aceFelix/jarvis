# 打扰预算被自己的冲刷路径旁路（感知事件「每两条必播一条」）

> 缺陷发现方式：W2 实机走查（人工测试），阶段三 D2/D3 场景
> 影响版本：阶段 8（W1 打扰预算）～ 阶段 9（W2 感知层接线）
> 修复分支：`fix/notify-budget-speech`　|　@author aceFelix　|　2026-10-18

## 一、现象

用户在桌面「感知源」面板开着窗口源，聊天区反复冒出成串的气泡：

```
📥 静默时段积压 1 条，一并呈上：
  • [percept:window] 切到 explorer.exe：C:\Users\许发明\.jarvis
📥 静默时段积压 1 条，一并呈上：
  • [percept:window] 切到 electron.exe：J.A.R.V.I.S 桌面工作台
📥 静默时段积压 1 条，一并呈上：
  • [percept:window] 切到 Snipaste.exe：Snipper - Snipaste
```

三个疑点：

1. 现在是下午五点，**根本不在静默时段**（出厂 `quiet_hours = "23:00-07:00"`），
   文案却说"静默时段积压"；
2. "积压"只有 **1 条**，还"一并呈上"——聚合了个寂寞；
3. 用户配的是"每日最多 10 条"，但切屏这种低价值提醒几乎每次切换都冒出来。

同时 `~/.jarvis/notify_state.json` 显示 `count: 9`（当日 10 的额度已用掉 9 条），
而 `pending: []`。

## 二、排查过程

1. **先确认事件从哪来**：`events.jsonl` 里 `focus_changed` 的 `salience_hint`
   全是 0.3，说明采集侧正常，问题在播报侧。
2. **算一遍重要度**（`budget.py` 的评分公式）：
   `score = 来源权重 × 紧迫度 × 相关性 × 接受率`。宿主传的 source 是
   `percept:window`，而 `_DEFAULT_SOURCE_WEIGHTS` 里**没有登记任何 `percept:*`**
   → 权重落到 `default = 0.5`；接受率初始 1.0、相关性 1.0：

   ```
   0.5 × 0.3 = 0.15  <  min_salience 0.3   → 判"不值得打扰"
   ```

   顺路核了其他源：`dir_grew` 0.5×0.45=0.225、默认先验 0.5×0.5=0.25
   ——**所有感知事件都低于门槛**。
3. **门槛以下为什么会播出来**：读 `gate()` 五步，发现两处叠加：
   - 第 ④ 步低于门槛时不是丢弃，而是 `_append_pending()` **入库等补播**；
   - 第 ① 步「非静默时段且队列非空」就无条件 `_flush_pending()`，
     而**这一步在第 ④ 步之前 `return`**，冲刷路径完全不过门槛。

   于是形成乒乓：事件 A 入队 → 事件 B 到达触发冲刷（播出 A，B 自己被
   `return` 丢掉）→ 事件 C 入队 → 事件 D 触发冲刷……
4. **额度为什么被吃掉**：`_flush_pending()` 里 `state["count"] += 1`，
   摘要气泡自己占一次 `daily_max`。更糟的是预算已满时它把聚合文本
   **重新塞回 `pending`**（`source="digest"`），下一个事件又把它冲出来——
   自己咬自己的尾巴。

## 三、根因

**W1 预算闸门是按"低频高显著性"（每日简报、截止日期）设计的，W2 接进来的是
"高频低显著性"事件流，两侧口径没有对齐**，具体三处：

| # | 根因 | 位置 |
|---|---|---|
| 1 | 感知源权重未登记，全部落到 `default 0.5`，乘完必低于门槛 | `budget.py::_DEFAULT_SOURCE_WEIGHTS` |
| 2 | 门槛以下"降级入库" + 冲刷无冷却且绕开门槛 → 低价值事件照样到达用户，只是换了个说法 | `budget.py::gate` ①/④ |
| 3 | 冲刷占日额度、且满额时把摘要塞回队列 → 垃圾气泡挤掉真提醒的额度 | `budget.py::_flush_pending` |

第 2 点是设计层面的自相矛盾：门槛的语义是"不值得打扰"，而"暂缓到稍后再说"
预设了"它将来会变得值得"——感知事件不会。

## 四、修复方案

`agent/core/policy/budget.py`：

1. **登记感知源权重**：`percept:mail 0.9` / `vcs 0.7` / `file 0.6` /
   `window 0.5` / `vision 0.4`，收编位一并登记（将来接上不再重踩）。
2. **门槛以下改为直接 `suppress`**：不再入队，事件仍进总线与 `events.jsonl`，
   只是不出声。
3. **冲刷加冷却**：新增 `_flush_due()`，同一个 `aggregate_window_s` 内最多冲刷
   一次（复用现有配置，不新增字段）。
4. **冲刷不再吞事件**：拆出 `_gate_current()`，冲刷后继续裁决当前请求；两者都
   要播时**合并成一条**返回（一次调用只起一条气泡，不丢信息也不叠两个）。
5. **冲刷不占 `daily_max`**：它是补播不是新增打扰；同时删掉"满额时把摘要塞回
   队列"的分支（不占额度 ⇒ 一定能播出去，死锁自然消失）。
6. **文案与规模**：单条不套「积压 N 条」壳；聚合最多列 5 行并注明「另有 N 条
   更早的已略去」；措辞从"静默时段积压"改为"补播"（不再谎报原因）。

配套的口径校准（`agent/core/percept/`）：

- `source_window.py`：`focus_linger` 先验 0.45 → **0.65**。停留阈值是用户显式
  设的（`window_linger_minutes > 0` 才会发），0.5×0.65=0.325 刚好过闸；
  普通切换保持 0.3 ⇒ 0.15，永远只留痕。
- `source_file.py`：`dir_grew` 0.45 → **0.55**（0.6×0.55=0.33 过闸），
  `file_added` 保持 0.3（单个新文件不该打断）。

可观测性（E）：`build_status()` 新增 `notify_budget` 字段（由 RPC 层传入
`get_budget().stats()`，感知层不反向依赖判断层），桌面面板总览多一行
「打扰预算：今日 3/10 · 待补播 2 条」，后端未启用预算时该字段为 `null`、
整行不显示（不编一个 0/0 出来）。

## 五、验证结果

| 层 | 用例 | 结果 |
|---|---|---|
| `tests/policy/test_notify_budget.py` | 10 → **14 例**（4 例改判新口径 + 4 例新增：冲刷冷却、单条不套壳、聚合限行与略去计数、感知源权重） | 47 passed（含同目录其他） |
| `tests/core/test_percept_speech.py` | 新增 **9 例**（口播层：应用名映射、标题取首段、提示符省略、缺字段退回摘要、留痕不被改写） | passed |
| `tests/voice/test_tts_text.py` | 6 → **11 例**（口播归一化：路径取末段、`.exe`/后缀、时间读法、散文标点不动） | passed |
| `tests/serve/test_hub_percept.py` | 22 → **23 例**（新增 `test_broadcast_uses_speech_form_not_record_form`） | passed |
| `tests/core/test_percept_status.py` | 9 → **11 例**（预算透传与快照副本 + 未启用回空 dict） | passed |
| `tests/serve/test_serve_percept_rpc.py` | 20 → **21 例**（预算读取失败不带倒 status） | passed |
| jarvis 全量 | — | **2809 passed**（基线 2787，新增 22 例） |
| 桌面 | 490 → **492 passed** + typecheck + build | 全绿 |

三条被明确改写的旧断言（它们锁的是旧缺陷行为，不是回归）：低重要度
`queue` → `suppress`；超额度 `suppress` → `queue`（够重要的值得补播）；
TTS 文本末尾多一个句号（语调落下）。

## 六、涉及文件

后端：`agent/core/policy/budget.py`、`agent/core/percept/speech.py`（新增）、
`agent/core/percept/source_window.py`、`agent/core/percept/source_file.py`、
`agent/core/percept/status.py`、`agent/serve/rpc_percept.py`、
`agent/serve/hub.py`、`agent/voice/tts_text.py`

桌面：`src/shared/percept-contracts.ts`、`src/renderer/src/components/PerceptSourcePanel.tsx`、
`src/renderer/src/i18n-butler.ts`、`test/renderer/percept.test.tsx`

测试：`tests/policy/test_notify_budget.py`、`tests/core/test_percept_speech.py`（新增）、
`tests/core/test_percept_status.py`、`tests/serve/test_hub_percept.py`、
`tests/serve/test_hub.py`、`tests/serve/test_serve_percept_rpc.py`、`tests/voice/test_tts_text.py`

## 七、经验总结

1. **"入队等稍后再说"必须回答"什么时候再说、由谁触发"**。没有触发条件的
   暂缓队列，最终都会被某条捷径清空——而那条捷径通常绕开了当初决定暂缓的
   那个判断。
2. **新事件源接进旧闸门时，先核对"权重/先验"这类分组键是否覆盖**。
   `dict.get(key, default)` 的静默默认值，会让一整类新数据落在错误的档位上，
   而单测各测各的、接口测试看不出统计偏差。
3. **一条聚合消息只有一条内容时，它就不是聚合**；文案里的原因（"静默时段"）
   如果不是真实原因，就是谎话——**归因文案要么准确要么中性**。
4. **可观测性字段是缺陷的一半**：这次能定位快，靠的是 `notify_state.json` 里
   `count: 9` 与 `pending: []` 同时可见。把"为什么没说话"露到面板上，
   下一次用户不必来问。
5. **人工走查仍不可替代**：`daily_max=10` 的默认值让单测里的"超额度"路径
   几乎不会被真实节奏触发，而走查 20 分钟就撞出了乒乓。
