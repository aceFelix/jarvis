# /talk 工具混合方案：少量直带 + 委派后台全量 Agent（草案）

> 状态：**待审核，未实施**（2026-11-26）
> 作者：aceFelix　审阅：用户
> 关联：[talk-voice-layering.md](talk-voice-layering.md)、
> [roadmap/voice/PLAN-future-realtime-voice-upgrade.md](../../roadmap/voice/PLAN-future-realtime-voice-upgrade.md)、
> 方案对比调研 [research/voice-agent-collaboration-options.md](../../research/voice-agent-collaboration-options.md)

## 1. 背景与结论

`/talk` 的实时模型本身支持 Function Calling。问题不在“能不能调”，而在**把多少
schema 塞进实时会话**：

- 默认 `tools_mode="builtin"` 只注册 2 个工具（时间、退出），延迟最低；
- `tools_mode="all"` 会把默认注册表 50 个 + MCP 工具全量注册；
- 2026-10 实机探针：2 个 schema 建响应 0.67s，294 个 3.45s，450 个 4.89s；
  该延迟与响应救援（1.8s 补发）、server_vad 尾音重检叠加，会形成
  `response.done[cancelled]` 死循环（“说完话半天不回复”）；
- `/talk` 的 `execute_tool` 固定 `permission_mode="yolo"`，全量注册等于把
  `Bash` / `FileWrite` / `SendEmail` 等执行面直接交给实时模型。

**本方案结论**：不做全量注入。改为

1. **少量直带工具**：只读、低风险、结果短、一次调用能答完（≤5 个 schema）；
2. **一个委派入口** `delegate_task`：把多步/重工具任务交给后台全职 Agent
   （QueryLoop + 全量 ToolRegistry + MCP + 正常权限策略），结果再回到语音播报。

实时模型始终只看到 5~6 个 schema，延迟与工具总数解耦；同时“所有工具”都能用。

## 2. 目标与非目标

### 目标

- 星期五可以直接完成：报时/日期、退出、定位、系统健康等一句话查询；
- 复杂任务（读代码、改文件、跑命令、搜网页后整理、MCP 工具）通过
  `delegate_task` 交给后台 Agent 完成，并用口语播报结果；
- 委派执行**不继承 yolo**，高风险操作按正常策略拒绝或回报，不静默执行；
- 终端 `/talk` 与桌面全双工共用同一引擎，行为一致；
- 配置可关闭委派，关闭时行为完全回到当前 builtin。

### 非目标（本次不做）

- 不再启用 `tools_mode="all"`；
- 不做 `/talk` 待机 + 唤醒词（见 roadmap L1/L2，另行立项）；
- 不做按轮 tool-RAG 动态选工具；
- 不新增桌面 UI 面板（复用现有 `standby/speaking/listening` 状态与文本气泡）；
- 不改变现有 2 个内置工具的行为与退出意图门。

## 3. 架构总览

```text
用户语音
  │
  ▼
RealtimeEngine（/talk 会话，DashScope WS）
  ├─ 直带工具（≤5 schema）── 本地同步执行
  │    get_current_time / end_conversation / Location / SystemHealth
  │
  └─ delegate_task（1 schema）
       │  立即回执：「收到，我交给后台去办」
       ▼
  DelegateRunner（后台，单飞）
       ├─ QueryLoop + ToolRegistry（默认 50 + MCP）
       ├─ ToolContext：workdir / 正常 permission_mode / ui 捕获
       ├─ abort_event（用户打断/退出可取消）
       └─ 超时 120s
            │ 完成后
            ▼
  结果队列（等 response.done 且用户未说话）
       ├─ conversation.item.create(role=system, 摘要+播报指令)
       └─ response.create → 星期五口语播报 → 完整结果落日志
```

分层约束：`agent/talk` **不得 import `agent/voice`**（见
[talk-voice-layering.md](talk-voice-layering.md)）。委派执行器放在
`agent/talk/` 内，复用 `agent/core/query_loop.py` 与 `agent/core/tool.py`；
需要 MCP 工具时复用启动时已加载的注册表，不重新连 MCP。

## 4. 直带工具集

### 4.1 建议默认（4 个）

| 工具 | 类别 | 结果形态 | 风险 |
|---|---|---|---|
| `get_current_time` | 内置 | 短 JSON | 无 |
| `end_conversation` | 内置 | 回调 | 有退出意图门 |
| `Location` | 注册表 | 短文本 | 低（可能联网） |
| `SystemHealth` | 注册表 | 结构化短文本 | 低（可能调 PowerShell） |

### 4.2 可选（P1 后评估）

- `WebSearch`：一句话搜索能直接答，但耗时 2~5s、返回长文本，容易让语音长时间静默；
  首版建议**不放**，交给委派，由后台整理成一句话再播。
- `ConfigList` / `ConfigExplain`：只读，但问答上下文偏重，首版不放。

### 4.3 数量约束

直带 schema 总数（含委派）**≤ 6**。新增直带工具前必须先量一次建响应延迟，
超过 1.5s 即视为不合格。

## 5. `delegate_task` 工具契约

```json
{
  "name": "delegate_task",
  "description": "把需要多步操作、文件读写、命令执行、网页检索整理或实时模型无法直接完成的任务，交给后台全职管家执行；执行完会用一句话把结果告诉你。能直接回答的简单问题不要调用本工具。调用时先用一句口语告诉用户『收到，我去办』。",
  "parameters": {
    "type": "object",
    "properties": {
      "task": {"type": "string", "description": "完整、可独立执行的任务描述，包含目标与必要上下文"},
      "want_spoken_summary": {"type": "boolean", "description": "是否需要在完成后主动语音播报结果，默认 true"}
    },
    "required": ["task"]
  }
}
```

执行语义：

- 工具立即返回 `{"result": "accepted", "task_id": "..."}`，实时模型据此说一句回执；
- 同一时刻只允许一个委派（单飞）；已有任务时返回 `{"result": "busy"}`，
  模型应说“上一个任务还在跑，稍等”；
- 任务完成后由引擎注入结果并播报；失败/超时如实播报原因，不编造结果；
- 用户说“退下/结束”、ESC 或会话结束时取消后台任务（`abort_event.set()`），
  已产生的部分结果丢弃；普通打断/“停一下”只停播报，**不取消任务**（任务继续跑，
  完成后按门控播报）。

## 6. 后台执行器（DelegateRunner）

### 6.1 组成

| 组件 | 职责 |
|---|---|
| 任务队列 | 单飞；记录 `task_id`、开始时间、状态 |
| QueryLoop | 复用 `agent/core/query_loop.py`；provider 取当前 LLM 配置 |
| ToolRegistry | `build_default_registry()` + 启动时加载的 MCP 工具 |
| ToolContext | `workdir` 取会话 workdir；权限用正常策略，**不是 yolo** |
| UI 适配器 | 捕获中间输出，不直接写终端；只保留最终回答 |
| 超时/取消 | `asyncio.wait_for` + `abort_event`；默认 120s |
| 结果整理 | 完整结果写日志；播报用摘要（≤400 字，或由后台 Agent 自生成一句总结） |

### 6.2 权限策略（关键）

语音里没有交互式确认入口，因此委派任务：

- `auto` / `allow` 级：正常执行；
- `ask` 级：**直接拒绝并回报**“这一步需要你在文本模式确认”，不静默放行；
  实现上给 Runner 的 UI 适配器把 `ask_user()` 固定返回拒绝，并记录被拒原因；
- `deny` 级：拒绝并回报原因；
- 绝不使用 `permission_mode="yolo"`；模型口头“用户已同意”不作为授权依据。

默认模式下 FileWrite/FileEdit/Bash 都会命中 `ask` → 被拒，这是首版的安全基线；
若确实要开放写操作，需在后续阶段引入显式的语音二次确认或白名单。

### 6.3 执行线程模型

- 实时引擎是 asyncio 事件循环；QueryLoop 也是 async，但工具执行可能阻塞 →
  在**独立后台线程**中运行（该线程自己 `asyncio.run` 持有独立事件循环），
  不阻塞音频收发；
- 结果经 `queue.Queue` 交回引擎协程，由发送协程按门控注入；
- 同一任务内工具调用沿用 QueryLoop 的并行/串行策略，不在本方案改动。

## 7. 结果注入与播报状态机

### 7.1 协议可行性（已核实）

DashScope 实时文档明确：

- `conversation.item.create` 支持 `item.type=message`，`role` 可为
  `system` / `user` / `assistant`；
- `response.create` 在“等待用户下一轮输入”时允许手动触发；**当前有响应生成时
  不允许重复触发**；用户 turn 内（`speech_started` ~ `response.done`）不允许；
- 仅 `turn_detection` / `input_audio_format` 限制在 IDLE 修改；本方案不碰它们。

### 7.2 发送门控

结果到达后不立即发，进入待发队列，满足全部条件才发送：

1. `self._response_active == False`（无进行中的响应）；
2. `self._ai_speaking == False` 且用户当前未在说话（`_user_speaking_server=False`）；
3. 会话仍 `_running` 且未处于待机/退出流程。

若 30s 内条件一直不满足（用户持续说话/响应不断），丢弃播报、只落日志，
避免“排队到天荒地老突然冒出一句”。

### 7.3 注入内容

```json
{"type":"conversation.item.create",
 "item":{"type":"message","role":"system",
         "content":[{"type":"input_text",
           "text":"后台任务已完成。请用一两句口语向用户播报结果，不要念原始输出。结果摘要：……"}]}}
{"type":"response.create","response":{"modalities":["audio","text"]}}
```

- 摘要由执行器生成（优先让后台 Agent 输出一句 `summary`；没有则截断）；
- 完整结果写 `~/.jarvis/logs/talk-delegate/<task_id>.log`，供用户事后查看；
- `content` 的元素类型按厂商文档（示例为 `input_text`；system 角色实测时确认）；
- 是否在下一轮用 `conversation.item.delete` 清掉系统项：**待审核问题 Q5**。

### 7.4 失败路径

| 场景 | 播报 |
|---|---|
| 超时 | “这个任务超时了，我先停下；要不要我换个办法？” |
| 权限拒绝 | “这一步需要你在文本模式确认，我在语音里不能直接做。” |
| 执行异常 | “后台执行失败了：<一句原因>。要我把详细日志留着吗？” |
| 用户取消 | 不播报，仅记录“任务已取消” |

## 8. 配置设计（字段名待实施时按现有域表落）

```toml
[realtime_talk]
# 直带工具白名单；留空 = 用默认四个
direct_tools = ["get_current_time", "end_conversation", "Location", "SystemHealth"]
# 委派开关：首版默认 false，P0/P1 验证通过后再改 true
delegate_enabled = false
# 委派超时（秒）
delegate_timeout_s = 120
# 单次播报摘要上限（字符）
delegate_summary_chars = 400
```

对应 `Settings` 字段（拟）：`realtime_direct_tools: tuple[str, ...] = ()`、
`realtime_delegate_enabled: bool = False`、`realtime_delegate_timeout_s: float = 120`、
`realtime_delegate_summary_chars: int = 400`。

需要同步：`settings_domains.py` 的 `[realtime_talk]` 映射、`catalog.py` 说明、
两份 `settings.example.toml`、`config-docs/voice-setup.md`。

## 9. 时序（成功路径）

```text
用户：帮我把 voice_barge 的关流顺序改一下
Friday：收到，我去办。                         ← delegate_task 立即回执
  └─ DelegateRunner：QueryLoop 读取文件 → 修改 → 跑测试（60s）
       └─ 完成，生成 summary
引擎：等 response.done + 用户停顿
  ├─ conversation.item.create(system, "后台任务已完成…")
  └─ response.create
Friday：改好了，关流现在先 join 再 close，测试全过。
```

## 10. 测试计划

| 层 | 用例 |
|---|---|
| 单元 | 直带白名单过滤；delegate 单飞/busy；超时取消；权限 ask→拒绝回报；摘要截断 |
| 引擎 | 结果队列在 response 进行中不发送；用户说话时不发送；30s 过期丢弃；取消后不播报 |
| 协议（人工） | P0：手工注入 system item + response.create，确认不毒化、不重复响应、不打断用户 |
| 端到端（终端） | 报时直答；委派查文件并播报；委派改文件被 ask 拒绝并如实说明 |
| 桌面 | 同口径跑一遍，确认 `talk_status` 与气泡正常 |
| 回归 | 关闭 `delegate_enabled` 时行为与当前 builtin 完全一致（全量 pytest） |

## 11. 实施阶段与提交拆分（审核通过后）

1. **P0 协议验证**：临时脚本/分支验证 system item + response.create 时序，
   记录事件序列与异常；不改正式行为。
2. **P1 委派闭环**：`agent/talk/delegate.py`（Runner）+ `realtime_tools` 新工具 +
   引擎结果队列与取消；配置字段；终端用例。
3. **P2 直带工具与桌面**：白名单过滤、`engine_talk` 透传、桌面验证。
4. **文档与索引**：architecture 06、guide/voice、config-docs、CHANGELOG、AOCI。

每阶段独立提交、可单独回滚；`delegate_enabled` 默认 false，验证通过后再改默认。

## 12. 风险与未决问题

| # | 风险/问题 | 当前倾向 |
|---|---|---|
| R1 | system item 进入实时上下文，可能影响后续闲聊 | 先保留为上下文；若干扰明显，下一轮用 `conversation.item.delete` 清理（Q5） |
| R2 | 模型滥用委派，每句话都转后台 | 工具描述强调“能直接回答的不要委派”；监控委派率 |
| R3 | 结果播报打断用户说话 | 严格门控 + 30s 过期丢弃 |
| R4 | 委派执行写文件与用户语音指令冲突 | 单飞 + 可取消；执行前在回执里说明要做什么 |
| R5 | MCP 工具未复用启动时注册表，导致“全量”名不副实 | Runner 复用同一 registry 实例 |
| R6 | `response.create` 与 server_vad 自动响应竞态 | P0 专项验证；必要时在注入前等一个 `response.done` |
| R7 | 长任务期间用户退出语音 | 取消任务并记录，不后台静默跑 |

## 13. 待审核问题（请拍板）

- **Q1 直带集合**：默认四个（时间/退出/Location/SystemHealth）是否够？
  要不要首版就带 `WebSearch`？
- **Q2 委派默认值**：首版 `delegate_enabled` 默认 `false`（验证后开），
  还是直接 `true`？
- **Q3 结果播报**：任务完成即主动播报（推荐），还是等用户下一次开口再顺带说？
- **Q4 ask 级权限**：拒绝并回报（推荐），还是尝试做“语音二次确认”流程？
- **Q5 会话清理**：注入的 system 结果留在上下文，还是下一轮删除？
- **Q6 任务范围**：默认模式下写操作（FileWrite/Bash）会因 `ask` 被拒；
  首版就接受这个限制，还是为委派提供“语音二次确认/写操作白名单”？
