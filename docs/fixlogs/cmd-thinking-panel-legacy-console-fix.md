# cmd 下思考面板不显示（legacy Windows 控制台）修复复盘

> 模块：终端 UI · 思考过程面板
> 发现方式：用户实机对比 cmd 与 Git Bash
> 修复完成：2026-11-08
> 作者：aceFelix

---

## 1. 问题现象

同一轮对话、同一模型，在 **cmd.exe** 中运行时看不到「💭 思考过程」面板；
在 **Git Bash（mintty）** 中同一功能正常显示。用户提供了两张对照截图：

- cmd：只有工具结果、正文和 `✓ 完成` 汇总，思考内容完全不可见；
- Git Bash：思考阶段结束后能看到一整块「💭 思考过程」面板。

一开始怀疑是“cmd 窗口太矮、面板被顶出可视区”，但用户在运行过程中也没有观察到面板出现。

## 2. 排查过程

### 阶段一：先排除 TTY 判定

在 cmd 中执行：

```cmd
.venv\Scripts\python.exe -c "from rich.console import Console; c=Console(); print('is_terminal=',c.is_terminal,'legacy=',c.legacy_windows,'TERM=',__import__('os').environ.get('TERM'))"
```

输出：

```text
is_terminal= True legacy= True TERM= None
```

说明 cmd 是“真终端但 legacy 控制台”。

### 阶段二：对照思考面板的两条渲染路径

`agent/ui/thinking_panel.py` 原有分支：

- `console.is_terminal=True` → 建 Rich `Live`，12fps 原地刷新；
- 非 TTY（stdout 重定向 / 管道 / mintty）→ 静态模式，思考结束时打印一次。

Git Bash 的 Windows 原生 Python stdout 是 msys 管道，`is_terminal=False`，
因此走静态打印，**一直正常**；cmd `is_terminal=True`，走 Live 路径。

### 阶段三：确认根因

Rich 的 Live 原地刷新依赖光标移动与擦除转义；在 **legacy Windows 控制台**
（Rich 的 `legacy_windows=True`）下，这些控制序列不可靠，帧画不出来或错乱。
旧代码只判断了 `is_terminal`，没有排除 `legacy_windows`，因此 cmd 走了不可用的路径。

### 阶段四：修复后又发现 Panel 本身也会乱码

改成“legacy 一律静态打印”后，用真实 cmd 控制台探针复现，发现 Rich `Panel`
（带边框的思考框）在 legacy 渲染器下会被打成**一列竖排乱码**：每行一个字，
边框消失。最小对照探针确认：

- 内置 `print()` 与普通 `console.print("文本")` 正常；
- `console.print(Panel(...))` 在 legacy 控制台下异常。

因此静态打印不能再用 Panel，必须换成纯文本分隔块。

## 3. 根因分析

- cmd 的 `Console.legacy_windows=True`；
- 思考面板的启用条件 `console.is_terminal` 对 cmd 为真，误判为“支持 Live”；
- Rich Live 在 legacy 控制台无法稳定渲染，导致整块面板不可见；
- 进一步：即使不走 Live，Rich `Panel` 的边框/宽度计算在 legacy 渲染器下也不可靠，
  会被打成一列竖排乱码；
- Git Bash 因非 TTY 走了静态降级路径，且没有命中 Panel 的 legacy 渲染问题，反而正常。

## 4. 修复方案

1. `agent/ui/thinking_panel.py` 的 Live 启用条件增加 legacy 判断：

```python
use_live = (
    console
    and _HAS_RICH
    and console.is_terminal
    and not bool(getattr(console, "legacy_windows", False))
)
```

2. legacy 静态打印不再输出 Rich `Panel`，改用新增的 `plain_panel()` 纯文本块：

```text
---- 思考过程 ----
<完整思考内容>
------------------
```

现代终端（Windows Terminal、VS Code 终端等 `legacy_windows=False`）仍保留
Live 原地刷新与带边框 Panel，视觉不变。

## 5. 验证结果

- 新增单元测试 `TestLegacyWindowsConsole::test_legacy_windows_uses_static_mode`：
  假 legacy Console → 不创建 Live、思考结束只打印一次纯文本块（断言含标题与内容）；
- 真实 cmd 控制台探针（`legacy_windows=True`）：修复后
  `---- 思考过程 ----` 与内容正常横排显示，不再竖排乱码；
- 原有 Live / 静态降级 / 空白增量 / Live 泄漏收口用例全部通过；
- 全量测试：**4616 passed, 2 skipped**；
- `python -m compileall -q agent/` 与 `ruff check --select F821` 通过；
- 用户实机复测：重启 cmd 里的 jarvis 后确认。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/ui/thinking_panel.py` | Live 启用条件增加 `not legacy_windows`；注释补三种终端模式 |
| `tests/ui/test_thinking_panel.py` | 新增 legacy 控制台静态降级用例；测试辅助 Console 显式 `legacy_windows=False` |
| `CHANGELOG.md` | 记录本次修复 |

## 7. 经验总结

- **`is_terminal=True` 不等于“支持 Rich Live”**：Windows 还要看 `legacy_windows`。
- cmd / conhost 属于 legacy；Git Bash（mintty）对 Windows 原生 Python 是管道，
  会走静态降级，因此两者的分支完全不同。
- 排查终端渲染问题时，先打印 `is_terminal` / `legacy_windows` / `TERM` 三个值，
  比凭现象猜“窗口高度/滚动”快得多。
- 这类兼容判定应集中在渲染层（`thinking_panel.py`），不要散落到 CLI 调用点。
