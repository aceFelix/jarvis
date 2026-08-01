# 新增智谱官方 ZhipuAi SDK Provider 修复记录

## 问题现象

在 CLI 中使用智谱 `glm-4.7-flash` 模型（通过 OpenAI 兼容接口）对话时：

1. 简单问题 `"贾维斯在干嘛"` 响应耗时约 30 秒，明显慢于 DeepSeek 等其他厂商。
2. 此前工具调用后还出现过长时间无响应、空回复等异常。
3. 用户怀疑 OpenAI 兼容接口与智谱模型存在兼容性/性能问题，希望改用智谱官方 SDK。

---

## 排查与修复过程

### 第一阶段：确认 OpenAI 兼容层问题

**操作**：
- 检查 `agent/llm/openai_provider.py` 对智谱 GLM 的消息格式处理。
- 已在前序修复中解决 assistant with tool_calls 缺少 `content=null`、role="tool" content 为 list 等问题。
- 但即使修复后，简单对话仍有 30 秒延迟，说明问题不限于消息格式。

**结论**：OpenAI 兼容层虽然能跑通，但智谱对原生 SDK 可能有更好的性能和稳定性。

---

### 第二阶段：调研智谱官方 SDK

**操作**：
- 阅读项目文档 `zai-docs/智谱官方Python SDK.md`。
- 发现智谱提供 `zai.ZhipuAiClient`，其 `chat.completions.create()` 接口与 OpenAI 完全兼容：
  - 支持流式 `stream=True`
  - 支持 `thinking={"type": "enabled/disabled"}`
  - 支持 `tools` function calling
  - 支持多模态 `image_url`

**结论**：可以直接基于官方 SDK 实现一个新的 Provider，复用现有的 OpenAI 消息转换逻辑。

---

### 第三阶段：设计并实现 ZaiProvider

**决策**：
- 新增 `agent/llm/zai_provider.py`，不改动 `openai_provider.py` 的现有逻辑。
- 复用 `openai_provider._messages_to_openai` 和 `_parse_tool_args`，保证消息格式与 OpenAI 兼容层一致。
- zai-sdk 是同步客户端，使用 `asyncio.Queue + 后台线程` 桥接到 async generator（与 DashScopeProvider 模式一致）。
- 在 `main.py` 的 `_build_provider` 中注册 `api_format="zai"`。
- 在 `model_manager.py` 的 `/models` 表单中添加 `"智谱 ZhipuAi SDK"` 选项。

---

## 最终修复方案

### 新增 1：智谱原生 Provider

**文件**：`agent/llm/zai_provider.py`

实现 `ZaiProvider`：
- 使用 `zai.ZhipuAiClient` 作为底层客户端。
- `stream()` 方法把同步流通过 queue 桥接到 async。
- 支持 thinking、function calling、多模态图片。
- 设置 180 秒总超时，避免无响应时无限挂起。

### 修改 2：注册到 Provider 工厂

**文件**：`agent/main.py`

在 `_build_provider()` 中增加 `api_format == "zai"` 分支：

```python
if name == "zai":
    from agent.llm.zai_provider import ZaiProvider
    return ZaiProvider(...)
```

### 修改 3：/models 表单支持选择智谱 SDK

**文件**：`agent/model_manager.py`

在三处接口类型选项列表中增加 `("zai", "智谱 ZhipuAi SDK")`：
- `_add_custom_model_flow`
- `_edit_custom_model`
- `_edit_builtin_model`

同时在 `_infer_base_url` 和 `_edit_builtin_model` 中把 `zai` 与 `dashscope` 同等处理：base_url 由 SDK 自管，留空。

### 修改 4：故障转移兼容新 api_format

**文件**：`agent/core/query_loop.py`

`_try_failover()` 中优先读取 `api_format`，兼容旧 `provider_type`：

```python
api_fmt = cfg.get("api_format") or cfg.get("provider_type", "openai")
```

### 修改 5：依赖与文档

**文件**：`pyproject.toml`

在 `dependencies` 中新增 `zai-sdk>=0.2.2`。

**文件**：`README.md`、`agent/configs/settings.example.toml`

- 更新 `api_format` 可选值说明（增加 `zai`）。
- 在自定义模型接口类型表格中增加智谱 SDK。
- 增加 `glm-4.7-flash` 的 `zai` 配置示例。
- 目录结构增加 `zai_provider.py`。

---

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/llm/zai_provider.py` | 新增智谱官方 SDK Provider |
| `agent/main.py` | `_build_provider` 注册 `api_format="zai"` |
| `agent/model_manager.py` | `/models` 表单增加智谱 SDK 选项；base_url 推断兼容 zai |
| `agent/core/query_loop.py` | 故障转移优先读取 `api_format` |
| `pyproject.toml` | 新增依赖 `zai-sdk>=0.2.2` |
| `README.md` | 更新 api_format 说明、接口类型表格、配置示例、目录结构 |
| `agent/configs/settings.example.toml` | 更新 api_format 注释说明 |

---

## 验证结果

- `python -m py_compile agent/llm/zai_provider.py agent/main.py agent/model_manager.py agent/core/query_loop.py` 通过。
- 安装依赖：`pip install zai-sdk>=0.2.2`。
- 人工验证步骤：
  1. 在 `~/.jarvis/settings.toml` 中添加 `[llm.custom_models."glm-4.7-flash"]`，`api_format = "zai"`。
  2. 启动 jarvis，输入 `/model glm-4.7-flash`。
  3. 进行简单对话和工具调用，确认响应速度优于 OpenAI 兼容接口。

---

## 经验教训

1. **兼容层只能兜底，原生 SDK 才是性能/稳定性的最优解**。当某个厂商提供官方 SDK 且接口与现有抽象兼容时，应优先实现原生 Provider。
2. **复用而非复制**。ZaiProvider 复用了 `openai_provider` 的消息转换和参数解析，避免重复代码和后续维护两份逻辑。
3. **同步 SDK 桥接到 async 是成熟模式**。DashScopeProvider 已经验证了 queue + 线程的模式，ZaiProvider 可以直接沿用。
4. **新增 Provider 需要同步修改多处表单/文档**。除了 `_build_provider`，还要更新 `/models` 表单、base_url 推断、README、pyproject.toml 等。
5. **依赖声明必须同步**。新增 SDK 依赖后，要在 `pyproject.toml` 中声明，否则安装后运行会报 ImportError。
