# 子代理任务卡（C3-lite.2）工程方案

> 来源：[cockpit-multicards.md](cockpit-multicards.md) 的多任务浮窗扩展。
> 状态：**已实现（2026-11-08）**，待用户实机体验验收。
> 范围：终端独立 HUD 旁边的“每个子代理/队友一张卡”；不改抓取拓扑、不做桌面端改动。
> 前置：C1b 主 HUD、C3-lite 工具浮窗已落地。
> 作者：aceFelix

---

## 1. 背景与已定取舍

C3-lite 的浮窗卡是“每个运行中工具一张”，粒度到工具。子代理再套用这套会产生
两个问题：

1. 一个子代理会连续调十几次工具，弹十几张卡，屏幕被瞬间铺满；
2. 子代理工具与主任务工具事件混在一起，主 HUD 的步数/失败数也被污染。

用户 2026-11-08 已拍板的四个取舍：

| 项 | 决策 |
|---|---|
| 卡片粒度 | **每个子代理/队友一张卡**，卡内显示当前工具与步数，不按工具弹卡 |
| 终端原始输出 | **保留**：子代理的工具卡/流式文本继续打印到终端，同时更新代理卡 |
| 队友空闲 | **保留空闲卡**（暗色，显示“空闲/等待任务”），超上限时优先收空闲卡 |
| 布局 | **主 HUD 左侧第二列**：工具卡紧贴 HUD，代理卡再往左一列 |

由此得到两个硬性工程结论：

- 不能直接套用现有 `AgentActivityUI`——它会把子代理输出收进台账、不再打印到终端，
  与“保留终端原始输出”冲突；需要新增 **TeeActivityUI**（转发 + 记账）。
- 主任务的工具浮窗必须过滤子代理工具事件，否则同一动作会出现“代理卡 + 工具卡”两份。

---

## 2. 现状与可复用资产（已核对代码）

| 事实 | 位置 |
|---|---|
| 已有代理活动台账 `AgentActivity`，字段覆盖卡片所需：agent_id/name/role/mode/team/status/task/current_tool/activity/output/iterations/tool_calls/error | `agent/collaboration/activity.py:37-92` |
| 台账已有节流通知、约 300ms 合并；状态跃迁立即通知 | `activity.py:95-192`、`_notify` |
| 目前只有一个 `_listener`，`has_listener()` 只服务桌面右栏 | `activity.py:211-224` |
| `run_subagent` 的 `activity_id` 是可选参数；终端入口 `subagent_tool._run_sync` / `_run_batch` **已生成并传入**（`sub-<type>-<hex>`），本次只补批量展示名 | `agent/collaboration/subagent.py:326-389`、`agent/tools/collaboration/subagent_tool.py:200-279` |
| 子代理/队友写入台账：start/update/finish 均已就绪 | `subagent.py:369-509`、`teammate.py:214-475` |
| `AgentActivityUI` 会把子代理的 tool_use/tool_result/text/thinking 收进台账、不打印 | `activity.py:289-355` |
| `ToolContext` 有 `extra` 自由字典，可放 `agent_id` 等元数据 | `agent/core/context.py:93-120` |
| trace 事件当前只有 `run_id`，没有 `agent_id`，主任务卡无法区分来源 | `agent/core/trace.py:_emit` |

---

## 3. 总体架构

```text
subagent / teammate
   │  工具调用、思考、文本
   ├─ TeeActivityUI（新）── 转发给 parent_ui（终端照常打印）
   │        └─ update_agent(current_tool/status/activity/output)
   │
   └─ AgentActivityRegistry（已有）
            ├─ set_listener       → 桌面右栏（不变）
            └─ add_observer(新)   → CockpitWindow.feed_agents(快照)
                                           │
                              Tk 线程 AgentCardPlanner
                                           │
                              AgentCardWindow（左侧第二列）
```

主任务工具卡仍走原链路：

```text
TraceRecorder → cockpit_cards.CardPlanner（忽略带 agent_id 的事件）
```

两条链路互不替代：工具卡回答“主任务这一步在做什么”，代理卡回答“哪个子代理在做什么”。

---

## 4. 需要补的四处管线

### 4.1 台账多观察者（不改桌面单监听器）

`AgentActivityRegistry` 新增：

```python
def add_observer(self, cb: Callable[[list[dict]], None]) -> None: ...
def remove_observer(self, cb) -> None: ...
def has_card_observer(self) -> bool: ...
```

- `_notify` 把快照同时发给桌面 listener 与所有 observer；
- observer 异常一律吞掉，不能影响台账写入；
- `clear(keep_listener=True)` 保留 observer，进程退出时调用 `remove_observer`；
- 桌面 `set_listener` 语义完全不变。

### 4.2 TeeActivityUI（保留终端输出，正文整段缓冲）

新增 `agent/collaboration/activity.TeeActivityUI`（子代理与队友都走它）：

| 方法 | 行为 |
|---|---|
| `assistant_text` | **累积整段正文并实时更新台账**，不在片段级别写终端；新正文开始时先把思考块整段输出 |
| `assistant_thinking` | 累积思考块 + `status="thinking"`，不喂共享思考面板（防多代理混流） |
| `tool_use` | 先 `flush()`（思考→正文整段输出），再转发 parent_ui，再更新当前工具 |
| `tool_result` | 先 `flush()`，再转发 parent_ui，再记录返回摘要 |
| `flush()` | 本轮结束/工具边界把整段思考与正文一次性交给宿主 |
| 其它方法 | 原样转发（`__getattr__`） |

**为什么要整段缓冲**（2026-11-08 实机）：两个队友并行输出 Markdown 表格时，逐片段转发
会让两张表的列逐列交错，终端看起来完全错位；整段输出保证每张表完整、各段之间有换行。

选择逻辑（`run_subagent` / `TeammateRunner._run` 内）：

| 条件 | 子代理/队友 UI |
|---|---|
| 桌面 `set_listener` 已注册 | `AgentActivityUI`（收进台账，桌面行为不变） |
| 无桌面 listener，但 `has_card_observer()` | `TeeActivityUI`（终端输出整段保留 + 台账更新） |
| 都没有 | `parent_ui`（旧行为，零变化） |

### 4.3 终端路径的 `activity_id`（已存在，本次只补展示名）

- `subagent_tool._run_sync` 已生成 `sub-<agent_type>-<8位hex>`；`_run_batch` 为每个
  并行任务各生成一个，一次性子代理与批量路径都已传入 `run_subagent`；
- 本次补：批量路径增加 `activity_names`，展示名带 id 后缀（如 `explorer-3f2a`），
  避免多张同名卡无法区分；
- 队友已有 `name@team` 形式的 `agent_id`，直接复用，不改写；
- 没有 `activity_id` 时静默不记台账、不弹代理卡（旧调用方零变化）。

### 4.4 主任务工具卡过滤子代理事件

- `run_subagent` 在 `sub_ctx.extra` 写入：
  ```python
  {"agent_id": activity_id, "agent_name": activity_name, "agent_mode": "subagent"}
  ```
- `TraceRecorder.on_tool_before/after` 从 `payload["ctx"].extra` 读取，写入 trace 事件：
  `agent_id` / `agent_name` / `agent_mode`（缺失则省略，不写假值）；
- `CockpitState.update` 与 `cockpit_cards.CardPlanner` 对带 `agent_id` 的
  `tool_start` / `tool_end` **直接忽略**（不增加主任务步数/失败，不弹工具卡）；
- `/trace` 回看时在行尾追加 `· @agent_name`，便于区分来源；
- 子代理的 trace JSONL 照常落盘，不破坏审计。

---

## 5. 卡片设计

### 5.1 形态

- 尺寸约 `300×140`，独立 `Toplevel`，无边框、深色、置顶、不抢焦点；
- 与工具卡区分配色：工具卡青色，代理卡**琥珀色**边框与状态点，右上角 `AGENT` 标签；
- 内容五行：

```text
┌──────────────────────────────┐
│ ◆ Explorer-1        AGENT 3.2s│
│ explorer · 子代理             │
│ 调研 React 19 的 breaking...  │
│ 调用 Grep：src/hooks/         │
│ 3 轮 · 5 工具 · 运行中        │
└──────────────────────────────┘
```

- 角色徽章：`explorer` / `researcher` / `coder` / `general`；
- 队友模式显示 `队友 · teamName`；
- `idle` 状态整卡降低亮度，显示“空闲 · 等待任务”；
- `done` / `failed` / `stopped` 显示结果色，`done/failed` 保留 `agent_card_hold_ms` 后收起，
  `stopped` 立即收起。

### 5.2 布局

```text
代理卡列        工具卡列        主 HUD
┌─────┐        ┌─────┐        ┌──────┐
│agent│        │tool │        │      │
└─────┘        └─────┘        │      │
   ┌─────┐        ┌─────┐      │      │
   │agent│        │tool │      │      │
   └─────┘        └─────┘      └──────┘
```

- 代理卡槽位：`x = hud_x - 2*CARD_W - 32`，`y = hud_y + slot*(CARD_H+10)`；
  **不再斜向重叠**，而是纵向完全散开（用户 2026-11-08 实测反馈“卡片叠在一起看不清”）；
- 代理卡**按住卡面即可拖动**，拖动后位置在本会话内保留，不会被状态刷新重置；
- 工具卡仍在 `hud_x - CARD_W - 16`；
- 屏幕左边界不够时向左夹到 8px；屏幕底部放不下时向上夹住；
- 最多 `agent_card_max`（默认 3）；超出的代理只在主 HUD 显示 `+N 代理`；
- 主 HUD 隐藏时，代理卡与工具卡一起隐藏；下一轮有活跃代理时一起重现。

### 5.3 生命周期

| 台账状态 | 卡片 |
|---|---|
| `starting` / `running` / `thinking` | 显示（琥珀色，扫描线可复用 `effects` 开关） |
| `idle` | 保留（暗色）；`agent_card_keep_idle=false` 时收起 |
| `done` / `failed` | 显示结果，保留 `agent_card_hold_ms` 后收起 |
| `stopped` | 立即收起 |
| 台账 `clear()` / 进程退出 | 全部销毁 |

出现阈值：`agent_card_min_ms` 默认 0（子代理通常 >1s，无需防闪）；需要防快子代理时再调。

---

## 6. 配置管理

继续放在 `[visualization]` 段，受总开关 `enabled` 约束：

```toml
[visualization]
# ...已有的 task_cockpit / tool_cards ...

# 子代理 / 队友任务卡
agent_cards = "auto"          # auto / off；off = 完全回到旧终端行为
agent_card_max = 3            # 同时最多几张（1–6）
agent_card_hold_ms = 800      # done/failed 保留时长
agent_card_keep_idle = true   # 队友空闲时保留暗色卡
agent_card_min_ms = 0         # 出现阈值；0 = 立即
```

校验：枚举未知 → auto；`agent_card_max` clamp 1–6；hold clamp 0–3000；
min clamp 0–5000；布尔解析失败 → true/false 对应默认。

行为矩阵：

| 配置 | 终端原始输出 | 代理卡 | 台账 |
|---|---|---|---|
| `agent_cards=off` | 保留 | 无 | 旧行为（无 observer，子代理仍走 parent_ui） |
| `agent_cards=auto`、无桌面 listener | **保留（整段输出）** | 有 | 子代理/队友都用 TeeActivityUI 记账 |
| 桌面 listener 已注册 | 收进桌面右栏（不变） | 桌面不弹终端卡 | AgentActivityUI（不变） |
| `enabled=false` | 保留 | 无 | 全部关闭 |

---

## 7. 文件与模块规划

| 文件 | 类型 | 职责 | 预计行数 |
|---|---|---|---|
| `agent/collaboration/activity.py` | 修改 | 多 observer API + `TeeActivityUI` + `has_card_observer` | +≈ 80 |
| `agent/collaboration/subagent.py` | 修改 | 终端 path 生成/传递 `activity_id`；Tee UI 选择；`sub_ctx.extra` 写 agent 元数据 | +≈ 35 |
| `agent/core/trace.py` | 修改 | 从 `ctx.extra` 读 agent_id/name/mode 写入事件 | +≈ 20 |
| `agent/ui/cockpit_agents.py` | **新增** | `AgentCardSpec` / `AgentCardPlanner`（纯逻辑）+ `AgentCardWindow` / `AgentCardManager` | ≈ 330 |
| `agent/ui/cockpit.py` | 修改 | 主状态忽略 agent 事件；代理卡计数摘要（可选） | +≈ 20 |
| `agent/ui/cockpit_cards.py` | 修改 | 工具卡 planner 忽略带 `agent_id` 的事件 | +≈ 10 |
| `agent/ui/cockpit_window.py` | 修改 | 注册 observer、`feed_agents`、Tk 线程同步代理卡、关闭/退出联动 | +≈ 90 |
| `agent/ui/trace_view.py` | 修改 | `/trace` 行尾标注 `@agent_name` | +≈ 10 |
| `agent/ui/hud_theme.py` | 修改 | 新增琥珀色/代理卡字体常量 | +≈ 10 |
| `agent/config/{settings,settings_domains,catalog}.py`、`configs/settings.example.toml` | 修改 | 新增 5 个 `agent_card*` 配置 | +≈ 25 |
| `tests/collaboration/test_activity_observers.py` | **新增** | observer 广播、异常隔离、Tee UI 转发与记账 | ≈ 140 |
| `tests/ui/test_cockpit_agents.py` | **新增** | 状态映射、空闲保留、上限、收起、换轮清理 | ≈ 170 |
| `tests/ui/test_cockpit_cards.py` | 修改 | agent_id 事件被工具卡忽略 | +≈ 20 |
| `README.md` / `README.en.md`、`CHANGELOG.md` | 修改 | 功能与配置说明 | — |

单文件仍 ≤900 行；`cockpit_window.py` 预计 800 行左右，接近上限，若超先把主 HUD 渲染
拆到 `cockpit_hud.py`（S0 预留）。

---

## 8. 实施顺序

| 步骤 | 内容 | 验证 |
|---|---|---|
| S0 | 本文评审通过；确认 `TeeActivityUI` 保留终端输出、代理卡列布局 | 用户确认 |
| S1 | 台账多 observer + TeeActivityUI + 单测 | `pytest tests/collaboration/` |
| S2 | 终端 path 的 `activity_id` 生成与传递；`ctx.extra` 元数据 | 子代理运行后台账有记录 |
| S3 | trace 带 agent 元数据；主工具卡/主状态过滤 | `pytest tests/core/test_trace.py tests/ui/test_cockpit_cards.py` |
| S4 | `AgentCardPlanner` 纯逻辑 + 单测 | `pytest tests/ui/test_cockpit_agents.py` |
| S5 | `AgentCardManager` Tk 窗口 + 真实 GUI 探针 | 手动 + 截图 |
| S6 | 配置四处 + README/CHANGELOG/AOCI + 全量测试 | `pytest tests/ -q` |

S1–S4 不碰 Tk，可先落地；S5 是唯一需要真实窗口验证的一步。

---

## 9. 人工验收

1. 让主代理派生 2 个子代理 → 左侧第二列出现 2 张琥珀色代理卡，各显示任务、当前工具、轮次；
2. 子代理调工具时，**终端仍打印原有工具卡与文本**（用户选择保留），代理卡同步刷新；
3. 子代理完成 → 卡片显示 ✓，800ms 后收起；
4. 队友空闲 → 暗色“空闲 · 等待任务”卡保留；有任务时恢复亮度；
5. 并行 4 个代理、上限 3 → 只显示 3 张，主 HUD 显示 `+1 代理`；
6. 主 HUD 点 ✕ → 主 HUD、工具卡、代理卡一起隐藏，jarvis 不退出；下一轮有活跃代理时重现；
7. `agent_cards = "off"` → 重启后终端行为完全恢复旧样：无代理卡，子代理输出照旧；
8. 桌面端（serve）→ 右栏「代理团队」不变，终端代理卡不出现（桌面有 listener 分支）；
9. 退出进程 → 无 `Tcl_AsyncDelete` 崩溃。

---

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| 终端输出与代理卡信息重复 | 用户已选择保留输出；卡片只做“哪个代理在做什么”的汇总，不复述工具结果 |
| 子代理没有 `activity_id` | 终端入口统一生成 `sub-<type>-<hex>`；无 id 时静默不弹代理卡 |
| 嵌套子代理导致卡爆炸 | `agent_card_max` 截断；超出只在主 HUD 显示 `+N`；未来做分组 |
| 队友空闲卡长期占位 | 暗色 + 上限；超上限优先收 idle；可配 `agent_card_keep_idle=false` |
| 子代理工具污染主 HUD 步数 | trace 带 agent_id，主状态/工具卡双重过滤 |
| 桌面右栏与终端代理卡冲突 | 桌面 listener 优先，终端卡只在无 listener 时启用 |
| Tk 线程/退出崩溃 | 复用现有 CardManager 的 detach 机制；所有 Tk 操作只在 HUD 线程 |
| `cockpit_window.py` 接近 900 行 | 超限先拆 `cockpit_hud.py` |

---

## 11. 本轮不做

- ❌ 代理卡点击展开完整输出/日志；
- ❌ 代理依赖拓扑（谁派生了谁）；
- ❌ 子代理卡在桌面上重复出现（桌面已有右栏）；
- ❌ 嵌套代理分组/折叠；
- ❌ 代理卡拖动或独立关闭按钮。
