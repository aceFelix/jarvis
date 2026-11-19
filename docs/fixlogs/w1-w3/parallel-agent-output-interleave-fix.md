# 并行子代理/队友输出表格错位修复复盘

> 模块：终端代理卡（C3-lite.2）/ 子代理与队友 UI 输出
> 发现时间：2026-11-08（用户实机，派 4 个 explorer 队友后截图）
> 修复时间：2026-11-09
> 作者：aceFelix

---

## 1. 问题现象

用户同时派生多个 explorer 队友做目录统计后，终端输出出现明显错位：

- 两个队友的 Markdown 表格被**逐列交错**，例如：

  ```text
  `tests | `da/serve/` (`emon/` | 11含 `serve/ |
  `memory/` | 8 |
  ✓ 完成 · 共 11 步 · 文件工具 0 次 · 0 失败 · 11.8s
  maintenance/` | engine/` 20 个) | 30 |
  ```

- 主任务的 `✓ 完成 ...` 汇总行插在表格中间；
- 视觉上像“思考信息流/回答流”整体错位，实际是**多条流式输出并发写入同一个终端**；
- 子代理/队友的代理卡本身正常（名字、任务、轮次都能更新）。

复现路径：终端启动 jarvis → 让主代理同时派 2–4 个 explorer 队友/子代理 →
让它们各自输出 Markdown 表格或长报告。

---

## 2. 排查过程

### 阶段 1：先怀疑驾驶舱遮挡

截图左侧终端文字被代理卡盖住一部分，最初怀疑是 HUD 浮窗遮挡导致“看起来错位”。
裁剪放大后确认：被遮挡区域之外，表格本身也是列列交错的，**不是遮挡问题**。

### 阶段 2：检查输出链路

终端子代理输出有两条路径：

1. `run_subagent`：有 `activity_id` 时按条件选 `AgentActivityUI` / `TeeActivityUI` / `parent_ui`；
2. `TeammateRunner._run`：**只判断 `has_listener()`，否则直接 `parent_ui`**。

也就是说，终端里的后台队友**完全绕过了 `TeeActivityUI`**，多个队友的
`assistant_text` 片段直接写同一个 `RichCLI`。这是主要来源。

### 阶段 3：确认片段级写入无法保证整行完整

即使子代理走了 `TeeActivityUI`，它也是“每个流式片段先转发 parent_ui”。
`RichCLI.assistant_text` 每次只打印一个片段并追加到共享的 `_assistant_buf`；
多个代理交替调用时，Rich Console 的单次 `print` 虽然线程安全，但**片段之间**
仍会交错，一张 Markdown 表格会与另一张逐列穿插。

结论：要保证表格完整，必须在**整段 assistant 消息**粒度上输出，而不是逐片段转发。

---

## 3. 根因分析

1. **队友未接入 TeeActivityUI**：终端队友直接复用主 UI，多个队友并发流式写入；
2. **TeeActivityUI 逐片段转发**：即使接入，片段级写入仍会让多张表交错；
3. **思考面板共用**：子代理/队友的 `assistant_thinking` 直接喂同一个
   `ThinkingPanel`，多代理思考会混在同一个面板里；
4. **主任务 `run_end` 汇总是单行原子输出**，与仍在流式输出的队友正文交错，
   视觉上插在表格中间。

---

## 4. 修复方案

### 4.1 队友接入 Tee

`agent/collaboration/teammate.py` 的 `_run` 中选择 UI 的口径与 `run_subagent` 对齐：

| 条件 | UI |
|---|---|
| 桌面 `has_listener()` | `AgentActivityUI`（桌面右栏，行为不变） |
| 终端 `has_card_observer()` | `TeeActivityUI`（转发 + 记账） |
| 都没有 | `parent_ui`（旧行为） |

### 4.2 TeeActivityUI 改为整段缓冲

`agent/collaboration/activity.py`：

- `assistant_text`：只累积到 `_text_buf` 并更新台账，**不逐片段写终端**；
- `assistant_thinking`：累积到 `_think_buf`，只更新卡片状态，**不喂共享思考面板**；
- `tool_use` / `tool_result`：先 `flush()`，再转发工具卡/结果卡；
- `flush()`：把思考块（带 `💭 代理名` 前缀）与整段正文一次性交给宿主；
- 正文末尾统一补换行，两个代理先后 flush 不会粘成一段；
- `flush_ui()` 供 runner 在成功/异常/停止路径统一冲刷。

### 4.3 runner 收尾冲刷

- `run_subagent` 在成功、异常、`CancelledError` 三条路径都先 `flush_ui(sub_ui)`，
  再更新台账/打印“完成”；
- `TeammateRunner._execute_turn` 在 `loop.run()` 成功与异常后都先冲刷，
  再标记 `idle`/`failed`。

这样每个代理的每条 assistant 消息都是**原子输出**；多个代理依次输出完整块，
不再出现列列交错。

---

## 5. 验证结果

- 新增单测：
  - `test_tee_ui_forwards_then_records`：正文分片不立即写终端，`flush()` 后整段输出；
  - `test_tee_ui_flushes_text_before_tool_card`：工具调用前先输出整段正文；
  - 思考不喂共享面板，只在卡片台账里标记 `thinking`。
- 相关测试：`tests/collaboration/test_activity_observers.py`、
  `tests/ui/test_cockpit_agents.py`、`tests/ui/test_cockpit_cards.py`、
  `tests/core/test_trace.py` 全部通过（66 passed）。
- 全量测试：`4662 passed, 2 skipped`（修复前基线同量级）。
- 待用户按原 4 子代理场景复测终端表格是否完整、无交错。

---

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/collaboration/activity.py` | `TeeActivityUI` 整段缓冲、思考块输出、`flush_ui` |
| `agent/collaboration/teammate.py` | 队友在终端接入 `TeeActivityUI`；轮次结束冲刷 |
| `agent/collaboration/subagent.py` | 成功/异常/停止路径冲刷 Tee 缓冲 |
| `tests/collaboration/test_activity_observers.py` | 新语义单测 |

---

## 7. 经验总结

- **观测层不能假设“片段级转发”是安全的**：多个 writer 共用一个 UI 时，
  必须按消息/段落原子输出，否则 Markdown 表格、代码块一定会互相穿插。
- **终端与桌面的 UI 选择口径要成对维护**：`subagent` 和 `teammate` 曾各自
  判断 listener，漏掉一方就会出现“子代理正常、队友错位”的半修复状态。
- **共享思考面板只能有一个 writer**：多代理并行时，思考应进各自卡片或按块
  带名前缀输出，不能继续喂同一个 `Live`。
- 以后新增任何“后台代理”入口，先确认它走的是 `AgentActivityUI` /
  `TeeActivityUI` / `parent_ui` 中的哪一支。
