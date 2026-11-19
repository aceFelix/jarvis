# DevServer 工具修复记录

## 问题现象

Jarvis 新增 `DevServer` 工具用于一键启动前端开发服务器后，出现两个问题：

1. **路径解析错误**：AI 传入 Git Bash 风格路径 `/e/J.A.R.V.I.S_Work/...`，DevServer 在 Windows 上解析成 `E:\e\J.A.R.V.I.S_Work\...`（在盘符后错误拼接了一个 `e` 目录）。
2. **启动后 jarvis 卡死**：项目启动成功后，用户继续向 jarvis 发送消息，jarvis 完全不回复。但杀死开发服务器进程后，jarvis 恢复正常。

---

## 排查与修复过程

### 第一阶段：路径解析修复

**时间**：2026-07-23
**操作**：分析 `_resolve_project_dir` 方法，发现 `Path("/e/...")` 在 Windows 上被当作 POSIX 绝对路径（根目录 `/` 下），`resolve()` 后变成 `E:\e\...`。

**根因**：Windows 上 `Path` 不识别 Git Bash 的 `/e/...` 格式（应映射为 `E:\...`）。

**修复**：
- 在 `_resolve_project_dir` 中增加 `_normalize_gitbash_path()` 预处理。
- 用正则 `/^([a-zA-Z])/(.*)/` 匹配 Git Bash 风格路径，转为 `{DRIVE}:\{path}`。
- 非 Windows 平台或非 Git Bash 格式路径保持不变。

**文件**：[agent/tools/extensions/dev_server_tool.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/extensions/dev_server_tool.py)

**结果**：路径解析正确，`/e/...` → `E:\...`。

---

### 第二阶段：卡死排查（误判·文件句柄）

**时间**：2026-07-23
**假设**：`subprocess.Popen` 通过 `stdout=open(log_file, "w")` 传递文件句柄，Python 持有子进程的文件描述符可能导致父进程 IO 阻塞。

**修复**：
- 改为 shell 重定向 `> "log_file" 2>&1`，Python 不再持有文件句柄。
- 同时添加 `stdout=DEVNULL, stderr=DEVNULL`（因为 shell 已重定向，不再需要 Python 层面的管道）。

**结果**：问题未解决，jarvis 仍然在项目启动后卡死。

---

### 第三阶段：卡死排查（真凶·stdin 继承）

**时间**：2026-07-23
**关键观察**：用户反馈"把项目进程关了，jarvis 又能正常聊天了"。这说明运行中的开发服务器进程直接影响了 jarvis 的输入。

**根因**：`subprocess.Popen(shell=True)` 启动的子进程默认继承父进程的 stdin。Vite 开发服务器运行时监听键盘输入（如按 `h` 显示帮助），当用户在 jarvis 终端中敲键盘时，按键被 Vite 子进程截获，jarvis 主进程收不到用户输入，表现为"卡死不回复"。杀死子进程后 stdin 释放，jarvis 恢复正常。

**修复**：
- 在 `_start_process()` 中设置 `stdin=subprocess.DEVNULL`，断开子进程与父进程 stdin 的连接。

**文件**：[agent/tools/extensions/dev_server_tool.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/extensions/dev_server_tool.py)

**结果**：问题彻底解决。用户确认启动项目后可以正常和 jarvis 对话。

---

### 第四阶段：端口检测误判修复

**时间**：2026-07-23
**现象**：DevServer 返回 `"success": false, "status": "进程已启动，但端口未在预期时间内就绪"`，但日志显示 Vite 已在 683ms 内启动成功，端口 5173 可正常访问。

**根因**：`_is_port_in_use()` 只检测 IPv4 环回地址 `127.0.0.1`。但部分 Vite 实例可能仅绑定到 IPv6 环回地址 `::1`，导致端口检测始终失败。

**修复**：
- `_is_port_in_use()` 改为依次尝试 `127.0.0.1` 和 `::1`。
- 单个地址的 socket 连接超时从 1s 缩短为 0.5s，总检测时间不变。

**文件**：[agent/tools/extensions/dev_server_tool.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/extensions/dev_server_tool.py)

---

### 第五阶段：System Prompt 优化

**时间**：2026-07-23
**现象**：AI 在调用 DevServer 前先调了 3 次 Glob 查看项目结构，增加不必要的步骤。

**修复**：
- System prompt 明确指示："直接调用 DevServer 工具，不要先用 Glob/FileRead 查看项目结构——DevServer 内部已自动识别项目类型。"

**文件**：[agent/prompts/system.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/prompts/system.py)

---

## 涉及文件

| 文件 | 改动 |
|---|---|
| [agent/tools/extensions/dev_server_tool.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/extensions/dev_server_tool.py) | 新增 `_normalize_gitbash_path()`；`_start_process()` 加 `stdin=DEVNULL` 并用 shell 重定向；`_is_port_in_use()` 同时检测 IPv4/IPv6 |
| [agent/prompts/system.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/prompts/system.py) | DevServer 使用指引：禁止先 Glob 再 DevServer |
| [agent/main.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/main.py) | 新增 `/server` 命令；修复 `_server_start` 中 `ToolContext` 和 `PermissionResult` 的字段引用 |

---

## 经验总结

1. **子进程 stdin 继承是 Windows 上的经典陷阱**。任何 `shell=True` 启动的长期运行子进程，必须显式设置 `stdin=DEVNULL`，否则该进程可能截获父进程的键盘输入。
2. **Git Bash 路径不是 POSIX 路径**。`/e/...` 在 Windows 上应转为 `E:\...`，不能依赖 `Path` 的自动解析。
3. **端口检测需覆盖双栈**。仅检测 `127.0.0.1` 可能漏掉仅绑定 IPv6 的服务，应同时检测 `::1`。
4. **文件句柄交给 subprocess.Popen 不是好做法**。用 shell 重定向更可靠，避免 Python 持有可能干扰 IO 的文件描述符。
