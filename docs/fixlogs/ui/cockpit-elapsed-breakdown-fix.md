# 驾驶舱写「耗时 7.6s」、执行序列却是 467ms + 151ms：两个口径被摆在同一屏

日期：2026-11-18 ｜ 影响：HUD 与终端完成行的耗时展示（不涉及功能正确性） ｜ 状态：已修复，待实机复看

## 1. 问题现象

用户在 REPL 里问「当前目录下都有什么」，两条 `Bash` 分别耗时 467ms 与 151ms，
但驾驶舱三格指标写「耗时 ELAPSED **7.6s**」，终端完成行也是「… 0 失败 · 7.6s」。
用户的反馈原话是「这个统计时间一点不准，给我的感觉是一瞬间完成回答的，怎么统计出来
是 7.6s，太假了」。

复现方式：任意一轮带工具调用的对话，把 HUD 的「耗时」与执行序列里各步的 `ms`
放在一起看——两者差了十几倍。

## 2. 排查过程

| 阶段 | 假设 | 做法 | 结论 |
|---|---|---|---|
| 1 | HUD 的起算点不对，把用户输入前的时间也算进来了 | 读 `agent/ui/cockpit.py` | 排除：三格用的是 `elapsed_ms()`，结束时直接取 `run_end.duration_ms` 权威值 |
| 2 | `finish_run` 被延迟调用（存在 end timer） | 读 `agent/core/trace.py::finish_run` | 排除：`duration = monotonic() - self._started`，当场算差值 |
| 3 | 干脆看真实数据 | 读用户机器上这一轮的 trace：`~/.jarvis/traces/20261118-185646-15ec45.jsonl` | **关键转折**：逐事件时间戳完全对得上 7.58s，统计没有算错 |

实证时间线：

```text
18:56:46.629  run_start
18:56:49.513  tool_start  Bash      ← 2.884s：等模型决定调哪个工具
18:56:49.981  tool_end    467ms
18:56:51.316  tool_start  Bash      ← 1.335s：等模型看结果后决定下一步
18:56:51.468  tool_end    151ms
18:56:54.203  run_end               ← 2.735s：等模型组织并逐字输出回答
             duration_ms = 7580
```

工具净耗时合计 **0.62s（8%）**，等模型/生成合计 **6.95s（92%）**。

## 3. 根因分析

不是计时错误，是**展示口径**问题：三格指标里那个「耗时」是整轮墙钟（含等模型），
而执行序列里每一步的 `ms` 是工具净耗时。两者同屏出现，却没有任何一处说明差额在哪，
用户自然把它们当成"总和与分项"，一对比就觉得数字造假。

叠加一层感知偏差：用户眼睛看到的是最后 2.7s 的流式输出，而前面 4.2s 是两段**没有任何
进度反馈**的等待（等首 token），静默时间在主观上会被压缩——于是"感觉一瞬间"与
"显示 7.6s"同时成立。

## 4. 修复方案

把整轮耗时显式拆成两段，并在同一处展示：

1. **数据源**（`agent/core/trace.py`）：`on_tool_after` 把每步的 `(started, ended)` 记进
   `_tool_spans`；`finish_run` 用**区间并集** `_union_ms()` 求工具占用的墙钟，写进
   `run_end.tool_ms`。用并集而不是相加，是因为工具可以并行（`active` 里同时挂多个），
   相加会把重叠段算两遍，甚至拆出比整轮还大的"工具时间"。
2. **展示**（`agent/ui/cockpit.py`、`cockpit_window.py`）：`CockpitState.split_ms()`
   返回 `(工具, 模型)`，模型段 = 整轮 − 工具段，并 clamp 掉极端场景下的负数；
   `completion_line()` 输出 `共 2 步 · 7.6s（模型 7.0s · 工具 0.6s） · 窗口保留`。
3. **终端**（`agent/ui/trace_view.py`）：完成行同源追加同样的括号。
4. **不伪造缺失数据**：旧 trace 文件没有 `tool_ms`、或工具段为 0 时，保持原来的短句式，
   不显示「模型 0ms」——那会把"没有数据"伪装成"模型没花时间"。

刻意**没做**的两件事：三格指标的数字不变（它本来就是整轮，是对的，只是标签语义更明确了）；
也没有去改"静默 4 秒"的体感（那是另一个问题，需要先定首帧占位的交互）。

## 5. 验证结果

- **端到端构造**：`300ms 等待 → 100ms 工具 → 200ms 等待`，得到
  `duration_ms=606 / tool_ms=102`，两处文案均为
  `606ms（模型 504ms · 工具 102ms）`，与手算一致。
- **新增 12 例测试**：
  - `tests/core/test_trace.py`：区间并集 4 组（串行/重叠/包含/乱序零长）、`run_end` 必带
    `tool_ms`、两段工具之间的空闲不计入工具段；
  - `tests/ui/test_cockpit.py`：拆分取值、运行中不拆、旧 trace 不拆、clamp 防负数、
    HUD 文案两态；
  - `tests/ui/test_trace_view_run_end.py`：有 `tool_ms` 才出现括号、缺失与 0 都保持原句。
- **反向变异验牙**：把 `run_end` 里的 `tool_ms` 注释掉，
  `test_run_end_carries_tool_ms` 与 `test_tool_ms_excludes_idle_between_tools` 立刻红
  （`KeyError: 'tool_ms'` / 断言失败），其余用例仍绿。
- 相关三个测试文件合计 **71 passed**。
- **待人工**：下一轮真实对话里看 HUD 与终端是否显示 `模型 x.xs · 工具 x.xs`。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/core/trace.py` | 新增 `_union_ms()` 与 `_tool_spans`；`run_end` 写入 `tool_ms` |
| `agent/ui/cockpit.py` | `CockpitState.final_tool_ms` / `split_ms()`；新增 `completion_line()` |
| `agent/ui/cockpit_window.py` | 完成带第二行改用 `completion_line()` |
| `agent/ui/trace_view.py` | 终端完成行追加「模型/工具」拆分 |
| `tests/core/test_trace.py` | 并集、`tool_ms`、空闲不计入 3 例 |
| `tests/ui/test_cockpit.py` | 拆分与 clamp、HUD 文案 6 例 |
| `tests/ui/test_trace_view_run_end.py` | 终端渲染 3 例（新增文件） |
| `docs/plans/visualization/terminal-cockpit.md` | 补「完成带第二行」与三格口径 |
| `docs/log/CHANGELOG.md` | Unreleased 记录 |

## 7. 经验总结

- **"数字看起来假"多半不是算错，而是两个口径被摆在同一屏**。凡是同一界面出现两个时间/
  两个计数，先问一句：它们能相加吗？不能就得分别标注，否则用户只能自己脑补关系。
- **并行耗时必须用区间并集，不能 sum**。`sum(duration)` 在有并行工具时既会高估，也可能
  超过墙钟，反而制造出新的"假数据"。
- **用真实 trace 反推时间线，比读代码猜快得多**。这轮前两个假设（起算点错、收尾延迟）
  都被 trace 的六个时间戳一次性否掉了。
- **展示层绝不伪造缺失字段**。旧 trace 没有 `tool_ms` 就不显示拆分，宁可少说一句，
  也不输出「模型 0ms」这种会被当成结论的错误信息。
- 这类"用户觉得不对但代码没坏"的问题，复盘的价值不在于修了什么，而在于把**口径**
  写进文档（`terminal-cockpit.md` §13.3）与测试，避免下次又靠人解释。
