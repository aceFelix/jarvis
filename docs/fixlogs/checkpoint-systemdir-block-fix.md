# 终端首轮卡死：同步检查点在系统目录上无限阻塞

> 状态：**已实施**（2026-11-20，本地 `jarvis` 仓库；PyPI 版需重新发版才生效）
> 发现版本：PyPI `jarvis-agent 2.6.0`（`pip install "jarvis-agent[all]"`）
> 对照源码：本地 `jarvis` 仓库 2.5.4（缺陷结构同源）

## 1. 问题现象

- 环境：Windows 10 19045.6466，系统 Python 3.13（`D:\Program\Python\Python313`），
  PyPI 安装 2.6.0 + 全 extras。
- 操作：`jarvis --init` 走完三问 → `jarvis` 启动 → 输入「你好jarvis」。
- 表现：**没有任何回复、没有报错、没有超时提示**，终端停在提示符后无限等待。
- 关键上下文：启动面板显示 `workdir C:\Windows\System32`（cmd 默认目录被当成工作目录）。

复现步骤：

```cmd
cd /d C:\Windows\System32
jarvis
> 你好jarvis
```

## 2. 排查过程

### 阶段一：排除模型 / Key / 网络（均正常）

| 检查 | 结果 |
|---|---|
| `~/.jarvis/models.toml` | `last_model = "deepseek-flash"`，`base_url = https://api.deepseek.com/v1` |
| `GET https://api.deepseek.com/v1/models` | 返回 `deepseek-flash`、`deepseek-v4-pro` → 模型名合法 |
| `POST /v1/chat/completions`（同 key、同模型） | 正常响应，无 401/400 → Key 与网络均通 |

结论：**不是模型名非法，不是 Key 无效，不是网络不通**。

### 阶段二：日志只写了"请求发出"，没有后续

`~/.jarvis/jarvis.log` 全文仅一行：

```
2026-11-19 22:51:09,423 [DEBUG] jarvis: LLM 请求: model=deepseek-flash
base_url='https://api.deepseek.com/v1' msgs=2 tools=0 system_chars=0
thinking=False effort=None
```

即：请求已构造，但**主循环随后没有推进**。`~/.jarvis/logs/diag.log` 不存在。

### 阶段三：检查点目录给出了决定性线索

```
~/.jarvis/checkpoints/
└── System32-be02d7437908    创建于 2026-11-19 22:51:39
```

目录名即 `_workdir_key()` 的产物（`basename-sha1[:12]`），指向 `C:\Windows\System32`。

### 阶段四：进程实况坐实阻塞点

| 进程 | PID | 启动时间 | 状态 |
|---|---|---|---|
| `git.exe` | 112 | 22:51:39 | **累计 CPU 125 秒，仍在运行** |
| `python.exe`（jarvis 主进程） | 18484 | 22:51:22 | CPU 仅 4.09s，内存 124MB，在等子进程 |
| `jarvis.exe`（console 启动器） | 22600 | 22:51:22 | 3.6MB，CPU 0 |

结论：主进程没有死、没有崩，它在**同步等待 `git` 遍历并快照整个 `System32`**。

### 阶段五：代码定位

| 位置 | 事实 |
|---|---|
| `agent/main.py:521-526` | REPL 在**发送消息前**同步调用 `ckpt_mgr.create(reason=stripped)` |
| `agent/core/checkpoint.py:273-315` | `create()` → `self._git("add", "-A")`，对整棵工作树做 hash |
| `agent/core/checkpoint.py:164-183` | `_git()` 用 `subprocess.run(..., timeout=self._timeout)`，默认 10 秒 |
| `agent/config/settings.py:445-449` | `checkpoint_timeout_seconds: int = 10`（配置项确实存在） |
| `agent/config/settings_domains.py:247-251` | `[checkpoint] timeout_seconds` 已接线到 `checkpoint_timeout_seconds` |

## 3. 根因分析

三层原因叠加，缺一都不会卡死：

1. **关键路径同步**：检查点建在发送路径上，主循环必须等它完成才发请求。
   `docs/fixlogs/desktop-checkpoint-blocks-10s.md` 已把**桌面引擎**改成后台线程，
   但**终端 REPL 未同步整改**，两宿主行为分叉。
2. **Windows 上超时形同虚设**：`subprocess.run(timeout=10)` 超时只终止直接子进程
   `git.exe`；git 派生的孙进程继承了 stdout/stderr 管道句柄，`communicate()` 等不到
   `EOF` 会继续阻塞。实测 `git` 已消耗 125 秒 CPU 仍未被回收，证明 10 秒超时**从未生效**。
3. **缺少 workdir 护栏**：`_DEFAULT_EXCLUDES` 只排除了 `.venv`/`node_modules`/`dist`
   等开发产物，对**系统目录**没有任何保护；也没有文件数/体积预检。
   而首启默认 `workdir = cwd`，用户从 `C:\Windows\System32` 启动就直接踩中。

放大因素：`System32` 数万文件、GB 级、含受保护与被占用文件，`git add -A` 的耗时
不是线性而是"看不到尽头"；同一目录后续 `/rewind`、`/undo --task` 还会试图
**回滚系统文件**，风险远高于卡顿。

## 4. 修复方案（待实施）

按优先级，四处改动：

### A. REPL 检查点后台化（`agent/main.py`）

轮前不再同步等待：

- 提交后台线程/任务执行 `ckpt_mgr.create(...)`；
- 主循环立即发送 LLM 请求；
- 轮末（或下一轮开始时）以极短超时取结果：拿到则把 cid 挂到本轮用户消息
  `extra.checkpoint_id`；拿不到就写空串并在 `/rewind` 时明确回执
  「本轮没有文件检查点，只能回退对话」。
- 不得把上一轮的 cid 留在 `ctx.extra`（`effect_policy.turn_checkpoint_id`
  已警告这会"多退一轮"）。

### B. 真超时 + 进程树回收（`agent/core/checkpoint.py`）

- `_git()` 由 `subprocess.run` 改为 `Popen` + `communicate(timeout)`；
- 超时后 Windows 用 `taskkill /PID <pid> /T /F` 终止整棵进程树，POSIX 用进程组 kill；
- 显式关闭/丢弃管道，避免孙进程持句柄导致永久阻塞；
- 超时后对**当前 workdir** 打上"本会话禁用文件检查点"的标记，不再每轮重试
  （否则每轮都要再耗一次超时）。

### C. workdir 护栏

- 新增判定：`C:\Windows`（含 `System32`/`SysWOW64`）、`C:\Program Files*`、
  `C:\ProgramData`、盘根（`C:\`、`D:\`）→ 视为**不安全工作目录**；
- 命中即 `available()` 返回 False，并给一次性提示（stderr + 首屏）：
  「当前目录不适合打文件检查点，已禁用文件回滚（对话回退仍可用）；建议
  `jarvis --workdir <普通目录>`」；
- 另加规模预检（建议阈值：文件数 > 20000 或总体积 > 2GB 时跳过），
  预检自身也要有超时与降级。

### D. 首启提示（`agent/main.py`）

`workdir` 默认取 cwd 时，若命中 C 的黑名单，在启动面板/首屏打一条警告，
避免用户以为"安装坏了"。

### 需要同步的配置与文档

- 若新增阈值字段（如 `checkpoint_max_files`），须按既有纪律五处同步：
  `config_specs.py`、`settings_domains.py`、两份 `*.example.toml`、架构文档；
- `docs/architecture/15-消息回溯与检查点.md` 补一条"不可用降级"的判定口径；
- 本文件状态随实施进展更新（实施完成并实机验证后再改为"已修复"）。

## 5. 验证结果

**已实施并跑通单元回归（2026-11-20）**：

- `tests/core/test_checkpoint.py` 新增 `TestWorkdirGuard`（4 例）与
  `TestBackgroundRunner`（2 例）：系统目录/盘根判不可用、普通目录可用、
  不可用时不启动后台线程、可用时轮末能收到 cid；
- 受影响 8 个测试文件回归 → **86 passed**。

**实现与本文原方案的差异（已同步）**：

1. 「文件数/体积预检」**未做**：预检本身要遍历目录，在系统目录上同样会卡，
   等于把要防的问题引回来；改为「系统目录黑名单 + 硬超时 + 超时后本会话停用」
   三层兜底。
2. 轮前检查点后台化后，**本轮工具账本拿不到 cid**（副作用退化为不可撤销），
   轮末最多等 2 秒收取；拿不到时 `/rewind` 如实回「该消息没有关联检查点」。
3. 首屏会打一条 `ui.warn`，写明不可用原因（系统目录/盘根/缺 git/已关闭/超时）。

**仍待实机验证**（需真实终端跑）：

1. `cd /d C:\Windows\System32 && jarvis` → 输入任意消息，**2 秒内**开始流式回复，
   首屏出现"已禁用文件检查点"提示，`git.exe` 不驻留；
2. 普通目录（如 `E:\jarvis-test`）→ 首轮正常回复，`~/.jarvis/checkpoints/<目录>/`
   正常生成，`/rewind` 能连带回滚文件；
3. 人为制造超时（在超大目录下把 `[checkpoint].timeout_seconds` 调到 1）→
   主循环不卡，回执为"本轮无文件检查点"；
4. 回归：桌面引擎（`serve/engine`）的检查点仍为后台线程，行为不变。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/main.py` | 轮前检查点改后台执行；系统目录警告；cid 降级回执 |
| `agent/core/checkpoint.py` | `_git()` 改 Popen+进程树 kill；workdir 黑名单；超时后本会话禁用；新增 `BackgroundCheckpointRunner` |
| `agent/config/settings.py` | 如新增阈值字段则在 `[checkpoint]` 段追加 |
| `agent/config/settings_domains.py` | 新字段的 TOML 映射（否则 `save_value` 找不到） |
| `agent/config/config_specs.py` | 新字段可代改性声明（若开放给模型/面板） |
| `docs/architecture/15-消息回溯与检查点.md` | 补"不可用降级"判定 |
| `docs/fixlogs/checkpoint-systemdir-block-fix.md` | 本复盘 |

## 7. 经验总结

- **同步子进程 + 管道 + Windows = 超时不可信**。`subprocess.run(timeout=...)`
  在孙进程持有管道时不会返回，必须 `Popen` + 进程树 kill + 显式关管道。
  凡是"必须执行完"的外部命令，都要有可观测的失败路径。
- **每轮必做的前置动作不能放在关键路径上**。检查点属于"锦上添花"的能力
  （决定能否回滚文件），绝不该决定"能不能对话"。
- **默认值也是产品决策**：`workdir = cwd` 让用户在系统目录启动就必然踩坑，
  应显式给出警告与替代命令。
- **同类缺陷要一次改完两条宿主**：桌面修了、终端没修，同一个坑会以更严重的形式
  复现（10 秒 → 无限期）。修共享能力的阻塞问题时，必须 grep 所有调用点。
- **进程三件套（CPU/启动时间/父进程）是定位"到底卡在哪"的最快证据**，
  比反复读日志更快。
