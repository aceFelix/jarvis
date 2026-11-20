# 终端展示两个问题：cmd 表格丢失边框 / Git Bash 缩放吞掉顶部文字

> 状态：**P1 / P1-d 已实施；P2（mintty 缩放）待实机定界**（2026-11-20）
> 发现版本：PyPI `jarvis-agent 2.6.0`（cmd 与 Git Bash 两种终端）
> 关联：`docs/fixlogs/maximized-boot-resize-garble-fix.md`（120 列封顶）、
> `docs/fixlogs/cmd-thinking-panel-legacy-console-fix.md`（legacy 控制台降级）

## 1. 问题现象

用户在 `E:\J.A.R.V.I.S_Work\jarvis-work`（普通目录，检查点阻塞已绕过）分别用两种终端测试：

### P1：cmd（conhost，`C:\WINDOWS\system32\cmd.exe`）下展示不友好

- `/doctor` 的**所有表格边框完全消失**：只剩标题、表头与两列文本，`┌─┬─┐`、`│`、`├─┼─┤` 一个都没有；
- 表格仍然按列对齐（说明表格结构与列宽计算是好的，缺的只是 box 字符）；
- `Permissions file` 这种长路径把行撑到接近窗口宽度，出现折行/贴边；
- 标题里的 emoji（`📦` `⚙️` `⚠️` `🛠` 等）不可见（`make_stdio_crash_proof` 的 `errors="replace"` 兜底后变成 `?` 或空）。

### P2：Git Bash（mintty）下缩放吞字

- 把 Git Bash 窗口**放大后再缩小**，**最上面的文字被吞掉消失**；
- **重复"放大→缩小"会一点点持续吞掉**，每次少一截；
- 图片佐证：缩放后视野从表格中段（`用户配置` 行）开始，上方的「环境信息 / Provider 状态」整块不见；
- 期间**没有**在思考（无 Rich Live 活动），处于 `>` 提示符等待输入状态。

## 2. 排查过程

### 2.1 先排除 Rich 的 legacy 行为

用 2.6.0 同版本 Rich（15.0.0）做最小复现：

```python
Console(legacy_windows=True, force_terminal=True, width=60, color_system=None)
Table(title="T", expand=True).add_column(...)
```

输出：

```
                             T
┌─────────────────────────────┬────────────────────────────┐
│ A                           │ B                          │
├─────────────────────────────┼────────────────────────────┤
│ 1                           │ 2                          │
└─────────────────────────────┴────────────────────────────┘
```

结论：**legacy 下 Rich 用的是方角 SQUARE，边框正常输出**。
（与既有记忆「Rich 面板边框字符的平台差异与渲染确定性策略」一致：
legacy 只是把 ROUNDED 换成 SQUARE，不会抹掉边框。）
→ **cmd 缺边框不是 Rich 的 legacy 分支造成的**，需要继续定界到编码/字体/Renderable 上下文。

### 2.2 发现实锤缺陷：`_global_console` 根本不存在

`agent/ui/markdown_renderer.py:21-35`：

```python
def _get_console(console: Any | None) -> Any | None:
    if console is not None:
        return console
    try:
        from agent.ui.cli import _global_console   # ← 符号不存在
        if _global_console is not None:
            return _global_console
    except Exception:                              # ← ImportError 被静默吞掉
        pass
    try:
        from rich.console import Console
        return Console()                           # ← 每次新建一个默认 Console
    except Exception:
        return None
```

- 全仓 grep `_global_console`：**只有这一处引用**，`agent/ui/cli.py` 里从未定义，
  也没有其他模块注入过；
- 因此 `/doctor`、`/context`、`/config show`、`/cost` 等所有走
  `render_table / render_panel / render_tree(console=None)` 的输出，
  **每个表格都新建一次默认 `Console()`**；
- 默认 `Console` **不是 `_CappedConsole`**，所以：
  1. **120 列封顶对这些命令完全失效**（封顶只作用于 `RichCLI._console`）；
  2. 每个表格独立探测终端能力/宽度，与 REPL 主 Console 的认知不一致；
  3. `Console()` 构造时的 `legacy_windows`/`encoding` 与主 Console 可能不同，
     这正是"同一份代码在 cmd 与 mintty 表现不同"的合理怀疑点之一。

这条与仓库反复出现的"静默吞错"陷阱同族（参见 `undefined-name-imports-fix.md`：
`/doctor` 曾因 `Path` 未导入直接 NameError 崩掉会话）。

### 2.3 P2 的机制分析（mintty）

REPL 输入走 `agent/ui/cli.py` 的 `PromptSession`（`_pt_session.prompt()`），
非全屏模式；Rich 输出走 `_CappedConsole`（宽度封顶 120，**height 仍取真实值**）。

mintty 在窗口宽度变化时会 **reflow（重排）** 已有的软换行：

- 放大后缩窄：原本 120 列的行被压成 2 行 → **可见区实际行数变多**；
- prompt_toolkit 的 Renderer 用"上次渲染高度"做相对光标回退（`move_cursor_up`
  + `erase_down`）来重绘提示符；它记的是**逻辑高度**，不是 mintty reflow 后的
  物理高度；
- 回退不足 → 擦除起点落在提示符上方 → **把上方内容一起擦掉**；
- 每次 resize 都错一次，于是"一点点被吞掉"。

旁证：conhost（cmd）**不做 reflow**，所以同样的缩放不会出现这个现象——这与
用户"只有 Git Bash 有"的观察吻合。

结论：**P2 是 prompt_toolkit × mintty reflow 的交互问题**，jarvis 的 120 列封顶
只降低了触发概率（超过 120 列的行仍会 reflow），不能根治。

## 3. 根因分析

| 问题 | 根因 | 性质 |
|---|---|---|
| P1-a 边框消失 | 待定界：候选（a）cmd 字体/代码页导致 box 字符不可见；（b）`_get_console` 新建的 Console 与主 Console 上下文不一致 | **待验证** |
| P1-b 表格超宽/长路径贴边 | 默认 `Console` 未封顶 120；`Permissions file` 列无 `overflow`/`no_wrap` 策略 | 确定 |
| P1-c emoji 缺失 | cmd 区域编码 cp936 编不出 emoji，`replace` 兜底成 `?`/空 | 确定（设计已知，但缺 ASCII 替代品） |
| P1-d 封顶失效 | `_global_console` 从未定义，`_get_console` 静默 fallback 到新 Console | **确定（实锤缺陷）** |
| P2 mintty 吞字 | prompt_toolkit 相对光标定位 × mintty resize reflow 行数误算 | 高置信（机制） |

## 4. 修复方案（待实施）

### P0-1 修掉 `_global_console` 幽灵引用

两条路，择一（推荐 B）：

- A：在 `agent/ui/cli.py` 定义模块级 `_global_console`，`RichCLI.__init__` 时赋值，
  进程退出/重建时置空；`markdown_renderer` 不动。
- B：删掉 `_global_console` 导入，改为**显式传入 console**：所有命令处理器从
  `ctx.ui` 取 `ui.console` 传给 `render_*`；`_get_console(None)` 只保留
  "新建 Console"作为最后兜底，并在 `render_*` 上标注"不传 console 会失去封顶"。

风险：B 需要改多处调用点（`/doctor`、`/cost`、`/context`、`/config show`、
`/diff`、`/trace` 等），但能一次性消除"哪个 Console 在渲染"的歧义。
必须同时补一个测试：断言 `render_table(..., console=None)` **不会**绕过封顶
（或在 B 方案下断言调用方都传了 `ui.console`）。

### P0-2 legacy / 窄终端下的降级口径

- `Console.legacy_windows` 为真时，表格显式 `box=box.ASCII`（`+ - |`），
  彻底避开"制表符在 cmd 字体里不可见"的不确定性；
- 同条件下把 emoji 换成 ASCII 标记：`[OK]` / `[!]` / `[PKG]` / `[CFG]`；
- 命令输出层统一走一个 `_is_plain_terminal()` 判据，避免各处自己判断。

### P1-1 列宽与长路径

- `render_table` 增加列策略参数：第一列 `no_wrap=True`，值列
  `overflow="ellipsis"`（路径用 `…` 截断，完整值由 `/config explain` 或面板查看）；
- 表格默认 `expand=False` 已够用；若某表需要整宽，显式传参。

### P1-2 mintty 缩放对策（缓解，非根治）

按代价从低到高：

1. **文档与首屏提示**：Git Bash 用户可在 mintty 选项里关闭 resize reflow；
2. **resize 主动重绘**：检测 `SIGWINCH`（mintty 支持）→ 输出
   `\x1b[2J\x1b[3J\x1b[H` 清屏并重印紧凑横幅；
   代价是丢失 scrollback 可见历史，必须做成可选（配置项默认关）；
3. **降低封顶列数**（120 → 100/80）：减少触发 reflow 的窗口区间，但牺牲宽屏可读性；
4. 不采用：改用裸 `input()`——会丢掉补全与历史，体验倒退更大。

## 5. 验证结果

**已实施部分（2026-11-20）**：

- `_global_console` 幽灵引用已修（实际采用方案 A，改动面最小）：`cli.py` 定义
  模块级变量并在 `RichCLI.__init__` 赋值；`markdown_renderer._get_console`
  只捕获 `ImportError`，不再吞掉一切；
  实测 `_get_console(None) is ui._console` → `True`，类型 `_CappedConsole`；
- legacy 降级已实施：`render_table` 在 `legacy_windows=True` 时用 `box.ASCII`、
  列 `overflow="ellipsis" + no_wrap`、标题去 emoji；`render_panel` 同步换 ASCII
  边框；`warn/error/ask_user/notify_reminder` 的 emoji 前缀降级为
  `[!] / [x] / [?] / [提醒]`；
- 实测 legacy 渲染：输出 `+------+` / `| 项 | 值 |`，标题 emoji 已被移除；
- `tests/ui/test_capped_console.py` 新增「命令输出复用封顶 Console」回归用例。

**P2（mintty 缩放吞字）未实施**：机制是 prompt_toolkit 相对光标 × mintty
reflow，清屏重绘会丢 scrollback，属于有副作用的方案，须先实机定界再决定。

仍需补的定界证据：

### 定界 P1（请在实际 cmd 里执行，把输出贴回）

```cmd
chcp
"D:\Program\Python\Python313\python.exe" -c "from rich.console import Console;from rich.table import Table;c=Console();print('legacy=',c.legacy_windows,'enc=',c.encoding,'w=',c.width,'color=',c.color_system);t=Table(title='T');t.add_column('A');t.add_column('B');t.add_row('1','2');c.print(t)"
```

判读：

- 若**这条最小用例也没有边框** → 终端/字体/编码问题（非 jarvis 代码），
  修复按 P0-2（强制 ASCII box）走；
- 若**这条有边框、但 `/doctor` 没有** → 问题在 jarvis 的
  `_get_console`/Renderable 上下文（优先按 P0-1 B 方案修）。

### 定界 P2（Git Bash 里执行）

```bash
stty size
python -c "print('X'*200)"   # 先输出一条超宽行，再反复放大/缩小窗口
```

- 若纯 Python 输出也会被吞 → 确认是 mintty reflow + 终端自身行为；
- 若只有 jarvis 会吞 → 重点查 prompt_toolkit 的 Renderer 高度计算。

### 实施后的验收

1. cmd 下 `/doctor` 至少能看到 ASCII 边框与 `[OK]` 类标记，长路径不撑破行；
2. Git Bash 下反复放大/缩小 5 次，顶部内容不再累积丢失（或在开启
   "resize 重绘"选项后表现为清屏重印而非错位）；
3. `/cost`、`/context`、`/config show`、`/trace` 在宽终端（>120 列）下
   输出宽度 ≤ 120（证明封顶生效）；
4. `tests/ui/test_capped_console.py` 增补"命令输出也走封顶 Console"的用例；
5. 回归：`pytest tests/ui tests/commands -q` 全绿。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/ui/markdown_renderer.py` | 修掉 `_global_console` 幽灵引用；`render_table` 增加列策略与 plain 降级 |
| `agent/ui/cli.py` | 如需暴露全局 console 或增加 `_is_plain_terminal()`；ASCII 降级口径 |
| `agent/commands/handlers/core_commands.py` | 各 `render_*` 调用显式传 `ui.console` |
| `agent/commands/handlers/config_commands.py` / `system_commands.py` 等 | 同上（所有命令输出点） |
| `agent/doctor.py` | `run_doctor` 的表格同样接入 plain 降级与列策略 |
| `tests/ui/test_capped_console.py` | 补"命令输出不绕过封顶"用例 |
| `docs/guide/commands.md` | 说明 cmd/legacy 下的降级外观 |
| `docs/fixlogs/terminal-ui-cmd-render-and-mintty-resize-fix.md` | 本复盘 |

## 7. 经验总结

- **`except Exception: pass` 会把"符号不存在"变成"静默换实现"**。这次是
  `from agent.ui.cli import _global_console` 从未存在，导致所有命令输出
  悄悄换成新建 Console——功能看着正常，封顶与终端能力判断全部失效。
  导入失败必须是显式的，兜底分支要能被测试观测到。
- **UI 降级判断只能有一个真源**。`legacy_windows` 在主 Console 上判一次，
  却因为新建 Console 而各自判一遍，必然分叉。凡是"按终端能力选择外观"的逻辑，
  都必须绑定同一个 Console 实例。
- **终端内容是"一次成像"的**：resize 只影响旧行的重排，程序无法重绘 scrollback。
  因此"封顶列数 + 缩短行"是唯一可靠的预防手段；任何依赖"resize 后再修正"的方案
  都只能做可选清屏重绘。
- **两种终端要分别验收**：conhost 无 reflow、mintty 有 reflow，同一 UI 代码
  的故障模式完全不同。终端 UI 的验收清单必须包含"cmd + Git Bash + 缩放"三项。
