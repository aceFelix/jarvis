# 后台命令（打开浏览器）仍要卡满超时：MSYS2 bash 会等后台 job

**日期**：2026-10-28
**影响**：让 jarvis「打开 B 站」后，网页早就加载完了，jarvis 还要等到命令超时（120 秒）才有反应
**作者**：aceFelix

---

## 一、现象

`/voice` 下说「帮我打开B站」，模型这次的思考过程很完整，最后执行：

```bash
"/d/Program Files/Mozilla Firefox/firefox.exe" "https://www.bilibili.com" &
```

**已经加了 `&`**（模型自己说「加 & 让它在后台运行不阻塞」），但 jarvis 照样卡住：

- 火狐起来了、B 站首页也加载出来了
- jarvis 迟迟没有回复
- Ctrl+C / ESC 都停不下来

上一轮修复（`bash-timeout-retry-and-abort-gap-fix.md`）解决了「超时被当成未知错误重试」和「abort 无人响应」，但**命令本身仍然要等满 120 秒**——因为一开始就判断错了等待对象。

## 二、排查：到底在等什么

用真实 Git Bash 逐条计时（`Measure-Command`）：

| 命令写法 | 耗时 | 结论 |
|---|---|---|
| `bash -c "echo hi"` | 0.14s | 基准 |
| `bash -c "sleep 3 &"` | **3.18s** | **加了 `&` 也没用** |
| `bash -c "( sleep 3 & )"` | 3.17s | 子 shell 也不管用 |
| `bash -c "sleep 3 & disown"` | 3.15s | `disown` 也不管用 |
| `bash -c "sleep 3 2>/dev/null &"` | 3.13s | 只断 stderr 无效 |
| `bash -c "sleep 3 >/dev/null &"` | **0.21s** | **断 stdout 就对了** |
| `bash -c "sleep 3 >/dev/null 2>&1 &"` | **0.18s** | 同上 |
| `bash -c "nohup sleep 3 >/dev/null 2>&1 &"` | 0.19s | 也可以，但会留 nohup.out |

**结论：MSYS2/Git Bash 的 `bash -c 'cmd &'` 根本不会立刻返回。** 只要后台 job 还接着
bash 的 stdout，bash 就会等它结束才退出。`disown`、子 shell 都不改变这一点，只有把
stdout 断开（`> /dev/null`）才行。

所以「网页加载出来了吗」跟这件事毫无关系——卡的不是页面渲染，是 bash 进程本身没退出。

## 三、连带发现的另外两个坑

### 3.1 `communicate()` 等的是管道 EOF，不是进程退出

原先用 `asyncio.wait_for(proc.communicate(), timeout)`。`communicate()` 等价于
「等进程结束 **且** 两个管道都 EOF」。

后台进程（firefox）继承了 stdout/stderr 的**写端**，只要浏览器开着，EOF 就永不到来。
即便 shell 早就退出了，`communicate()` 也会一直挂着。

### 3.2 反过来，只等 `proc.wait()` 会死锁

那就改成只等进程退出？也不行。管道缓冲只有 64KB，输出超过它时 shell 会阻塞在
`write()` 上、永远不退出——没人读管道，`proc.wait()` 就等到超时。

**必须边等边读。**

### 3.3 kill 之后 `wait()` 也未必返回

实测 `exec sleep 30` 被杀后，Windows 上 `proc.wait()` 迟迟不完成（孙进程还握着管道）。
原先写的 `await asyncio.gather(wait_task)` 会在这里无限挂起——又变成「Ctrl+C 没反应」。

### 3.4 沙箱的 Job Object 把后台进程一起回收了（用户实机日志暴露）

上面三项修完后，用户在实机上遇到了第三个问题：**窗口开了，又自己关掉了**。

他当时的日志里每条命令都挂着 `🛡️沙箱 | 风险=高`。沙箱在 Windows 上就是 Job Object，
且带 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`——`_run_sandboxed_windows` 在 finally 里
`CloseHandle(job_handle)`，组内**所有**进程随即被 TerminateProcess。

而 `risk.needs_sandbox` 的定义是「中及以上都进」，只有只读命令例外。所以无论怎么写，
firefox 一定在 Job 里，命令一结束就被回收。模型的诊断「进程在命令结束后被回收」是
对的（它说的另一半「沙箱阻止 GUI 创建」是错的，PowerShell 当时已经查到了进程）。

顺带查出一条因果链：**风险=高 这个标签是我们自己弄出来的**。`_HIGH_PATTERNS` 里的
`r">\s*\S+"` 本意是拦「用 `>` 覆盖文件」，但它会把 `> /dev/null` 和 `2>&1`
一并算进去——而 3.1 节刚为后台命令补上了这两个重定向。实测：

| 命令 | 风险（修复前） |
|---|---|
| `firefox.exe URL` | 中 |
| `firefox.exe URL > /dev/null 2>&1 &` | **高** |
| `ls -la` | 低 |

模型最终是靠 `schtasks` 让火狐活下来的——那**是沙箱逃逸**（Task Scheduler 服务创建
进程，不在我们的 Job 里），不值得当成解法。

## 四、修法

### 4.1 后台命令自动补重定向（`_detach_background`）

```python
_BACKGROUND_TAIL = re.compile(r"(?<!&)&\s*$")   # 排除 && 连接符

def _detach_background(command: str) -> tuple[str, bool]:
    ...
    return f"{body} > /dev/null 2>&1 &", True
```

只在「以 `&` 结尾」时改写；模型自己已经丢了输出的不重复加。
改写后回执里会注明「已在后台启动：命令已脱离本工具，其后续输出不再捕获」。

### 4.2 边等边读（`_StreamCollector`）

启动两个后台任务持续把 stdout/stderr 读走（防 64KB 死锁），
`proc.wait()` 一返回就在 0.05 秒宽限内收尾取数据（不等待 EOF，防后台写端拖住）。

### 4.3 kill 后只给短宽限回收

```python
try:
    await asyncio.wait_for(wait_task, timeout=_KILL_REAP_TIMEOUT_S)  # 2.0s
except (asyncio.TimeoutError, asyncio.CancelledError):
    wait_task.cancel()
```

最后再显式 `transport.close()`，避免 unclosed transport 警告。

### 4.4 让模型一开始就写对

Bash 工具 description 增加一句：

> 启动 GUI 程序或常驻服务（浏览器、编辑器、dev server 等）必须写成后台形式并丢弃输出，
> 如 `firefox.exe https://x.com > /dev/null 2>&1 &`；写成前台会一直挂到程序退出为止。

超时回执（`_TIMEOUT_HINT`）也同步改成这个写法。

### 4.5 后台命令跳过沙箱

`BashTool.call` 里把「是否后台命令」作为沙箱路由的判据：

```python
_run_cmd, is_background = _detach_background(command)
if sandbox_enabled and risk.needs_sandbox and not is_background:
    return await self._call_sandboxed(...)
return await self._call_normal(...)
```

语义上自洽：**后台命令的意图就是「启动后不管」，进一个「命令结束就清场」的 Job
等于必然被回收。** 风险仍由权限层兜住（HIGH/CRITICAL 照常 ASK）。

这里有个容易踩的细节：`_detach_background` 的第二项必须是「**是否后台命令**」，
而不是「**有没有被改写**」。否则模型自己写全了 `> /dev/null 2>&1 &` 时不会被改写，
于是被当成前台命令而进 Job——正好跳过最需要豁免的那一类。

### 4.6 `> /dev/null` 不再算高风险

把「丢弃输出 / 复制描述符」从 `_HIGH_PATTERNS` 里拆出来单独判：

```python
_SAFE_REDIRECT_RE = re.compile(r"""
    \d*>&\d+                                      # 2>&1
  | &>\s*(?:/dev/null|/dev/stdout|/dev/stderr|NUL|nul)\b
  | \d?\s*>\s*(?:/dev/null|/dev/stdout|/dev/stderr|NUL|nul)\b
""", re.IGNORECASE | re.VERBOSE)

def _has_file_redirect(command: str) -> bool:
    return bool(_FILE_REDIRECT_RE.search(_SAFE_REDIRECT_RE.sub(" ", command)))
```

剔除后仍有 `>` 的才算写文件。这样 `firefox.exe URL > /dev/null 2>&1 &` 回到「中」
风险——既不再误触发文件快照，也能走已有的 `auto_allow_medium` 自动放行，
不用每次都弹确认。

## 五、验证

真实子进程实测（探针，跑完即删）：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| `sleep 30 &` | 30.20s（超时） | **0.29s** |
| `seq 1 100000`（>64KB 输出） | — | 0.15s，输出完整 588956 字符 |
| `echo hello-jarvis` | 0.12s | 0.10s |

新增测试 `tests/tools/test_bash_background.py`（9 例）：
- 5 例纯函数改写规则（含 `&&` 不误判、管道重定向落在最后一段）
- 3 例真实子进程：后台命令立即返回、大输出不死锁、执行中 abort 能打断
- 1 例沙箱路由：后台命令走普通路径、`rm -rf` 仍进沙箱、只读走普通路径

另外在 `tests/test_sandbox_more.py` 新增 `test_discard_redirect_is_not_high`。

**沙箱开启下的端到端验证**（探针）∶`sleep 20 &` 执行后 `tasklist` 确实查到两个
`sleep.exe` 存活，说明后台进程不再被 Job 回收。

全量 `pytest tests -q`：**3662 passed**，无回归。

## 六、遗留

- **模型不写 `&` 时仍会超时**（这就是前台语义，没法替它猜）。
  兜底是超时回执里的提示，模型下一轮会改成后台写法；而且这类命令仍会进沙箱。
- **沙箱逃逸面依旧存在**：`schtasks`、WMI、COM 激活、注册表 Run 键都能绕出 Job Object。
  沙箱的威胁模型是「命令跑飞 / 吃爆资源」，不能当安全边界用。
- **`sandbox.max_processes=10` 对 MSYS2 偏紧**，重命令会报 `fork: CreateProcessW failed`。
  目前只是原样回传给模型，尚未给出改造建议。
- 沙箱路径（`_call_sandboxed` → `SandboxExecutor.run`）内部仍是 `communicate()`，
  未接本轮的 `_StreamCollector`/abort 竞速。后台命令已绕开它，前景命令暂时不受影响，
  但同样存在「大输出 + 后台写端」的隐患。

## 七、教训

**"加了 `&` 就后台了"是个跨平台幻觉。** `&` 是 shell 的 job control 语义，而 MSYS2 的
bash 对后台 job 的处置和 Linux 不一样；能真正脱离的不是 `&`，是**切断 stdout**。

**排查这类「为什么不返回」，先测最底层那个进程的耗时，不要先怀疑自己的代码。**
`Measure-Command { bash -c "sleep 3 &" }` 这一个数字，直接就把方向从「asyncio 等待逻辑」
拨到了「bash 本身在等」。此前两轮修复都在错误的层面上做文章。

**修完一处要回头看一眼它的副作用。** 为了让命令不卡而补的 `> /dev/null 2>&1`，
顺手把风险从「中」抬到了「高」+ 触发文件快照；而风险等级又决定进不进沙箱，
沙箱的 `KILL_ON_JOB_CLOSE` 于是把刚启动的 GUI 杀掉了。**性能修复、风险评估、
资源隔离三层是同一条链上的**，只验一层等于没验。

**模型的自诊断不能直接当修复依据。** 它说「沙箱阻止了 GUI 进程创建」——错，
PowerShell 当时已经查到了进程；说「命令结束被回收」——对。两句在日志里一样可信。
