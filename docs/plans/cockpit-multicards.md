# 多任务浮窗（C3-lite）工程方案与配置管理

> 来源：[jarvis-cinematic-visualization.md](jarvis-cinematic-visualization.md) 的 C3 阶段精简版。
> 状态：**已实施（第一版，2026-11-08）**。默认值按 §10 推荐执行；后续优化在
> 本文件追加变更记录。
> 子代理/队友任务卡扩展（每代理一张、保留终端输出、左侧第二列）见
> [agent-cards.md](agent-cards.md)（方案待评审）。
> 范围：主 HUD 之外的“每个运行中工具一张浮窗卡”；本文件不含子代理专属卡片
>（见 [agent-cards.md](agent-cards.md)）；不做 Electron 桌面端、不做抓取拓扑。
> 前置：[terminal-cockpit.md](terminal-cockpit.md) §13（C1b 独立 HUD 已完成）。
> 作者：aceFelix

---

## 1. 背景与目标

C1b 已经有一个独立 HUD，但它是一张“汇总窗”：当前工具、步数、失败、耗时、数据流、抓取。
钢铁侠那种“操作可视化”还缺一层：**每个正在执行的任务各自有一张小卡，完成一个收一个**。

C3-lite 目标：

- 工具执行超过阈值时，弹出一张独立无边框小卡（不是主 HUD 内嵌区域）；
- 多张卡错位堆叠，互不遮挡关键信息；工具完成显示 ✓/✗ 后自动收起；
- 主 HUD 继续保留汇总，两者互不替代；
- 全部视觉能力进配置文件，可一键总关、可按功能开关；
- 任何窗口故障都不影响对话与工具执行。

非目标（本轮）：

- ❌ Electron 桌面端 / 透明窗口 / 跨屏动画（C3 完整版）；
- ❌ 子代理专属任务卡（事件里还没有稳定的 agent 身份，见 §8）；
- ❌ 抓取拓扑（C2/C4-lite，需要先补 `parent_url` / `depth`）；
- ❌ 声音、粒子、3D、录屏。

---

## 2. 现状与约束（已核对代码）

| 事实 | 位置 |
|---|---|
| `tool_start` 事件已带 `tool` / `tool_use_id` / `target` / `input`，可唯一标识一次工具调用 | `agent/core/trace.py:259-267` |
| `tool_end` 事件带 `ok` / `duration_ms` / `error`，可按 `tool_use_id` 回填 | `agent/core/trace.py` `on_tool_after` |
| 所有事件都带 `run_id` / `seq` / `ts`，可用于换轮清理与排序 | `agent/core/trace.py:_emit` |
| 主 HUD 已有 Tk 独占线程、事件队列、`CockpitWindow.feed` 与 `withdraw/deiconify` 生命周期 | `agent/ui/cockpit_window.py` |
| 主 HUD 关闭只隐藏，下一轮首个工具自动重现 | `agent/ui/cockpit_window.py`（2026-11-08 修复） |
| `CockpitState` 目前只有单个 `current`，并行工具会互相覆盖 | `agent/ui/cockpit.py` |
| 配置是扁平 `Settings` + `settings_domains` 段映射，新增段只需加映射与字段 | `agent/config/settings.py`、`settings_domains.py` |
| `cli.py` 已 800+ 行，不能再塞窗口逻辑 | `agent/ui/cli.py` |

硬约束：

1. 所有 Tk 调用（含 `Toplevel` 创建/销毁）必须只在 HUD 的 Tk 守护线程里执行；
2. `destroy()` 之后禁止再调用任何 Tk 方法（会 `alloc: invalid block`）；
3. 浮窗不能抢键盘焦点，不能挡住用户正在输入的行；
4. 任何浮窗异常只影响浮窗，主 HUD、trace、对话继续；
5. 非 Tk / 无桌面 / 语音模式 / 总开关关闭时，零输出、零行为变化。

---

## 3. 交互与视觉设计

### 3.1 形态

- 每张卡是一个独立 `Toplevel`：无边框、深色、置顶、不可缩放、无标题栏；
- 尺寸约 `300×118`，右上角一个极小的状态点，不提供关闭按钮（完成自动收）；
- 卡内容四行：

```text
┌────────────────────────────┐
│ ● Bash              2.4s   │
│ date "+%Y-%m-%d"           │
│ cwd E:\J.A.R.V.I.S_Work    │
│ 运行中 · 扫描线             │
└────────────────────────────┘
```

完成态：

```text
┌────────────────────────────┐
│ ✓ FileWrite          37ms  │
│ alpha.txt                  │
│ +1 / -0                    │
│ 完成                       │
└────────────────────────────┘
```

失败态：`✗` + 红色状态点 + 错误摘要一行（截断）。

**点击展开（2026-11-08 用户实测新增）**：鼠标单击卡面切换展开态，卡片从
`300×112` 变为 `440×300`，显示目标与工具入参 JSON（trace 的 slim input，
最多 3 个字段、每字段 120 字符），失败时在底部显示错误摘要；再点一次收起。
工具结果正文不落 trace，因此不在卡片里展示（避免把文件内容带进 HUD）。

### 3.2 布局与堆叠

主 HUD 固定在屏幕右侧（`x = screen_w - W - 24`）。浮窗卡放在主 HUD **左侧**：

```text
卡片 1   卡片 2   主 HUD
┌─────┐  ┌─────┐  ┌──────┐
│     │  │     │  │      │
└─────┘  └─────┘  │      │
   ┌─────┐        │      │
   │     │        └──────┘
   └─────┘
```

- 第 n 张卡：`x = hud_x - card_w - 16 - n * 24`，`y = hud_y + n * 16`；
- 超出屏幕左边界时改为向下错位（`x` 固定，`y += card_h + 12`）；
- 主 HUD 被用户隐藏时，卡片以屏幕右上角为锚点；
- 卡片不参与主 HUD 的拖动；主 HUD 拖动不改卡片锚点（v1 固定屏幕坐标）。

### 3.3 出现阈值与防闪

| 模式 | 行为 |
|---|---|
| `off` | 不创建任何卡片 |
| `auto`（默认） | `tool_start` 后延迟 `tool_card_min_ms`（默认 300ms）；到点时工具仍在运行才显示；提前结束则不显示 |
| `always` | `tool_start` 立即显示（快工具会闪，仅调试/演示用） |

- 完成态保留 `tool_card_hold_ms`（默认 600ms）再收起，让用户看到 ✓/✗；
- 同一时刻最多 `tool_card_max`（默认 4）张；并行超过上限时，多出的工具只进主 HUD，
  卡片区显示 `+N` 提示（不做滚动）；
- 卡片出现/收起不做淡入淡出（v1 用瞬间显示/隐藏，避免 Tk alpha 在 legacy 控制台上的兼容问题）；
- `effects=off` 时关闭扫描线与状态点闪烁，卡片静态刷新耗时文本。

### 3.4 主 HUD 关闭与重现

- 主 HUD 点 ✕：主 HUD `withdraw`，同时**隐藏所有卡片**（不销毁，保留状态）；
- 同一轮后续工具：不重新弹主 HUD，也不弹卡片；
- 下一轮任务的首个工具：主 HUD `deiconify`，卡片按新事件重新出现；
- 进程退出：主 HUD 线程单次销毁所有 `Toplevel` 后再销毁根窗口。

### 3.5 主 HUD 与卡片的分工

| 区域 | 回答的问题 |
|---|---|
| 主 HUD | 这一轮跑到第几步、失败几次、总耗时、最近数据流、抓取列表 |
| 浮窗卡 | 当前这个工具在做什么、目标是什么、跑了多久、成功还是失败 |

主 HUD 的“当前动作带”在并行时改为：

```text
● 3 个工具并行  Bash · FileWrite · WebFetch
```

卡片只覆盖最近 `tool_card_max` 个；其余在数据流里可见。

---

## 4. 状态与事件模型

### 4.1 `CockpitState` 增加 active 集合

现状只有 `current`，并行工具会覆盖。新增：

| 字段 | 类型 | 说明 |
|---|---|---|
| `active` | `dict[str, dict]` | key=`tool_use_id`，value=`{tool,target,input,started_at,ok,ms}` |
| `active_order` | `list[str]` | 启动顺序，决定卡片槽位 |
| `current` | 保留 | 兼容主 HUD：指向最后启动的 active 项 |

`tool_start`：写入 `active[tool_use_id]`，追加 `active_order`；
`tool_end`：回填 `ok/ms`，从 `active` 移除，但保留在 `step_history`；
`run_start`：清空 `active/active_order`；
`run_end`：清空 `active`（正常不应有残留）。

### 4.2 卡片规划器（纯逻辑，可单测）

`CardPlanner` 不碰 Tk，只吃事件与时钟：

```python
class CardSpec:
    tool_use_id: str
    tool: str
    target: str | None
    input_summary: str
    started_at: float
    state: "pending" | "running" | "done" | "failed"
    ms: int | None
```

规划规则：

1. `on_tool_start` → 生成 `pending` 卡，设定“到期时间 = now + min_ms”；
2. 到期检查（Tk 线程每 40ms）→ 若工具仍 active，转 `running` 并显示；
3. `on_tool_end` → 若已显示，转 `done/failed`，设定“收起时间 = now + hold_ms”；
4. 到期收起；若从未显示（短工具），直接丢弃；
5. 已显示卡数量 > `max`：最早完成的先收；都在运行则后来的不显示；
6. `run_start` → 清空所有卡（含窗口）；`run_end` → 保留完成态直到 hold 时间到。

### 4.3 窗口管理

`CardManager` 在 Tk 线程内：

- `ensure_window(spec)`：创建/复用一个 `Toplevel`，`overrideredirect(True)`、
  `-topmost`、`geometry` 按槽位计算；
- `draw(spec)`：Canvas 全量重绘卡片（元素少）；
- `hide_all()`：主 HUD 关闭时 `withdraw` 所有卡；
- `show_all()`：下一轮按当前 active 重建；
- `destroy_all()`：退出时销毁所有 `Toplevel`；
- 每个 `Toplevel` 不调用 `focus_force`；不绑定键盘；鼠标单击只做“展开/收起详情”，不改焦点。

### 4.4 事件接入点

不新增 trace 事件，完全复用现有：

```
TraceRecorder._emit
  └─ ui.on_trace_event
       └─ CockpitWindow.feed → Queue
            └─ Tk 线程 _pump
                 ├─ CockpitState.update
                 ├─ CardPlanner.update
                 └─ CardManager.draw/refresh
```

主 HUD 与卡片共用同一份状态，不重复解析事件。

---

## 5. 配置管理（重点）

### 5.1 设计原则

1. **一个总开关**：关掉所有可视化，行为回到“只有终端滚动输出”；
2. **每个功能一个独立开关**：任务驾驶舱、浮窗卡、后续拓扑/抽取表各管各的；
3. **不预埋死配置**：未实现的功能不提前加键，等功能落地再加；
4. **非法值降级**：未知枚举按默认值处理并记一条诊断；
5. **向后兼容**：现有 `[ui] visual_cockpit` 继续有效，不强迫用户改配置；
6. **配置可发现**：catalog 有中文说明、示例 TOML 有注释、README 有配置表。

### 5.2 推荐落点：新增 `[visualization]` 段

```toml
[visualization]
# 总开关：false 时关闭所有可视化窗口（主 HUD、浮窗卡、未来的拓扑/抽取表）
enabled = true

# 主任务驾驶舱：window（默认）/ off
task_cockpit = "window"

# 多任务浮窗：auto（默认，只对 ≥ tool_card_min_ms 的工具弹）/ always / off
tool_cards = "auto"
tool_card_min_ms = 300      # auto 模式下工具持续多久才弹卡
tool_card_max = 4           # 同时最多几张卡（1–6）
tool_card_hold_ms = 600     # 完成后保留多久再收

# 动效：off（静态）/ subtle（默认：扫描线+状态点）/ full（预留）
effects = "subtle"

# 主 HUD 位置记忆（~/.jarvis/ui_state.json）
position_memory = true
```

### 5.3 兼容与优先级

| 场景 | 行为 |
|---|---|
| 只有 `[ui] visual_cockpit` | 按旧值工作（`window` / `off`） |
| `[visualization] task_cockpit` 与 `[ui] visual_cockpit` 同时存在 | `[visualization]` 优先；catalog 标注 `[ui]` 为历史别名 |
| `[visualization] enabled = false` | 覆盖所有子项，全部关闭 |
| 未写 `[visualization]` 段 | 全部使用默认值（主 HUD 开、浮窗卡 auto） |
| `tool_cards=auto` 且 `task_cockpit=off` | 浮窗卡独立工作，锚定屏幕右上角 |
| 语音模式 / 无桌面 / tkinter 缺失 | 全部关闭，不报错、不输出 |

建议保留 `[ui] visual_cockpit` 至少一个次版本，README 提示迁移；后续版本再标记废弃。

### 5.4 Settings 字段与映射

新增扁平字段（沿用现有 `Settings` 风格）：

```python
visual_enabled: bool = True
visual_cockpit: str = "window"          # 现有字段复用（两个 TOML 键都可映射到它）
visual_tool_cards: str = "auto"
visual_tool_card_min_ms: int = 300
visual_tool_card_max: int = 4
visual_tool_card_hold_ms: int = 600
visual_effects: str = "subtle"
visual_position_memory: bool = True
```

`settings_domains.py` 新增：

```python
("visualization", (
    ("enabled", "visual_enabled"),
    ("task_cockpit", "visual_cockpit"),   # 与 [ui] visual_cockpit 同字段，后应用者优先
    ("tool_cards", "visual_tool_cards"),
    ("tool_card_min_ms", "visual_tool_card_min_ms"),
    ("tool_card_max", "visual_tool_card_max"),
    ("tool_card_hold_ms", "visual_tool_card_hold_ms"),
    ("effects", "visual_effects"),
    ("position_memory", "visual_position_memory"),
)),
```

段顺序放在 `ui` 之后，保证同名键以 `[visualization]` 为准。

### 5.5 校验与降级

| 键 | 合法值 | 非法时 |
|---|---|---|
| `enabled` | bool | 解析失败 → true |
| `task_cockpit` | `window` / `off`（历史 `auto` / `terminal` 归一为 `window`） | 未知 → `window` |
| `tool_cards` | `auto` / `always` / `off` | 未知 → `auto` |
| `tool_card_min_ms` | 0–5000 | 越界 clamp，非法 → 300 |
| `tool_card_max` | 1–6 | 越界 clamp，非法 → 4 |
| `tool_card_hold_ms` | 0–3000 | 越界 clamp，非法 → 600 |
| `effects` | `off` / `subtle` / `full` | 未知 → `subtle` |
| `position_memory` | bool | 解析失败 → true |

降级只记一条 `diag_info`，不在终端刷警告。

### 5.6 配置管理范围（全功能视图）

| 功能 | 配置键 | 状态 |
|---|---|---|
| 主任务驾驶舱 | `visualization.task_cockpit`（兼容 `[ui] visual_cockpit`） | 已实现，需迁移文档 |
| 多任务浮窗 | `visualization.tool_cards` + 4 个阈值/上限键 | 已实现 |
| 子代理/队友卡 | `visualization.agent_cards` + 4 个阈值/上限键 | 已实现（C3-lite.2） |
| 左侧爬虫驾驶舱 | `visualization.crawl_window`（auto/off） | 已实现（C2 完整版 P1-P3，2026-11-09）：清单/抽取/分层拓扑 |
| 动效强度 | `visualization.effects` | 本方案（先 subtle/off） |
| 位置记忆 | `visualization.position_memory` | 本方案（主 HUD 位置） |
| 抓取拓扑连线 | `visualization.crawl_topology`（auto/off）+ `crawl_max_nodes` | 已实现（P3，2026-11-09）：分层节点 + 父→子连线 + 节点↔抽取行高亮 |
| 抽取结果表 | `visualization.extract_table` | 已实现为抓取窗内抽取区；WebExtract / Crawl 为生产者（P1/P2） |
| 桌面端多浮窗 | `visualization.desktop_panels` | **未实现，不预埋**（C3 完整版） |

### 5.7 运行时查看（可选，S5）

新增 `/visual` 只读命令（不做热改）：

```text
可视化配置
  总开关      on
  任务驾驶舱  window
  多任务浮窗  auto · ≥300ms · 最多 4 · 完成保留 600ms
  动效        subtle
  位置记忆    on
  配置文件    ~/.jarvis/settings.toml
```

热切换留到后续；配置变更需重启进程生效，避免运行中状态不一致。

---

## 6. 文件与模块规划

| 文件 | 类型 | 职责 | 预计行数 |
|---|---|---|---|
| `agent/ui/cockpit_cards.py` | **新增** | `CardSpec` / `CardPlanner`（纯逻辑）+ `CardWindow` / `CardManager`（Tk `Toplevel`） | ≈ 320 |
| `agent/ui/cockpit.py` | 修改 | `active` / `active_order`；并行工具的当前动作摘要 | +≈ 45 |
| `agent/ui/cockpit_window.py` | 修改 | 创建 `CardManager`；转发事件；关闭/重现/退出联动；位置记忆 | +≈ 80 |
| `agent/config/settings.py` | 修改 | 新增 7 个扁平字段与注释 | +≈ 20 |
| `agent/config/settings_domains.py` | 修改 | 新增 `[visualization]` 段映射 | +≈ 12 |
| `agent/config/catalog.py` | 修改 | 新键说明 + 历史别名标注 | +≈ 10 |
| `agent/configs/settings.example.toml` | 修改 | 新增 `[visualization]` 示例 | +≈ 20 |
| `tests/ui/test_cockpit_cards.py` | **新增** | 规划器：阈值、上限、完成收起、换轮清理、位置计算 | ≈ 180 |
| `tests/ui/test_cockpit.py` | 修改 | `active` 并行工具状态 | +≈ 40 |
| `tests/ui/test_cli_cockpit.py` | 修改 | 配置总开关/子开关路由 | +≈ 30 |
| `README.md` / `README.en.md` | 修改 | 功能表 + `[visualization]` 配置表 | — |
| `docs/plans/jarvis-cinematic-visualization.md` | 修改 | C3-lite 引用与阶段更新 | +≈ 6 |
| `docs/plans/terminal-cockpit.md` | 修改 | 交叉链接 C3-lite | +≈ 2 |

单文件仍 ≤900 行；`cockpit_window.py` 预计 500 行左右，安全。

---

## 7. 实施顺序

| 步骤 | 内容 | 验证 |
|---|---|---|
| S0 | 本文评审通过；确认配置段名、默认值、卡片位置 | 用户确认 |
| S1 | `CockpitState.active` + 并行工具摘要 | `pytest tests/ui/test_cockpit.py` |
| S2 | `CardPlanner` 纯逻辑（阈值/上限/收起/换轮/位置） | `pytest tests/ui/test_cockpit_cards.py` |
| S3 | `CardManager`（Tk `Toplevel`）+ 主 HUD 联动；真实 GUI 探针 | 手动 + 截图 |
| S4 | `[visualization]` 配置四处 + 兼容别名 + `/visual`（可选） | 配置/路由单测 |
| S5 | README/CHANGELOG/AOCI + 全量测试 | `pytest tests/ -q` |

S1/S2 不依赖 Tk，可先落地；S3 是唯一需要真实窗口验证的一步。

---

## 8. 已知限制与风险

| 风险 | 对策 |
|---|---|
| Tk `Toplevel` 在 legacy 控制台/多线程下不稳定 | 所有 Tk 操作只在 HUD 线程；`withdraw` 复用，不频繁 `destroy`；`destroy` 后不调 Tk |
| 卡片太多挡屏 | 默认上限 4、300ms 阈值、完成 600ms 自动收；总开关可关 |
| 快工具闪屏 | `auto` 延迟显示；短工具从未创建窗口 |
| 并行工具覆盖状态 | `active` 用 `tool_use_id` 做 key；`current` 仅兼容显示 |
| 子代理工具混入 | v1 不区分 agent；卡片可能显示子代理工具，文档明示；后续补 agent 身份 |
| 主 HUD 关闭后卡片残留 | 关闭时 `hide_all`；下一轮 `show_all` 按新事件重建 |
| 配置非法值 | clamp + 默认值 + 一条 diag |
| 老配置失效 | `[ui] visual_cockpit` 兼容；`[visualization]` 优先 |
| 动效影响性能 | `effects=off` 可关；卡片只在事件/计时刷新，不做全屏动画 |

---

## 9. 人工验收清单

1. 执行一个 2 秒以上的工具 → 约 300ms 后出现一张卡，完成显示 ✓ 并在 600ms 后收起；
2. 执行一个 50ms 的短工具 → 不出现卡片；
3. 并行 3 个工具 → 3 张卡错位堆叠；主 HUD 当前动作显示“3 个工具并行”；
4. 同时并行 6 个工具（上限 4）→ 只显示 4 张，主 HUD 出现 `+2`；
5. 点主 HUD ✕ → 主 HUD 与所有卡片隐藏，jarvis 不退出；
6. 下一轮任务首个工具 → 主 HUD 与卡片自动重现；
7. `[visualization] enabled = false` → 重启后全程无窗口，终端输出与未安装可视化时一致；
8. `[ui] visual_cockpit = "off"` 老配置 → 主 HUD 关闭，浮窗卡仍按 `tool_cards` 工作（或按评审结论改为联动关闭）；
9. 语音模式 / `--json` / 管道输出 → 无窗口、无输出。

---

## 10. 待评审决策

| 项 | 推荐 | 备选 |
|---|---|---|
| 配置落点 | 新增 `[visualization]` 段（总开关 + 子项） | 继续放 `[ui]`，键名 `visual_*` |
| 浮窗默认 | `tool_cards = "auto"`，≥300ms 才显示 | 默认 `off`，用户手动开 |
| 卡片上限 | 4 | 3 / 6 |
| 完成后保留 | 600ms | 立即收起 / 1200ms |
| 卡片位置 | 主 HUD 左侧斜向堆叠 | 屏幕左上 / 右下 |
| 主 HUD 关闭时 | 卡片一起隐藏，下一轮一起重现 | 卡片独立继续显示 |
| `task_cockpit=off` 时 | 卡片独立工作（屏幕右上角锚点） | 卡片也强制关闭 |
| 子代理工具 | v1 不区分，先共用同一套卡 | v1 就按 agent 分组（需先补事件） |

以上默认值如无异议，S1 按推荐值实施；有调整请在评审时直接标注。
