# Bash 复合后台命令仍卡满 timeout 的修复复盘

> 模块：`agent/tools/bash.py` · 发现方式：2026-11-07 操作可见性实机走查
> 作者：aceFelix

## 1. 问题现象

用户实测「打开记事本 → 输入 → 另存为」整条链路时，第一条命令：

```bash
cd /e/J.A.R.V.I.S_Work/jarvis-work && notepad.exe > /dev/null 2>&1 &
```

**卡满 120 秒**后返回超时，而记事本窗口其实早就打开了：

```
✗ 122.1s · 命令超时（120秒）: cd /e/... && notepad.exe > /dev/null 2>&1 &
```

后果：整轮 143.4 秒（其中 120 秒是纯等待），且被记为 1 次失败；用户看到的是
「记事本已经开了，jarvis 还在等」。

## 2. 排查过程

1. **先看后台化逻辑是否生效**：`_detach_background()` 只把「以 `&` 结尾」的命令
   改写成 `body > /dev/null 2>&1 &`，模型这条命令已自带完整重定向，于是**原样放行**，
   并标记为后台命令（`is_background=True`，用于跳过沙箱）。→ 逻辑按设计执行。
2. **再复现最小用例**：`cd . && sleep 3 &` 同样等满 3 秒才返回（应为 0.2 秒内）。
   `sleep 3 &` 本身却正常。差异只有一个：**命令是复合列表**（`&&`）。
3. **确认根因**：Bash 中 `A && B &` 的 `&` 作用于**整个列表**，即 `(A && B) &`。
   形成的是一个后台**子 shell**；模型写的 `> /dev/null 2>&1` 只挂在 `B`（notepad）上，
   子 shell 自己仍继承父进程的 stdout/stderr 管道。工具侧 `communicate()` 等的是 EOF，
   子 shell 不退出（notepad 不关），EOF 永不到来 → 等满 timeout。
   这与 `_StreamCollector` 注释里记录的历史根因是同一类，只是这次多了一层壳。

## 3. 根因分析

`_detach_background` 的重定向只对「单条命令」正确；对复合后台列表，重定向必须包在
**整组外面**，否则后台子 shell 握着管道，工具侧永远等不到 EOF。

之前的测试只覆盖了单条命令（`firefox.exe URL &`、`make build | tee log.txt &`），
没有覆盖 `cd ... && GUI &` 这种最常见的「先切目录再启动程序」写法。

## 4. 修复方案

只改 `agent/tools/bash.py::_detach_background`：

- 识别复合体（`&&`、`||`、`;`）；
- 复合体且以 `&` 结尾 → 一律改写为 `( body ) > /dev/null 2>&1 &`；
- 单条命令保持原规则（已自带完整重定向则原样放行）。

效果：

```bash
cd /path && notepad.exe > /dev/null 2>&1 &
# →
( cd /path && notepad.exe > /dev/null 2>&1 ) > /dev/null 2>&1 &
```

无临时 workaround；管道脱离语义仍然正确，后台命令照旧不进沙箱。

## 5. 验证结果

- 新增单测：`test_compound_background_is_wrapped`、
  `test_compound_background_without_redirect_is_wrapped`；
- 新增真实子进程回归：`test_compound_background_command_returns_promptly`
  （`cd . && sleep 3 &` 必须在 3 秒内返回）；
- `tests/tools/test_bash_background.py` 全绿，连同操作可见性单测共 37 例通过；
- 未改动前台命令与沙箱路由，原有回归用例全部通过。

## 6. 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/tools/bash.py` | `_detach_background` 支持复合后台列表整组重定向 |
| `tests/tools/test_bash_background.py` | 新增 3 例（两条改写规则 + 一条真实耗时） |
| `agent/core/trace.py` | 顺带把 `KeyTap` 纳入延迟跟随（Ctrl+S 弹窗也能看见） |
| `agent/ui/trace_view.py` | 摘要改为「文件工具 N 次」，避免 GUI 另存的文件被误读为「没改文件」 |

## 7. 经验总结

- **测后台启动，必须用复合命令**：`cd ... && exe &` 才是模型真实写法；只测单条会漏。
- **看到「窗口已出现但工具还在等」先怀疑管道继承**，不是程序没启动。
- 对 `&` 的重定向，作用域永远要问一句「`&` 作用到哪一段」；复合列表要整组包起来。
- 修复后仍建议模型优先用 `Start-Process` / `start` 启动 GUI，`&` 只是兜底。
