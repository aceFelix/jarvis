# 核心机制详解

> [← 返回 README](../../README.md) · [命令参考](commands.md) · [扩展生态](ecosystem.md) · [权限系统架构](../architecture/04-权限系统.md)

---

## 五层权限系统

Jarvis 拥有多层安全防护，确保 AI 不会越权操作你的电脑：

| 层级 | 说明 |
|---|---|
| **L1 硬阻断** | `.ssh`/`.aws`/`.gnupg` 等敏感目录永久拒绝访问；`rm -rf /` 等危险命令永久拦截 |
| **L2 路径守护** | 限制 AI 的文件操作范围，防止读写关键系统目录 |
| **L3 命令分类** | 将命令分为安全/危险/敏感三级，危险命令需确认 |
| **L4 权限模式** | `default` 逐次确认 / `plan` 只读规划 / `accept_edits` 编辑自动通过 / `yolo` 全自动 |
| **L5 用户确认** | 关键操作（删除文件、执行脚本）弹窗确认 |

切换权限模式：`/mode yolo`

## 安全沙箱执行（P3-8）

高风险操作在隔离环境中运行，防止误操作破坏系统。跨平台支持：

| 平台 | 沙箱机制 | 说明 |
|------|------|------|
| Windows | Job Object | 内存/进程数限制，KILL_ON_JOB_CLOSE 终止进程树 |
| Linux | resource.setrlimit | RLIMIT_AS/CPU/NPROC 资源限制 |
| macOS | sandbox-exec + rlimit | Apple Sandbox 命令包装 + 资源限制 |

**四级风险分类**：

| 风险等级 | 策略 | 示例命令 |
|------|------|------|
| LOW | 直接放行 | ls, cat, git status |
| MEDIUM | 沙箱开启时自动放行 | npm install, git commit, python script.py |
| HIGH | 强制沙箱 + 文件快照 | rm, del, git push --force |
| CRITICAL | 沙箱 + 快照 + 用户确认 | rm -rf, sudo, format, reg delete |

**文件快照保护**：高风险操作前自动备份目标文件，操作失败可回滚（`~/.jarvis/sandbox_snapshots/`）。

**审计日志**：所有沙箱操作记录到 `~/.jarvis/sandbox_audit.jsonl`，支持统计查询。

```toml
[sandbox]
enabled = false              # 总开关
max_memory_mb = 512          # 沙箱内最大内存（MB）
max_cpu_seconds = 60         # 最大 CPU 时间（秒）
max_processes = 10           # 最大子进程数（防 fork bomb）
timeout = 120                # 命令总超时（秒）
block_network = false        # 是否阻断网络
auto_allow_medium = true     # 沙箱开启时自动放行中等风险
audit = true                 # 记录审计日志
max_snapshots = 20           # 文件快照最大保留数
excluded_commands = []       # 不走沙箱的命令（如 ["docker", "wsl"]）
```

## 上下文压缩

采用**分层上下文管理**（冻结前缀 + 滑动窗口）——压缩后的摘要锁定为「冻结区」永不修改，后续请求前缀稳定 → LLM 缓存持续命中。

- **比例触发**：总 token（冻结摘要 + 活跃窗口 + system prompt）≥ `context_window` × `compact_ratio`（默认 128000 × 0.5 = 64000）才压缩，未超比例绝不压缩，用满窗口前半程；冻结后总量增长不足 `compact_refreeze_growth`（默认 1.25 倍）不重复压缩（防抖）
- **冻结策略**：压缩后的摘要锁定为「冻结前缀」永不修改，后续请求前缀稳定，LLM 缓存持续命中
- **图片驱逐**：旧图片替换为文字占位符释放 Token（仅作用于活跃窗口）
- **工具结果折叠**：旧工具结果缩成一行摘要（仅作用于活跃窗口）
- **反应式压缩**：遇到 Context Too Long 错误自动压缩后重试
- 手动触发：`/compact`

```toml
[context]
compaction = true
# 压缩条件：总 token（冻结摘要+活跃窗口+system prompt）≥ context_window × compact_ratio 才压缩，
# 未超比例绝不压缩，用满窗口前半程。
context_window = 128000           # 模型上下文窗口（token），换模型时同步修改，勿设 0
compact_ratio = 0.5               # 触发比例（占窗口百分比），如 0.5 = 超 50% 才压缩
compact_refreeze_growth = 1.25    # 防抖：冻结后总量增长不足此倍数不重复压缩
compact_max_output_tokens = 2048  # 压缩摘要请求的输出 token 上限
tool_result_keep_recent = 4       # 工具结果折叠时保留最近 N 条完整输出（其余缩成一行摘要）
```

## 记忆系统

Jarvis 支持多层记忆持久化：

- **会话记忆**：每轮对话后自动存盘，`/save` `/load` `/sessions` 手动管理恢复（启动不再自动恢复上次会话——旧 auto-latest 全局指针不区分项目目录，多项目并行会串台，已下线）
- **长期记忆**：`~/.jarvis/MEMORY.md`（用户级）+ `<workdir>/.jarvis/MEMORY.md`（项目级），启动时注入系统提示
- **画像记忆**：会话结束后自动用 LLM 提炼你的偏好/习惯/背景（如"习惯熬夜""主力 GLM"），
  存 `~/.jarvis/memory/profile.json`，下次会话限额注入系统提示——Jarvis 越用越懂你。
  后台异步提炼（默认 600 秒节流，可用 `profile_refine_interval` 配置，不影响响应速度）+ 每日凌晨维护（过时记忆自动衰减淡忘）。
  `/memory` 查看 / `/memory add` 手动添加 / `/memory del` 删除 / `/memory refine` 立即提炼。
  可在 `settings.toml` `[memory.refine]` 配置独立便宜模型跑提炼。
  `/memory sync` 可把本地画像同步到 aceFelix 知识图谱（先预览后确认，图谱为唯一事实源）
- **知识图谱画像桥**：`[profile_bridge] enabled = true` 后，启动时经 MCP 拉取 aceFelix
  知识图谱画像注入系统提示（技能/项目/兴趣等结构化信息，无需重新聊天）；
  前提：`~/.jarvis/mcp.json` 已配置 `acefelix-knowledge` server
- **自动恢复**：异常退出后下次启动自动提示恢复

## Skill 技能包

通过 Skill 文件为 AI 注入专业知识和工作流程：

```
~/.jarvis/skills/<name>/SKILL.md        # 用户级技能包
<workdir>/.jarvis/skills/<name>/SKILL.md # 项目级技能包
```

SKILL.md 包含：
- **Frontmatter**：name / description / when_to_use / trigger_words
- **正文**：Markdown 格式的专业知识指令

查看已加载技能：`/skills`

## 工具延迟加载

Jarvis 集成 100+ 工具后，采用**分组延迟加载**策略控制请求体积：

- **核心工具**（~15 个）：Bash / FileRead / FileEdit / WebSearch 等高频工具始终携带
- **延迟工具**（~80 个）：MCP / GUI / 浏览器 / 摄像头 / 协作工具等仅发名字摘要
- **ToolSearch**：模型需要延迟工具时搜索关键词加载完整 Schema，下轮即可调用
- **纯聊天检测**：短问候（"你好"、"在吗"）发 0 工具，秒回

> 参考 Claude Code deferred tool loading 机制，兼顾功能完整性与响应速度。

## MCP 集成

支持 [Model Context Protocol](https://modelcontextprotocol.io/) 接入外部工具：

- 配置文件：`~/.jarvis/mcp.json`
- 工具命名：`mcp__<server>__<tool>` 格式注册
- 默认 ASK 权限（外部进程），yolo 模式可放宽
- 查看状态：`/mcp`

## 安全性

### API Key 加密存储

J.A.R.V.I.S 使用操作系统原生凭据管理器加密存储 API Key，替代 TOML 文件明文：

- **Windows**：Windows Credential Manager（WinVaultKeyring）
- **macOS**：Keychain
- **Linux**：Secret Service / KWallet

存储时优先写入 keyring，失败降级到 TOML 明文。读取时按 环境变量 → keyring → TOML 优先级查询。

### 操作审计日志

所有工具调用自动记录到 `~/.jarvis/tool_audit.jsonl`：工具名、参数、权限模式、耗时、成功/失败、写操作标记。yolo 模式下的 FileWrite / Bash / DeleteFile 等写操作特别标记。

### 敏感字段脱敏

`/config show`、`/doctor`、错误提示中所有 API Key 自动脱敏为 `sk-xxxx...xxxx`。

## 消息级回溯检查点（shadow git）

```toml
[checkpoint]
enabled = true                    # 总开关：每轮对话前对工作目录打检查点，/rewind / 桌面撤回可连带回滚文件（关闭后仅支持对话回退）
max_per_session = 20              # 每会话检查点保留上限（超出修剪最早的）
timeout_seconds = 10               # 单条 git 命令超时（秒）；未装 git 时自动降级为仅对话回退
```

## 工具错误自愈

Jarvis 内置 **Tool Self-Healing**，工具调用失败时不会立刻把错误抛给 LLM，而是先自动分类、重试、降级或询问用户：

- **错误分类**：网络抖动、API 限流、超时、文件缺失、权限不足、依赖缺失、配置错误等
- **自动重试**：临时网络错误 / 限流按指数退避重试
- **自动修复**：文件缺失时自动创建父目录；超时时自动延长 `timeout`
- **用户询问**：可分类的可恢复错误重试耗尽后询问用户是否再试一次；**未知错误不再询问**，直接 fail-fast 交回 LLM 决策（避免桌面/serve 宿主下阻塞等待导致整轮卡死）
- **非零退出≠工具失败**：Bash 命令非零退出（curl 连不上=7、grep 无匹配=1 等）属正常结果，带 `[exit=N]` 表头原样回传给 LLM 自行判断，不进入自愈重试/询问链路
- **遥测统计**：`/doctor` 可查看自愈配置、错误分布、最近事件

### 配置

在 `configs/settings.toml` 或 `~/.jarvis/settings.toml` 中配置：

```toml
[self_healing]
enable_tool_self_healing = true
tool_retry_max = 3
tool_retry_backoff_base = 1.0
tool_retry_backoff_max = 30.0
```

### 会话历史自愈（悬空工具调用）

LLM 协议要求每个 `assistant.tool_use` 在**紧随的下一条消息**里都有配对的 `tool_result`。若只写了调用、没写结果（如工具执行中被用户中断、输出被截断），残缺片段会留在会话历史里，导致该会话**此后每次请求都被 API 拒收**——重试、换措辞、换模型均无效。

Jarvis 用「三道源头 + 一道出口」自动修好：

- **源头补齐**：工具执行被中断 / 抛异常 / 输出被截断 / 编排器缺项 → 立即注入 `is_error=True` 的占位结果（说明该调用已失效）
- **出口兜底**：所有发往 LLM 的消息在 provider 转换入口先跑 `ensure_tool_pairing()` 校验配对，并可**自愈已中毒的旧会话**（无需丢弃历史）
- **不破坏缓存**：仅在有违规时重建发送副本，健康历史零拷贝返回（`id`/`timestamp` 不变），冻结前缀的 prompt cache 不受影响

详见 [docs/fixlogs/dangling-tool-use-fix.md](../fixlogs/dangling-tool-use-fix.md)。

## 性能优化

| 优化 | 说明 |
|---|---|
| MCP 连接并行化 | 7 个 server 并发连接，启动时间从 ΣT 降到 max(T) |
| HTTP 连接池复用 | 所有 Provider 共享 httpx.AsyncClient，切换模型不重建 TCP 连接 |
| 工具注册缓存 | `build_default_registry()` 结果由 `@lru_cache` 缓存，多处调用仅执行一次 |
| 实时语音延迟加载 | 先连 WebSocket 显示"已连接"，MCP 工具后台加载完热更新 |
| 工具延迟加载 | 14 核心工具始终携带，~80 延迟工具按需搜索，纯聊天零工具 |
