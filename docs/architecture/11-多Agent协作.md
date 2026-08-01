# 11 - 多 Agent 协作

J.A.R.V.I.S 支持派生子 Agent 并行处理复杂任务，以及团队协作模式。

## 一、核心文件

| 文件 | 职责 |
|---|---|
| [collaboration/subagent.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/collaboration/subagent.py) | 子代理定义与同步执行 |
| [collaboration/team.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/collaboration/team.py) | 团队生命周期与成员管理 |
| [collaboration/teammate.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/collaboration/teammate.py) | 后台队友执行引擎 |
| [collaboration/teammate_registry.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/collaboration/teammate_registry.py) | 进程内 teammate 注册表 |
| [collaboration/mailbox.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/collaboration/mailbox.py) | Agent 间消息邮箱 |
| [collaboration/task_list.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/collaboration/task_list.py) | 共享任务列表 |

## 二、子代理 Subagent

### 概念

主 Agent 可创建子代理处理独立的子任务，结果汇总后继续。

### 工具

`Agent` 工具（别名 `Subagent`）支持两种模式：

```python
# 同步子代理（一次性任务）
AI: 调用 Agent
  input: {
    "prompt": "搜索所有 TODO 注释",
    "agent_type": "explorer"
  }
→ 子代理独立运行，返回结果

# 背景队友（持久协作）
AI: 调用 Agent
  input: {
    "prompt": "改造登录模块",
    "agent_type": "coder",
    "run_in_background": true,
    "name": "coder-1",
    "team_name": "my-project"
  }
→ 派生持久队友，加入团队，通过 SendMessage 持续通信
```

### 批量并行

`Agent` 工具支持一次派发多个同步子任务：

```python
AI: 调用 Agent
  input: {
    "tasks": [
      {"prompt": "搜索 TODO", "agent_type": "explorer"},
      {"prompt": "检查类型", "agent_type": "researcher"},
      {"prompt": "运行测试", "agent_type": "coder"}
    ]
  }
→ 多个子代理并行执行，结果按编号聚合返回
```

### 实现

[subagent.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/collaboration/subagent.py)：

```python
def run_subagent(agent_def, prompt, provider, workdir, permission_mode, parent_ui) -> SubagentResult:
    # 1. 构建受限工具集
    sub_registry = _build_sub_registry(agent_def.allowed_tools)
    # 2. 构建子 QueryLoop
    sub_loop = QueryLoop(provider=provider, registry=sub_registry, ...)
    # 3. 运行
    stats = await sub_loop.run(prompt, ctx)
    # 4. 返回结果
    return SubagentResult(report=..., iterations=..., tool_calls=...)
```

`run_subagents_parallel()` 支持多个子代理并行：内部使用 `asyncio.gather` 同时调度多个 `run_subagent` 调用。

### AgentDefinition

```python
@dataclass
class AgentDefinition:
    agent_type: str
    description: str
    system_prompt: str
    allowed_tools: list[str] | None = None
    model: str | None = None
    max_iterations: int = 15
```

### SubagentResult

```python
@dataclass
class SubagentResult:
    report: str             # 最终汇报文本
    iterations: int         # 迭代轮数
    tool_calls: int         # 工具调用次数
    success: bool
    error: str | None = None
```

**为什么用子代理**：
- 独立子任务不污染主对话上下文
- 并行加速
- 工具子集限制权限

## 三、团队 Team

### 概念

创建 Agent 团队，分配不同角色和工具集，通过邮箱通信。一个会话只能领导一个团队。

### 创建团队

```python
AI: 调用 TeamCreate
  input: {
    "name": "code-review",
    "members": [
      {"name": "reviewer", "role": "代码审查员", "tools": ["FileRead", "Grep"]},
      {"name": "tester", "role": "测试工程师", "tools": ["Bash", "FileWrite"]}
    ]
  }
```

### TeamManager

[team.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/collaboration/team.py)：

```python
class TeamManager:
    def create(self, name, *, lead_session_id=None, description="", cwd="", model=None) -> TeamFile: ...
    def delete(self, name) -> bool: ...
    def load(self, name) -> TeamFile | None: ...
    def add_member(self, name, member: TeamMember) -> TeamFile: ...
    def mark_member_active(self, name, active: bool) -> TeamFile | None: ...
    @property
    def active_team(self) -> str | None: ...
```

### Teammate

[teammate.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/collaboration/teammate.py)：

```python
@dataclass
class TeammateIdentity:
    agent_id: str            # name@teamName
    agent_name: str
    team_name: str
    color: str | None = None
    plan_mode_required: bool = False

@dataclass
class TeammateState:
    status: str              # pending/running/idle/shutting_down/terminated
    current_task_id: str
    pending_request_id: str
    last_heartbeat_at: float
    plan_approved_event: asyncio.Event
    permission_approved_event: asyncio.Event
    ...
```

### 后台队友

- 队友在后台独立运行
- 空闲时自动发送 `idle_notification`
- 可自动从 `TaskList` 领取 pending 且无阻塞的任务
- 执行写操作前可在 PLAN/ASK 模式下发送 `plan_approval_request`
- 每 30 秒发送一次 `heartbeat`
- 主 Agent（leader）自动读取邮箱

## 四、邮箱 Mailbox

### 概念

Agent 之间通过文件邮箱通信，避免共享状态。

### 核心文件

[mailbox.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/collaboration/mailbox.py)：

```python
def write_mailbox(recipient, message: TeammateMessage, team_name) -> None: ...
def read_mailbox(owner, team_name, *, unread_only=True, mark_read=True) -> list[TeammateMessage]: ...
def has_unread(owner, team_name) -> bool: ...
def clear_mailbox(owner, team_name) -> None: ...
def broadcast_mailbox(sender, message, team_name, exclude=None) -> None: ...
```

### 消息类型

```python
@dataclass
class TeammateMessage:
    type: str                # plain/broadcast/idle_notification/shutdown_request/shutdown_response
                             # plan_approval_request/plan_approval_response
                             # permission_request/permission_response
                             # task_assignment/task_claimed/task_completed/heartbeat
    from_name: str
    timestamp: str
    text: str
    summary: str
    read: bool
    request_id: str
    approve: bool | None
    task_id: str
    task_subject: str
    color: str | None
    action: str              # permission_request 用
    tool: str                # permission_request 用
    args: dict | None        # permission_request 用
    status: str              # task_completed/heartbeat 用
    data: dict | None        # 扩展字段 / 未知字段兜底
```

消息字段采用驼峰序列化（`requestId`、`taskId`、`taskSubject`），`from_dict` 对未知字段收敛到 `data`，保证旧消息前向兼容。

### 自动注入

[query_loop.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/query_loop.py#L557-L601) 的 `_inject_teammate_notifications()`：

每轮工具执行后自动读取 leader 邮箱，注入到对话：

```python
def _inject_teammate_notifications(ctx):
    messages = read_mailbox("team-lead", team_name, unread_only=True, mark_read=True)
    if messages:
        lines = ["[以下来自团队队友的状态更新]"]
        for msg in messages:
            if msg.type == "idle_notification":
                lines.append(f"- {msg.from_name}: {msg.summary}")
            elif msg.type == "task_claimed":
                lines.append(f"- {msg.from_name}: 领取任务 #{msg.task_id} {msg.task_subject}")
            elif msg.type == "task_completed":
                lines.append(f"- {msg.from_name}: [{msg.status}] {msg.summary}")
            elif msg.type == "plan_approval_request":
                lines.append(f"- {msg.from_name}: 请求审批计划 (request_id={msg.request_id})\n  计划: {msg.text[:200]}")
            elif msg.type == "permission_request":
                lines.append(f"- {msg.from_name}: 请求权限 (request_id={msg.request_id})\n  操作: {msg.action} | 工具: {msg.tool}")
            elif msg.type == "shutdown_response":
                lines.append(f"- {msg.from_name}: {'同意关闭' if msg.approve else '拒绝关闭'}")
            elif msg.type == "heartbeat":
                continue  # 心跳不渲染，仅内部更新健康时间戳
        ctx.messages.append(Message(role="user", content=[TextContent(text="\n".join(lines))]))
```

**为什么自动注入**：leader 无需 Sleep 轮询或手动检查，每轮工具执行后自动"看到"队友动向。

## 五、共享任务列表 TaskList

### 核心文件

[task_list.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/collaboration/task_list.py)：

```python
class TaskList:
    def create(self, subject, description="", *, owner=None, ...) -> str: ...
    def read(self, task_id: str) -> TodoTask | None: ...
    def list_all(self) -> list[TodoTask]: ...
    def update(self, task_id, *, status=None, owner=None, add_blocks=None, add_blocked_by=None, ...) -> TodoTask | None: ...
    def delete(self, task_id: str) -> TodoTask | None: ...
    def get_available_tasks(self) -> list[TodoTask]: ...
    def set_hooks(self, *, on_completed=None, on_deleted=None, on_owner_changed=None) -> None: ...
```

### Task 结构

```python
@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str = "pending"       # pending/in_progress/completed/deleted
    owner: str | None = None      # 负责人
    blocks: list[str] = []        # 此任务阻塞的任务 ID
    blocked_by: list[str] = []    # 阻塞此任务的任务 ID
    active_form: str | None = None
    metadata: dict | None = None
    created_at: float
    updated_at: float
```

### 工具

| 工具 | 功能 |
|---|---|
| `TaskCreate` | 创建任务 |
| `TaskGet` | 获取详情 |
| `TaskList` | 列出任务 |
| `TaskUpdate` | 更新状态 / owner / 依赖 |
| `TaskStop` | 终止后台 teammate |
| `TeamStatus` | 查看团队状态概要 |

### 生命周期回调

`TaskList.set_hooks()` 支持三个回调：

- `on_completed(task)`：任务完成时触发，用于向 leader 发送 `task_completed` 通知
- `on_owner_changed(task, old_owner)`：owner 变更时触发
- `on_deleted(task)`：任务删除时触发

在 [subagent_tool.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/collaboration/subagent_tool.py) 中，后台队友创建时会绑定 `on_completed`，使任务完成后自动通知 leader。

## 六、队友自主任务领取

后台 teammate 进入空闲状态后，会定期扫描共享 `TaskList`：

```python
if waiting_for_more_work:
    for _ in range(40):
        if _ % 10 == 0:
            claimed = self._claim_available_task(parent_ui)
            if claimed:
                waiting_for_more_work = False
                break
        await asyncio.sleep(0.5)
```

`_claim_available_task`：

```python
def _claim_available_task(self, parent_ui) -> bool:
    available = self._task_list.get_available_tasks()
    for task in available:
        if task.owner:
            continue
        updated = self._task_list.update(
            task.id, status="in_progress", owner=self._identity.agent_name
        )
        if updated:
            self._state.current_task_id = task.id
            self._state.pending_user_messages.append(f"你领取了任务 #{task.id}: {task.subject}")
            write_mailbox("team-lead", make_task_claimed(...), team_name)
            return True
    return False
```

并发安全依赖 `TaskList.update` 的文件锁；claim 后若返回 `None` 说明已被别人领走。

## 七、计划/权限审批

teammate 的 system prompt 中明确要求：在 PLAN/ASK 权限模式下，执行写操作前必须先向 `team-lead` 发送 `plan_approval_request`，收到 `plan_approval_response` 批准后才能执行。

审批等待逻辑：

```python
async def request_plan_approval(self, plan_text, timeout=60.0) -> tuple[bool, str]:
    request_id = self._reset_approval_events()
    write_mailbox("team-lead", make_plan_approval_request(...), team_name)
    return await self._wait_for_approval(
        request_id, self._state.plan_approved_event,
        self._state.plan_rejected_event, timeout
    )
```

leader 收到注入的审批请求后，调用 `SendMessage type=plan_approval_response` 回复，teammate 在 `_handle_message` 中设置对应事件并继续执行。

## 八、生命周期与终止

### 心跳保活

teammate 每 30 秒发送一次 `heartbeat` 消息到 `team-lead`，便于 `TeamStatus` 判断队友是否"失联"。

### 进程内注册表

[teammate_registry.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/collaboration/teammate_registry.py) 保存 `(team_name, agent_name) -> TeammateRunner` 映射，供 `TaskStopTool` 查找并直接 `shutdown`。teammate 终止时在 `finally` 块中注销。

### TaskStop 工具

[agent/tools/collaboration/task_stop.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/collaboration/task_stop.py)：

```python
async def call(self, args, ctx):
    # 1. 优先通过注册表直接 shutdown
    runner = self._registry.get(team_name, name)
    if runner is not None:
        await runner.shutdown(reason=reason)
    else:
        # 2. 兜底：发送 shutdown_request 到其邮箱
        write_mailbox(name, make_shutdown_request(...), team_name)
    # 3. 更新成员状态
    self._mgr.mark_member_active(team_name, name, False)
```

### 关机协议

leader 向 teammate 发送 `shutdown_request`，teammate 回复 `shutdown_response` 后退出主循环，并在 `finally` 中发送最后一条 `idle_notification`（terminated）。

## 九、完整协作流程

```
用户: "帮我审查 src/ 目录的代码并运行测试"

主 Agent (leader):
  1. 调用 TeamCreate 创建 "code-review" 团队
     - reviewer: 代码审查员 (FileRead, Grep)
     - tester: 测试工程师 (Bash, FileWrite)

  2. 调用 TaskCreate 创建任务
     - task-1: "审查 src/ 代码" (pending)
     - task-2: "运行测试" (pending, blockedBy=[task-1])

  3. 调用 Agent run_in_background=true 启动 reviewer/tester

  4. 队友后台执行...
     - reviewer 空闲 → 自动领取 task-1
     - reviewer 完成 task-1 → task-2 解除阻塞
     - tester 空闲 → 自动领取 task-2

  5. 主 Agent 继续其他工作

  6. reviewer 完成 → 邮箱发 task_completed
     → _inject_teammate_notifications 注入:
       "[reviewer: 完成任务 #1]"

  7. tester 完成 → 邮箱发 task_completed
     → 注入: "[tester: 完成任务 #2]"

  8. 主 Agent 汇总结果回复用户

  9. 调用 TaskStop 终止后台 teammate（或发送 shutdown_request）
```

## 十、Plan 模式

### 工具

| 工具 | 功能 |
|---|---|
| `EnterPlanMode` | 进入规划模式（只读） |
| `ExitPlanMode` | 退出规划模式 |

### 行为

进入 plan 模式后：
- 权限切为 `PLAN`（只放行只读工具）
- AI 只能分析/规划，不能修改
- 退出后恢复正常权限

## 十一、设计取舍

| 决策 | 选择 | 理由 |
|---|---|---|
| 子代理 | 独立 QueryLoop | 上下文隔离 |
| 通信 | 文件邮箱 | 避免共享状态 |
| 任务跟踪 | 共享 TaskList | 团队可见 |
| 通知注入 | 自动 | leader 无需轮询 |
| 后台执行 | in-process asyncio | 不阻塞主 Agent，实现简单 |
| 任务领取 | 自主领取 + 文件锁 | 减少 leader 负担，保证并发安全 |
| 终止 | 注册表 + shutdown_request 兜底 | 正常和异常场景都能收尾 |
| 心跳 | 30 秒文件邮箱 | 低成本保活，无需额外协议 |
