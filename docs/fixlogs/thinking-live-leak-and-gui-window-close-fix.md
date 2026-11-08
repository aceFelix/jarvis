# 思考面板 Live 残留导致输出错乱 + 火狐被收尾关闭修复复盘

> 发现方式：用户实机走查（终端 REPL 日志 + 桌面观察），2026-11-08 12:50。
> 场景：让 jarvis“把当前目录下的贪吃蛇小游戏用火狐浏览器打开我看一下”。
> 作者：aceFelix

---

## 1. 问题现象

同一轮任务同时暴露两个问题：

1. **输出错乱**：最终回复被撕成一字一行，每行后面粘着一条
   `╭─ 💭 思考过程 ───…` 边框；`🪟 已关闭 1 个临时窗口`、`✓ 完成 …` 也被插进面板中间。
   工具结果卡片第一次出现时（`✅ ToolSearch 结果`）右边缘已经粘着思考文本。
2. **浏览器被关**：用户要求“用火狐打开我看一下”，火狐确实启动了，但任务收尾时
   `run_end` 前出现 `window_action close`，把火狐窗口关掉了。

对应 trace：`~/.jarvis/traces/20261108-125055-d332c4.jsonl`

```text
seq 10  tool_start Bash  firefox.exe file:///.../snake/index.html   12:51:03.4
seq 12  window_action show hwnd=3082486「Mozilla Firefox」          12:51:05.1
seq 14  tool_end   ToolSearch                                       12:51:05.9
seq 16  tool_end   Bash sleep 3                                     12:51:10.2
seq 18  tool_end   ScreenShot                                       12:51:11.5
seq 19  window_action close「已关闭 1 个临时窗口」                     12:51:16.09
seq 20  run_end    steps=8 failed=0                                 12:51:16.10
```

---

## 2. 排查过程

### 2.1 输出错乱：先看时序

关键时间差：窗口动作 `show` 发生在 **12:51:05.1**，下一段思考面板（日志里
`╭─ 💭 思考过程`）紧接着在 **12:51:05.2** 出现。窗口动作由 `WindowObserver`
的定时线程触发，最终走 `safe_info → ui.info → _end_assistant_line → _end_thinking`；
而模型线程正在同一时刻调用 `assistant_thinking` 启动 Live。两条线程撞在同一个
状态机上。

### 2.2 状态机复现

`_end_thinking` 旧实现：

```python
if not self._thinking_started:
    self._thinking_buf = ""
    return          # ← 标志为 False 就整段跳过，不会 stop Live
self._thinking_started = False
if self._thinking_live is not None:
    self._thinking_live.update(...)
    self._thinking_live.stop()   # ← 这里抛异常就不会清 _thinking_live
```

竞态窗口：

```text
模型线程:  _thinking_buf += text
模型线程:  _thinking_started = True      ← 先把标志立起来
观察器线程:            _end_thinking()  → 标志看到 True，置 False，Live 还是 None，什么都不停
模型线程:  _thinking_live = Live(...); start()   ← 此时标志已是 False
```

结果就是 **`_thinking_started=False` 但 Live 在跑**。之后每次 `_end_thinking`
都在开头早退，泄漏的 Live 以 12fps 持续重绘自己的面板，正文每打一个字就被它
插一段边框——与日志完全一致。收尾时 `Live.stop()` 的终帧也因此在 `run_end`
之后才被打印出来。

### 2.3 火狐被关：看收尾保留规则

上一轮修复（WPS 壳窗口残留）把“前台窗口”从保留集合里去掉，只要它本轮被操作过。
本轮火狐：启动 → 被观察器置前（hwnd 进 `_involved`）→ 前台；
后续 `sleep/截图` 让它不再是“最后一步打开的窗口”。三条保留规则全部不成立，
于是 `close_transients` 把它当中间产物关了。

根因：**“用户要求打开的窗口”没有被识别为最终结果**。工具语义上它是 Bash 的
`start`/GUI 启动命令，但收尾规则只看“哪一步、是不是前台”。

---

## 3. 根因

| 问题 | 根因 |
|---|---|
| 输出错乱 | 窗口观察器线程与模型线程共享思考面板状态，缺少锁；`_end_thinking` 用 `_thinking_started` 早退，无法收口“标志已清、Live 仍在跑”的残留 |
| 火狐被关 | 收尾保留规则不认识“GUI 启动命令打开的窗口”，把它当作可关闭的中间产物 |

---

## 4. 修复方案

### 4.1 Live 生命周期（`agent/ui/cli.py`）

- 新增 `_live_lock`（RLock）与 `_live_handles` 句柄清单；
- `assistant_thinking` 把“累积文本 + 置标志 + 创建/启动 Live”整段放进锁内，
  启动前先 `_stop_live_handles_locked()` 清掉任何残留；
- `_end_thinking` **不再看 `_thinking_started` 决定是否收口**：按句柄清单逐个
  `update` + `stop`，任何异常吞掉并把句柄放回清单等下次重试；
- 出现“有句柄但标志已清”时写一条 `diag_warn`，下次可在 `diag.log` 直接看到；
- 静态模式（非交互终端）行为不变：仍只打印一次终帧。

### 4.2 收尾保留 GUI 结果窗口（`agent/core/trace.py`）

- 新增 `agent/core/gui_launch.py`，把 `cmd //c start` / `start ""` / `Start-Process`
  的识别口径抽成共享常量；`agent/tools/bash.py` 改为引用同一份（避免两处口径漂移）；
- `on_tool_after` 识别 Bash 的 GUI 启动步，记录 `_gui_launch_at` 时间点
  （窗口常晚 1~2 秒出现，不能只看该步快照）；
- `note_action` 在 GUI 启动后 10 秒内把“新出现且被置前”的窗口记入 `_keep_hwnds`；
- `_cleanup_transient_windows` 把 `_keep_hwnds` 并入保留集合。

结果：WPS 这类“工具自己改文档、壳窗口是副产物”仍会被收尾关闭；
火狐这类“用户要求打开看”的窗口会保留。

### 4.3 契约式收尾（本轮追加，避免下一次又换一种误判）

启发式只能猜“窗口属于谁”，语义只有工具自己知道，因此补一层声明契约：

- `Tool.window_policy(args) -> "keep" | "transient" | None`（默认 None）；
- orchestrator 在 `tool_after` 里把声明透传给 trace；
- 收尾判定优先级（高 → 低）：
  1. 工具显式 `transient` → 关；
  2. 工具显式 `keep` → 留；
  3. 显式关闭动作（`*Close` / `action=close`）晚于窗口置前 → 关（WPS 壳走这条）；
  4. 未声明 → **默认保留**（`[ui] window_cleanup_unknown = "keep"`）；
     配成 `transient` 才恢复旧激进行为；
  5. 未保存窗口永远跳过。
- 最后一步打开的窗口与 GUI 启动窗口始终保留（显式 transient 除外）。

这样未来新增“打开给用户看”的工具，只要实现 `window_policy` 返回 `keep`，
不改收尾代码也不会被误关；新 harness 若打开的是内部副产物，声明 `transient` 即可。

---

## 5. 验证结果

| 测试 | 断言 |
|---|---|
| `test_end_thinking_stops_live_even_when_flag_cleared` | 标志被清掉时仍强制 stop，句柄清单清空 |
| `test_info_stops_leaked_live_before_printing` | `info()` 打印前先收掉残留 Live |
| `test_gui_launch_window_is_kept_at_cleanup` | GUI 启动后置前的窗口收尾不关闭 |
| `test_gui_launch_command_marks_launch_time` | Bash GUI 启动步记录时间点 |
| `test_cleanup_keeps_undeclared_windows_by_default` | 未声明意图默认保留（宁多留不误关） |
| `test_cleanup_closes_declared_transient_window` | 显式 transient 优先于“最后一步保留” |
| `test_cleanup_declared_keep_beats_later_close_intent` | 显式 keep 优先于关闭动作启发式 |
| `test_cleanup_close_intent_before_show_keeps_window` | 关闭动作早于置前不算中间产物 |
| `test_cleanup_unknown_transient_mode_is_aggressive` | 配置切换后恢复旧激进行为 |
| `test_close_intent_detected_from_action_close` | harness 的 `action=close` 记为关闭意图 |
| `test_window_policy_from_hook_recorded` | orchestrator 透传的声明按步骤落库 |
| `TestWindowPolicy`（Bash） | GUI 启动返回 keep、普通命令返回 None |
| `tests/core/test_gui_launch.py` | 4 种 GUI 写法命中、5 种普通命令不误判 |

命令与结果：

```text
pytest tests/ui/test_thinking_panel.py tests/core/test_trace.py \
       tests/core/test_gui_launch.py tests/tools/test_bash_background.py -q
→ 49 passed
```

实机建议复测：同一句“用火狐打开贪吃蛇”，确认（1）输出不再一字一行，
（2）任务结束后火狐保持打开。

---

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/ui/cli.py` | Live 句柄清单 + 状态锁；`_end_thinking` 强制收口，不再被标志早退 |
| `agent/core/tool.py` | 新增 `Tool.window_policy` 声明契约（keep/transient/None） |
| `agent/core/orchestrator.py` | `tool_after` 透传工具窗口意图 |
| `agent/core/trace.py` | 按步骤记录声明；close 动作/置前时刻；契约式收尾判定 |
| `agent/core/gui_launch.py` | **新增**：GUI 启动命令识别口径（Bash 与收尾共用） |
| `agent/tools/bash.py` | 复用共享口径；GUI 启动声明 `keep` |
| `agent/config/{settings,settings_domains,catalog}.py`、`configs/settings.example.toml` | 新增 `window_cleanup_unknown` 配置 |
| `tests/ui/test_thinking_panel.py` | 新增 Live 残留收口两条用例 |
| `tests/core/test_trace.py` | 新增契约/保守默认/关闭意图等七条用例 |
| `tests/core/test_gui_launch.py` | **新增**：GUI 命令识别用例 |
| `tests/tools/test_bash_background.py` | 新增 Bash 窗口声明用例 |
| `docs/fixlogs/thinking-live-leak-and-gui-window-close-fix.md` | 本复盘 |
| `CHANGELOG.md` | `[Unreleased]` 记录两处修复 |

## 7. 经验总结

- **凡是“线程 + 共享可变状态 + 定时刷新”的组合，先问锁在哪**：窗口观察器是
  后台线程，思考面板是前台线程的 Live，二者只共享一个布尔标志时就必然出竞态。
- **收口函数不要用“是否启动过”作为早退条件**：一旦状态不一致，早退会让故障
  永久化。句柄清单 + 无条件 stop + 失败重试才是幂等收口。
- **“谁打开的窗口”要按语义判断**：GUI 启动命令是用户明确要求“打开看”的唯一
  可靠信号；靠“第几步 / 是否前台 / 是否被操作”都会误判。
- 同一份识别口径（GUI 启动）必须单点定义，否则 Bash 后台化认、收尾清理不认，
  就会复现“启动时当后台、结束时当垃圾”的分裂行为。
- **收尾清理是"语义问题"，不是"规则问题"**：窗口是结果还是副产物，只有打开它的
  工具知道。最终应采用"工具声明 + 保守默认"，启发式只做兜底；继续堆规则只会把
  误判从一种形态换成另一种。
