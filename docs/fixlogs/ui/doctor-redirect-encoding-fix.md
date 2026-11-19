# `jarvis --doctor > log.txt` 直接崩：重定向后 stdout 变成 GBK 严格编码

日期：2026-11-10 ｜ 影响：中文 Windows 下任何「把输出重定向/接管道」的场景 ｜ 状态：已修复并二次验证

## 1. 问题现象

2.5.2 发版后的安装态验收里，用隔离测试台跑诊断取日志：

```powershell
.jv-test\venv\Scripts\jarvis.exe --doctor > .jv-test\out-a.txt 2>&1
```

进程以 traceback 退出，磁盘上的日志文件里连一张表都没有：

```text
Traceback (most recent call last):
  ...
  File "...\rich\console.py", line 2091, in _write_buffer
    legacy_windows_render(buffer, LegacyWindowsTerm(self.file))
  File "...\rich\_win32_console.py", line 402, in write_text
    self.write(text)
UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f4e6' in position 31: illegal multibyte sequence
```

同一台机器、同一个 venv，**不重定向**直接敲 `jarvis --doctor` 却是完好的，表格正常打印。
最小复现：

```powershell
python -c "from rich.console import Console; Console().print('box 📦')" | Out-Null
# UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f4e6'
```

## 2. 排查过程

| 阶段 | 假设 | 做法 | 结论 |
|---|---|---|---|
| 1 | 是 Rich 的 legacy Windows 渲染器在作怪（traceback 里有 `legacy_windows_render`） | `Console()` 打印 `legacy_windows` / `is_terminal` | **`legacy=False`**，假设推翻；`Console(legacy_windows=False)` 照样崩 |
| 2 | 那崩点是编码本身 | 检查控制台代码页 `chcp` | **936**（简体中文 GBK） |
| 3 | 为什么直连不崩、重定向就崩 | 分别测 `sys.stdout.encoding` | 直连控制台走 WindowsConsoleIO → **utf-8**；被重定向时走普通文件对象 → **cp936 + strict**。两条路径编码不同，这才是分水岭 |
| 4 | 谁的字符编不出来 | `git log -S "📦 Python 包状态"` | `agent/doctor.py:406` 表格标题；同一文件里还有 🔧（`_render_table_three_col` 的系统依赖表）、⚙️（配置表）、✓/⚠（汇总）。提交是 `06fc104`，**远早于 2.5.2**，不是本版引入 |
| 5 | 影响面只有 doctor 吗 | 想一遍输出去向 | 不止。**任何**经过 Rich 打印 emoji 的命令，只要 stdout 被重定向 / 接管道 / 被父进程捕获，都会在同一条路径上崩——CI 里 `... > build.log` 这种写法很常见 |

关键转折是第 3 步：差异不在 Rich 的渲染模式，而在 **Python 给 stdout 选哪套编码**。
这也解释了它为什么能潜伏很久——没人会一边接管道一边看诊断输出，而真接了管道的人
只会看到"程序崩了"，不会去怀疑是表格标题里的一个图标。

## 3. 根因分析

Python 在 Windows 上给标准流选编码有两条路：

- **直连控制台** → `io.WindowsConsoleIO`，固定用 UTF-8，emoji 写得出去；
- **被重定向 / 管道 / 被 spawn 捕获** → 普通文件对象，编码取区域设置（简体中文 = cp936 / GBK），
  且 `errors` 默认是 `strict`。

于是 `📦`（U+1F4E6）在第二条路上直接抛 `UnicodeEncodeError`。异常发生在 Rich 的
`Console.print()` 内部，从 `run_doctor()` 一路抛穿 `main()`，用户拿到的不是"诊断结果里
少了几个图标"，而是**一条完整的命令都没跑成**。

补充一个反直觉点：崩溃时的 traceback 里出现了 `legacy_windows_render`，很容易让人
（包括本次排查的第一阶段）误判成"conhost 老渲染器的问题"。实测 `Console().legacy_windows`
是 `False`——Rich 的 `_write_buffer` 在**缓冲区写失败**时走的是同一条 backtrace 路径，
看栈不能只看最上面那两帧。

## 4. 修复方案

新增 `agent/core/stdio.py::make_stdio_crash_proof()`：把 `sys.stdout` / `sys.stderr` 的
编码错误策略从 `strict` 放宽为 `replace`，编不出来的字符降级成 `?`，其余字节不变。

四处取舍都是刻意的：

1. **只改 errors，不换编码**。换成 utf-8 的话，下游按 cp936 解码的 PowerShell 5.1 会把中文
   读成乱码——问题从"崩"变成"糊"，更难排查。
2. **用 `replace` 而不是 `backslashreplace`**。后者把 emoji 变成 `\U0001f4e6`，会把表格列宽
   撑爆；`?` 只占一格，图个可读。
3. **修在入口，不修在字符串**。去把每个 emoji 从每处文案里删掉是 N 处改动，而且下次谁
   再往标题里放一个 🚀 又会复发；入口一次兜底能覆盖所有未来字符。
4. **对"帮不上忙"的流静默跳过**。`pythonw` 下 `sys.stdout is None`、`StringIO` 没有
   `reconfigure`、流已关闭时 `reconfigure` 会抛 `ValueError`——这三种情况全部吞掉，
   因为这个函数存在的意义就是不让人栽在打印上，它自己绝不能再抛一次。

接入两个入口（`run_doctor()` 是 `jarvis --doctor` 与 `python -m agent.doctor` 的公共落点，
`main()` 覆盖其余所有 CLI 路径），幂等，重复调用无副作用。

## 5. 验证结果

- **修复前**：`jarvis --doctor > out-a.txt` → exit 1 + `UnicodeEncodeError`，日志里无表格。
- **修复后**：同一条命令 → exit 1（3 项依赖缺失导致，属预期），日志末行
  `? 总结: 26/29 项通过，3 项缺失或异常`——emoji 降级成 `?`，中文与表格完整。
- **新增 8 例回归测** `tests/core/test_stdio_guard.py`：反证"没兜底必抛"、降级后中文不受伤、
  无 `reconfigure` / `None` / `reconfigure` 抛错三种流全部静默跳过、幂等，以及两条入口级
  用例（`run_doctor` 在 gbk 重定向流下跑通、`main()` 确实调了兜底）。
- **反向变异验牙**：把 `run_doctor()` 里那次调用改成 `_ = make_stdio_crash_proof`（只导入不调用），
  `test_run_doctor_survives_redirected_gbk_stdout` 立刻红，报的正是
  `UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f4e6'`；其余 7 例仍绿。
- 全量 `pytest tests/`：4858 passed / 2 skipped（基线 4850 + 新增 8）。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/core/stdio.py` | 新增：`make_stdio_crash_proof()`，只放宽 errors 不动编码 |
| `agent/main.py` | `main()` 第一行接入兜底（排在 `parse_args` 之前） |
| `agent/doctor.py` | `run_doctor()` 第一行接入兜底，覆盖 `python -m agent.doctor` |
| `tests/core/test_stdio_guard.py` | 新增 8 例：流降级契约 + 两个入口的接线 |
| `CHANGELOG.md` | 2.5.3 条目 |

## 7. 经验总结

- **"直连能用、重定向就崩"是编码问题的指纹**。Windows 上控制台与管道是两套编码路径，
  只在其中一条上试过就等于没测。凡是"用户把输出导到文件"的场景，都要单独过一遍。
- **看 traceback 别只看最上面两帧**。这次栈顶是 `rich` 的 legacy 渲染器，真凶却在
  `sys.stdout.encoding`——第一阶段的假设把排查带偏了一轮，直到第 3 步去测编码才回来。
- **修在入口比修在调用点稳**。同一个缺陷在 N 处文案里都能触发，改 N 处只会留 N-1 个坑。
- **字符集要按最窄的那条通道设计**。GBK 是中文的超集，所以中文文案从来不炸；真正的地雷
  是 emoji 与罕见符号。往终端输出里加装饰性字符前，先想一遍它能不能过 cp936。
- **发版后的安装态验收不是走过场**。这个缺陷在源码树里跑 `jarvis --doctor` 永远正常，
  只有从 PyPI 装出来的包 + 重定向才现形——正是"以普通用户视角验证发布产物"这一步抓到的。
