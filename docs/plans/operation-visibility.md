# 操作可见性（Operation Visibility）实施计划

> 分阶段：先做终端 jarvis，桌面 jarvis-desktop 后接。
> 状态：P1–P3 已实现（2026-11-07）；窗口跟随的下一步改造见
> [window-observer.md](window-observer.md)（取代工具白名单）；
> 电影式多弹窗与爬虫可视化构想见
> [jarvis-cinematic-visualization.md](jarvis-cinematic-visualization.md)。
> 作者：aceFelix

---

## 1. 背景与目标

### 1.1 问题

J.A.R.V.I.S 已经能操作电脑（窗口、鼠标、键盘、文件、浏览器），但用户在整个过程中**只能看到工具名和结果**：

- 不确定它到底点了哪个窗口、点了哪里；
- 不知道它正在改哪个文件、改了什么；
- 任务卡住时看不出卡在哪一步；
- 事后想复盘"它刚才干了什么"，只能翻日志。

自动化越强，**不可见的自动化越难被信任**。这是产品化的真实门槛，不是锦上添花。

### 1.2 目标

让"操作过程"在终端里可读、可追、可回看，并让被操作的窗口真实出现在用户眼前：

1. **终端信息不减少**：每一轮对话原有的工具卡片、结果、错误照旧输出。
2. **新增操作轨迹（trace）**：一条时间线，逐条显示工具、目标、耗时、结果、失败原因。
3. **窗口跟随（window follow）**：操作哪个窗口，就把那个窗口显示到前台；操作文件时定位到所在文件夹；编辑时让编辑器窗口可见。
4. **可回看**：`/trace` 查看最近一轮 / 最近 N 轮，可导出 JSONL。
5. **为桌面预留**：事件模型与窗口编排层**宿主无关**，jarvis-desktop 后续直接复用，不返工。

### 1.3 非目标

- ❌ 不做屏幕录制、视频流、远程直播。
- ❌ 不做"终端内嵌 GUI 画面"。
- ❌ 不侵入任何工具实现（不在 `screen.py` / `window.py` / `file_edit.py` 里加埋点）。
- ❌ 本阶段不改桌面壳；桌面接入单独立项。
- ❌ 不做"AI 自动接管整台电脑"的无人值守模式（现有权限模型不变）。

---

## 2. 用户视角

### 2.1 场景 A：改文件

```
> 把 report.md 的第二节改短一点

▶ FileRead(report.md)                     ✓ 12ms
✎ FileEdit(report.md)  +12 / -38          ✓ 46ms
  🪟 已定位到「Documents」文件夹（未接管输入）
  ⤷ 变更：report.md 第 2 节
✓ 完成 · 共 2 步 · 1 个文件 · 0 失败
```

### 2.2 场景 B：操作记事本

```
> 打开记事本，把这段话写进去

▶ WindowFocus(记事本)                      ✓ 8ms
  🪟 已把「无标题 - 记事本」置于前台（右侧，未接管输入）
▶ TypeText(35 字)                          ✓ 210ms
  ⌨ 输入：「贾维斯测试文本……」
✓ 完成 · 共 2 步 · 0 失败
```

### 2.3 档位差异

| 档位 | 用户看到 | 用户键鼠 |
|---|---|---|
| `off` | 只有终端 trace | 不受影响 |
| `show`（默认） | 窗口置顶可见、不激活 | 不受影响 |
| `focus` | 窗口真正获得焦点 | 会被接管，终端明示 |

---

## 3. 术语

| 术语 | 含义 |
|---|---|
| **trace** | 一次运行（run）内所有操作事件的结构化记录 |
| **run** | 一轮用户输入触发的完整执行过程（可能包含多次工具调用） |
| **window follow** | 根据当前工具动作，把相关窗口显示到前台的行为 |
| **show** | 置顶可见但**不激活**（`SWP_NOACTIVATE`） |
| **focus** | 真正激活并获得输入焦点（`SetForegroundWindow`） |
| **explorer select** | `explorer.exe /select,<path>` 打开所在目录并选中文件 |

---

## 4. 架构

### 4.1 挂载点：复用现有 Hooks（零侵入）

项目已有 [`agent/core/hooks.py`](../agent/core/hooks.py)，且 [`orchestrator.py`](../agent/core/orchestrator.py) 已在工具执行前后触发：

| 事件 | 触发位置 | 现有 payload |
|---|---|---|
| `tool_before` | `orchestrator.py:344` | `tool_name`、`tool_input`、`tool_use_id`、`ctx` |
| `tool_after` | `orchestrator.py:505` | `tool_name`、`tool_input`、`tool_use_id`、`result`、`is_error` |
| `file_changed` | `orchestrator.py:522` | `tool_name`、`path`、`operation` |

钩子本身有超时与异常隔离（"钩子失败不炸主流程"），**正好适合做旁路观测**。

> 结论：不需要改 orchestrator，不需要改工具，不需要加装饰器。

### 4.2 数据流

```
用户输入
   │
   ▼
QueryLoop ──► Orchestrator ──► Tool 执行
                   │
                   ├─ tool_before ─┐
                   ├─ tool_after  ─┼─► TraceRecorder ──► trace.jsonl（本地）
                   └─ file_changed ┘         │
                                             ├─► UIProtocol.info  ──► 终端时间线
                                             └─► （预留）WS run_trace 事件 ──► 桌面

tool_before ──► WindowDirector ──► Win32Backend ──► 恢复/置顶/定位（不激活）
```

### 4.3 新增模块（6 个文件）

| 文件 | 职责 | 依赖方向 |
|---|---|---|
| `agent/core/trace.py` | 事件模型、采集、脱敏、JSONL 落盘、环形清理 | 只依赖标准库 + hooks |
| `agent/core/window_follow.py` | 策略表、目标解析、节流、安全摆放、失败降级 | 依赖 `win32_window.py` |
| `agent/core/win32_window.py` | ctypes 封装：枚举/恢复/置顶/移动/激活 | 只依赖 ctypes |
| `agent/ui/trace_view.py` | 终端渲染：单行事件、回合摘要卡 | 依赖 `UIProtocol` |
| `agent/commands/trace_cmd.py` | `/trace` 子命令：最近/导出/开关 | 依赖 trace.py |
| `tests/core/test_trace.py` 等 | 单测（见 §11） | — |

> 文件均需符合 900 行上限与 `@author aceFelix` 注释规范。

### 4.4 改动点（5 处）

| 文件 | 改动 |
|---|---|
| `agent/config/settings.py` | 新增 `[ui]` 配置字段（见 §8） |
| `agent/config/settings_domains.py` | `ui` 表补键映射 |
| `agent/bootstrap.py` 或 `main.py` 装配处 | `install_observability(ctx)`：注册 hooks |
| `agent/commands/router.py` | 注册 `/trace` |
| `docs/*`、`README.md`、`USER_GUIDE.md`、`CHANGELOG.md` | 见 §14 |

---

## 5. 事件模型（trace schema v1）

### 5.1 公共字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `v` | int | schema 版本，固定 `1` |
| `ts` | str | ISO8601 本地时间（毫秒） |
| `run_id` | str | 一次用户输入对应一个 run |
| `seq` | int | run 内自增序号 |
| `type` | str | `run_start` / `tool_start` / `tool_end` / `file_change` / `window_action` / `run_end` |

### 5.2 事件形状

```json
{"v":1,"ts":"2026-11-07T11:20:03.120+08:00","run_id":"r-7f3a","seq":1,"type":"run_start","input_chars":18}

{"v":1,"ts":"2026-11-07T11:20:03.401+08:00","run_id":"r-7f3a","seq":2,
 "type":"tool_start","tool":"FileEdit","tool_use_id":"tu_9",
 "target":{"kind":"file","path":"E:/proj/report.md"},
 "input":{"file_path":"E:/proj/report.md"}}

{"v":1,"ts":"2026-11-07T11:20:03.447+08:00","run_id":"r-7f3a","seq":3,
 "type":"tool_end","tool":"FileEdit","tool_use_id":"tu_9",
 "ok":true,"duration_ms":46,"result_chars":0}

{"v":1,"ts":"2026-11-07T11:20:03.450+08:00","run_id":"r-7f3a","seq":4,
 "type":"file_change","path":"E:/proj/report.md","operation":"edit",
 "diff":{"added":12,"removed":38}}

{"v":1,"ts":"2026-11-07T11:20:03.452+08:00","run_id":"r-7f3a","seq":5,
 "type":"window_action","action":"show_dir","target":"E:/proj","ok":true}

{"v":1,"ts":"2026-11-07T11:20:03.900+08:00","run_id":"r-7f3a","seq":6,
 "type":"run_end","steps":2,"failed":0,"files_changed":1,"duration_ms":2799}
```

### 5.3 采集规则

| 来源 | 处理 |
|---|---|
| `tool_before` | 记 `tool_start`，同时把 `tool_input` 中的目标提取为 `target` |
| `tool_after` | 记 `tool_end`；`duration_ms` = 同 `tool_use_id` 的 after - before |
| `file_changed` | 记 `file_change`；`diff` 取 checkpoint / shadow git 的增删行数 |
| `run_start` / `run_end` | 由 QueryLoop 或 CLI 回合边界触发（本阶段先由 trace 自身按"首个 tool_start / 汇总"推断，避免侵入循环） |

> 实现注意：`tool_after` 的 `result` 是字符串。trace **只记长度与前 200 字摘要**，不落完整结果，避免与对话历史重复膨胀。

### 5.4 脱敏

| 对象 | 规则 |
|---|---|
| 窗口标题 | 命中 `trace_mask_patterns`（密码管理器、银行、邮箱标题）时替换为 `‹已脱敏窗口›` |
| 文件路径 | 保留绝对路径（用户需要定位）；不记录文件内容 |
| 输入文本 | `TypeText` 只记字符数与首 40 字；含 `password`/`token`/`secret` 关键词时只记长度 |
| 截图 | 默认不采集；仅失败时采一张，文件名不含窗口标题 |

---

## 6. 窗口编排规则

### 6.1 工具 → 动作映射

> ⚠️ **本节表格已作废（2026-11-07）**：跟随决策改由
> [window-observer.md](window-observer.md) 的快照差分机制负责，代码中不再存在
> 工具名白名单；新工具（MCP/插件/harness/Bash）开窗自动覆盖。下表仅作历史记录。

| 工具族 | 目标解析 | 默认动作 |
|---|---|---|
| `MouseClick` / `MouseDrag` / `MouseMove` / `MouseScroll` | 当前前台窗口（或坐标所属窗口） | `show` |
| `WindowFocus` | `title` | `show`（`focus` 档才激活） |
| `WindowMove` / `WindowRect` / `WindowClose` / `WindowList` | `title` | `none` |
| `TypeText` | 当前前台窗口 | `show` |
| `ScreenShot` / `GuiVision` | — | `none`（截图本身可见） |
| `FileRead` | `file_path` | `none` |
| `FileWrite` / `FileEdit` | `file_path` | `show_dir`（节流） |
| `Bash` / 进程类 | `cwd` | `none` |
| `Browser*` | — | `none`（浏览器由工具自身置前） |

### 6.2 目标解析优先级

1. 工具入参中的显式窗口标题（`title` / `window`）；
2. 文件路径 → 所在目录（`dirname`）；
3. 进程名 / 可执行名（可配置映射表）；
4. 解析失败 → 降级为终端提示，不做窗口动作。

### 6.3 `show` 算法（默认档）

```
1. 查找窗口（EnumWindows + 标题包含匹配；优先返回可见、非工具的窗口）
2. 最小化 → ShowWindow(hwnd, SW_RESTORE)
3. 计算位置：
   - 已在主屏安全区且未遮挡终端 → 不动
   - 在屏幕外 / 最小化恢复 → 移到主屏右半区（宽 ≤45%）
4. SetWindowPos(hwnd, HWND_TOPMOST,
       x, y, w, h,
       SWP_NOACTIVATE | SWP_SHOWWINDOW)
5. 保持 window_follow_hold_ms（默认 1200ms）
6. SetWindowPos(hwnd, HWND_NOTOPMOST, 0,0,0,0, SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE)
```

**全程不调用 `SetForegroundWindow`**，因此不会打断用户键鼠。

### 6.4 `focus` 档（需显式开启）

```
SetForegroundWindow(hwnd)
  └─ 失败（返回 0）→ AttachThreadInput 到前台线程 → 再试一次
      └─ 仍失败 → 终端提示「无法激活（Windows 前台锁）」
```

终端必须明示：`⚠ 已接管输入焦点 1.2s，请勿操作键鼠`。

### 6.5 节流

| 场景 | 规则 |
|---|---|
| 同目录 `explorer select` | 同一目录 60s 内只弹一次 |
| 同一窗口 `show` | 5s 内不重复置顶 |
| 连续同类鼠标动作 | 只跟随首个动作，避免每点一次闪一下 |

### 6.6 失败降级

窗口找不到 / 被其他虚拟桌面占用 / 权限不足 → 终端输出：

```
🪟 未能显示「记事本」：窗口在其他虚拟桌面
```

不抛异常、不阻塞工具执行（钩子已有异常隔离）。

### 6.7 任务收尾清理（2026-11 追加）

一轮任务结束时关闭**中间产物窗口**，保留用户要看的结果：

| 规则 | 说明 |
|---|---|
| 只关本轮新开的窗口 | 以回合开始时的窗口快照为基线，`before` 里已有的一律不碰 |
| 只关被操作过的窗口 | 必须出现在窗口跟随/操作记录里（`hwnd` 进 involved 集合） |
| 保留最终结果 | 最后一步打开的窗口不关（通常就是结果） |
| 保留当前前台 | 回合结束时用户正在看的窗口不关 |
| 未保存不关 | 标题含 `*` 的跳过并上报，绝不强杀丢数据 |
| 关闭方式 | `WM_CLOSE`（应用可弹保存确认），不是 `TerminateProcess` |
| 开关 | `[ui] window_cleanup = "transient"`（默认）/ `"off"` |

终端会多一行：`🪟 已关闭 N 个临时窗口；保留 M 个未保存窗口`。

---

## 7. Win32 实现与平台矩阵

### 7.1 API 清单

| API | 用途 | 备注 |
|---|---|---|
| `EnumWindows` / `GetWindowTextW` | 枚举顶层窗口 | 过滤不可见、无标题 |
| `IsIconic` / `ShowWindow` | 判断/恢复最小化 | `SW_RESTORE` |
| `SetWindowPos` | 置顶、移动、尺寸 | `HWND_TOPMOST` / `HWND_NOTOPMOST` / `SWP_NOACTIVATE` |
| `SetForegroundWindow` | 激活（仅 focus 档） | 受前台锁限制 |
| `GetWindowThreadProcessId` + `ProcessIdToName` | 按进程名找窗口 | 可选，需 pywin32 或 ctypes |
| `GetWindowRect` / `MonitorFromWindow` / `GetMonitorInfo` | 多屏与 DPI 计算 | 需要 per-monitor DPI 处理 |

### 7.2 实现选择

| 能力 | 选型 | 理由 |
|---|---|---|
| 窗口枚举/置顶/移动 | **ctypes + Win32 API** | 零新增依赖；`SWP_NOACTIVATE` 只有它能精确控制 |
| 窗口标题查找 | `pygetwindow`（现有） | `WindowFocus` 已在用，保持一致性 |
| 进程/PID 映射 | `pywin32`（已在 extras） | 仅在需要时调用，缺失则跳过 |

### 7.3 平台矩阵

| 平台 | 支持度 | 说明 |
|---|---|---|
| Windows 10/11 | ✅ 完整 | `show` / `focus` / `explorer select` 全支持 |
| macOS | ⚠️ 降级 | 只能 `osascript activate`；无置顶不激活 → 自动降为 `off` 或 `focus` |
| Linux X11 | ⚠️ 降级 | 需外部 `wmctrl`/`xdotool`，检测到才用 |
| Linux Wayland | ❌ 不支持 | 无公开窗口控制接口，直接降为 `off` |

**默认只在 Windows 启用 `show`，其他平台自动降级并在终端说明一次。**

---

## 8. 配置

新增到 `[ui]` 表（该表已存在，当前只有 `boot_animation`）：

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `trace_enabled` | bool | `true` | 是否记录操作轨迹 |
| `trace_keep_runs` | int | `20` | 保留最近多少轮 |
| `trace_capture_on_error` | bool | `true` | 工具失败时是否截一张图 |
| `window_follow` | str | `show` | `off` / `show` / `focus` |
| `window_follow_hold_ms` | int | `1200` | 置顶保持时长 |
| `window_follow_dir_throttle_s` | int | `60` | 同目录弹窗节流 |
| `window_follow_move` | str | `minimal` | `minimal`（只在屏外/最小化时移动）/ `side`（总是挪到侧边） |
| `window_follow_multi_monitor` | str | `primary` | `primary` / `current` |
| `window_cleanup` | str | `transient` | `transient` 关中间产物窗口 / `off` 不关 |
| `trace_mask_patterns` | list | 内置 | 窗口标题脱敏关键词 |

示例：

```toml
[ui]
boot_animation = true
trace_enabled = true
window_follow = "show"
window_follow_hold_ms = 1200
```

旧配置无这些键时全部走默认值，不做迁移、不写回。

---

## 9. 权限与安全

- **不改变权限模型**：窗口显示是"输出侧"行为，不执行输入，不需要 `ask`。
- **`focus` 档属于输入侧**（抢焦点），但真正输入仍由 `TypeText` / `MouseClick` 的既有权限判断把关；`focus` 只影响焦点归属。
- **自动打开编辑器默认关闭**：避免文件锁、自动保存、格式化、编码变化等副作用。默认只做 `explorer /select`。
- **隐私**：trace 落盘在 `~/.jarvis/traces/`，只含路径、标题、摘要，不含文件内容；密码类窗口标题脱敏。
- **可一键关闭**：`/trace off` 与配置项双重入口；关闭后不记录、不跟随窗口。

---

## 10. 打包与依赖

| 项 | 影响 |
|---|---|
| 新增 Python 依赖 | **无**（只用 ctypes；pygetwindow/pywin32 已在现有 extras） |
| 安装包体积 | 约 0 变化 |
| `jarvis-serve.spec` | 若启用 pywin32 PID 查询，需要把 `collect_all("pywin32")` 修正为 `collect_submodules("win32")` + `collect_submodules("win32com")`（当前 collect_all 对非包名会告警） |
| `gate.py` | 新增可选探针 `window.follow`（`kind=python`、`optional=false`，仅 Windows） |
| CI | 现有 ubuntu 矩阵不含 Windows，窗口编排单测必须用假 backend，不依赖真实桌面 |

---

## 11. 测试计划

### 11.1 单元测试（不碰真实窗口）

| 文件 | 覆盖 |
|---|---|
| `tests/core/test_trace.py` | 事件字段、seq 单调、duration 计算、摘要截断、脱敏、环形清理 |
| `tests/core/test_window_follow_policy.py` | 工具→动作映射、目标解析优先级、节流窗口、失败降级 |
| `tests/core/test_win32_window.py` | 用注入的 `FakeBackend` 断言 `show` 调用序列（RESTORE → TOPMOST → NOTOPMOST），且**断言从不调用 `SetForegroundWindow`** |
| `tests/ui/test_trace_view.py` | 单行渲染、失败高亮、回合摘要 |
| `tests/commands/test_trace_cmd.py` | `/trace` 最近/导出/开关 |

### 11.2 集成测试

- `tool_before` → `tool_after` 完整链路（mock 工具）；
- 钩子抛异常时工具仍执行（验证隔离）；
- trace 关闭时零落盘。

### 11.3 手工走查（Windows）

| 场景 | 预期 |
|---|---|
| 记事本写入 | 窗口可见、不抢焦点、终端显示字数 |
| 资源管理器定位 | 打开目录并选中文件，同目录不重复弹 |
| 窗口最小化 | 自动恢复并置顶 |
| 双显示器 | 显示在主屏安全区 |
| 其他虚拟桌面窗口 | 终端提示，不崩溃 |
| `focus` 档 | 终端提示接管，工具结束恢复 |
| 失败路径 | 找不到窗口时只提示，不阻塞执行 |

---

## 12. 分阶段交付

| 阶段 | 内容 | 默认开关 | 验收 |
|---|---|---|---|
| **P1 轨迹** | `trace.py` + `trace_view.py` + `/trace` | `trace_enabled=true` | 终端能看到每步工具、耗时、失败原因；`/trace` 可查最近一轮 |
| **P2 窗口跟随** | `window_follow.py` + `win32_window.py`（`show` 档） | `window_follow=show` | 操作窗口/文件时窗口可见、不抢焦点 |
| **P3 定位与跟随** | `explorer /select`、编辑器窗口跟随、节流 | 同 P2 | 改文件弹出目录并选中；同目录不重复弹 |
| **P4 桌面接入** | serve 发 `run_trace` 事件 + 桌面时间线/置顶小窗 | 后续立项 | 复用同一事件与 Director，无重复实现 |

> P1 先落地即可自用验证；P2 风险最高，必须在 P1 稳定后再开。

---

## 13. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| Windows 前台锁 | `focus` 档激活失败 | 默认不用 `focus`；失败仅提示 |
| 输入冲突 | 用户与 jarvis 同时操作 | `show` 不激活；`focus` 限时 + 明示 |
| 自动打开编辑器 | 文件锁 / 自动保存 / 格式化 | 默认只定位目录，不自动打开 |
| 窗口乱跳 | 每步都挪窗口，体验差 | 节流 + `minimal` 模式 + 安全区判断 |
| 虚拟桌面 | 窗口搬不过来 | 降级提示 |
| 性能 | 截屏/枚举窗口开销 | 只在 GUI 工具触发；失败时才截图 |
| 隐私 | 标题/路径落盘 | 脱敏 + 保留轮数上限 + 可关闭 |

**回滚**：全部能力有独立开关（`trace_enabled` / `window_follow`），关闭后行为回到当前版本，无数据迁移。

---

## 14. 文档与 AOCI 影响

实现阶段需同步：

- `README.md`：能力表新增"操作可见性"一行；
- `USER_GUIDE.md`：新增"它在干什么"章节 + `/trace` 用法 + `[ui]` 配置；
- `docs/architecture/`：新增或补充操作轨迹与窗口编排的架构说明；
- `CHANGELOG.md`：新增能力条目；
- `docs/plans/operation-visibility.md`：本文，随阶段推进更新状态；
- AOCI：新增文件与改动文件各一条 Entry，`gate.py` 若加探针需同步。

---

## 15. 默认值（已按推荐实现）

开工前留的 5 个待确认项，按推荐值落地如下：

| 项 | 已实现值 |
|---|---|
| `window_follow` 默认档 | `show`（可见不抢焦点） |
| 自动弹资源管理器 | 是（同目录 60s 节流，读文件不弹） |
| `focus` 档 | 保留，默认不用 |
| 任务收尾清理 | `transient`（关中间产物，保留最终结果/前台/未保存） |
| trace 保留轮数 | 20 轮 |
| 失败时截图 | 暂未实现（P1 只记错误摘要；截图需要额外依赖与隐私处理，留待后续） |

---

## 16. 附：事件与终端渲染对照

| trace 事件 | 终端显示 |
|---|---|
| `tool_start` | `▶ FileEdit(report.md)` |
| `tool_end` ok | `✓ 46ms`（超过 2s 显示秒数） |
| `tool_end` fail | `✗ 46ms · 权限被拒`（红色） |
| `file_change` | `✎ +12 / -38` |
| `window_action` | `🪟 已把「记事本」置于前台（右侧，未接管输入）` |
| `run_end` | `✓ 完成 · 共 N 步 · 文件工具 M 次 · K 失败 · 2.8s` |
