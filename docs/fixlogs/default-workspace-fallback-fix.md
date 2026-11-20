# 默认工作目录：从「进程 cwd」改为「安全 cwd → 默认工作区」

> 状态：**已实施**（2026-11-20，本地 `jarvis` 仓库；PyPI 版需重新发版）
> 用户原话：「用户安装 jarvis 之后，他没有默认的 jarvis 工作目录，这是个缺陷，
> 所以说还得指定用户新建一个 jarvis 的工作目录才行。」
> 关联：`checkpoint-systemdir-block-fix.md`（检查点护栏）、
> `workdir-unwritable-session-memory-crash-fix`（会话记忆回退，同批）

## 1. 问题现象

同一类“目录问题”在三个场景下以三种面目出现：

| 启动位置 | 表现 |
|---|---|
| `C:\Program Files` | 启动即 traceback：`PermissionError: [WinError 5] 'C:\Program Files\.jarvis'` |
| `C:\Windows\System32` | 输入消息后无限无响应（检查点 `git add -A` 遍历数万文件） |
| `C:\Users\<user>`（主目录根） | 能跑，但把桌面/下载/文档整体当成一个项目：检查点巨大、`/rewind` 影响面失控 |

外加一个静默缺陷：`settings.toml` 里写 `workdir = ...` **不生效**（见 §3.4）。

## 2. 排查过程

1. `load_settings` 的初始目录：`cwd = Path(workdir or os.getcwd()).resolve()`
   → **默认值就是进程 cwd**，没有“默认工作区”概念。
2. `settings.py` 只把 `str(cwd)` 塞进 `Settings(workdir=...)`，之后 TOML/环境变量
   可以覆盖，但没有对“显式值”和“缺省值”做区分。
3. 依赖 workdir 的两个副作用都在关键路径上：
   - `_session_memory_path()` 无条件 `makedirs(workdir/.jarvis)`；
   - 每轮发送前 `CheckpointManager.create()` 对 workdir 做 `git add -A`。
4. 检查 `_apply_toml` 的顶层白名单：**没有 `"workdir"`**——模板里的
   `workdir = "./workspace"` 是死键，用户手写也无效，而 `catalog.py` 与
   `settings.example.toml` 都在告诉用户“可以改”。这属于仓库反复出现的
   “配置写了不生效”陷阱。

## 3. 根因分析

### 3.1 默认值选错了

把「进程碰巧所在的目录」当成了「用户要处理的项目目录」。这两件事多数时候
相同（开发者 `cd project && jarvis`），但一旦不同（从安装目录、系统目录、
开始菜单快捷方式启动），就会把系统目录当成项目。

### 3.2 缺少“不适宜目录”的判定

系统目录、盘根、主目录根都有明确的不适宜理由（无写权限 / 体量过大 /
影响面过大），但代码里没有任何判定，全靠用户自觉。

### 3.3 关键路径无降级

目录问题本该只影响“文件回退/项目记忆”，却因为同步调用被放大成
“启动崩溃 / 对话卡死”（详见两份关联复盘）。

### 3.4 配置项假可见

`workdir` 出现在 catalog 与两份模板里，却不在 `_apply_toml` 白名单内：
**看得见、写得下、不生效**。这会让用户在被默认值坑了之后，连“手改配置”
这条退路都没有。

## 4. 修复方案

### 4.1 三档解析（`agent/config/workspace.py`）

```
1. 显式值：--workdir / 配置 workdir / 桌面最近项目   → 原样采用
2. cwd 是普通项目目录（非系统目录/盘根/主目录根）      → 继续用 cwd
3. 其余                                               → ~/.jarvis/workspace（自动创建）
```

- `unsafe_reason()` 只做字符串判定（不遍历、不写探测），避免预检本身成为卡点；
- 回退时记一条提示语，由宿主首屏打印一次（`consume_workdir_note()`）；
- 默认工作区与 `~/.jarvis` 同根，一定可写。

### 4.2 接线

- `settings.load_settings()`：初始目录改走 `resolve_workdir(workdir)`；
- `main.repl()`：`ui.info("[workdir] …（可用 --workdir 指定项目目录）")`，只打一次；
- `onboarding` 文案从「工作目录用当前目录」改为「工作目录：…」（不再宣称用 cwd）。

### 4.3 修掉配置死键

- `_apply_toml` 白名单加入 `"workdir"`；
- 合并完成后统一 `expanduser().resolve()`，保证运行时拿到绝对路径；
- 两份 `settings.example.toml` 同步：删除实际的 `workdir = "./workspace"`
  （它会把所有用户的默认值改成 `<cwd>/workspace`），改为注释说明优先级。

### 4.4 明确不采用的方案

**强制固定工作区**（所有项目都必须放进一个 jarvis 目录）：会让 D 盘/E 盘/网络盘
的项目“看起来不被管理”，且违背“项目在哪就在哪干活”的直觉。跨盘项目的隔离靠
workdir 指纹（`~/.jarvis/checkpoints/<目录哈希>`、`memory/projects/<名-哈希>`），
不靠物理集中。

## 5. 验证结果

**单测**：`tests/config` → **544 passed**（新增 8 例：显式优先/安全 cwd/主目录根/
盘根/系统目录判定/提示只消费一次/配置 workdir 生效）。

**实机（`C:\Windows\System32`）**：

```
WORKDIR= C:\Users\<user>\.jarvis\workspace
NOTE= 当前目录不适合当工作区（系统目录 C:\Windows\System32），
      已改用 C:\Users\<user>\.jarvis\workspace
```

**仍待实机验证**：

1. `cd C:\Program Files && jarvis` → 能进 REPL、能对话，首屏有 `[workdir]` 提示；
2. `cd E:\proj && jarvis` → workdir 仍是 `E:\proj`（不被打扰）；
3. `jarvis --workdir D:\other` → 生效且无回退提示；
4. `~/.jarvis/settings.toml` 写 `workdir = "E:\\proj"` → 生效（此前无效）。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/config/workspace.py` | 新增：三档解析、不适宜目录判定、回退提示 |
| `agent/config/settings.py` | 初始目录走 `resolve_workdir`；白名单加 `workdir`；合并后 resolve |
| `agent/main.py` | 首屏打印一次工作目录回退提示 |
| `agent/config/onboarding.py` | 文案改为「工作目录：…」 |
| `agent/configs/settings.example.toml` / `configs/settings.example.toml` | 删死键，改注释说明优先级（两份同改） |
| `tests/config/test_workspace_resolution.py` | 新增 8 例回归 |
| `tests/config/test_onboarding.py` | 同步文案断言 |
| `docs/fixlogs/default-workspace-fallback-fix.md` | 本复盘 |

## 7. 经验总结

- **默认值就是产品决策**。`workdir = cwd` 在开发者机器上无感，在普通用户机器上
  就是把“安装目录”当项目。默认值必须按“最差环境”设计。
- **不要强迫用户搬家**。默认工作区只该是“没有项目时的落脚点”；项目识别靠
  workdir 指纹与最近项目列表，不靠把所有项目聚到一个目录。
- **配置项必须真能改**。catalog、模板、命令帮助三处都在教用户改 `workdir`，
  而写入路径漏了白名单——这类“假配置”比不提供配置更伤人，因为它消耗了
  用户唯一的退路。
- **预检不能变成新卡点**：目录安全判定只做字符串比较，绝不遍历目录、不做
  大范围写探测；真正的耗时操作一律放在带超时/后台化的路径里。
