# 任务收尾关窗失效（前台窗口被误当“用户正在看”）修复复盘

> 发现方式：用户实机走查（终端 REPL 日志 + 桌面观察），2026-11-08 11:44。
> 场景：让 jarvis 用 WPS 新建 docx 并写入“你好，我是jarvis”。
> 作者：aceFelix

---

## 1. 问题现象

任务成功后，终端汇报“全程没有打扰您的桌面”，但实际：

1. 执行 `edit_text` 时终端打印过
   `🪟 已把「WPS Office」置于前台（1200ms，未接管输入）`——窗口确实被置前了；
2. 执行 `writer close` 后，**WPS 应用壳窗口仍留在桌面上**，任务结束时没有收尾关闭；
3. 终端也没有出现 `🪟 已关闭 N 个临时窗口` 那一行。

对应 trace：`~/.jarvis/traces/20261108-114409-72e464.jsonl`

```text
seq 9  window_action show hwnd=107153540「WPS Office」
seq 16 tool_end       cli_anything__wps close（saved=true）
seq 17 run_end        steps=7 failed=0 duration_ms=22595   ← 无 window_action close
```

## 2. 排查过程

### 2.1 先确认清理逻辑有没有跑

`TraceRecorder.finish_run()` → `_cleanup_transient_windows()` 确实存在，配置
`[ui] window_cleanup` 未显式设置，走默认 `transient`。所以不是“没执行”，而是
“执行了但一个窗口都没关”（无关闭事件、无跳过事件时函数静默返回）。

### 2.2 用同一套 WPS harness 复现

`jarvis-harness-wps` 与本次会话同一命令，逐步观察窗口与前台句柄：

| 步骤 | 可见窗口 | 前台 hwnd |
|---|---|---|
| `writer --action new_doc --headless` | 无 WPS 窗口（14 个窗口） | 5571540（终端） |
| `writer --action edit_text --headless` | 新增 `WPS Office`（wps.exe:27072） | **265516 = WPS 窗口** |
| `writer --action save --headless` | `wps-probe.docx - WPS Office` 同一 hwnd | 265516 |
| `writer --action close --headless` | 标题退成 `WPS Office - WPS Office`，**窗口仍在** | 265516 |

结论 A：**WPS 的 COM 写入会自动激活窗口**，把“被操作窗口”变成前台窗口。
结论 B：`writer close` 只关文档，不退出应用，应用壳窗口继续存在。

### 2.3 验证 WM_CLOSE 能否关掉壳窗口

对 265516 调 `Win32Backend.close()`（内部就是 `PostMessage(WM_CLOSE)`）：
返回 `True`，2 秒后窗口消失，WPS 可见窗口为 0。说明清理机制本身有效，只是被判定逻辑挡住了。

## 3. 根因分析

`_cleanup_transient_windows` 的保留规则里有一条：

```python
if isinstance(fg, int):
    keep.add(fg)          # “当前前台窗口 = 用户正在看”
```

这条假设在“用户自己切窗口”时成立，但**工具自己把窗口带到前台时也成立**——
WPS `edit_text` 激活窗口后，它就是前台，于是被当成“用户正在看”保留下来，
导致“本轮新开 + 被操作过”的 WPS 窗口永远关不掉。

二次成因：`writer close` 只关文档，应用壳窗口的 hwnd 与文档窗口是同一个，
标题从 `xxx.docx - WPS Office` 变成 `WPS Office - WPS Office`，仍属本轮新开窗口，
本应由收尾清理关闭。

## 4. 修复方案

`agent/core/trace.py`：前台保留条件加一条“本轮没操作过”：

```python
# 只有"本轮没操作过"的前台窗口才算用户正在看。
# 工具自己把窗口带到前台时（WPS 的 COM 写入会激活窗口），若照旧保留，
# 收尾清理永远关不掉它。
if isinstance(fg, int) and fg not in self._involved:
    keep.add(fg)
```

其余规则不变：本轮新开但没被操作过的窗口仍保留（防误关用户自己开的窗口）；
最后一步打开的窗口仍保留（最终结果）；未保存窗口仍跳过并上报。

## 5. 验证结果

- 新增/调整单元测试：

  | 用例 | 覆盖点 |
  |---|---|
  | `test_cleanup_closes_intermediate_keeps_final_and_foreground` | 用户自己的前台窗口（未参与本轮）仍保留 |
  | `test_cleanup_closes_foreground_window_touched_by_run` | 本轮操作过的前台窗口必须关闭 |

- `pytest tests/core/test_trace.py tests/core/test_window_follow.py tests/core/test_window_observer.py`
  → **32 passed**。
- 实机复现链路：WPS 写入 → 前台变为 WPS → `WM_CLOSE` 关闭成功（见 §2.3）。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/core/trace.py` | 前台窗口只有“未被本轮操作过”才进入保留集合 |
| `tests/core/test_trace.py` | 原用例拆分为“用户前台保留”与“本轮前台关闭”两条 |
| `docs/fixlogs/wps-window-cleanup-foreground-fix.md` | 本复盘 |
| `CHANGELOG.md` | `[Unreleased]` 记录修复 |

## 7. 经验总结

- **“当前前台 = 用户在看”不是可靠的用户意图信号**：任何自动化（COM、pywinauto、
  `SetForegroundWindow`）都会改变前台。判断“是谁的窗口”要看“本轮有没有操作过它”。
- **工具层的 close 语义要确认到进程级**：WPS `writer close` 只关文档，应用进程仍在。
  任务收尾清理必须按窗口（而不是按工具成功与否）判断。
- **终端汇报不能宣称“没打扰”**：窗口跟随本身就会置顶窗口，模型看不到这个运行时行为。
  这类“环境副作用”不该由系统提示词约束模型，应由运行时统一处理并在终端如实上报。
- 该问题不是提示词能解决的：模型无法枚举窗口、也无法可靠判断“该不该关”；
  写进系统提示词只会产生口头承诺或误调 `WindowClose`，真正的保障在收尾清理。
