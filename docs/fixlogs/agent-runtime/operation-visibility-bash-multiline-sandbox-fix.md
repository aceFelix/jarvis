# 操作可见性：Bash 多行后台命令误入沙箱 + 窗口标题误配修复复盘

> 发现：2026-11-08 人工实测「让 jarvis 打开记事本并写入」。
> 作者：aceFelix

## 1. 问题现象

用户实测时出现：

- **写入非常慢、迟迟不写入**：一次任务等了 2 分多钟才有动作；
- 记事本里出现重复文本 `I am jarvisI am jarvis`；
- 模型开始“乱试”：`Start-Process`、`cmd //c start`、`explorer`、甚至
  `schtasks /create` 建计划任务来拉起记事本；
- 中途把点击发到了 **Notepad++**，而不是目标记事本。

## 2. 排查过程

操作轨迹（`~/.jarvis/traces/20261108-002949-d7e425.jsonl`）给出了完整时间线：

| 时间 | 事件 | 证据 |
|---|---|---|
| 00:29:59 → 00:31:59 | `Bash` 耗时 **120037ms** | 错误 `[沙箱] 命令超时（120秒）` |
| 00:32:14 | `WindowFocus("helloJarvis.txt")` 失败 | 没找到标题匹配的窗口 |
| 00:33:20 | 观察器跟随到 **Notepad++** | `E:\...\helloJarvis.txt - Notepad++` |
| 00:33:50 | `WindowClick` 也落到 Notepad++ | 同一条路径片段命中两个窗口 |
| 00:33:56 | `KeyTap` 入参为空 | 模型在盲试 |
| 00:33:57 | `TypeText` 耗时 669ms | 写入本身正常 |
| 00:33:24 | `FileWrite` 直接改文件 | 编辑器已打开该文件，导致“重新加载” |

也就是说：**不是写入慢，是前面两次各卡满超时**（120s + 40s），写入只在最后发生。

## 3. 根因

### 3.1 Bash 多行后台命令被误判为前台

模型写的是：

```bash
cd /e/... && notepad.exe "..." > /dev/null 2>&1 &
sleep 2; echo started
```

旧 `_detach_background` 只看**整个命令末尾**有没有 `&`；这里 `&` 在中间行，
于是判定为前台命令 → 送进沙箱 → 沙箱等 GUI 子进程退出 → 120 秒超时，
**沙箱结束时还会连带杀掉刚拉起的记事本**。模型因此不断换方式重开。

### 3.1.1 更关键：改写结果根本没被执行

第一次修复后复测仍然是 120 秒。第二次轨迹显示：

```
Bash  cmd //c start "" notepad.exe ...
  122085ms  命令超时（120秒）
```

查 `BashTool.call` 才确认：`_run_cmd` 虽然算出来了，但两条执行路径
（沙箱 `_call_sandboxed` / 普通 `_call_normal`）**传的都还是原始 `command`**，
改写等于没发生。所以修了“判定”，没修“接线”。

### 3.1.2 GUI 启动没有真正后台化

即使改写作数生效，`cmd //c start` 本身没有 `&`，普通路径仍会 `wait()` 到超时。
现在对 `start` / `Start-Process` 整段包成 `( 命令 ) > /dev/null 2>&1 &`，
确保 bash 立刻返回。

### 3.2 `start` / `Start-Process` 同样被沙箱等待

`cmd //c start "" notepad` 与 `powershell Start-Process` 都是“立刻返回、GUI 留在后台”，
但沙箱会等子进程树结束，于是又吃满 40 秒超时。

### 3.3 窗口标题模糊匹配取第一条

`helloJarvis.txt` 同时命中：

- `helloJarvis.txt - 记事本`（短，目标）
- `E:\...\helloJarvis.txt - Notepad++`（长，碰巧包含路径）

旧实现取枚举到的第一条，于是 `WindowClick` 和观察器把动作发给了 Notepad++。

### 3.4 编辑器打开时又用 FileWrite 直改文件

文件被外部改写，Notepad++ 弹出“重新加载”，模型截图看到状态不一致，
进一步误判“没写进去”，于是重复输入。

## 4. 修复

| 修复 | 实现 |
|---|---|
| 逐行识别后台命令 | `_detach_background` 改为逐行扫描，任意一行以 `&` 结尾就按后台处理并整组重定向；后续行照常执行 |
| GUI 启动跳过沙箱 | 新增 `_GUI_LAUNCH`：`cmd //c start` / `start ""` / `Start-Process` 一律包成后台命令 |
| **改写结果接入执行** | `call` 两条路径改用 `_run_cmd`，不再传原始 `command` |
| 标题匹配排序 | 工具层 `_find_windows` 与 Win32 后端 `find_by_title` 都改为：精确优先 → **标题更短优先** |
| 回归测试 | 多行后台、GUI 启动、改写接线、短标题优先、精确优先 |

## 5. 验证

- `tests/tools/test_bash_background.py`、`tests/tools/test_window_title_match.py`、
  `tests/core/test_win32_window.py`、`tests/core/test_window_observer.py`、
  `tests/core/test_window_follow.py`、`tests/core/test_trace.py`
- 结果：**20 passed, 1 skipped**（本轮子集）；此前全量相关 48 passed
- 待人工复测：同一条指令应不再出现 120 秒等待，且动作落到记事本而非 Notepad++。

## 6. 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/tools/bash.py` | 多行后台识别、GUI 启动跳过沙箱 |
| `agent/tools/system/window.py` | 模糊匹配短标题优先 |
| `agent/core/win32_window.py` | `find_by_title` 精确优先 + 短标题优先 |
| `tests/tools/test_bash_background.py` | 新增 3 例 |
| `tests/tools/test_window_title_match.py` | 新增文件，2 例 |
| `tests/core/test_win32_window.py` | 新增 1 例 |

## 7. 经验

1. **“卡住”先看轨迹，不要猜**：JSONL 的 `duration_ms` 一眼定位到两次超时；
2. **`&` 的位置语义很脆**：模型会把后台命令和后续命令写在同一段脚本里，按行判断才稳；
3. **沙箱与 GUI 天然冲突**：任何会留下常驻窗口的命令，都不该进“等子进程树结束”的沙箱；
4. **模糊匹配必须有确定性排序**：否则同一文件名出现在两个编辑器标题里就会发错窗口；
5. **编辑器已打开文件时不要用 FileWrite 直改**：会触发重新加载并让模型误判，后续应在提示词或工具层提示“优先 TypeText 或先关闭编辑器”。

## 8. 遗留

- 本轮模型自行创建了计划任务 `JarvisNotepad`（`schtasks /create`），属于模型绕路行为，
  需要用户手动清理：`schtasks /delete /tn JarvisNotepad /f`；
- 是否要在 `Bash` 层拦截 `schtasks /create` 这类持久化副作用，后续单独评估。
