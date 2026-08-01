# P0 多 Agent 协作增强升级复盘

## 1. 背景

Jarvis 多 Agent 协作在阶段五已实现基础能力：团队创建、后台队友、文件邮箱、共享任务列表。但在实际复杂任务场景中，存在以下明显短板：

- teammate 只能处理 3 种消息，计划审批流程断链
- teammate 不会主动领取任务，leader 必须手动 SendMessage 分配
- 任务完成/转派时无法自动通知 leader 和相关队友
- 没有团队状态查询工具，leader 难以掌握全局
- 没有强制终止后台 teammate 的手段
- 同步子代理不支持批量并行
- 架构文档与实际实现严重不符

## 2. 目标

按照 `docs/roadmap/jarvis-upgrade-roadmap.md` 的 P0 规划，将 Jarvis 多 Agent 协作能力从 ⭐⭐ 提升到 ⭐⭐⭐，使团队/队友/任务/邮箱四者真正联动，复杂任务能自动拆分、自主执行、结果汇总。

参考：ClaudeCode `teammate.ts` / `teammateMailbox.ts` / `forkSubagent.ts`；OpenClaw `agent-runner.ts`（OpenClaw 源码中未找到直接对应的协作核心文件，主要参考 ClaudeCode 设计思想）。

## 3. 主要改动

### 3.1 消息协议扩展

- 文件：`agent/collaboration/mailbox.py`
- 扩展 `TeammateMessage` 字段：`action`、`tool`、`args`、`status`、`data`
- 新增工厂函数：`make_permission_request`、`make_permission_response`、`make_task_claimed`、`make_task_completed`、`make_heartbeat`
- `from_dict` 对未知字段收敛到 `data`，保证旧消息前向兼容

### 3.2 队友消息处理增强

- 文件：`agent/collaboration/teammate.py`
- 扩展 `TeammateState`：新增 `current_task_id`、`pending_request_id`、`last_heartbeat_at` 及审批事件
- `_handle_message` 支持 `plan_approval_response`、`permission_response`、`task_assignment`、`shutdown_request`
- 新增 `request_plan_approval`、`request_permission` 方法，支持审批等待与超时
- 新增 `_maybe_send_heartbeat` 方法，每 30 秒发送心跳
- 主循环 finally 中从注册表注销并发送 terminated 通知

### 3.3 自主任务领取

- 文件：`agent/collaboration/teammate.py`
- 空闲 teammate 每 5 秒扫描 `TaskList.get_available_tasks()`
- 领取时原子更新 `status=in_progress` + `owner=agent_name`，并发送 `task_claimed` 通知
- 任务完成后调用 `_maybe_complete_current_task` 标记 completed 并发送 `task_completed`

### 3.4 TaskList 回调绑定到 Mailbox

- 文件：`agent/tools/collaboration/subagent_tool.py`
- 后台队友创建时设置 `TaskList` 的 `on_completed` 回调
- 任务完成时自动向 `team-lead` 发送 `task_completed` 消息

### 3.5 Leader 自动注入增强

- 文件：`agent/core/query_loop.py`
- `_inject_teammate_notifications` 支持渲染 `task_claimed`、`task_completed`、`plan_approval_request`、`permission_request`、`shutdown_response`
- `heartbeat` 不渲染，仅内部保留扩展点

### 3.6 新增 TeamStatus / TaskStop 工具

- 文件：`agent/tools/collaboration/team_status.py`、`agent/tools/collaboration/task_stop.py`
- `TeamStatus`：返回成员列表、任务统计、未读邮件数
- `TaskStop`：通过注册表直接 shutdown，或兜底发送 `shutdown_request`

### 3.7 进程内注册表

- 文件：`agent/collaboration/teammate_registry.py`
- 按 `(team_name, agent_name)` 索引 `TeammateRunner`
- 在 `SubagentTool._spawn_teammate` 中注册，teammate 终止时注销

### 3.8 同步子代理批量并行

- 文件：`agent/tools/collaboration/subagent_tool.py`
- `Agent` 工具新增 `tasks` 数组参数
- 调用 `run_subagents_parallel()` 并行执行，结果按编号聚合

### 3.9 工具注册

- 文件：`agent/core/tool.py`
- `register_team_tools` 注册 `TeamStatusTool` 和 `TaskStopTool`
- `register_subagent_tool` 注入 `task_list` 供后台队友使用

### 3.10 SendMessage 工具扩展

- 文件：`agent/tools/collaboration/send_message.py`
- 新增 `permission_request` / `permission_response` 消息类型处理

## 4. 问题与解决

### 4.1 文件路径错误

**现象**：最初查找 `subagent_tool.py` 和 `team_tools.py` 时提示文件不存在。

**原因**：误以为协作工具在 `agent/tools/extensions/` 下。

**解决**：通过 `Grep` 确认实际路径为 `agent/tools/collaboration/`，后续操作使用正确路径。

### 4.2 OpenClaw 源码缺少直接参考

**现象**：在 OpenClaw 源码中搜索 `team`、`swarm`、`collaborat` 等关键词未找到多 Agent 协作核心文件。

**原因**：OpenClaw 当前版本未实现同类团队/队友模型。

**解决**：以 ClaudeCode 设计思想为主，结合 Jarvis 现有架构做适配，不照搬 ClaudeCode 的 fork 上下文继承。

### 4.3 字符串替换失败

**现象**：编辑 `teammate.py` 时某次替换因旧字符串格式问题失败。

**原因**：旧字符串中包含多余的 `>` 符号。

**解决**：调整 old_string 格式后重新替换成功。

### 4.4 测试发现实现细节差异

**现象**：编写测试时发现 `TeammateMessage.from_dict` 未将未知字段收敛到 `data`。

**原因**：实现与 PLAN 中"前向兼容"设计不符。

**解决**：修改 `from_dict`，将未知字段统一放入 `data`，并补充对应单元测试。

## 5. 测试覆盖

新增 `tests/collaboration/` 目录：

| 文件 | 覆盖内容 |
|---|---|
| `test_mailbox.py` | 消息工厂、序列化/反序列化、邮箱读写、广播、未读检测、清空 |
| `test_task_list.py` | CRUD、依赖链、可领取任务、回调 |
| `test_team.py` | TeamManager 创建/加载/删除、成员管理、leader 判断 |
| `test_teammate_messages.py` | `_handle_message` 分支、自主任务领取、审批超时、注册表 |

验证结果：

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis
python -m pytest tests -q
# 56 passed
```

## 6. 文档更新

| 文档 | 更新内容 |
|---|---|
| `docs/architecture/11-多Agent协作.md` | 修正工具列表、补充 TeamStatus/TaskStop、更新协作流程图、补充计划审批和自主任务领取说明、补充心跳与生命周期 |
| `README.md` | 多 Agent 协作章节增加批量并行、自动任务领取、计划审批、团队状态查询、生命周期管理说明 |
| 本文档 | 新增升级复盘 |

## 7. 风险与应对

| 风险 | 应对 |
|---|---|
| 计划审批等待阻塞 teammate 主循环 | 使用 `asyncio.wait_for` + `abort_event` 支持随时中断 |
| 多个 teammate 并发抢任务导致重复执行 | 依赖 `TaskList.update` 文件锁，claim 后检查返回值 |
| 新增消息类型导致旧版本团队配置不兼容 | `TeammateMessage.from_dict` 对未知字段使用 `data` 兜底 |
| TaskStop 注册表跨进程失效 | 当前为 in-process 实现；leader 进程重启后注册表清空，teammate 实际状态仍以 TeamFile/mailbox 为准 |
| 测试依赖文件系统 | 使用 `tmp_path` fixture 隔离，避免污染真实 `~/.jarvis` |

## 8. 后续可优化方向

- 引入跨进程 teammate 状态存储，解决注册表 leader 重启失效问题
- 支持 teammate 间直接通信（目前所有消息经 team-lead 中转）
- 任务失败/重试策略（目前任务完成即结束，无自动重试）
- Leader 基于 heartbeat 自动检测并重启失联 teammate
- 更细粒度的权限审批（按文件/命令匹配规则）

## 9. 总结

本次升级在保持 Jarvis 现有"文件邮箱 + in-process asyncio"架构不变的前提下，补齐了多 Agent 协作的关键短板：队友能自主领取任务、计划审批流程闭合、团队状态可观测、后台 teammate 可强制终止、同步子代理支持批量并行。所有新增/修改代码均通过 Python 语法验证和单元测试，文档已同步更新。
