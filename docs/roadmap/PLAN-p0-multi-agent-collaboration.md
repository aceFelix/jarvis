# P0 多 Agent 协作增强升级方案

> 优先级：🔴 P0  
> 目标：把 Jarvis 多 Agent 协作从 ⭐⭐ 提升到 ⭐⭐⭐，让团队/队友/任务/邮箱四者真正联动，复杂任务能自动拆分、自主执行、结果汇总。
> 参考：ClaudeCode `teammate.ts` / `teammateMailbox.ts`、`forkSubagent.ts`；OpenClaw `agent-runner.ts` / `commands-subagents-dispatch.ts`。

---

## 一、目标与验收标准

### 1.1 目标

1. **队友能自主领取任务**：空闲 teammate 自动查看共享 TaskList，领取 pending + 无阻塞任务并执行。
2. **计划审批可落地**：teammate 在执行写操作前可向 leader 发送 `plan_approval_request`，leader 审批后继续。
3. **消息协议对齐 ClaudeCode**：补齐 `permission_request`、`heartbeat`、`task_claimed`、`task_completed` 等消息类型。
4. **Leader 自动感知状态**：每轮自动注入队友 idle、任务完成、计划审批、异常等状态更新。
5. **团队状态可查询**：新增 `TeamStatus` 工具，leader 可随时查看团队成员/任务/邮箱概要。
6. **生命周期更健壮**：teammate 异常退出可检测、shutdown 有超时、TaskStop 能终止后台队友。
7. **同步子代理批量并行**：`Agent` 工具支持一次派发多个同步子任务并聚合结果。

### 1.2 验收标准

- [ ] `Agent` 后台模式创建的 teammate 能自动领取并执行 TaskCreate 创建的任务。
- [ ] teammate 在 PLAN/ASK 模式下执行写工具前发送 `plan_approval_request`，leader 审批后执行。
- [ ] leader 每轮自动收到 teammate 的 idle_notification / task_completed / heartbeat 注入。
- [ ] `TeamStatus` 工具返回成员状态、任务统计、未读邮件数。
- [ ] `TaskStop` 工具可终止指定后台 teammate。
- [ ] 所有新增/修改代码通过 `mvn compile -q` / `npx vite build --mode development` 等对应编译检查。
- [ ] 新增单元测试覆盖核心流程（mailbox 读写、TaskList 依赖、teammate 消息处理）。
- [ ] 更新 `docs/architecture/11-多Agent协作.md` 和 `README.md` 相关章节。

---

## 二、当前状态与差距

### 2.1 已具备能力

| 模块 | 能力 | 文件 |
|---|---|---|
| Team | 创建、加载、保存、删除、成员管理 | `agent/collaboration/team.py` |
| Teammate | In-process 后台执行、邮箱轮询、idle 通知 | `agent/collaboration/teammate.py` |
| Mailbox | 10+ 消息类型、文件锁、广播 | `agent/collaboration/mailbox.py` |
| TaskList | 创建/更新/删除、依赖链、owner 分配 | `agent/collaboration/task_list.py` |
| Subagent | 同步子代理 + 后台队友两种模式 | `agent/collaboration/subagent.py` / `tools/collaboration/subagent_tool.py` |
| 工具 | TeamCreate/TeamDelete/SendMessage/TaskCreate/TaskUpdate/TaskList | `agent/tools/collaboration/` |
| Leader 注入 | 每轮自动读取 team-lead 邮箱并注入 | `agent/core/query_loop.py#L557-L601` |

### 2.2 关键差距

| 差距 | 影响 | 优先级 |
|---|---|---|
| teammate 只处理 3 种消息（shutdown/plain/task_assignment），不处理 `plan_approval_response` / `permission_request` | 计划审批流程断链 | 高 |
| teammate 不会主动读取 TaskList 领取任务 | leader 必须手动 SendMessage 分配，无法自动并行 | 高 |
| TaskList 的 `on_completed/on_owner_changed` 回调未绑定到 mailbox | 任务完成/转派时队友无法自动感知 | 高 |
| 没有 `TeamStatus` 工具，leader 难以掌握全局 | 可观测性差 | 中 |
| 没有 `TaskStop` 工具终止后台 teammate | 队友异常时无法强制收尾 | 中 |
| `TeammateRunner` 异常后没有心跳/保活机制 | leader 无法区分“空闲”和“卡死” | 中 |
| `Agent` 同步模式不支持批量并行 | 多个独立子任务需要多次调用 | 低 |
| 架构文档 `11-多Agent协作.md` 与当前实现不符（如 TaskGet/TaskStop 不存在） | 文档过时 | 中 |

---

## 三、详细设计

### 3.1 消息协议扩展（mailbox.py）

新增/完善以下消息工厂函数，并同步更新 `TeammateMessage` 的 `to_dict/from_dict`：

```python
make_permission_request(from_name, action, tool, args, request_id)
make_permission_response(from_name, request_id, approve, reason)
make_task_claimed(from_name, task_id, task_subject)
make_task_completed(from_name, task_id, status, summary)
make_heartbeat(from_name, status, task_id)
make_plan_approval_request(from_name, plan_text, request_id)  # 已存在，补齐调用点
make_plan_approval_response(from_name, request_id, approve, feedback)  # 已存在，补齐调用点
```

消息类型注册到 `SendMessageTool` 的 enum 中：`permission_request/permission_response/task_claimed/task_completed/heartbeat`。

### 3.2 队友消息处理增强（teammate.py）

`_handle_message` 扩展为：

| 消息类型 | 行为 |
|---|---|
| `plain` | 加入 `pending_user_messages` |
| `task_assignment` | 同上，并记录当前 task_id |
| `plan_approval_response` | 若 approve=True，设置 `plan_approved_event`；否则标记 `plan_rejected` |
| `permission_response` | 同上 |
| `shutdown_request` | 回复 `shutdown_response` 并返回 `"shutdown"` |

新增 `_wait_for_plan_approval(timeout=60)`：
- 在 teammate 执行写工具前，若 permission_mode 为 PLAN/ASK，先向 leader 发送 `plan_approval_request`。
- 主循环暂停等待 response；超时则视为拒绝。

> 实现方式：teammate 侧不直接拦截工具调用（避免深度侵入 ToolOrchestrator），改为在 system prompt 中明确要求：
> “执行任何写操作前，必须先调用 SendMessage 向 team-lead 发送 plan_approval_request，收到 approval_response 后才能执行。”
> 同时给 teammate 的 registry 注入一个特殊 `TeammateSendMessage` 工具用于发送请求。

### 3.3 队友自主任务领取

在 `TeammateRunner._run` 的空闲分支中：

```python
if waiting_for_more_work and not self._state.pending_user_messages:
    available = self._task_list.get_available_tasks()
    for task in available:
        if not task.owner:  # 未分配
            # 原子 claim：更新 owner + status
            updated = self._task_list.update(
                task.id, status="in_progress", owner=self._identity.agent_name
            )
            if updated:
                # 发送 task_claimed 通知
                write_mailbox("team-lead", make_task_claimed(...), team_name)
                self._state.pending_user_messages.append(
                    f"你领取了任务 #{task.id}: {task.subject}\n{task.description}"
                )
                break
```

为防止多个 teammate 并发抢同一任务，`TaskList.update` 已带文件锁；claim 后若返回 `None` 说明已被别人领走，继续下一个。

### 3.4 TaskList 回调绑定到 Mailbox

在 `SubagentTool._spawn_teammate` 中创建 `TaskList` 后设置回调：

```python
self._task_list.set_hooks(
    on_completed=lambda task: _notify_task_completed(task, team_name),
    on_owner_changed=lambda task, old_owner: _notify_owner_changed(task, old_owner, team_name),
)
```

`_notify_task_completed`：
- 向 `team-lead` 和所有相关队友发送 `task_completed` 消息，便于解除阻塞感知。

### 3.5 Leader 自动注入增强（query_loop.py）

`_inject_teammate_notifications` 扩展消息渲染：

| 消息类型 | 渲染 |
|---|---|
| `idle_notification` | `{name}: 空闲 (summary)` |
| `task_claimed` | `{name}: 领取任务 #{id} {subject}` |
| `task_completed` | `{name}: 完成任务 #{id} {status}` |
| `plan_approval_request` | `{name}: 请求审批计划：{plan_text}` |
| `permission_request` | `{name}: 请求权限：{tool} {action}` |
| `shutdown_response` | `{name}: 同意/拒绝关闭` |
| `heartbeat` | 不渲染，仅更新内部健康时间戳 |

对于 `plan_approval_request` / `permission_request`，注入后让 leader 模型自然地在下一轮调用 `SendMessage` 回复。

### 3.6 新增 TeamStatus 工具

文件：`agent/tools/collaboration/team_status.py`

```python
class TeamStatusTool(Tool):
    name = "TeamStatus"
    description = "查看当前活跃团队的状态：成员列表、运行状态、任务统计、未读邮件数。"
    input_schema = {"type": "object", "properties": {}}

    def call(self, args, ctx):
        team = mgr.load(mgr.active_team)
        tasks = task_list.list_all()
        unread = count_unread("team-lead", team.name)
        return ToolResult(data=formatted_summary)
```

### 3.7 新增 TaskStop 工具

文件：`agent/tools/collaboration/task_stop.py`

```python
class TaskStopTool(Tool):
    name = "TaskStop"
    description = "终止团队中指定的后台 teammate。"
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "要终止的队友名"},
            "reason": {"type": "string", "description": "原因"},
        },
        "required": ["name"],
    }
```

需要一个进程内注册表保存 `name -> TeammateRunner` 映射，供 `TaskStopTool` 查找。在 `SubagentTool._spawn_teammate` 中注册；teammate 终止时注销。

### 3.8 同步子代理批量并行

扩展 `Agent` 工具 schema，新增可选 `tasks` 数组：

```json
{
  "tasks": {
    "type": "array",
    "items": {
      "type": "object",
      "properties": {
        "prompt": {"type": "string"},
        "agent_type": {"type": "string"},
        "description": {"type": "string"}
      },
      "required": ["prompt"]
    },
    "description": "批量同步子代理任务列表。提供时忽略顶层 prompt/agent_type。"
  }
}
```

实现：调用 `run_subagents_parallel()`，结果按编号聚合返回。

### 3.9 队友心跳与异常检测

- teammate 每 30 秒发送一次 `heartbeat` 消息到 `team-lead`。
- `TeamStatus` 根据最后心跳时间判断 teammate 是否“失联”。
- 心跳不注入对话，只更新内部时间戳。

---

## 四、实施步骤

| 步骤 | 内容 | 产出文件 | 风险 |
|---|---|---|---|
| 1 | 扩展 mailbox 消息工厂与 SendMessage enum | `mailbox.py`, `send_message.py` | 低 |
| 2 | 增强 teammate 消息处理与计划审批等待 | `teammate.py` | 中 |
| 3 | 实现队友自主任务领取 | `teammate.py` | 中 |
| 4 | 绑定 TaskList 回调到 mailbox | `subagent_tool.py`, `task_list.py` | 低 |
| 5 | 增强 leader 邮箱注入渲染 | `query_loop.py` | 低 |
| 6 | 新增 TeamStatus / TaskStop 工具 | `team_status.py`, `task_stop.py` | 低 |
| 7 | 扩展 Agent 工具批量并行 | `subagent_tool.py`, `subagent.py` | 低 |
| 8 | 添加 teammate 心跳 | `teammate.py`, `team_status.py` | 低 |
| 9 | 编写单元测试 | `tests/collaboration/` | 中 |
| 10 | 更新架构文档与 README | `docs/architecture/11-多Agent协作.md`, `README.md` | 低 |

---

## 五、测试计划

### 5.1 单元测试

新增 `jarvis/tests/collaboration/` 目录：

| 测试文件 | 覆盖内容 |
|---|---|
| `test_team.py` | TeamManager 创建/加载/删除、成员管理、leader 判断 |
| `test_mailbox.py` | 消息读写、广播、未读检测、文件锁并发 |
| `test_task_list.py` | 创建/更新/删除、依赖链、owner 变更回调 |
| `test_teammate_messages.py` | `_handle_message` 各分支、计划审批超时 |

### 5.2 集成测试

- 在临时目录创建团队 → 启动 teammate → 创建任务 → 验证 teammate 自动 claim → 验证 mailbox 收到 `task_claimed`。
- 使用 mock provider / mock LLM 避免依赖真实 API。

### 5.3 验证命令

后端：
```powershell
Set-Location e:\2.MyProjects\MyAgentChat\bitinn-dev\bitinn; mvn compile -q
```

前端（如协作工具有 UI 改动）：
```powershell
Set-Location e:\2.MyProjects\MyAgentChat\bitinn-dev\bitinn-vue; npx vite build --mode development
```

Python 测试：
```powershell
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis; python -m pytest tests/collaboration -q
```

---

## 六、文档更新

| 文档 | 更新内容 |
|---|---|
| `docs/architecture/11-多Agent协作.md` | 修正工具列表、补充 TeamStatus/TaskStop、更新协作流程图、补充计划审批和自主任务领取说明 |
| `README.md` | 多 Agent 协作章节增加“自动任务领取”“计划审批”“团队状态查询”说明 |
| `docs/test/` | 新增 `collaboration-testing.md` 测试文档 |

---

## 七、风险与应对

| 风险 | 应对 |
|---|---|
| 计划审批等待阻塞 teammate 主循环 | 使用 `asyncio.wait_for` + `abort_event` 支持随时中断 |
| 多个 teammate 并发抢任务导致重复执行 | 依赖 `TaskList.update` 文件锁，claim 后检查返回值 |
| 新增消息类型导致旧版本团队配置不兼容 | `TeammateMessage.from_dict` 对未知字段使用 `data` 兜底 |
| TaskStop 注册表跨进程失效 | 当前为 in-process 实现；后续如出进程隔离再引入外部状态 |
| 测试依赖文件系统，CI 可能慢 | 使用 `tmp_path` fixture，测试文件尽量小 |

---

## 八、备注

- 本方案保持 Jarvis 现有的“文件邮箱 + in-process asyncio”架构，不引入新进程模型，风险可控。
- 参考 ClaudeCode 的 `permission_request` / `idle_notification` / `forkSubagent` 设计，但不做完整的 fork 上下文继承（超出 P0 范围）。
- 所有修改遵循现有代码风格：dataclass、Javadoc/行内注释、文件锁、原子写入。
