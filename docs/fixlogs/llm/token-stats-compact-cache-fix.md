# Token 统计与压缩命令修复复盘

> 关键词：/cost、/context、/compact、system prompt、缓存命中、asyncio.run、Anthropic 兼容端点、协议语义冲突、思考模式、thinking_delta

## 1. 问题现象

在测试 JARVIS 缓存命中机制时，连续发现 4 个独立但相互关联的 bug：

### 问题一：/cost 显示的"当前上下文 token"严重偏低

3 轮简单对话后 `/cost` 显示：

```
| 当前上下文 token（估算）   | 21              |
```

实际 system prompt 就有几千 token，21 明显只数了对话历史，漏了 system prompt。

### 问题二：/compact 命令导致 jarvis 崩溃退出

执行 `/compact` 直接报错退出：

```
RuntimeError: asyncio.run() cannot be called from a running event loop
RuntimeWarning: coroutine 'QueryLoop.compact_now' was never awaited
```

进程整个退出，无法继续使用。

### 问题三：DeepSeek 走 Anthropic 兼容端点时缓存命中率显示 0%

同一套对话场景，不同模型表现完全不同：

| 模型 | 端点 | 缓存命中率 |
|---|---|---|
| deepseek-v4-pro | OpenAI 兼容 | 98.0% ✅ |
| deepseek-v4-flash | Anthropic 兼容 | 0.0% ❌ |

flash 的 system prompt（63k token）应该大量命中缓存，0% 明显异常。

### 问题四：DeepSeek 走 Anthropic 兼容端点时思考模式关闭

同一模型 `deepseek-v4-flash`，不同端点表现不同：

| 端点 | 思考过程 |
|---|---|
| OpenAI 兼容 | ✅ 显示 `╭─ 💭 思考过程 ───` |
| Anthropic 兼容 | ❌ 无思考过程，直接输出回答 |

DeepSeek 文档明确说"思考模式默认打开"，但走 Anthropic 兼容端点时却没有思考。

## 2. 排查过程

### 阶段一：定位 /cost 漏算 system prompt（问题一）

沿 `/cost` 命令链路排查：

```python
# core_commands.py:_print_cost
est_tokens = estimate_tokens(messages)  # ← 只遍历 messages 列表
```

```python
# compactor.py:estimate_tokens
def estimate_tokens(messages: list[Message]) -> int:
    for msg in messages:
        for block in msg.content:  # ← 只数 messages 的 content 块
```

发现 `estimate_tokens` 只遍历 `messages` 列表的 `content` 块，而 JARVIS 的
system prompt 是独立字符串（通过 `ctx.system_prompt` 传递），**不在 messages 里**。

3 轮简短对话（"中国的首都是哪里"等）约 21 token，完美对上，证实只数了对话历史。

### 阶段二：定位 /compact 崩溃（问题二）

错误堆栈指向 `core_commands.py:201`：

```python
def _compact(ui, loop, ctx):
    ...
    ok = asyncio.run(loop.compact_now(ctx))  # ← 崩溃点
```

而 `dispatch_command` 是 `async def`，已运行在事件循环里：

```python
# router.py
async def dispatch_command(ctx, stripped):
    handler = COMMAND_HANDLERS.get(cmd)
    result = handler(ctx, stripped)  # ← 已在事件循环里
    if inspect.isawaitable(result):
        return await result
```

Python 3.10+ 不允许在已有事件循环里嵌套调用 `asyncio.run()`，直接抛 `RuntimeError`。
异常没被捕获，传到顶层导致进程退出。

### 阶段三：定位 flash 缓存命中 0%（问题三）—— 最复杂

#### 初步排查

对比 pro 和 flash 的 usage 字段差异：

| 端点 | input_tokens 语义 | 缓存命中字段 |
|---|---|---|
| OpenAI 兼容（pro） | **含**缓存命中 | `prompt_cache_hit_tokens` |
| Anthropic 兼容（flash） | **不含**缓存命中 | `cache_read_input_tokens` |

#### 追踪 cache_policy

`anthropic_provider.py` 里 `self.name` 通过 `_derive_name` 根据 base_url 推断，
flash 走 DeepSeek 的 Anthropic 兼容端点 → `self.name = "deepseek"`。

查 `CACHE_POLICIES["deepseek"]`：

```python
"deepseek": CachePolicy(
    mode="implicit",
    hit_field="prompt_cache_hit_tokens",           # ← Anthropic 响应没这字段
    alt_hit_fields=("cache_read_input_tokens",),   # ← 走备选
),
```

主字段 `prompt_cache_hit_tokens` 在 Anthropic 响应里读不到 → 走备选字段
`cache_read_input_tokens`。

#### 发现"合理性校验"误杀

`parse_cache_usage` 的备选字段带累计值合理性校验：

```python
if input_tokens and v > input_tokens * 3:
    continue  # 丢弃
```

flash 第 1 轮实际数值：
- `input_tokens` = 896（Anthropic 协议，**仅未命中部分**）
- `cache_read_input_tokens` = 63,000（system prompt 命中）
- 校验：`63000 > 896 * 3 = 2688` → **命中数被误判为"累计值异常"丢弃**

根因确认：合理性校验假设 `input_tokens` 含缓存（OpenAI 协议），但 Anthropic 协议下
`input_tokens` 不含缓存，命中数远大于 input_tokens 是正常的。

#### 关联发现：/cost 命中率分母也有问题

```python
if provider_name == "anthropic":
    denom = session.input_tokens + session.cache_read_tokens
else:  # deepseek 走这里
    denom = session.input_tokens  # ← Anthropic 协议下 input_tokens 不含缓存！
```

flash 的 `provider_name = "deepseek"`（走 else），但实际是 Anthropic 协议，
分母用 `input_tokens` 会太小，命中率虚高（可能 >100%）。

### 阶段四：定位 flash 思考模式关闭（问题四）

对比 OpenAI Provider 和 Anthropic Provider 的思考模式实现：

#### OpenAI Provider（完整实现）

通过 `THINKING_CONFIGS` 配置表 + `apply_thinking()` 注入参数：

```python
# thinking.py 配置
"deepseek": ThinkingConfig(
    placement="extra_body",
    field="thinking",
    on_value={"type": "enabled"},
    reasoning_effort="high",
)

# openai_provider.py stream()
apply_thinking(request_kwargs, cfg, thinking_on, self._thinking_budget)
```

#### Anthropic Provider（三处缺失）

```python
# 缺失 1：默认关闭（与 OpenAI Provider 不一致）
self._thinking_enabled = False  # ← OpenAI Provider 是 True

# 缺失 2：set_thinking_enabled 只记录状态，stream() 不使用
def set_thinking_enabled(self, enabled):
    self._thinking_enabled = bool(enabled)  # ← 记录了但 stream() 没用

# 缺失 3：stream() 构建请求时没有 thinking 参数
request_kwargs = {
    "model": ...,
    "system": ...,
    "messages": ...,
    "max_tokens": ...,
    # ← 没有 thinking 参数！
}
```

#### 事件处理缺失

Anthropic SDK 流式响应中，思考内容通过 `thinking_delta` 事件返回，但 stream()
只处理了 `text_delta` 和 `input_json_delta`，没有 `thinking_delta`。

#### 对照 DeepSeek 思考模式文档

文档明确：
- Anthropic 格式控制参数：`{"thinking": {"type": "enabled/disabled"}}`
- 思考模式不支持 `temperature` 参数（设置不会报错但不生效）

Anthropic Provider 完全没传 `thinking` 参数，导致 DeepSeek 兼容端点不开启思考。

## 3. 根因分析

### 四个 bug 的共同根因：多厂商协议适配不充分

| Bug | 直接根因 | 深层根因 |
|---|---|---|
| /cost 漏算 system | `estimate_tokens` 只数 messages | system prompt 是独立字符串，未纳入统计 |
| /compact 崩溃 | `asyncio.run()` 嵌套调用 | 同步 handler 调异步函数，未改成 async |
| flash 缓存 0% | 合理性校验误杀命中数 | OpenAI/Anthropic 协议下 input_tokens 语义相反，校验未区分 |
| flash 思考关闭 | stream() 没传 thinking 参数 | Anthropic Provider 的思考模式是未完成功能，只记录状态不实际使用 |

**核心教训**：JARVIS 支持多厂商多协议（OpenAI/Anthropic/DashScope/ZAI），
但部分代码假设了单一协议语义。当同一厂商（如 DeepSeek）提供两种兼容端点时，
协议语义冲突就会暴露。OpenAI Provider 通过 `THINKING_CONFIGS` 配置表完整实现了
思考模式注入，但 Anthropic Provider 完全没走这套机制，导致功能缺失。

## 4. 修复方案

### 修复一：/cost 和 /context 纳入 system prompt

**新增 `estimate_text_tokens()` 辅助函数**（[compactor.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/memory/compactor.py)）：

```python
def estimate_text_tokens(text: str) -> int:
    """粗估纯文本字符串的 token 数（如 system prompt）。"""
    if not text:
        return 0
    return int(max(1, len(text) // _CHARS_PER_TOKEN))
```

**`/cost` 拆分 3 行显示**（[core_commands.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/commands/handlers/core_commands.py)）：

- System Prompt token（估算）
- 对话历史 token（估算）
- 完整上下文 token（含 system）

**`/context`** 加 `system_prompt` 参数，system 参与 token 与窗口占比统计。
窗口默认值从 32768 改为 128000（主流大模型保守值）。

### 修复二：/compact 改为 async

**`_compact` 和 `handle_compact` 改为 `async def`**：

```python
async def _compact(ui, loop, ctx):
    ...
    ok = await loop.compact_now(ctx)  # 直接 await，不嵌套 asyncio.run
```

`dispatch_command` 本就支持异步 handler（`inspect.isawaitable` 检查），无需改动。

**顺带改进提示信息**：新增 `keep_recent_messages` 和 `enable_compaction` 两个
只读属性，`_compact` 提前判断并给出准确提示（区分"禁用"/"消息太少"/"调用失败"）。

### 修复三：缓存统计协议语义适配（三层修复）

**第一层：`parse_cache_usage` 加 `input_includes_cache` 参数**（[cache_policy.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/cache_policy.py)）：

```python
def parse_cache_usage(
    usage_obj, policy, input_tokens=0, *,
    input_includes_cache: bool = True,  # ← 新增
) -> tuple[int, int]:
    ...
    # 仅当 input_tokens 含缓存时做累计值校验
    if input_includes_cache and input_tokens and v > input_tokens * 3:
        continue
```

- `True`（OpenAI/DashScope 协议）：保留累计值校验
- `False`（Anthropic 协议）：跳过校验

**第二层：`anthropic_provider.py` 调用时传 `input_includes_cache=False`**：

```python
cached, created = parse_cache_usage(
    u, cache_cfg,
    input_tokens=u.input_tokens,
    input_includes_cache=False,  # Anthropic 协议：input_tokens 不含缓存
)
```

**第三层：`/cost` 命中率分母按数据特征判断**（而非厂商名）：

```python
# 旧逻辑：if provider_name == "anthropic"
# 新逻辑：看 cache_read 是否大于 input_tokens
if session.cache_read_tokens > session.input_tokens:
    denom = session.input_tokens + session.cache_read_tokens  # Anthropic 协议
else:
    denom = session.input_tokens  # OpenAI/DashScope 协议
```

用数据特征判断比厂商名更可靠，因为同一厂商可能走不同协议。

### 修复四：Anthropic Provider 思考模式完整实现（三层修复）

**第一层：默认开启思考**：

```python
# 旧：self._thinking_enabled = False
# 新：self._thinking_enabled = True  # 与 OpenAI Provider 一致
```

**第二层：`stream()` 注入 thinking 参数**（[anthropic_provider.py:215-231](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/anthropic_provider.py#L215-L231)）：

```python
if self._thinking_enabled:
    if self.name == "anthropic":
        # Anthropic 原生需要 budget_tokens
        request_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 10000}
    else:
        # DeepSeek 兼容端点只需 type
        request_kwargs["thinking"] = {"type": "enabled"}
    # 思考模式下不传 temperature（DeepSeek 文档：不支持）
elif temperature is not None:
    request_kwargs["temperature"] = temperature
```

关键点：**思考模式下不传 `temperature`**——DeepSeek 文档明确说"思考模式不支持
temperature，设置不会报错但不生效"。

**第三层：处理 `thinking_delta` 事件**：

```python
elif delta.type == "thinking_delta":
    # thinking_delta 带 .thinking 属性，不是 .text
    yield ThinkingDelta(text=delta.thinking)
```

Anthropic SDK 流式响应中，思考内容通过 `thinking_delta` 事件返回，
`delta.thinking` 属性（不是 `delta.text`）。

## 5. 验证结果

### 测试环境

- 模型：deepseek-v4-pro（OpenAI 兼容）+ deepseek-v4-flash（Anthropic 兼容）
- 场景：3-5 轮简单对话（各国首都问答）
- 验证命令：`/cost`、`/context`、`/compact`、思考过程显示

### 验证数据

#### /cost 修复验证（pro，OpenAI 兼容端点）

| 轮次 | System Prompt | 对话历史 | 完整上下文 | 缓存命中 | 命中率 |
|---|---|---|---|---|---|
| 第1轮 | 63,923 | 5 | 63,928 | 70,912 | 98.0% |
| 第2轮 | 63,923 | 16 | 63,939 | 143,232 | 98.9% |
| 第3轮 | 63,923 | 14 | 63,937 | 212,736 | 98.0% |

#### /cost 修复验证（flash，Anthropic 兼容端点）

| 轮次 | System Prompt | 对话历史 | 完整上下文 | 缓存命中 | 命中率 |
|---|---|---|---|---|---|
| 第1轮 | 63,923 | 17 | 63,940 | 71,168 | 98.2% |
| 第2轮 | 63,923 | 46 | 63,969 | 143,488 | 99.0% |

flash 从修复前 0% → 修复后 98%+，**修复完全生效**。

#### /context 验证

```
| 角色      | 消息数 | tokens   |
| system    | 1      | 63,923   |
| user      | 2      | 4        |
| assistant | 2      | 12       |
合计: 4 条消息 + system prompt / 63,939 tokens / 窗口占比 50.0%
```

窗口占比从 195.1%（32768 窗口）降到 50.0%（128000 窗口），合理。

#### /compact 验证

- 6 条消息时：提示"消息数 ≤ 保留阈值（6），全部保留，无需压缩"（不再崩溃）
- 10 条消息时：成功触发压缩（67 → 199 tokens），不崩溃

#### 命中率分母自动判断验证

- flash：`cache_read (71,168) > input_tokens (1,280)` → 走 Anthropic 分支 ✅
- pro：`cache_read (71,168) < input_tokens (72,466)` → 走 OpenAI 分支 ✅

#### 思考模式验证（flash，Anthropic 兼容端点）

修复前：无思考过程，直接输出回答
修复后：

```
> 中国的首都是哪里
╭─ 💭 思考过程 ────────────────────────────────────────────────────╮
│ 用户问的是中国的首都是哪里。这是一个非常简单的事实性问题...     │
╰────────────────────────────────────────────────────────────────╯
先生，中国的首都是**北京**。
```

- ✅ 显示 `╭─ 💭 思考过程 ───` 窗口
- ✅ 思考内容正常流式显示
- ✅ 最终回答正常输出
- ✅ pro（OpenAI 兼容端点）思考过程不受影响

### 回归验证

- `estimate_tokens` 签名未变，`/compact` 压缩阈值判断不受影响
- DeepSeek OpenAI 兼容端点（pro）缓存统计保持正确
- 编译验证通过（`uv run python -c "from agent... import ..."`）

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| [agent/core/memory/compactor.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/memory/compactor.py) | 新增 `estimate_text_tokens()` 辅助函数 |
| [agent/commands/handlers/core_commands.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/commands/handlers/core_commands.py) | `/cost` 拆 3 行 + system prompt 统计；`/context` 含 system；`/compact` 改 async + 提前判断；命中率分母按数据特征判断 |
| [agent/core/query_loop.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/query_loop.py) | 新增 `keep_recent_messages` 和 `enable_compaction` 只读属性 |
| [agent/llm/cache_policy.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/cache_policy.py) | `parse_cache_usage` 新增 `input_includes_cache` 参数，仅 True 时做累计值校验 |
| [agent/llm/anthropic_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/anthropic_provider.py) | 调用 `parse_cache_usage` 时传 `input_includes_cache=False`；默认开启思考；`stream()` 注入 thinking 参数；处理 `thinking_delta` 事件；导入 ThinkingDelta |

## 7. 经验总结

### 教训一：同一厂商多协议时，协议语义可能完全相反

DeepSeek 同时提供 OpenAI 兼容和 Anthropic 兼容端点，两者的 `input_tokens` 语义
**完全相反**：
- OpenAI 协议：`input_tokens` **含**缓存命中
- Anthropic 协议：`input_tokens` **不含**缓存命中

后续遇到"同一厂商多端点"场景时，必须检查每个字段的协议语义，不能假设统一。

### 教训二：合理性校验要区分场景

`parse_cache_usage` 的累计值校验是为防止 DeepSeek OpenAI 端点返回累计值，但对
Anthropic 端点却误杀了正常命中。**防御性代码如果不区分场景，反而会成为 bug 源头**。

后续设计校验逻辑时，应通过参数明确区分场景，而非用统一规则覆盖所有情况。

### 教训三：async/asyncio.run 不能混用

Python 3.10+ 不允许在已有事件循环里嵌套 `asyncio.run()`。如果上层已经是 async
上下文（如 `dispatch_command`），下层调用必须直接 `await`，不能套 `asyncio.run()`。

`dispatch_command` 的 `inspect.isawaitable` 机制已经支持异步 handler，新增命令
处理器时优先用 `async def`。

### 教训四：统计类命令要覆盖完整数据源

`/cost` 和 `/context` 是统计类命令，必须覆盖所有数据源。system prompt 是 JARVIS
上下文的大头（63k token），漏算会导致用户误判上下文使用情况。

后续设计统计命令时，列出所有数据源（system / messages / tools / images），
确保每一项都被统计或显式说明不统计的原因。

### 教训五：用数据特征判断比用名称判断更可靠

`/cost` 命中率分母原本用 `provider_name == "anthropic"` 判断，但 DeepSeek 走
Anthropic 兼容端点时 `provider_name = "deepseek"`，判断失效。

改为用 `cache_read_tokens > input_tokens` 判断协议语义，因为：
- OpenAI 协议下 cache_read 是 input_tokens 的子集（cache_read ≤ input_tokens）
- Anthropic 协议下 cache_read 独立于 input_tokens（cache_read 可能 >> input_tokens）

**用数据特征判断比用名称判断更可靠**，因为名称可能被复用（如 deepseek 走两种协议）。

### 可固化的规则

1. **新增命令处理器优先用 `async def`**：避免 asyncio.run 嵌套问题
2. **缓存策略配置表应记录协议语义**：`input_includes_cache` 字段应进 CachePolicy
3. **统计命令覆盖完整数据源**：system + messages + tools + images 全部纳入
4. **功能开关必须端到端打通**：`set_thinking_enabled` 不能只记录状态，
   stream() 必须实际使用该标志注入参数
5. **同一功能在多 Provider 间应保持实现一致**：OpenAI Provider 通过
   `THINKING_CONFIGS` 实现了思考模式，Anthropic Provider 也应走类似机制，
   而非独立实现且遗漏关键步骤

## 8. 后续待优化项

- **CachePolicy 增加 `input_includes_cache` 字段**：当前通过 Provider 传参，
  后续可固化到配置表，让策略信息更完整
- **Anthropic Provider 思考模式接入 THINKING_CONFIGS**：当前在 stream() 里
  硬编码 thinking 参数，后续可像 OpenAI Provider 一样通过配置表驱动
- **模型→窗口映射表**：当前窗口硬编码 128000，后续可按模型动态获取实际窗口
- **短对话压缩优化**：压缩后 token 变多（67→199）是边界情况，可考虑
  `estimate_tokens(messages) < 500` 时直接跳过压缩
- **anyio 异步生成器清理问题**：`/compact` 触发 LLM 调用时可能暴露 MCP 客户端的
  anyio 清理 bug（`Attempted to exit cancel scope in a different task`），
  虽然被 compact_now 的 try/except 捕获不崩溃，但根本问题待解决
- **Anthropic 原生端点 budget_tokens 可配置**：当前硬编码 10000，
  后续可根据模型和能力动态调整
