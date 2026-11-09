# 驾驶舱关闭导致 jarvis 退出 + 关闭后无法自动重现 修复复盘

> 模块：独立任务驾驶舱窗口（C1b）
> 发现方式：用户实机点击窗口 ✕
> 修复完成：2026-11-08
> 作者：aceFelix

---

## 1. 问题现象

用户点击驾驶舱窗口右上角 ✕ 后：

1. **整个 jarvis 进程退出**，终端回到 PowerShell；
2. 终端打印一行 Tcl 内存错误：

```text
alloc: invalid block: 000001BE419B7E00: 90 41
PS C:\Users\aceFelix>
```

3. 期望行为（用户明确）：关闭窗口只隐藏，jarvis 继续聊天；后续任务需要展示时，
   驾驶舱要能自动重新出现。

## 2. 排查过程

### 阶段一：定位关闭链路

`_Hud` 的关闭按钮回调是 `CockpitWindow._close_from_ui()`，旧实现直接：

```python
self._closed.set()
self._root.destroy()
```

`destroy()` 后 `root.mainloop()` 返回，进入 `_thread_main` 的 `finally`：

```python
if root is not None and root.winfo_exists():
    root.destroy()
```

### 阶段二：确认 Tcl 报错来源

`root.destroy()` 已经释放了 Tcl 解释器；`finally` 里再调用
`root.winfo_exists()`，等于对已释放的 Tcl 解释器发命令，属于未定义行为。
实机表现就是 Tcl 分配器报 `alloc: invalid block`，随后解释器/进程崩溃。

同时，旧语义把关闭当成“本会话永久关闭”（`_closed.set()`），
即使用户只是想临时收起，也不符合需求。

## 3. 根因分析

1. **关闭路径销毁 Tk 后继续调用 Tk 方法**：`winfo_exists()` 命中已释放的
   Tcl 解释器 → `alloc: invalid block` → 整个 Python 进程退出；
2. **关闭语义设计错误**：把“点 ✕”实现成“销毁 + 永久禁用”，
   与用户“收起、稍后还能自动出现”的预期相反；
3. Tk 在非主线程运行时，销毁路径必须格外小心；能不销毁就不销毁。

## 4. 修复方案

### 4.1 关闭只隐藏，不销毁

`_close_from_ui()` 改为：

```python
self._hidden = True
self._hidden_run_id = self._run_id
self._root.withdraw()
```

- 不调用 `destroy()`，不置 `_closed`，进程和 Tk 线程继续运行；
- 窗口只是不可见，状态仍在后台更新。

### 4.2 下一轮任务自动重现

`feed()` 记录每个事件的 `run_id`：

- 关闭时记下 `_hidden_run_id`；
- 收到**不同 run_id** 的 `tool_start` 时，投递 `__show__` 指令；
- Tk 线程执行 `deiconify + topmost + lift`，窗口重新出现；
- 同一轮的后续工具不再弹出（避免用户刚关掉又被立刻拉起）。

### 4.3 退出时才销毁，且只销毁一次

- `__shutdown__`（`goodbye()` / `atexit`）→ `_destroy_root()`，只执行一次；
- `finally` 中不再调用 `winfo_exists()`，只用 `_destroyed` 标志判断是否需要销毁；
- 初始化失败路径销毁后同样置 `_destroyed=True`，避免二次销毁。

## 5. 验证结果

### 实机 GUI 探针（真实 Tk 窗口 + Win32 可见性检查）

```text
exit=0
after_start_visible=True
after_close_visible=False
same_run_visible=False
next_run_visible=True
shutdown_returned=True
```

- 关闭后窗口隐藏、进程未退出；
- 同一轮继续调用工具，窗口保持隐藏；
- 下一轮 tool_start 到达，窗口自动重新显示；
- 正常 shutdown 后退出码 0，**不再出现 `alloc: invalid block`**。

### 单元测试

新增 `TestWindowHideReopen` 四条：

- 关闭只隐藏、不置 `_closed`；
- 同一轮保持隐藏；
- 下一轮 tool_start 投递 `__show__` 并取消隐藏；
- 空闲时关闭，下一个任务首个工具就重现。

全量测试：**4620 passed, 2 skipped**（含新增用例）。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/ui/cockpit_window.py` | 关闭改 withdraw；新增 `_show_window/_destroy_root` 与 `__show__/__user_close__`；移除销毁后的 `winfo_exists()`；`_destroyed` 单次销毁保护 |
| `tests/ui/test_cockpit.py` | 新增 `TestWindowHideReopen` 四条用例 |
| `README.md` / `README.en.md` | 更新关闭语义：隐藏，下一轮任务自动重现 |
| `docs/plans/terminal-cockpit.md` | §13 更新关闭/重现/退出生命周期 |
| `CHANGELOG.md` | 记录本次修复 |

## 7. 经验总结

- **`destroy()` 之后不能再调用任何 Tk 方法**，包括 `winfo_exists()`；
  Tcl 解释器已释放，轻则异常，重则整个进程 `alloc: invalid block`。
- “关闭窗口”和“退出程序”必须是两件事：GUI 关闭默认应只隐藏/收起。
- 窗口生命周期用**单次销毁标志**表达（`_destroyed`），不要靠 `winfo_exists()` 猜。
- 非主线程跑 Tk 时，能复用就复用：`withdraw`/`deiconify` 比反复 `destroy`/`Tk()`
  安全得多，也避免 legacy 控制台下的 Tcl 内存问题。
