# 智谱 GLM 模型工具调用后无响应修复记录

## 问题现象

在 CLI 中使用智谱 `glm-4.7-flash` 模型对话时：

1. 用户输入 `"jarvis几点了"`。
2. 模型正确发起工具调用 `mcp__12306-mcp__get-current-date`。
3. 工具执行完成，结果 `2026-07-29` 已回显到终端。
4. **之后模型再无输出，等待超过 3 分钟仍无响应**，也没有报错提示。

同一会话中，`glm-4.6v-flash` 则直接返回空回复（已在 `query_loop.py` 中增加空回复过滤）。

---

## 排查与修复过程

### 第一阶段：检查工具调用流程

**操作**：
- 确认 `QueryLoop.run()` 中工具结果已回灌到 `ctx.messages`。
- 确认工具结果回灌后 `for iteration in range(self._max_iterations)` 会进入下一轮并继续调用 `_stream_once`。
- 代码逻辑本身没有问题。

**结论**：循环逻辑正常，问题出在**第二轮 LLM 调用**上。

---

### 第二阶段：对比 DeepSeek 与 GLM 行为

**发现**：
- DeepSeek 模型在相同流程下可以正常在工具调用后继续生成回复。
- GLM 模型第一次调用能成功，但工具调用后的第二次调用挂起。
- 这说明 GLM 对**工具调用相关的消息格式**比 DeepSeek 更严格。

---

### 第三阶段：审查 OpenAI 消息转换格式（根因定位）

**操作**：检查 `agent/llm/openai_provider.py` 中的 `_messages_to_openai()`。

**发现两个不符合 OpenAI 官方规范的地方**：

1. **assistant message with `tool_calls` 缺少 `content` 字段**

   当模型只调用工具、没有附带文本时，原代码直接省略 `content`：

   ```python
   entry: dict[str, Any] = {"role": "assistant"}
   if content_text:
       entry["content"] = content_text
   if tool_calls:
       entry["tool_calls"] = tool_calls
   ```

   OpenAI 官方规范要求：`content` 字段必须存在，且此时应为 `null`。

2. **`role="tool"` 消息在带图片时 `content` 为 list**

   原代码对带图片的 tool_result 构造：

   ```python
   content_list = [
       {"type": "text", "text": b.content},
       {"type": "image_url", ...},
   ]
   out.append({"role": "tool", "tool_call_id": ..., "content": content_list})
   ```

   规范要求 `role="tool"` 的 `content` 必须是 string。部分兼容接口（如智谱 GLM）收到 list 后可能挂起或报错。

3. **AsyncOpenAI 未设置 timeout**

   `self._client = AsyncOpenAI(**kwargs)` 没有传 `timeout`，默认总超时较长，服务端不响应时用户侧会长时间挂起。

---

## 最终修复方案

### 修复 1：assistant with tool_calls 时显式设置 content=null

**文件**：`agent/llm/openai_provider.py`

```python
entry: dict[str, Any] = {"role": "assistant"}
if content_text:
    entry["content"] = content_text
elif tool_calls:
    # OpenAI 规范要求 assistant message with tool_calls 的 content 字段存在且为 null；
    # 智谱 GLM 等兼容接口在字段缺失时可能挂起或报错。
    entry["content"] = None
if tool_calls:
    entry["tool_calls"] = tool_calls
```

### 修复 2：role="tool" 的 content 始终为 string

**文件**：`agent/llm/openai_provider.py`

带图片时把图片描述以文本附加，content 保持字符串：

```python
if b.images:
    if skip_images:
        tool_content = b.content + f"\n[附带 {len(b.images)} 张图片（当前为纯文本模型，图片已省略）]"
    else:
        tool_content = b.content + f"\n[附带 {len(b.images)} 张图片]"
    out.append({
        "role": "tool",
        "tool_call_id": b.tool_use_id,
        "content": tool_content,
    })
```

### 修复 3：为 AsyncOpenAI 设置 180 秒总超时

**文件**：`agent/llm/openai_provider.py`

```python
kwargs: dict[str, Any] = {"timeout": 180.0}
if api_key:
    kwargs["api_key"] = api_key
if base_url:
    kwargs["base_url"] = base_url
self._client = AsyncOpenAI(**kwargs)
```

---

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/llm/openai_provider.py` | assistant with tool_calls 时补 `content=null`；role="tool" content 固定为 string；AsyncOpenAI 增加 180s timeout |

---

## 验证结果

- `python -m py_compile agent/llm/openai_provider.py` 通过，exit code 0。
- 修复后需人工在 CLI 中再次验证：
  1. `/model glm-4.7-flash`
  2. 输入会触发工具调用的问题（如 "jarvis几点了"）
  3. 工具结果回显后，模型应在正常时间内继续生成最终回复

---

## 经验教训

1. **OpenAI 兼容接口不等于完全兼容**。DeepSeek 对消息格式较宽松，但智谱 GLM 对 `assistant.content=null`、`role="tool"` content 类型等细节更严格，必须严格遵循 OpenAI 官方规范。
2. **工具调用相关的 assistant message 必须保留 `content` 字段**。即使为空也要显式设为 `null`，不能省略。
3. **`role="tool"` 的 content 必须是 string**。不要为了追求多模态把图片直接塞进 tool message，应通过独立 user message 或文字描述处理。
4. **网络客户端必须配置 timeout**。无 timeout 时服务端异常会让用户侧无限挂起，问题难以定位。
5. **多厂商测试不能只在一家通过就结束**。涉及 OpenAI 兼容接口的改动应在 DeepSeek、智谱、Moonshot 等多家验证工具调用路径。
