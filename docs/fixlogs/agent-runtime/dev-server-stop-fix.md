# 开发服务器「只进不出」修复记录（语音模式下无法关闭项目）

## 问题现象

在一次语音交互中，用户让 Jarvis 用浏览器打开了前端项目（Vite，开发服务器跑在 5174 端口），随后进入待机。

**场景复现**：

1. 「OK，非常不错啊，退下吧。」→ Jarvis 礼貌告别并输出 `<standby/>`，进入待机；
2. 「贾维斯还在吗？」→ 唤醒，回到对话模式；
3. 「把项目关了。」→ Jarvis 开始**长时间思考**，思考过程里反复推演如何用 Bash 的 `kill` / `taskkill` 去关进程，迟迟不结束，最终被用户打断：

```
╭─ 💭 思考过程 ────────────────╮
│ ... 反复推演如何用 Bash kill 掉 5174 端口进程 ... │
╰──────────────────────────────╯
⚠️  🔇 检测到打断，已停止回复（继续聆听）
```

**影响范围**：

- 开发服务器无法通过对话关闭，用户只能手动杀进程；
- 语音模式下表现为「听不懂话、一直思考」，体验严重受损；
- 同一问题在文本模式下同样存在（只是文本模式可以通过键盘确认绕过，不易被察觉）。

**用户疑问**：是不是待机唤醒后 Jarvis 记不得之前的对话了？还是提示词的问题？

---

## 排查过程

### 第一阶段：证伪「待机唤醒丢记忆」的假设

**用户假设**：待机后再唤醒，Jarvis 不记得之前启动过项目，所以不知道要关什么。

**取证**：阅读 [voice_loop.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/voice/voice_loop.py#L580-L644) 的待机/唤醒逻辑。

```python
in_dialog = True  # True=对话阶段, False=待机阶段
...
if in_dialog:
    ...
    in_dialog = False      # 进入待机
...
    in_dialog = True       # 唤醒
```

**结论**：待机只是切换 `in_dialog` 布尔标志，**全程复用同一个 `ctx.messages`**，历史消息一条都没丢。**用户假设被证伪**——不是记忆问题。

### 第二阶段：确认根因——DevServer「只进不出」

**观察**：DevServer 工具只有 `start` 一个动作，**没有任何关闭入口**；system prompt 的「开发服务器」段也只教「怎么启动」，只字未提「怎么关闭」。

**推断**：模型被问到「把项目关了」时，没有任何一等公民工具可用，只能自己想办法——于是跌进「Bash `kill` / `taskkill` / `netstat` 找 PID」的长链推理里，越推越长。

### 第三阶段：定位放大器——语音模式的阻塞式权限确认

**取证**：权限编排层在 `check_permissions()` 返回 `ASK` 时会调用 `ctx.ui.ask_user()`；REPL 场景下该实现是阻塞式的 `input()` / `Prompt.ask`。

**结论**：Bash 的 `kill` / `taskkill` 属于写操作，会被判 `ASK` → 语音模式下**没人能按键盘** → 卡死。这就是「一直思考、迟迟不结束」的直接原因。

**关键转折点**：把「模型无工具可用」与「语音下确认会阻塞」两条线索合到一起，根因清晰了——

> 模型因缺少关闭工具被迫走 Bash kill 长链，而 kill 被判 ASK，在语音模式下阻塞成死循环。

### 第四阶段：修复方向决策

通过 AskUserQuestion 与用户确认，用户选择**方案 A：给 DevServer 加关闭能力**（治本，让模型能直接听懂「把项目关了」）。

---

## 根因分析

| 层级 | 问题 | 说明 |
|---|---|---|
| 根因 | **DevServer 只进不出** | 工具只有 `start`，无 `stop`/`close`/`kill`；prompt 也只教启动 |
| 放大器 | **语音模式权限确认阻塞** | `ASK` 最终落到阻塞式 `input()`，语音下无人按键，卡死 |
| 表象 | 长时间思考 / 听不懂话 | 模型被迫自行拼 Bash kill 链，且该链被判 ASK 阻塞 |

**为什么之前的实现会出错**：DevServer 诞生时只考虑了「把项目跑起来」这一核心诉求，进程生命周期管理被有意搁置（见旧文档「设计取舍：不管理进程生命周期」）。但「启动」与「关闭」是对称诉求，缺一半就会把成本转嫁给模型去硬凑。

**涉及模块**：DevServer 工具、system prompt、工具权限编排、语音模式 UI。

---

## 修复方案

核心思路：**补齐「关闭」这一等公民能力，并在「关闭 Jarvis 自己启动的服务器」时直接放行权限**，从根上消灭「模型被迫用 Bash kill」的路径。

### 1. 抽出进程管理底层能力（遵守单文件 ≤900 行规则）

新建 [dev_server_process.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/extensions/dev_server_process.py)，把与「登记表 + 进程/端口」相关的纯函数集中管理：

- 登记表：`registry_path()` / `load_registry()` / `save_registry()` / `register_server()`（同 PID 覆盖）/ `unregister_server(pid|port)`
- 进程：`pid_alive()` / `kill_process_tree()`
- 端口：`find_pids_by_port()` / `is_port_in_use()`（IPv4+IPv6 双查）/ `wait_for_port_free()`
- 路径比较：`same_path()`（Windows 大小写不敏感）

登记表落盘于 `~/.jarvis/dev_servers.json`，记录 `pid / port / project_dir / command / url / log_file / started_at`，**跨会话可查**。

**为什么不用 psutil**：psutil 属于 daemon 可选依赖，为保证工具在最小安装下可用，全部改用标准库实现——Windows 走 `tasklist` / `taskkill` / `netstat`，Unix 走 `os.kill` / `os.killpg` / `lsof`。

### 2. DevServerTool 增加 `action="stop"`

修改 [dev_server_tool.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/extensions/dev_server_tool.py)：

- `input_schema` 增加 `action`（enum `["start","stop"]`，默认 `start`）与 `pid`；
- `call()` 变为分派器：`stop → _stop_server()`，否则 `_start_server()`；
- `_start_server()` 成功启动后新增 `register_server({...})`，把进程登记进表；
- `_find_registered_targets()`：**权限判定与真正关闭共用同一套定位逻辑**，优先级 `显式 pid > 显式 port > 显式 project_dir > 最近启动的一个`，且只返回 `pid_alive` 的项；
- `_stop_server()`：定位目标 → `kill_process_tree()` → `wait_for_port_free()` → `unregister_server()`，返回 `{action:"stop", stopped:[...], failed:[...]}`；未命中登记表时按显式 `pid`/`port` 构造合成目标兜底。

### 3. 权限分流（关键设计）

[check_permissions](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/extensions/dev_server_tool.py#L148-L163)：

- `action=stop` **且命中登记表**（关闭 Jarvis 自己启动的服务器，属安全收尾）→ `PermissionResult.allow(...)`，**直接放行**，不再走阻塞式确认；
- `action=stop` 但目标未登记 → `ask`（仍需确认，防止误杀外部进程）；
- `action=start` → 一律 `ask`。

这样语音模式下说「把项目关了」会立刻执行、不再卡死。

### 4. system prompt 补「关闭」指引

修改 [system.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/prompts/system.py#L118-L122) 的「开发服务器」段，新增「**关闭开发服务器**（重要）」小节：

- 要求用户说「把项目关了」时**直接调用 `DevServer(action="stop")`**；
- **明确禁止**用 Bash 的 `kill` / `taskkill` / `pkill` / `netstat`（语音模式会阻塞）；
- 列出定位参数与「无需确认」规则。

### 5. CLI 新增 `/server stop` 子命令

重构 [tool_commands.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/commands/handlers/tool_commands.py) 的 `/server` 处理，**消除原 `_server_start` 的复制粘贴**，拆成三个职责单一的函数：

- `_split_server_args()`：识别首 token（`stop`/`close`/`kill`/`shutdown` → stop；`start`/`run`/`serve`/`up` → start）；
- `_parse_server_args()`：解析位置目录、`--port`、`--pid`、`--command`、`--wait`；
- `_run_dev_server()`：统一的权限确认 + `tool.call()` + 结果打印（`action_label` 区分「启动 / 关闭开发服务器」）。

配套更新帮助文案：[cli.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/ui/cli.py) 的 `_COMMANDS` 与 [core_commands.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/commands/handlers/core_commands.py) 的 `/help`。

---

## 验证结果

### 单元测试（全部通过）

新增 [test_dev_server_tool.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/tools/test_dev_server_tool.py)，**不真正启停进程**（登记表 / 进程操作全 monkeypatch），覆盖 19 个用例：

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis
python -m pytest tests/tools/test_dev_server_tool.py -q
# → 19 passed in 0.42s
```

覆盖点：元数据（非只读 / 并发安全 / schema 含 stop 与 pid）、权限（start→ASK、stop 命中→ALLOW、stop 未命中→ASK）、活动描述、目标定位（按 pid / port / project_dir / 最近启动）、关闭逻辑（无目标报错、显式 pid 终止+注销、显式 port 无进程报错、终止失败进 failed）、`call` 分派、start 成功写入登记表。

### 人工验证

- 语音说「把项目关了」→ 直接触发 `DevServer(action="stop")`，登记表命中的服务器被关闭，不再卡死；
- `/server stop` / `/server stop --port 5174` / `/server stop --pid N` 均能正确定位并关闭；
- 关闭未登记目标时仍会弹出确认。

---

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| [agent/tools/extensions/dev_server_process.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/extensions/dev_server_process.py) | **新建**，抽出登记表与进程/端口管理纯函数 |
| [agent/tools/extensions/dev_server_tool.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/extensions/dev_server_tool.py) | 新增 `action=stop`、`_stop_server`、`_find_registered_targets`；start 后登记；权限分流；`_start_server` 由 `async` 改 `def` |
| [agent/prompts/system.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/prompts/system.py) | 「开发服务器」段新增关闭指引，禁止 Bash kill |
| [agent/commands/handlers/tool_commands.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/commands/handlers/tool_commands.py) | 新增 `/server stop`，拆函数消除复制粘贴 |
| [agent/ui/cli.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/ui/cli.py) | `_COMMANDS` 增加 `/server stop` 帮助项 |
| [agent/commands/handlers/core_commands.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/commands/handlers/core_commands.py) | `/help` 增加 `/server stop` 说明 |
| [tests/tools/test_dev_server_tool.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/tools/test_dev_server_tool.py) | **新建**，19 个单元测试 |
| [docs/architecture/10-扩展生态.md](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/docs/architecture/10-扩展生态.md) | DevServer 章节升级为「启动 / 关闭」，说明为何关闭能力必须由工具承担 |
| [docs/guide/ecosystem.md](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/docs/guide/ecosystem.md) | 开发服务器文档补 stop 命令与特性 |
| [docs/guide/commands.md](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/docs/guide/commands.md) | 命令表新增 `/server stop` |
| [docs/architecture/05-工具系统.md](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/docs/architecture/05-工具系统.md) | DevServer 行改为「一键启动 / 关闭」 |
| [docs/test/TEST_CHECKLIST.md](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/docs/test/TEST_CHECKLIST.md) | §27 新增 T-DS-001~T-DS-006 关闭用例 |

---

## 经验总结

1. **对称能力必须成对交付**。有「启动」就要有「关闭」。只做一半，模型不会「优雅降级」，而是被迫用底层命令硬凑，最终把成本转嫁给用户体验。

2. **语音模式下任何 `ASK` 都是潜在死锁**。文本模式能按键确认，语音模式不能。凡是语音高频、且属于「安全收尾」的操作（关闭自己启动的东西），应在权限层直接放行，而不是让模型绕过。

3. **别把手段当目的**。用户让「关项目」，模型却去思考 `netstat` 找 PID——这是典型的「没有一等公民工具，只好自己拼」的表现。发现模型在长链推演底层系统命令时，优先考虑**是不是缺一个专门的工具**。

4. **权限判定与业务定位共用一套逻辑**。`_find_registered_targets()` 同时服务 `check_permissions` 与 `_stop_server`，避免两处判断口径不一致（否则会出现「判定时算命中、关闭时又找不到」的矛盾）。

5. **注意 async/await 的一致性**。本次修复过程中引入过一个真实 bug：把 `call` 拆出 `_start_server` 时，`_start_server` 被误标为 `async def`，但 `call` 里没 `await`，导致返回 coroutine 而非 `ToolResult`，start 路径完全失效（单测报 `coroutine ... has no attribute 'is_error'`）。**内部无 `await` 的方法不要标 `async`**，拆函数后务必跑测试。

6. **可复现的测试要克制真实副作用**。进程启停类工具的单测必须 monkeypatch 掉真正的 `Popen` / `kill`，否则测试会真起进程、污染环境。
