# 02 - Agent 运行时

Agent 运行时是 J.A.R.V.I.S 的"大脑"，负责驱动整个对话循环、调度工具执行、管理上下文。

## 一、核心组件

| 组件 | 文件 | 职责 |
|---|---|---|
| **QueryLoop** | [query_loop.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/query_loop.py) | Agent 主循环：用户输入→LLM流→工具调用→回灌→再调 |
| **ToolOrchestrator** | [orchestrator.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/orchestrator.py) | 工具编排：权限校验、并发分组、执行、结果收集 |
| **Tool 协议** | [tool.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/tool.py) | Tool 基类、ToolRegistry、PermissionMatcher |
| **Message** | [message.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/message.py) | 消息/内容块数据结构 |
| **ToolContext** | [context.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/context.py) | 工具执行上下文 + UI 协议 |
| **ToolPairing** | [tool_pairing.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/tool_pairing.py) | 配对不变量：`tool_use` ↔ `tool_result` 补齐/去孤儿 |
| **TeamNotify** | [team_notify.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/team_notify.py) | 多 Agent 邮箱同步注入 |
| **Hooks** | [hooks.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/hooks.py) | 钩子系统（扩展点） |

## 二、QueryLoop 主循环

### 核心逻辑

```
while True:
    events = await provider.stream(messages, tools)
    assistant_msg = accumulate(events)         # 文本 + tool_use
    messages.append(assistant_msg)
    if not assistant_msg.has_tool_use:
        break                                   # 模型说完了
    tool_results = await orchestrator.execute(assistant_msg.tool_uses)
    messages.append(user(tool_results))        # 工具结果作为新 user 消息
    # 继续下一轮，让模型看到工具结果
```

这就是 **ReAct（Reasoning + Acting）循环**：Think → Act → Observe → Think → ...

### 配对不变量（重要约束）

LLM 协议（Anthropic / OpenAI 一致）要求 `tool_use` 与 `tool_result` **双向配对**：

- 每个 `assistant.tool_use` 必须在**紧随的下一条消息**里有 `tool_result`
- 每个 `tool_result` 必须有对应的 `tool_use`

违反的后果不是"这一轮报错"，而是**整个请求在受理前被 API 拒收**（报 `tool_use ids were found without tool_result blocks`）。由于残缺片段已经落进会话历史，**该会话此后每次请求都失败**——重试、换措辞、换模型都无效。工具**执行失败**反而是安全的（走正常路径，会产出 `is_error=True` 的结果）；真正中毒的只有"调用已记录、结果未写入"这半截状态。

因此产生了四条必须在源头堵住的悬空路径：

| 路径 | 处理方式 |
|---|---|
| 工具执行中被用户中断（`CancelledError`） | 补 `REASON_ABORTED` 占位结果后再 break |
| 工具执行抛非取消异常 | 补 `REASON_EXEC_ERROR` 占位结果 + UI 报错 + `stopped_reason="tool_error"` 后 break |
| 输出被截断（`length` / `max_tokens`）且含 `tool_use` | 补 `REASON_TRUNCATED` 占位结果，与续写提示合并成**一条** user 消息 |
| 编排器调度缺项 | 结果对齐改为"缺失补占位"，输出与输入严格等长 |

另有一道**出口兜底**：两个 provider 的消息转换入口（`_messages_to_openai` / `_messages_to_anthropic`）都会先跑 `ensure_tool_pairing()`，兜住主循环之外的所有 LLM 请求路径（压缩、标题生成、realtime 语音等），并能自愈已中毒的旧会话。

详见 [tool_pairing.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/tool_pairing.py) 与 [docs/fixlogs/dangling-tool-use-fix.md](../fixlogs/dangling-tool-use-fix.md)。

### `ensure_tool_pairing()` 关键设计

```python
def ensure_tool_pairing(messages: list[Message]) -> list[Message]:
    # 健康历史（无悬空/无孤儿）→ 返回同一个列表对象（零拷贝）
    # 有违规 → 重建副本：
    #   · 悬空 tool_use → 注入 is_error=True 占位结果（文案说明该调用已失效）
    #   · 孤儿 tool_result → 丢弃（协议同样不允许）
    #   · 重复 tool_result → 只留第一条
    #   · 占位结果优先并入紧随其后的 user 消息（块前置），否则另起一条 user
    #   · 重建时保留原 id / timestamp
```

**为什么只改发送副本、绝不就地改历史**：冻结前缀被 `cache_control` 锁定，就地改会破坏 LLM 服务端前缀缓存。健康历史返回同一对象、`id`/`timestamp` 不变，前缀缓存不受影响。

### 分层上下文（冻结前缀 + 滑动窗口）

[LayeredContext](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/layered_context.py) 是 P 系列性能改进后 QueryLoop 的上下文管理器：

- **冻结区**：压缩后的摘要，一旦锁定永不修改 → 后续请求前缀稳定 → LLM 缓存持续命中
- **滑动窗口**：最近 N 条原始消息，自然追加增长 → 前缀缓存继续命中
- **冻结触发**：活跃窗口 token 超阈值 → 一次性压缩合并进冻结区 → 窗口重置
- **反应式压缩**：API 报 context too long → `compact_reactive()` 强制压缩（冻结前缀不变）

**为什么替换原水位方案**：原方案"原地篡改历史消息"会破坏 LLM 服务端前缀缓存（每次请求前缀都变）。
分层上下文让压缩后的摘要固定下来，长对话也能保持缓存命中（省 token、降延迟）。

### 运行流程详解

[QueryLoop.run()](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/query_loop.py#L231-L504) 的完整流程：

1. **Hook: USER_PROMPT** — 钩子可修改用户输入
2. **追加用户消息**（文本 + 可选图片 + **Skill 按需加载**）
   - 调用 `match_skills_for_message(user_text, workdir)` 检查触发词
   - 命中的 skill 完整正文作为上下文前置到当前用户消息
   - 详见 [10-扩展生态 - Skill 按需加载](10-扩展生态.md#三skill-技能包按需加载机制)
3. **构建 LayeredContext** — 冻结 + 窗口分离：
   - 窗口 token 超阈值 → 冻结（压缩 + 锁定前缀）
   - 工具结果折叠（仅活跃窗口，保留最近 4 个）
   - 图片淘汰（仅活跃窗口，保留最新一张）
4. **进入 ReAct 循环**（最多 max_iterations 轮）：
   - 调用 `_stream_once()` 获取 LLM 流式响应（发冻结区 + 窗口快照）
   - 累积成 assistant message（ThinkingContent + TextContent + ToolUseContent）
   - **空回复过滤**：assistant 消息内容为空 → 提示"模型返回了空回复"并结束本轮，不污染对话历史
   - 若无 tool_use → 本轮结束
   - 若有 tool_use → 调用 `orchestrator.execute_calls()` 执行
   - 工具结果作为新 user 消息回灌到窗口
   - 多 Agent 邮箱自动同步（`team_notify.inject_teammate_notifications()`）
5. **异常处理**：
   - `ProviderError` 且包含 context 超长关键词 → `layered.compact_reactive()` 后重试
   - **网络错误自动重试**：每轮最多重试 1 次（1.5s 退避）
   - Provider 故障转移：切到备选厂商模型（重建 provider 时同步思考模式覆盖状态）
   - `stop_reason="length"` → 输出截断，自动续写
   - **工具执行被取消 / 抛异常** → 先补齐 `tool_result` 占位再退出，确保历史不留悬空调用（见「配对不变量」）
6. **Hook: ASSISTANT_RESPONSE** — 钩子可后处理响应

### Skill 按需加载集成

[QueryLoop.run()](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/query_loop.py) 在追加用户消息时集成 skill 触发词匹配：

```python
# 追加用户消息（文本 + 可选图片）
content_blocks: list[ContentBlock] = [TextContent(text=user_text)]

# ── Skill 按需加载：触发词匹配 ──
# system prompt 只含 skill 摘要（省 60k+ token），
# 用户消息匹配到触发词时，把完整正文作为上下文附加到当前消息。
try:
    from agent.core.extensions.skills import match_skills_for_message
    skill_content = match_skills_for_message(user_text, ctx.workdir)
    if skill_content:
        content_blocks.insert(0, TextContent(text=skill_content))
except Exception:
    pass  # skill 加载失败不影响主流程

if images:
    content_blocks.extend(images)
ctx.messages.append(Message(role="user", content=content_blocks))
```

**关键点**：
- skill 正文作为 user 消息的**前置上下文**，不污染 system prompt
- skill 加载失败不影响主流程（try/except 兜底）
- 每轮对话只在该轮注入匹配的 skill，不会持续占用 token

### 单轮流式推理 _stream_once()

[_stream_once()](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/query_loop.py#L526-L614) 处理 LLM 流式事件：

```python
async for event in self._provider.stream(...):
    if isinstance(event, ThinkingDelta):
        # 深度思考：reasoning_content 先于 content 到达
        thinking_buf += event.text
        ctx.ui.assistant_thinking(event.text)
    elif isinstance(event, TextDelta):
        # 正式回复文本
        text_buf += event.text
        ctx.ui.assistant_text(event.text)
        # 语音模式：文本增量同时喂给 TTS
        if ctx.on_assistant_text:
            ctx.on_assistant_text(event.text)
    elif isinstance(event, ToolCall):
        # 工具调用（flush 之前的文本/思考）
        content_blocks.append(ToolUseContent(id, name, input))
    elif isinstance(event, Stop):
        # 响应结束
        pass
```

**事件顺序**：`ThinkingDelta* → TextDelta* → ToolCall* → Stop`

### 防护机制

- **max_iterations**（默认 25）：防止无限循环
- **abort_event**：用户 Ctrl+C 中断（优雅退出本轮并重置事件）
- **空回复保护**：空 assistant 消息不加入历史，避免污染后续请求
- **单轮失败不炸主循环**：错误回灌给模型让它自我修正
- **网络重试**：网络错误每轮自动重试 1 次
- **故障转移**：`_try_failover()` 切到备选厂商（`_thinking_override` 同步到新 provider）
- **手动切模型**：`switch_model()` 就地换 `_provider` / `_model`（REPL `/model` 与工作台/桌面壳
  `models.select` 共用同一链路：不重建 QueryLoop，会话 token 累计 / 思考模式覆盖 / 消息上下文
  全保留，旧 provider 交调用方 `close()`；见 [07-UI层.md](07-UI层.md) 「模型热切换」）

## 三、ToolOrchestrator 工具编排器

[ToolOrchestrator](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/orchestrator.py#L35-L374) 负责把模型返回的 tool_use 批量调度执行。

### 执行流程

1. **解析 + 权限校验**：
   - 工具不存在 → 返回 `is_error=True` 的结果
   - 权限校验 → DENY 直接拒绝，ASK 走 UI 问用户
2. **并发安全分组**：
   - `is_concurrency_safe=True` → 并行执行（信号量限流，默认 5 并发）
   - `is_concurrency_safe=False` → 串行执行（避免竞争）
3. **执行 + 结果收集**：
   - Hook: TOOL_BEFORE（可拒绝/修改输入）
   - **错误自愈包装**：`ToolRecoveryExecutor.execute()` 按错误分类自动重试/降级/提示用户
   - 调用 `tool.call(input, ctx)`
   - 异常封装为 `is_error=True` 的 ToolResultContent
   - 超长结果截断（默认 20000 字符）+ 落盘持久化
   - Hook: TOOL_AFTER
   - Hook: FILE_CHANGED（文件类工具触发）
4. **结果对齐（严格等长）**：按输入顺序逐项取结果，**缺失项补占位**（`REASON_ORPHAN`）——输出长度与输入严格一致，不会让模型收到"少了几条工具结果"的历史

### 关键设计

```python
# 并发安全分组
safe = [p for p in pending if p.tool.is_concurrency_safe(p.tool_use.input)]
unsafe = [p for p in pending if not p.tool.is_concurrency_safe(p.tool_use.input)]

# 串行组顺序执行
for p in unsafe:
    content = await self._run_one(p, ctx)

# 并行组用信号量限流
sem = asyncio.Semaphore(self._max_concurrency)
async def run_safe(p):
    async with sem:
        return p.tool_use.id, await self._run_one(p, ctx)
gathered = await asyncio.gather(*(run_safe(p) for p in safe))
```

**为什么这样设计**：文件读写等工具不能并行（会竞争），但 web 请求、glob 搜索等可以。让工具自己声明并发安全性，编排器据此分组。

### 结果截断与落盘

```python
max_chars = getattr(tool, "max_result_chars", 20_000)
if len(content_str) > max_chars:
    # 超大结果落盘
    persisted_path = _persist_result(content_str, tu.name, tu.id, ctx)
    # 模型只收预览（前 500 + 后 500 字符）
    content_str = content_str[:500] + f"... [完整结果已保存到 {persisted_path}] ..." + content_str[-500:]
```

**为什么**：LLM 上下文有限，超大工具结果（如 grep 全仓库）会爆 token。落盘后模型知道去哪看完整结果。

## 四、Tool 协议

### Tool 基类

[Tool](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/tool.py#L30-L97) 是抽象基类，核心设计是 **fail-closed**：

```python
class Tool(abc.ABC):
    name: str = ""
    description: str = ""
    input_schema: JSONSchema = {}
    max_result_chars: int = 20_000

    @abc.abstractmethod
    async def call(self, args, ctx) -> ToolResult: ...

    # 安全属性（默认全 False = 最危险侧）
    def is_read_only(self, args) -> bool: return False
    def is_destructive(self, args) -> bool: return False
    def is_concurrency_safe(self, args) -> bool: return False

    # 权限（默认 ASK = 最安全）
    def check_permissions(self, args, ctx) -> PermissionResult:
        return PermissionResult.ask("no tool-specific permission rule")
```

**为什么 fail-closed**：新工具不显式声明安全，默认被当危险操作要求确认。防止意外破坏。

### ToolRegistry

[ToolRegistry](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/tool.py#L144-L190) 管理工具注册、查找、别名，并区分核心/延迟工具：

```python
registry.register(BashTool(), aliases=["Shell"])
registry.get("Bash")  # 或 registry.get("Shell") 通过别名
registry.all_core()      # deferred=False 的核心工具（每次请求都带）
registry.all_deferred()  # deferred=True 的延迟工具池（需 ToolSearch 发现）
```

### 工具注册顺序（延迟加载 + 缓存）

[build_default_registry()](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/tool.py#L193-L243) 装配默认工具集（**P-03 缓存**：`@lru_cache` 复用同一 Registry）：
1. **核心工具**（必装，deferred=False）：Bash/FileRead/FileEdit/FileWrite/Glob/Grep/Todo/AskUser/WebFetch/WebSearch/Location/SendEmail/DevServer
2. **GUI 工具**（可选，pyautogui 缺失则跳过，**deferred=True**）
3. **浏览器工具**（可选，playwright 缺失则跳过，**deferred=True**）
4. **摄像头工具**（可选，opencv 缺失则跳过，**deferred=True**）

动态注册（运行时）：
- `register_dynamic_tools()` — MCP + CLI-Anything harness 工具
- `register_subagent_tool()` — 子代理工具（deferred）
- `register_team_tools()` — 团队协作工具（deferred）
- `register_plan_tools()` — Plan 模式工具（deferred）
- `register_lsp_tool()` — LSP 代码工具（deferred）

**延迟加载（deferred）**：除核心工具外的工具不随每次请求发送完整 schema，模型需通过
`ToolSearchTool` 发现后才能调用（参考 Claude Code deferred tool loading），减少每次请求的 token 开销。

**为什么可选依赖静默跳过**：用户可能只装核心包，不应该因为缺 playwright 就无法启动。

## 五、消息数据结构

### ContentBlock 类型

[message.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/message.py) 定义 5 种内容块：

| 类型 | 用途 | 出现在 |
|---|---|---|
| `TextContent` | 文本回复 | user/assistant |
| `ThinkingContent` | 思维链思考 | assistant（ToolUseContent 之前） |
| `ToolUseContent` | 工具调用请求 | assistant |
| `ToolResultContent` | 工具执行结果 | user（回灌给模型） |
| `ImageContent` | 图片（base64） | user/assistant/tool_result |

```python
ContentBlock = ThinkingContent | TextContent | ToolUseContent | ToolResultContent | ImageContent
```

### Message 结构

```python
@dataclass
class Message:
    role: Literal["user", "assistant", "system"]
    content: list[ContentBlock]
    id: str       # UUID
    timestamp: float
```

一条 assistant 消息可能包含：`[ThinkingContent, TextContent, ToolUseContent, ToolUseContent]`（思考+回复+两个工具调用）

**为什么用 dataclass 不用 pydantic**：内部数据结构，不需要序列化校验开销。给 LLM 的格式转换在 llm/ 层完成。

## 六、ToolContext 运行时上下文

[ToolContext](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/context.py#L79-L117) 是工具执行时的上下文：

```python
@dataclass
class ToolContext:
    workdir: str                              # 工作目录
    messages: list[Message]                   # 对话历史（只读引用）
    abort_event: asyncio.Event                # 取消信号
    permission_mode: str = "default"          # 权限模式
    ui: UIProtocol | None = None              # UI 抽象
    extra: dict[str, Any]                     # 自由存储区
    on_assistant_text: Callable | None = None # 语音模式 TTS 回调
```

**设计要点**：
- 工具**不应直接修改** messages，应通过返回 `ToolResult.new_messages`
- `extra` 是自由存储区（如 `pending_images`、`_recent_files`）
- `clone_for_subagent()` 支持子代理场景克隆上下文

## 七、UIProtocol 协议

[UIProtocol](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/context.py#L22-L38) 是 UI 层的极简协议：

```python
class UIProtocol(Protocol):
    def assistant_text(self, text: str) -> None: ...
    def assistant_thinking(self, text: str) -> None: ...
    def tool_use(self, tool_name, tool_input, tool_use_id) -> None: ...
    def tool_result(self, tool_name, tool_use_id, content, *, is_error) -> None: ...
    def info(self, text: str) -> None: ...
    def warn(self, text: str) -> None: ...
    def error(self, text: str) -> None: ...
    def ask_user(self, prompt: str) -> str: ...
```

**为什么用 Protocol**：QueryLoop 和工具只依赖抽象，不依赖具体 Rich 实现。这让终端 CLI、Webview 窗口、甚至无头模式都能复用同一套 Agent 逻辑。

### RealtimeTalkUI 扩展协议

[RealtimeTalkUI](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/context.py#L41-L77) 在 UIProtocol 基础上扩展实时语音专用回调：

```python
class RealtimeTalkUI(UIProtocol, Protocol):
    def on_status(self, status: str) -> None: ...      # connecting/standby/listening/speaking
    def on_volume(self, level: float) -> None: ...     # 0.0~1.0
    def on_user_speaking(self, speaking: bool) -> None: ...
    def on_ai_speaking(self, speaking: bool) -> None: ...
    def on_user_transcript(self, text: str) -> None: ...
    def on_ai_transcript(self, text: str) -> None: ...
    def is_running(self) -> bool: ...
```

RichCLI 和 WebviewRealtimeTalkUI 都实现这个协议，让 `/talk` 能在终端和窗口两种 UI 下运行。

## 八、Hooks 钩子系统

[hooks.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/hooks.py) 提供扩展点，避免硬编码：

| 钩子事件 | 触发时机 | 可做 |
|---|---|---|
| `USER_PROMPT` | 用户输入后 | 修改输入 |
| `ASSISTANT_RESPONSE` | 响应完成后 | 后处理 |
| `TOOL_BEFORE` | 工具执行前 | 拒绝/修改输入 |
| `TOOL_AFTER` | 工具执行后 | 记录/分析 |
| `FILE_CHANGED` | 文件类工具后 | 通知/索引 |
| `ERROR` | 工具异常时 | 告警/重试 |

**为什么用钩子**：解耦。想加日志/监控/审计，注册钩子即可，不用改核心代码。

## 九、工具调用完整生命周期

```
用户输入 "帮我创建 hello.py"
    │
    ▼
QueryLoop.run()
    │
    ├─ 追加 user message
    ├─ 水位压缩检查
    │
    ▼
_stream_once()  →  LLM 流式响应
    │
    ├─ ThinkingDelta → "我需要用 FileWrite 工具..."
    ├─ TextDelta → "好的，我来创建..."
    ├─ ToolCall(name="FileWrite", input={"path":"hello.py","content":"..."})
    └─ Stop
    │
    ▼
assistant_msg = [ThinkingContent, TextContent, ToolUseContent]
    │
    ▼
ToolOrchestrator.execute_calls([tool_use])
    │
    ├─ registry.get("FileWrite")  → 找到工具
    ├─ checker.check(tool, input, ctx)
    │   ├─ tool.check_permissions() → ASK
    │   ├─ path_guard → safe
    │   ├─ mode (yolo) → ALLOW
    │   └─ 规则匹配 → 无命中
    ├─ Hook: TOOL_BEFORE
    ├─ tool.call(input, ctx)  → 执行写入
    ├─ Hook: TOOL_AFTER
    ├─ Hook: FILE_CHANGED
    └─ 返回 ToolResultContent(content="文件已创建")
    │
    ▼
messages.append(user([tool_result]))
    │
    ▼
继续下一轮 _stream_once()
    │
    ├─ TextDelta → "hello.py 已创建完成！"
    └─ Stop
    │
    ▼
无 tool_use → 本轮结束
```

## 十、设计取舍总结

| 决策 | 选择 | 理由 |
|---|---|---|
| 数据结构 | dataclass | 内部传递，无需 pydantic 校验开销 |
| 上下文管理 | LayeredContext 冻结前缀 | 压缩后前缀稳定 → LLM 缓存命中，省 token |
| 安全默认值 | fail-closed | 新工具默认危险，防止意外破坏 |
| 并发模型 | asyncio | IO bound（LLM API），asyncio 足够 |
| 工具并发 | 声明式 | 工具自己最懂自己能否并行 |
| 工具加载 | deferred 延迟 | 减少每次请求的 schema token 开销 |
| UI 抽象 | Protocol | 解耦，支持多种 UI 实现 |
| 扩展机制 | Hooks | 避免硬编码，解耦 |
| 错误处理 | 分类自愈 + 网络重试 | 工具错误自动恢复，网络抖动自动重试 |
| 结果截断 | 落盘+预览 | 防 token 爆炸，模型知道去哪看完整结果 |
| 工具结果缺失 | 补占位而非留空 | `tool_use`/`tool_result` 必须配对，悬空调用会让整个请求被拒收 |
