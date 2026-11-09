# 任务驾驶舱“纯闲聊也弹窗”修复复盘

> 模块：任务驾驶舱（`agent/ui/cockpit_window.py`，独立 tkinter HUD）
> 发现时间：2026-11-09（用户实机：连续几轮只是聊天、没调用任何工具，HUD 仍反复弹出）
> 修复时间：2026-11-09
> 作者：aceFelix

---

## 1. 问题现象

用户在用 JARVIS 闲聊（“下吧”“好的”“退下吧”等），**每轮都没有调用任何工具**，
但屏幕右侧的任务驾驶舱仍然不断弹出，并且内容全是零：

```text
J.A.R.V.I.S · TASK COCKPIT // 任务驾驶舱
步骤 0   失败 0   耗时 2.2s
✓ 任务完成
共 0 步 · 2.2s · 窗口保留
执行序列 // EXEC
— 暂无工具调用
数据流 // STREAM
— 暂无事件
抓取 // FETCH
— 暂无抓取
```

终端侧同样每轮打出一行 `完成 · 共 0 步 · 工具 0 次 · 0 失败 · 2.2s`。

复现步骤：

1. 启动 jarvis，任意聊两句（不触发工具）；
2. 观察右侧屏幕：驾驶舱窗口弹出，显示 0 步 / 0 工具 / 任务完成。

---

## 2. 排查过程

### 阶段 1：确认弹窗入口只有一个

全仓检索 `CockpitWindow` 的创建与展示：

- `agent/ui/cli.py` 的 `on_trace_event()` 在**任意** trace 事件上懒创建
  `CockpitWindow` 对象（此时还只是普通 Python 对象，不建 Tk 窗口）；
- 真正建窗在 `CockpitWindow.feed()`：`if self._thread is None and event_type in
  ("run_start", "tool_start"): self._ensure_started()`。

结论：弹窗既不是工具卡、也不是代理卡，就是主 HUD 自己——只要收到 `run_start`
就建窗。

### 阶段 2：确认闲聊轮次是否真的发 `run_start`

查 `TraceRecorder`：每轮对话都会 `run_start` → 若干事件 → `run_end`，
**不区分这轮有没有工具**。所以“第一轮任务弹窗”和“闲聊也弹窗”在
`feed()` 看来完全一样。

### 阶段 3：定位“为什么以前没被发现”

C1b 的设计口径是“首次任务开始时弹出”。当时 `run_start` 与“任务开始”是等价的，
但模型后来支持了**不调用工具的纯文本回合**（闲聊、确认、退下等），这个前提就不成立了。
属于设计前提漂移，而非某个函数写错。

### 阶段 4：顺带发现第二个问题

即使不建新窗口，`run_start` + `run_end` 仍会**清空状态**并重绘：
已常驻的 HUD 会被闲聊轮次刷成“0 步 / 任务完成 2.2s”，把上一轮真实任务汇总冲掉。
所以修复不能只做“不建窗”，还要丢弃无工具的整轮事件。

---

## 3. 根因分析

`CockpitWindow.feed()` 把 `run_start` 当作“任务开始”的唯一判据：

```python
if self._thread is None and event_type in ("run_start", "tool_start"):
    self._ensure_started()      # ← 闲聊也会走到这里
```

- `run_start` 是**每轮**都会发出的生命周期事件，不代表“有任务要展示”；
- 驾驶舱的展示语义实际是“**有工具调用**的任务”，判据应当是本轮是否出现过
  工具 / 抓取 / 文件变更事件；
- 同时 `run_end` 无条件投递，导致无工具轮次也会重置状态。

---

## 4. 修复方案

改动集中在 `agent/ui/cockpit_window.py` 的 `feed()`，新增两个状态字段
`_pending_run_start` / `_run_has_activity`：

| 事件 | 旧行为 | 新行为 |
|---|---|---|
| `run_start` | 立即建窗 + 投递 | 只记 `_run_id`、缓存事件，**不建窗、不投递** |
| 首个工具/抓取/文件事件 | 建窗（若尚未建） | 建窗（若尚未建），并把缓存的 `run_start` **先补投**，保证耗时从任务开始算 |
| 无工具的 `run_end` | 投递（状态被清成 0 步） | 直接丢弃，窗口保持上一轮汇总 |
| 有工具的 `run_end` | 投递 | 投递（行为不变） |

```python
if event_type == "run_start":
    self._run_id = run_id
    self._run_has_activity = False
    self._pending_run_start = event
    return
if event_type == "run_end":
    if not self._run_has_activity:
        self._pending_run_start = None
        return
    ...
else:
    self._run_has_activity = True      # 工具/抓取/文件变更都算任务活动
```

没有引入配置项、没有改数据模型（`CockpitState` 不动），也没有临时 workaround：
判据从“轮次开始”改成“轮次内是否真的调用过工具”，与展示语义一致。

---

## 5. 验证结果

- 新增 3 个单测（`tests/ui/test_cockpit.py::TestWindowChatOnlyTurns`）：
  - `test_chat_only_turn_never_starts_window`：`run_start`+`run_end` 无工具 →
    线程未启动、队列为空；
  - `test_first_tool_start_replays_run_start`：首个工具才建窗，队列顺序为
    `run_start` → `tool_start`；
  - `test_chat_turn_keeps_previous_summary`：窗口已开时闲聊轮次不入队、不冲掉汇总，
    下一轮真任务仍正常补投 `run_start`。
- `python -m pytest tests/ui -q` → **123 passed**（原 120 + 新增 3）。
- 人工走查（待用户复测）：连聊多轮不弹窗；跑一个带工具的任务仍然弹出且耗时从
  任务开始计算。

---

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/ui/cockpit_window.py` | `feed()` 改为“首个工具才建窗 + 补投 run_start + 丢弃无工具 run_end”；新增 `_pending_run_start` / `_run_has_activity` |
| `tests/ui/test_cockpit.py` | 新增 `TestWindowChatOnlyTurns` 三个用例 |
| `README.md` / `README.en.md` | 任务驾驶舱能力行补充“仅本轮真正调用工具时才弹出，纯闲聊不弹” |
| `docs/plans/terminal-cockpit.md` | 生命周期表与人工验收补充“纯闲聊不弹”口径 |
| `CHANGELOG.md` | Unreleased 记录本次修复 |

---

## 7. 经验总结

- **生命周期事件 ≠ 业务判据**。`run_start` 只说明“一轮开始了”，不能代表“有任务要展示”；
  凡是“某某时候弹窗/显示”的需求，判据要落在**业务活动**（工具、抓取、文件变更）上。
- **不要只看“会不会多弹”，还要看“会不会覆盖”**。同一个 `run_start` 既触发建窗也触发
  `reset()`，只在建窗处打补丁会让常驻窗口被闲聊清成 0 步，必须整轮事件一起丢弃。
- **缓存 + 补投**是处理“延迟决策”的通用手法：先不消费，等真正需要时按原顺序补投，
  状态模型无需改动即可保持时间口径正确。
- 建议后续为“窗口何时出现”这类交互补一条人工走查项（已加入
  `docs/plans/terminal-cockpit.md` §13.5 第 7 条）。
