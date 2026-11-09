# 终端任务驾驶舱（C1）工程实施计划

> 来源：[jarvis-cinematic-visualization.md](jarvis-cinematic-visualization.md) 的 C1 阶段。
> 状态：**已实施（C1b 独立窗口，2026-11-08）**。原「终端底部内嵌 Rich Live」方案经用户
> 实机评估否决（终端里一闪而过、且不是想要的钢铁侠感），§2–§12 保留为历史决策记录，
> **当前实现与验收口径以 §13 为准**。
> 范围：只做终端进程拉起的独立 HUD 窗口；C2 ASCII 拓扑、C3/C4 桌面浮窗仍不在本文。
> 作者：aceFelix

---

## 1. 背景与目标

现状：一轮任务里用户看到的是**逐行滚动**的工具卡片 + trace 单行（`▶ Tool` / `✓ 12ms`）。
任务超过 5 步后，用户很难回答三个问题：

1. 现在进行到第几步、还在做什么？
2. 有几步失败了、总耗时多少？
3. 抓了哪些页面、拿到哪些字段？

C1 目标：在**交互式终端**里加一块稳定的「任务驾驶舱」，实时汇总上面三类信息；
滚动区照旧保留原有工具卡片与 trace 行。**输入行不受影响**，非交互环境自动退回现状。

形态先说清楚：驾驶舱是**同一个终端窗口内的底部区域**（Rich `Live` 渲染块），
**不新开窗口、不新开标签页、不唤起桌面窗口**。它在工具执行期间出现、结束即擦除；
桌面上的多浮窗任务卡属于 C3，不在本轮范围。想"单独开一个窗口"应走 C3，而不是改 C1。

验收标准（人工 + 单测）：

- 交互终端执行 ≥3 步任务时，工具执行期间底部出现驾驶舱，步骤/失败/耗时随事件变化；
- 驾驶舱出现期间用户正在输入的行不会被擦除、不会串行；
- `--json`、`serve`、语音模式、管道/重定向输出下，**零输出、零行为变化**；
- `[ui] visual_cockpit = "off"` 可完全关闭，行为与 2.5.1 一致。

---

## 2. 现状与硬约束（已核对代码）

| 事实 | 位置 |
|---|---|
| 终端 UI 是 `RichCLI(UIProtocol)`，用 Rich `Console`（`_CappedConsole`）+ prompt_toolkit `PromptSession` | `agent/ui/cli.py:149-185`、`agent/ui/cli.py:718-735` |
| 思考过程已有独立 Rich `Live`（12fps，`transient=False`），在正文到达或 `end_thinking()` 时 stop | `agent/ui/cli.py:207-322` |
| 工具卡片 `tool_use`、结果 `tool_result` 都是静态 `Panel` 打印，不持有 Live | `agent/ui/cli.py:327-548` |
| 调用顺序：`ui.tool_use`（画卡片）→ hook `tool_before` → 执行 → `ui.tool_result` → hook `tool_after` | `agent/core/orchestrator.py:285/334/500/502` |
| trace 事件由 `TraceRecorder._emit()` 统一写 JSONL 并调 `render_event()` 上屏 | `agent/core/trace.py:441-462`、`agent/ui/trace_view.py:54-91` |
| 观测层在 `install_observability(settings, ui)` 装配，失败不拦启动 | `agent/core/trace.py:495-553`、`agent/main.py:329-340` |
| 用户输入行只在**两轮之间**阻塞等待；工具执行期间输入行不活跃 | `agent/ui/cli.py:718-735` |

由此得到两条硬约束：

1. **同一时刻不能有两个 Rich Live**（驾驶舱与思考面板必须互斥）。
2. **不能在 Live 活跃时随意 `console.print`**：工具结果卡片是动态文本，交给 Rich 的
   "打印到 Live 上方"行为不稳（`_CappedConsole` 还做了宽度封顶）。所以设计成
   **打印前先收 Live，事件到达再重开**，不依赖 Rich 的嵌套打印语义。

---

## 3. 总体设计

### 3.1 数据流

```
hooks(tool_before/tool_after/file_changed)
        │
        ▼
TraceRecorder._emit(event)
        ├─ ① 写 ~/.jarvis/traces/<run>.jsonl        （不变）
        ├─ ② render_event(event) → ui.info(line)     （不变：滚动行保留）
        └─ ③ ui.on_trace_event(event)                  （新增：驾驶舱增量）
                 │
                 ▼
        Cockpit.update(event) → Cockpit.render() → Rich Live(transient=True)
```

驾驶舱**只消费事件、不产生事件**，不读写文件、不碰工具、不影响 JSONL。

### 3.2 驾驶舱 Live 生命周期（精确到调用点）

| 时机 | 调用点 | 动作 |
|---|---|---|
| 工具卡片打印完 | `RichCLI.tool_use` 末尾 | 不启动（卡片先落到滚动区） |
| 工具开始 | `tool_start` 事件 → `on_trace_event` | `cockpit.start()`（若未启动）+ `update` + `refresh` |
| 工具结果打印前 | `RichCLI.tool_result` 开头 | `cockpit.stop()`（`transient=True` 自动擦除帧，避免与结果卡片抢屏） |
| 工具结束 | `tool_end` 事件 | `cockpit.start()` + `update`（把该步标记 ✓/✗，重新显示） |
| 模型开始流式正文 | `RichCLI.assistant_text` 首行 | `cockpit.stop()`（正文要占屏幕，驾驶舱让位） |
| 模型开始流式思考 | `assistant_thinking` 首次可见增量 | 先 `cockpit.stop()`，再启动思考 Live（**杜绝嵌套**） |
| 思考收口 | `end_thinking()`（`QueryLoop` 的 finally） | `cockpit.stop()` 兜底 |
| 新一轮输入前 | `read_user_input()` 开头 | `cockpit.stop()` 兜底（保证输入行绝对干净） |
| 一轮结束 | `run_end` 事件 | `update` 后 `stop()`；不再打印终帧（滚动区已有 `✓ 完成 · …` 行） |
| 异常 / Ctrl+C / 中断 | 上述 finally 与输入兜底 | 同 `stop()`，幂等 |

设计理由：

- 驾驶舱只在**工具真正执行**的窗口显示——这正是"看不到它在干什么"的痛点；
- 模型流式输出阶段终端需要展示正文/思考，驾驶舱会挤占可视高度且与 thinking Live 冲突；
- `transient=True` 让每次 stop 自动擦除，不会在屏幕上堆叠 N 块过期面板；
- `read_user_input` 是最后一道保险：无论前面发生什么，输入前一定没有 Live。

### 3.3 布局与降级

三块面板，自下而上占用终端底部固定高度（默认最多 10 行，不含边框）：

```
┌─ 任务 ────────────────────────────────────────────────┐
│ ● BrowserNavigate  https://example.com  步骤 3 · 0 失败 · 4.2s │
│ ✓ WebFetch  /a  320ms   ✗ WebFetch /b  超时                   │
└───────────────────────────────────────────────────────┘
┌─ 数据流 ──────────────────────────────────────────────┐
│ 12:01:02 ▶ WebFetch (https://example.com/a)            │
│ 12:01:02 ✓ 320ms                                       │
│ 12:01:03 🪟 已把「Chrome」置于前台                      │
└───────────────────────────────────────────────────────┘
┌─ 抓取 ────────────────────────────────────────────────┐
│ 状态 │ URL                    │ 耗时   │ 字节           │
│ ✓    │ https://example.com/a  │ 320ms  │ 12KB           │
└───────────────────────────────────────────────────────┘
```

降级矩阵（按 `console.width` / `console.height` / 终端能力判定，逐级回退）：

| 条件 | 形态 |
|---|---|
| `is_terminal=False`、`--json`、`serve`、`visual_cockpit=off` | 不创建驾驶舱，`on_trace_event` 直接 return |
| `height < 20` 或 `width < 80` | 只显示「任务」单面板（1 行摘要 + 最近 2 步），最多 4 行 |
| `20 ≤ height < 30` | 「任务」+「数据流」两块，最多 7 行 |
| `height ≥ 30` 且存在抓取事件 | 三块全显，最多 10 行 |
| 终端尺寸变化 | `Live` 每次刷新时读取 `console.size`，按上表重算 |

### 3.4 显示阈值与防闪（2026-11-08 补充）

短任务（例如“用火狐打开贪吃蛇”：6 步、13.5 秒、多数工具 <1s）如果每步都闪一下
底部面板，价值极低，还会打断阅读。因此 `auto` 模式带**出现阈值**：

| 条件 | 行为 |
|---|---|
| 前 2 个工具步 | 不显示（继续只用滚动区 trace） |
| 第 3 个工具步到达，或单个工具持续 ≥500ms，或本轮**工具执行**累计 ≥3s（不含模型思考/网络等待） | 首次显示 |
| 首次显示之后 | 本轮后续**每个工具阶段**都显示，不再逐步重新判断（防闪） |
| 本轮结束 / 回到输入行 | 擦除，不留终帧 |
| 短任务未达阈值 | 全程不出现，输出与未安装该功能时完全一致 |

阈值只在 `visual_cockpit="auto"` 下生效；后续若要“每次必显”，再加 `"always"`，
本轮不做。

**不影响现有输出**：驾驶舱只占用终端底部，滚动区的用户输入、思考面板、工具卡片、
工具结果、正文与 `✓ 完成` 汇总行照旧打印；不写 trace、不进 scrollback。

不做空闲动画/计时刷新：**只在事件到达时刷新**（`refresh_per_second=4` 仅用于事件突发时的合帧），
避免长任务空转耗电。

### 3.5 状态模型（`CockpitState`）

| 字段 | 类型 | 更新来源 | 说明 |
|---|---|---|---|
| `run_id` | str | `run_start` | 换轮清空所有状态 |
| `started_at` | float | `run_start` | 单调时钟，用于总耗时 |
| `steps` / `failed` | int | `tool_start` / `tool_end` | 与 trace 汇总口径一致 |
| `current` | dict | `tool_start` | 当前工具名、目标、开始时刻 |
| `step_history` | deque(maxlen=4) | `tool_start` / `tool_end` | 每步 `{tool, target, ok, ms}` |
| `feed` | deque(maxlen=8) | 全部事件 | 数据流单行摘要（复用 `render_event` 的文本） |
| `fetches` | deque(maxlen=20) | `web_fetch` | URL/状态/耗时/字节 |
| `extracts` | deque(maxlen=10) | `web_extract` | 结构化字段（本轮暂无数据源，见 §4） |
| `stats` | dict | `crawl_stats` | 队列/完成/失败/去重（本轮无数据源） |

---

## 4. 事件埋点：`web_fetch` / `web_extract` / `crawl_stats`

### 4.1 事件结构（与构想文档一致，写入 trace JSONL）

```json
{"type":"web_fetch","url":"https://example.com/a","parent_url":"https://example.com/",
 "depth":1,"status":"ok","http_status":200,"bytes":12288,"duration_ms":340}

{"type":"web_extract","url":"https://example.com/a",
 "fields":{"title":"React 19 新特性","published":"2026-11-01","author":"张三"},
 "field_count":3}

{"type":"crawl_stats","queued":12,"done":8,"failed":1,"dedup_hits":3,"bytes":98304}
```

### 4.2 产生方式：trace 侧映射，工具零改动（推荐）

沿用 2026-11 操作可见性的原则"不在工具实现里加埋点"，
由 `TraceRecorder.on_tool_after` 在工具结束后按工具名生成派生事件：

| 工具 | 派生事件 | 字段来源 |
|---|---|---|
| `WebFetch` | `web_fetch` | `url=tool_input["url"]`；`status=ok/error`；`bytes=len(result)`；`duration_ms` 由同 `tool_use_id` 的 before/after 差值 |
| `BrowserNavigate` | `web_fetch` | `url=tool_input["url"]`；`status`/`duration_ms` 同上；`bytes` 不适用可省略 |
| `BrowserGetText` | `web_fetch` + `web_extract`? | 见下 |
| `WebSearch` | 不派生 | `query` 不是 URL，避免把搜索词当节点污染拓扑 |

**字段缺失时省略，不写假值**：`parent_url` / `depth` 在本轮没有爬虫，直接省略；
`http_status` 从工具结果里拿不到，也省略（未来工具改造或 crawler 再补）。

`web_extract` 的取舍：`WebFetch` 只返回整页 Markdown，没有结构化字段；
`BrowserGetText` 返回纯文本，也没有。因此 **C1 不产生 `web_extract`**，
抓取面板只显示"URL / 状态 / 耗时 / 字节"；等未来有了抽取工具或 crawler 再打开表格列。
在计划文档中明确这条限制，避免界面上出现"假装有字段"的空表。

`crawl_stats` 同理：没有 frontier 队列，**C1 不产生**，仅为 schema 预留。

### 4.3 `/trace` 回看

`agent/ui/trace_view.py::render_event` 增加三行渲染（新增类型不在终端滚动区重复刷，
只在 `/trace` 回看时展示）：

- `web_fetch` → `🌐 GET https://… 200 · 12KB · 320ms`（缺失字段自动省略）；
- `web_extract` → `📋 https://… · 字段 3 个`；
- `crawl_stats` → `🕸 队列 12 · 完成 8 · 失败 1 · 去重 3`。

---

## 5. 配置

新增 `[ui] visual_cockpit`：

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `visual_cockpit` | str | `"auto"` | `auto`：交互终端且非语音时启用；`off`：完全关闭 |

需要同步的四个位置（沿用现有配置同步规范）：

- `agent/config/settings.py`（`UISettings` 字段 + 注释，约 L176-191）
- `agent/config/settings_domains.py`（`ui` 表键映射，约 L72-82）
- `agent/config/catalog.py`（键的中文说明，约 L205-215）
- `agent/configs/settings.example.toml`（`[ui]` 段示例，约 L121-133）

启用条件统一封装为 `_cockpit_enabled(settings, console, voice_mode)`，
只在一个地方判定，避免四处散落 if。

---

## 6. 文件改动清单

| 文件 | 类型 | 职责 | 预计行数 |
|---|---|---|---|
| `agent/ui/cockpit.py` | **新增** | `CockpitState`（状态/容量/降级布局）+ `render()`（返回 Rich `Group`）+ `CockpitLive`（Live 控制器：start/stop/refresh，异常吞掉） | ≈ 280 |
| `agent/core/trace.py` | 修改 | `TraceRecorder` 增加 `on_event: Callable | None`；`_emit()` 末尾安全调用；`install_observability` 把 `ui.on_trace_event` 接上；`on_tool_after` 派生 `web_fetch` | +≈ 55 |
| `agent/ui/cli.py` | 修改 | 懒创建 `CockpitLive`；新增 `on_trace_event`；在 `tool_result` / `assistant_text` / `assistant_thinking` / `end_thinking` / `read_user_input` 五处收 Live | +≈ 70 |
| `agent/ui/trace_view.py` | 修改 | 新增三类事件的单行渲染（`/trace` 回看） | +≈ 15 |
| `agent/config/{settings,settings_domains,catalog}.py`、`configs/settings.example.toml` | 修改 | 新增 `visual_cockpit` 配置 | +≈ 4 |
| `tests/ui/test_cockpit.py` | **新增** | 状态更新、按尺寸降级、渲染文本、Live 启停幂等、异常隔离 | ≈ 180 |
| `tests/core/test_trace.py` | 修改 | `web_fetch` 派生字段、sink 调用与异常隔离 | +≈ 50 |
| `tests/ui/test_cli_cockpit.py` | **新增** | 5 个生命周期钩子的互斥顺序（假 Console/假 Live，不需要真实 TTY） | ≈ 90 |
| `docs/plans/terminal-cockpit.md` | **新增** | 本文 | — |
| `docs/plans/jarvis-cinematic-visualization.md` | 修改 | 增加"工程方案见 terminal-cockpit.md"，C1 状态置为"方案待评审" | +≈ 3 |
| `README.md`、`CHANGELOG.md` | 修改 | 功能说明 + `[ui]` 配置项（**代码落地时**同步） | — |
| `aoci.code.txt` / `.aoci/baseline.json` | 修改 | 代码落地后 `aoci_maintain` 维护 | — |

单文件均 ≤900 行；`cockpit.py` 若超 300 行，按"状态模型 / 渲染 / Live 控制"拆成
`cockpit_render.py`，避免二次返工。

> ⚠️ 前置：`agent/ui/cli.py` 当前 865 行（含 2026-11-08 的 Live 收口修复），已逼近
> 900 行上限。按项目代码结构规则，**动驾驶舱之前先执行 S0 拆分**，把思考面板/Live
> 管理抽到 `agent/ui/thinking_panel.py`，否则本阶段一落地就会超限。

---

## 7. 测试计划

| 用例 | 断言 |
|---|---|
| `test_state_counts_steps_and_failures` | 3 步、1 失败 → `steps=3`、`failed=1` |
| `test_feed_keeps_latest_eight` | 12 条事件后只留 8 条，顺序正确 |
| `test_layout_degrades_by_height` | height=18 → 单面板；26 → 双面板；40 → 三面板 |
| `test_web_fetch_row_rendered` | URL/状态/耗时/字节出现在渲染文本；缺失字段不出现 `None` |
| `test_cockpit_disabled_in_non_tty` | 假 Console `is_terminal=False` → 不创建 Live、无输出 |
| `test_live_start_stop_idempotent` | 连续 start/stop 不抛异常、不重复渲染 |
| `test_sink_failure_does_not_break_trace` | `on_trace_event` 抛异常 → JSONL 照写、滚动行照打 |
| `test_web_fetch_derived_after_webfetch` | `on_tool_after(WebFetch)` 产生 1 条 `web_fetch`，`bytes=len(result)`，`duration_ms` 正确 |
| `test_web_fetch_not_derived_for_search` | `WebSearch` 不产生 `web_fetch` |
| `test_cli_stops_cockpit_before_thinking` | 先 `on_trace_event(tool_start)` 再 `assistant_thinking` → 假 Live 记录 `stop` 早于 `start(thinking)` |
| `test_cli_stops_cockpit_before_tool_result` | `tool_result` 打印前 Live 已 stop |
| `test_threshold_hides_short_task` | 2 步、单步 <500ms、总时长 <3s → 全程不显示（“打开贪吃蛇”这类） |
| `test_threshold_shows_on_third_step` | 第 3 个 tool_start 到达时首次显示 |
| `test_threshold_shows_on_long_tool` | 单个工具 ≥500ms 时首次显示 |
| `test_threshold_keeps_visible_after_trigger` | 首次触发后，后续短工具阶段不再重新判断，继续显示 |

命令：

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis
.\.venv\Scripts\python.exe -m pytest tests/ui/test_cockpit.py tests/ui/test_cli_cockpit.py tests/core/test_trace.py -q
```

人工验证：

1. 交互终端跑一轮涉及 ≥3 个工具的任务（如"读一个文件、改一处、再列一下目录"）；
2. 观察驾驶舱出现/消失时机，确认输入行不被擦除；
3. 用 `--json` 或 `| Out-File` 重定向跑同一任务，确认没有任何驾驶舱输出；
4. 语音模式（`/voice`）确认驾驶舱、工具卡与代理卡**同样出现并实时更新**（2026-11-09 用户要求）。

---

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| 与思考面板 Live 嵌套 | 生命周期互斥（§3.2），并有 `test_cli_stops_cockpit_before_thinking` 回归 |
| Live 与 `console.print` 抢屏 | 结果卡片打印前先 stop（`transient=True` 自动擦除），不依赖 Rich 的打印语义 |
| 终端高度不足/窄屏 | 三级降级（§3.3），`height<20` 只留一行摘要 |
| 宽字符/中文对齐 | 渲染只用单行截断 + `Text` 自动换行，不做手工列宽对齐；表格用 Rich `Table` |
| 事件频率过高 | 仅在事件到达时刷新；`refresh_per_second=4` 合帧；状态容器全部有上限 |
| 输出重复（滚动行 + 面板） | 面板只做汇总（步数/失败/当前），细节留在滚动区；数据流最多 8 条 |
| 非 Windows/无 Rich 环境 | `_HAS_RICH=False` 或 `is_terminal=False` 时直接禁用，不影响现有降级路径 |

---

## 9. 实施顺序（每步可独立验证）

| 步骤 | 内容 | 验证 |
|---|---|---|
| S0 | **先拆 `agent/ui/cli.py`**（已 865 行，逼近 900 上限）：把思考面板/Live 管理抽到 `agent/ui/thinking_panel.py`，`RichCLI` 只保留委托方法 | `pytest tests/ui/ -q`，行为不变 |
| S1 | `cockpit.py` 状态模型 + `render()`（纯函数，不接 Live） | `tests/ui/test_cockpit.py` 前 4 条用例 |
| S2 | `TraceRecorder.on_event` + `ui.on_trace_event` 接线（非 TTY 零副作用） | sink 旧测试 + 异常隔离用例 |
| S3 | `RichCLI` 五处 Live 生命周期 + 宽窄降级 | `test_cli_cockpit.py` + 人工走查 |
| S4 | `web_fetch` 派生 + `/trace` 三类渲染 | `test_trace.py` 新增 3 条 |
| S5 | 配置四处 + README/CHANGELOG + AOCI 维护 + 全量测试 | `pytest tests/ -q`、CI 3.11–3.14 |

S1/S2 不依赖终端，可先落地；S3 是唯一需要手工验证的步骤。

---

## 10. 明确不做（本轮）

- ❌ 真正的 crawler（frontier/深度/去重）——`crawl_stats` 只留 schema；
- ❌ `web_extract` 真实字段抽取——抓取面板先不显示字段列；
- ❌ ASCII 抓取拓扑（C2）；
- ❌ 桌面多浮窗（C3）、Canvas 拓扑（C4）、电影感动画与音效（C5）；
- ❌ 空闲计时动画、常驻 Live（只在工具执行窗口显示）；
- ❌ 独立终端窗口 / 独立 GUI 小窗（C1b 备选，见 §12，本轮不实现）；
- ❌ 改动任何工具实现（埋点走 trace 派生）。

---

## 11. 已确认决策（2026-11-08，用户答复）

| 项 | 决策 |
|---|---|
| 形态 | **终端底部内嵌**（Rich Live），不新开窗口；独立窗口列为 C1b 备选，本轮不实现（见 §12） |
| 默认启用 | `visual_cockpit = "auto"`：交互终端且非语音时自动启用；非 TTY / `--json` / `serve` 自动禁用；`"off"` 可完全关闭 |
| 显示阈值 | `auto` 下前 2 步不显示；第 3 步 / 单工具 ≥500ms / 工具执行累计 ≥3s 首次显示，之后本轮持续显示（防闪） |
| 面板构成 | **三块全显**：任务 + 数据流 + 抓取（按终端高度逐级降级，见 §3.3） |
| `web_fetch` 数据源 | **trace 近似**：由 `TraceRecorder.on_tool_after` 派生，工具零改动；缺 `http_status` / `parent_url` 时省略字段，不写假值 |

以上决策不再作为实施期可选项；如需变更，先改本文再改代码。

---

## 12. 附录：C1b「独立窗口」评估（备选，不实现）

若验证后仍嫌底部面板占屏，可做 C1b；先记录四种做法与结论，避免重复论证。

| 方案 | 难度 | 实际效果 | 主要坑 |
|---|---|---|---|
| A. 子进程新开控制台（`CREATE_NEW_CONSOLE` + stdin 管道推事件） | 中偏高 | 真独立窗口，可放副屏、常驻整轮；外观仍是控制台字体 | Win11 默认 Windows Terminal 时句柄/样式控制失效；去边框后难拖拽/关闭；子进程崩溃或被关要检测降级；打包态要额外入口 |
| B. 独立 tkinter 窗口（标准库） | 中 | 深色无边框小窗，可 `topmost` + `WS_EX_NOACTIVATE` 不抢焦点 | DPI、无边框拖动、中文字体、打包时 tkinter 可用性、生命周期与 IPC |
| C. Electron 浮窗（= C3） | 高 | 效果最好（透明/多屏/动画） | 属于桌面壳；终端用户用不到，从 CLI 拉起 Electron 不可接受 |
| D. `wt.exe` 新标签/新窗口 | 低 | 只是另一个终端标签，样式与生命周期不可控 | 依赖 WT 版本，父进程无法可靠控制/关闭 |

结论：

- 独立窗口的收益是“终端干净、可常驻、不与 Rich Live 抢屏”；代价是多进程 + IPC +
  窗口管理 + 一堆新故障模式，约 500–700 行；
- 外观仍受控制台/普通 GUI 限制，**电影感不会因此显著提升**，真正电影级仍是 C3；
- 若要实现，选 **B（tkinter）**而不是 A：不受 Windows Terminal 默认宿主影响、
  零新依赖、可干净降级回内嵌；
- 触发条件：C1 上线并使用一段时间后，用户明确要求“别占终端底部”再立项。

---

## 13. 现行实现：C1b 独立 HUD 窗口（2026-11-08）

> 用户实机结论：终端底部内嵌“一闪而过、看不到”，且形态不是想要的钢铁侠感。
> 决策改为**独立窗口 + 任务结束不关闭**；旧终端内嵌模式与阈值/降级代码全部移除。

### 13.1 形态与生命周期

| 项 | 实现 |
|---|---|
| 技术 | Python 标准库 tkinter（零新依赖）；无边框、深色、右上角置顶、整面可拖动、自绘关闭按钮 |
| 尺寸/位置 | 430×640，屏幕右侧留 24px，垂直居中偏上；多屏按主屏计算 |
| 出现 | **本轮真正调用工具时**才懒创建（首个 `tool_start` / 抓取类事件；`run_start` 只缓存并补投，用于计算耗时）；**纯闲聊轮次不弹窗**（2026-11-09 修复） |
| 任务结束 | 收到 `run_end` 只更新最终汇总，**窗口不关闭**，保留“任务完成 / 失败数 / 总耗时 / 窗口保留”；没有工具调用的 `run_end` 直接丢弃，不把已显示窗口刷成“0 步” |
| 新任务 | 本轮首个工具事件补投 `run_start`，清空上一轮状态并按真实起始时刻重绘 |
| 手动关闭 | 点击右上角 ✕ **只隐藏（`withdraw`），不销毁 Tk、不退出 jarvis**；下一轮任务的首个工具调用自动重新显示；关闭发生在任务中途时，同一轮后续工具不再弹出，避免刚关掉又被拉起 |
| 进程退出 | `goodbye()` / `atexit` 通知 Tk 线程**单次销毁**并等待退出；销毁后不再调用 `winfo_exists()` 等 Tk 方法，避免 `alloc: invalid block` 崩进程 |
| 语音模式 | **同样显示并实时更新**（2026-11-09 用户要求）；语音只影响 TTS 与终端思考面板，不再抑制 HUD/卡片 |
| 置顶 | 默认置顶；**前台是 Git Bash / cmd / Windows Terminal 等终端时自动让出**（`agent/ui/cockpit_zorder.py`，0.4s 检测一次进程名，不 lower 避免闪烁），切到其它程序再恢复；非 Windows 恒置顶 |
| 关闭开关 | `[ui] visual_cockpit = "window"`（默认）/ `"off"`；历史值 `auto` / `terminal` 一律按 window 处理 |

注意：独立窗口**不依赖 TTY**，Git Bash / mintty / cmd / Windows Terminal 都能显示；
原来“非 TTY 零输出”的限制只适用于终端内嵌方案，已随方案移除。

### 13.2 线程与数据流

```
TraceRecorder._emit(event)
        ├─ 写 JSONL / 渲染滚动行（不变）
        └─ ui.on_trace_event → CockpitWindow.feed → Queue
                                              │
                        Tk 守护线程每 40ms 取一批事件
                                              │
                            CockpitState.update → Canvas HUD 重绘
```

- 模型线程与窗口观察器只投递事件，绝不直接碰 Tk；
- Tk 线程独占 `Tk()` 与 `mainloop`；事件到达才整屏重绘，运行中只移动扫描线
  （不做整屏 `delete+重画`，避免与屏幕合成抢帧产生重影）；
- 窗口创建失败（tkinter 缺失、无桌面、Tk 异常）时静默禁用 + 记一条 diag，
  对话与 trace 完全不受影响。

### 13.3 面板内容

| 区块 | 内容 |
|---|---|
| 顶部 | `J.A.R.V.I.S / TASK COCKPIT // 任务驾驶舱`、状态点（RUN/IDLE）、关闭按钮 |
| 三格指标 | 步骤、失败数（>0 红色）、总耗时 |
| 当前动作带 | 运行中显示 `● 工具 目标` + 扫描线；完成显示 `✓ 任务完成` 或 `✗ 任务结束 · N 步失败` 与最终汇总 |
| 执行序列 | 最近 4 步：✓/✗/●、工具名、目标、耗时；**点击某步展开该步工具入参、目标、状态与失败错误**（2026-11-09）；**失败步固定置顶**，不会被最近 4 步挤掉（同日实机修复） |
| 数据流 | 最近 8 条事件摘要（复用 trace_view 渲染口径） |
| 抓取 | 最近 3 条 URL / 状态 / 耗时 / 字节（缺失字段留空，不写假值） |

### 13.4 文件

| 文件 | 职责 |
|---|---|
| `agent/ui/cockpit.py` | `CockpitState`（纯状态/格式化/模式归一化），无 Rich、无 Tk |
| `agent/ui/cockpit_window.py` | `CockpitWindow`（线程/队列/生命周期）+ `_Hud`（Canvas 渲染）；`python -m agent.ui.cockpit_window` 可本地预览 |
| `agent/ui/cli.py` | `on_trace_event` 懒创建窗口；`goodbye` 收口 |
| `agent/core/trace.py` | `on_event` 旁路不变 |
| `tests/ui/test_cockpit.py` / `test_cli_cockpit.py` | 状态、格式化、路由、关闭/常驻行为（不创建真实 Tk） |

### 13.5 人工验收

1. 启动 jarvis，正常跑任意 ≥1 个工具的任务 → 窗口在本轮首个工具调用时弹出；
2. 任务进行中：步骤/失败/耗时随事件变化，当前动作带有扫描线；
3. 任务结束：窗口**不关闭**，显示最终汇总；
4. 发下一条任务：窗口清空并重新计数；
5. 点右上角 ✕：窗口隐藏，**jarvis 不退出**；继续聊天，下一轮任务的首个工具调用时窗口自动重新显示；
6. `[ui] visual_cockpit = "off"`：全程不弹窗；
7. 纯闲聊（不调用任何工具）连续多轮：窗口**不弹出**；若上一条任务已让窗口常驻，闲聊期间保持上一轮汇总，不变成“0 步”。

### 13.6 已知限制（本轮）

- Windows 为主验证平台；macOS 的 Tk 要求主线程，若创建失败会静默禁用（不崩）；
- 子代理工具与主任务共用同一 trace 记录器，事件会混在同一窗口（暂不区分）；
- 抓取可视化已拆到**屏幕最左侧的独立「爬虫驾驶舱」窗口**（C2-lite，2026-11-09，原名“抓取网络”）：
  URL 去重清单 + 汇总 + 抽取表；**Canvas 抓取拓扑仍未做**（缺 `parent_url` / `depth`），
  桌面多浮窗（C3）与桌面 Canvas 拓扑（C4）也仍在后续阶段；
- 下一步 C3-lite 多任务浮窗方案与 `[visualization]` 配置管理见
  [cockpit-multicards.md](cockpit-multicards.md)。



