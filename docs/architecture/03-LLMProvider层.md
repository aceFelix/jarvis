# 03 - LLM Provider 抽象层

LLM Provider 层把不同厂商的 API 统一成一套流式事件协议，让 QueryLoop 不关心底层差异。

> 本文档已同步 A-02 / A-04 改进（思考模式配置表 + Provider 注册表驱动），
> 并新增智谱原生 SDK（ZaiProvider）与 LLM 错误分类（E-01）章节。

## 一、架构设计

```
QueryLoop
    │ 只依赖抽象
    ▼
LLMProvider (抽象基类)
    │
    ├─ OpenAIProvider    → openai SDK      → OpenAI/DeepSeek/智谱/Moonshot/兼容服务
    ├─ AnthropicProvider → anthropic SDK   → Claude 系列
    ├─ DashScopeProvider → dashscope SDK   → Qwen 原生协议
    ├─ ZaiProvider       → zai-sdk         → GLM 系列原生协议
    └─ MockProvider      → 无后端          → 测试/离线
```

Provider 的装配与厂商识别由 [provider_registry.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/provider_registry.py) 的配置表统一驱动（A-04）。

## 二、核心文件

| 文件 | 职责 |
|---|---|
| [base.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/base.py) | LLMProvider 基类 + 流式事件类型 |
| [openai_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/openai_provider.py) | OpenAI 兼容协议实现（含 DeepSeek/智谱等厂商参数差异化） |
| [anthropic_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/anthropic_provider.py) | Anthropic 协议实现 |
| [dashscope_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/dashscope_provider.py) | DashScope SDK 原生协议 |
| [zai_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/zai_provider.py) | 智谱 zai-sdk 原生协议（GLM 系列） |
| [mock.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/mock.py) | Mock Provider（测试/离线） |
| [provider_registry.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/provider_registry.py) | 厂商注册表（配置表驱动装配 + URL 检测） |
| [thinking.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/thinking.py) | 思考模式参数配置表（策略化取代 if-else） |
| [errors.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/errors.py) | LLM 错误分类（鉴权/限流/模型不存在等 → 可操作提示） |

## 三、流式事件类型

[base.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/base.py) 定义统一事件：

```python
# 一个完整的流由这些事件组成
LLMEvent = Union[ThinkingDelta, TextDelta, ToolCall, ToolCallEnd, Stop]
```

| 事件 | 含义 | 何时产生 |
|---|---|---|
| `ThinkingDelta(text)` | 思维链增量 | reasoning_content（qwen enable_thinking / GLM thinking） |
| `TextDelta(text)` | 文本增量 | 正式回复内容 |
| `ToolCall(id, name, input)` | 工具调用 | 模型请求调用工具 |
| `ToolCallEnd(id)` | 工具调用参数流结束 | 简化版：和 ToolCall 一起到达 |
| `Stop(reason, usage)` | 响应结束 | stop/length/content_filter |

**事件顺序**：`ThinkingDelta* → TextDelta* → ToolCall* → Stop`

## 四、LLMProvider 基类

[LLMProvider](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/base.py) 核心接口：

```python
class LLMProvider(abc.ABC):
    name: str = "base"
    default_model: str = ""

    @abc.abstractmethod
    async def stream(
        self, *,
        model: str,
        system: str,
        messages: list[Message],
        tools: list[ToolDef],
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """流式调用。必须 yield LLMEvent 序列，以 Stop 结尾。
        约定:
        - 出错时 raise ProviderError，不要 yield 半截序列
        - ToolCall 的 input 一定是完整解析后的 dict
        """
        ...

    # 思考模式统一控制
    def set_thinking_enabled(self, enabled: bool) -> None: ...  # 子类 override
    def is_thinking_enabled(self) -> bool: ...
    # 模型类型动态切换（multimodal / text）
    def set_model_type(self, model_type: str) -> None: ...
    async def close(self) -> None: ...
```

**关键约定**：
- 出错 `raise ProviderError`，不 yield 半截序列
- `ToolCall.input` 是完整 dict（不发参数 delta）
- 以 `Stop` 事件结尾
- `set_model_type()` 支持运行时切换模型类型（切换纯文本模型时跳过图片）

## 五、Provider 注册表驱动（A-04）

[provider_registry.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/provider_registry.py) 把厂商信息收敛到一张表：

```python
@dataclass
class ProviderMeta:
    api_format: str          # 对应 settings.api_format
    name: str                # 显示名称（也是 THINKING_CONFIGS 的 key）
    module_path: str         # Provider 类导入路径（延迟导入避免循环依赖）
    init_keys: tuple[str]    # 构造参数对应的 Settings 字段
    url_patterns: tuple[str] # base_url 子串 → 自动识别厂商
    thinking_key: str | None # 对应 THINKING_CONFIGS 的 key
    model_type: str          # 是否支持多模态

    def create(self, **kwargs): ...  # 延迟 import + 实例化
```

**注册表条目**（新增厂商只需加一行）：

| api_format | 厂商 | Provider 类 | thinking_key |
|---|---|---|---|
| `mock` | Mock | MockProvider | — |
| `anthropic` | Claude | AnthropicProvider | — |
| `openai` | OpenAI | OpenAIProvider | — |
| `dashscope` | 阿里云（兼容接口） | OpenAIProvider | dashscope |
| `deepseek` | DeepSeek | OpenAIProvider | deepseek |
| `zhipu` | 智谱（兼容接口） | OpenAIProvider | zhipu |
| `moonshot` | Moonshot | OpenAIProvider | — |
| `minimax` | MiniMax | OpenAIProvider | — |
| `xiaomimimo` | 小米 MiMo | OpenAIProvider | — |
| `google` | Google AI | OpenAIProvider | — |
| `siliconflow` | 硅基流动 | OpenAIProvider | — |
| `dashscope_sdk` | 阿里云（原生 SDK） | DashScopeProvider | dashscope_sdk |
| `zai` | 智谱（原生 SDK） | ZaiProvider | zai_sdk |

**URL 自动识别**：`lookup_by_url(base_url)` 遍历 `url_patterns` 子串匹配，
替换了原先 `_derive_name()` 的 if-else 链（如 `api.deepseek.com` → deepseek、`open.bigmodel.cn` → zhipu）。

**为什么配置表驱动**：此前每加一个厂商就要改 `_build_provider` 的 if-else。
现在新增厂商只加一行 `ProviderMeta`，工厂函数零改动。

## 六、四类 Provider 实现

### 1. OpenAI Provider

[openai_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/openai_provider.py) 适配 OpenAI 兼容协议：

- **适用模型**：DeepSeek / GPT-4o / 通义千问兼容模式 / 智谱兼容模式 / 各类兼容服务
- **SDK**：`openai` Python SDK（`AsyncOpenAI(timeout=180.0)`，防止后端无响应导致挂死）
- **消息格式转换**：内部 Message → OpenAI messages 格式
  - assistant 消息带 tool_calls 时显式设置 `content: null`
  - tool 消息 content 必须为字符串（不能是 list）
- **工具调用**：OpenAI function calling 格式 → ToolCall 事件
- **思考模式控制**：由 [thinking.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/thinking.py) 配置表驱动（见「八、思考模式控制」）
- **reasoning_content 处理**：检查 `is_thinking_enabled()` 后才 emit `ThinkingDelta`
- **多模态图片**：image content → OpenAI image_url 格式；`model_type="text"` 时跳过图片
- **厂商参数差异化**：通过 `_derive_name()`（现由 `lookup_by_url()` 支持）识别厂商，
  按 `THINKING_CONFIGS` 注入对应思考参数

**思考模式过滤**：
```python
# /think off 时，即使后端返回 reasoning_content 也不 emit ThinkingDelta
if self.is_thinking_enabled() and hasattr(delta, "reasoning_content"):
    yield ThinkingDelta(text=delta.reasoning_content)
```

### 2. Anthropic Provider

[anthropic_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/anthropic_provider.py) 适配 Anthropic Messages API：

- **适用模型**：Claude 3.5/3.7/4 系列，以及通过 Anthropic 兼容 API 接入的第三方模型（如 DeepSeek-v4-flash）
- **SDK**：`anthropic` Python SDK
- **消息格式转换**：内部 Message → Anthropic messages 格式
- **工具调用**：Anthropic tool_use block → ToolCall 事件
- **ThinkingContent 过滤**：Anthropic 兼容后端无法处理 ThinkingContent，发送前过滤
- **Provider 名推断**：从 base_url 动态推断 provider 名
- **多模态图片**：image content → Anthropic image block
- **思考模式默认开启**：DeepSeek-v4-flash 等通过 Anthropic 兼容 API 接入的模型，默认开启 thinking 模式
- **thinking_delta 事件处理**：流式响应中处理 `thinking_delta` 事件，emit `ThinkingDelta`
- **显式 disabled 状态**：关闭思考时显式注入 `thinking: {"type": "disabled"}`，而不是省略参数

**为什么过滤 ThinkingContent**：qwen3.6-flash 等模型生成的 ThinkingContent 块，DeepSeek/Anthropic 后端无法处理，会导致 API 报错。

**思考模式开关修复**（P 系列修复）：

之前关闭思考模式时只是省略 `thinking` 参数，但 DeepSeek-v4-flash 的 Anthropic 兼容 API 默认开启思考，省略参数等同于开启。修复后显式注入 `disabled` 状态：

```python
# stream() 里的思考参数注入
if self.is_thinking_enabled():
    request_kwargs["thinking"] = {"type": "enabled"}
    # 注入 thinking_budget 等
else:
    # 显式关闭：不能省略，否则 DeepSeek-v4-flash 会默认开启
    request_kwargs["thinking"] = {"type": "disabled"}

# 流式响应里处理 thinking_delta 事件
elif event.type == "content_block_delta":
    if event.delta.type == "thinking_delta":
        # 思考过程增量
        yield ThinkingDelta(text=event.delta.thinking)
    elif event.delta.type == "text_delta":
        yield TextDelta(text=event.delta.text)
```

**为什么需要显式 disabled**：不同 API 对省略 `thinking` 参数的默认行为不一致。Claude 官方 API 省略时默认关闭，但 DeepSeek-v4-flash 的 Anthropic 兼容 API 省略时默认开启。显式注入 `disabled` 消除歧义。

### 3. DashScope Provider

[dashscope_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/dashscope_provider.py) 适配 DashScope SDK 原生协议：

- **适用模型**：Qwen 系列（qwen3.7-plus / qwen3.6-flash / qwen3.5-flash 等）
- **SDK**：`dashscope` Python SDK
- **双端点选择**：
  - `MultiModalConversation.call()`：多模态模型（qwen3.5-flash 等视觉模型）
  - `Generation.call()`：纯文本模型
- **增量提取**：DashScope 流式响应是增量模式（每块只含新文本），需不同提取逻辑
- **消息格式转换**：使用 `tr.content` 而非 `tr.output`，`tr.tool_use_id` 而非 `tr.id`
- **思考模式控制**：`enable_thinking=True/False` 参数（由 THINKING_CONFIGS 的 `dashscope_sdk` 配置驱动）
- **思考过滤**：检查 `thinking_on` flag 后才 emit ThinkingDelta

**为什么有双端点**：qwen3.5-flash 是多模态模型，必须用 `MultiModalConversation`（multimodal-generation 端点），用 `Generation`（text-generation 端点）会报错。

### 4. Zai Provider（智谱原生 SDK）

[zai_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/zai_provider.py) 适配智谱官方 Python SDK：

- **适用模型**：GLM 系列（glm-4.7-flash / glm-4.6v-flash 等）
- **SDK**：`zai-sdk`（`ZhipuAiClient`），绕开 OpenAI 兼容层，响应更快更稳定
- **同步 SDK 异步桥接**：`asyncio.Queue + 后台线程` 把同步的 `ZhipuAiClient.chat.completions.create()` 桥接到异步生成器
- **思考模式控制**：`thinking={"type": "enabled"/"disabled"}` + `reasoning_effort`（由 THINKING_CONFIGS 的 `zai_sdk` 配置驱动）
- **工具调用**：OpenAI 风格 function calling → ToolCall 事件
- **消息格式转换**：复用 `_messages_to_openai()`，`model_type="text"` 时跳过图片
- **依赖**：未安装时抛 `ProviderError` 提示 `pip install zai-sdk`

**为什么接入原生 SDK**：GLM 系列经 OpenAI 兼容接口存在消息格式兼容问题（tool_calls 需 `content: null`、tool 消息需 string content）
和性能损耗；原生 SDK 规避这些问题，工具调用后能稳定续生成回复。

## 七、Provider 选择逻辑

在 [bootstrap.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/bootstrap.py) 的 `_build_provider()` 中通过 `PROVIDER_REGISTRY` 配置表选择：

```python
def _build_provider(settings: Settings, model_type: str = "multimodal"):
    from agent.llm.provider_registry import PROVIDER_REGISTRY
    fmt = settings.api_format.lower()
    meta = PROVIDER_REGISTRY.get(fmt)
    if not meta:
        raise ValueError(f"未知 provider: {fmt}（可选: {', '.join(PROVIDER_REGISTRY.keys())}）")
    # 从 settings 收集 init_keys 指定的构造参数（model_type 由调用方传入）
    ...
    return meta.create(**kwargs)
```

新增厂商 = 在 `PROVIDER_REGISTRY` 加一行 `ProviderMeta`，工厂函数无需修改。

## 八、思考模式控制（A-02 策略化）

[thinking.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/thinking.py) 用配置表取代 if-else：

```python
@dataclass
class ThinkingConfig:
    placement: str          # "extra_body"（OpenAI 兼容）/"top_level"（SDK 直接参数）
    field: str              # 参数字段名（"thinking"/"enable_thinking"），空 = 不支持
    on_value: Any           # 开启时的值（True 或 {"type": "enabled"}）
    off_value: Any          # 关闭时的值（False 或 {"type": "disabled"}）
    reasoning_effort: str | None = None   # 额外注入的推理强度（如 "high"）
    budget_field: str | None = None       # thinking_budget 字段名

THINKING_CONFIGS = {
    "dashscope":    ThinkingConfig(placement="extra_body", field="enable_thinking", budget_field="thinking_budget"),
    "deepseek":     ThinkingConfig(placement="extra_body", field="thinking", on_value={"type": "enabled"}, reasoning_effort="high"),
    "zhipu":        ThinkingConfig(placement="extra_body", field="thinking", on_value={"type": "enabled"}, reasoning_effort="high"),
    "dashscope_sdk":ThinkingConfig(placement="top_level", field="enable_thinking", budget_field="thinking_budget"),
    "zai_sdk":      ThinkingConfig(placement="top_level", field="thinking", on_value={"type": "enabled"}, reasoning_effort="high"),
}
```

统一入口 `apply_thinking(request_kwargs, config, thinking_on, thinking_budget)`：
- 按 `placement` 决定参数去向（extra_body / 顶层 kwargs）
- 开启时注入 `reasoning_effort` 与 `thinking_budget`
- 不支持的厂商（field 为空）直接跳过

### QueryLoop 统一开关

[QueryLoop.set_thinking_enabled()](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/query_loop.py#L131-L140) 统一开关：
- 同步到当前 provider
- 记录到 `_thinking_override`，故障转移后能同步到新 provider
  （语音模式强制关闭思考，避免故障转移后意外恢复）

**过滤逻辑**：`/think off` 时，Provider 层即使后端返回 `reasoning_content` 也不 emit `ThinkingDelta`，净化输出。

## 九、LLM 错误分类（E-01）

[errors.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/errors.py) 把原始 API 异常映射为用户可操作提示：

```python
class ErrorCategory(str, Enum):
    AUTH            # 鉴权失败（API Key 无效/过期）
    RATE_LIMIT      # 限流（QPS 超限/配额用尽）
    MODEL_NOT_FOUND # 模型不存在/无权访问
    CONTEXT_TOO_LONG# 上下文超长
    NETWORK         # 网络超时/连接失败
    SERVER_ERROR    # 服务端错误（5xx）
    BAD_REQUEST     # 参数错误（4xx）
    UNKNOWN         # 未分类

def classify(exc) -> ClassifiedError:
    # 按 HTTP 状态码 + 错误消息关键词分类
    # 返回 (category, user_message)
    # user_message = 原始错误 + 通俗解释 + 操作建议
    # 原始消息经 mask_error_message() 脱敏
```

**解决痛点**：此前智谱 405 返回原始 HTML，用户看不懂。现在统一输出
「原始错误 + [分类标题] + 操作建议」（如检查 API Key / 切换到其他模型）。

## 十、工具调用统一抽象

各协议的工具调用格式不同，Provider 层负责转换：

| 协议 | 工具定义格式 | 工具调用响应 |
|---|---|---|
| OpenAI | `functions` / `tools` | `delta.tool_calls` |
| Anthropic | `tools` | `content_block tool_use` |
| DashScope | `tools` | `output.tool_calls` |
| Zai | `tools` | `delta.tool_calls` |

Provider 把它们统一转成 `ToolCall(id, name, input)` 事件，QueryLoop 不需要关心差异。

## 十一、多模态图片处理

| 协议 | 图片格式 |
|---|---|
| OpenAI | `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}` |
| Anthropic | `{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "..."}}` |
| DashScope | `{"image": "data:image/jpeg;base64,..."}` |
| Zai | OpenAI 兼容 `image_url` 格式 |

Provider 把内部 `ImageContent(data, media_type)` 转换成各自格式。
`model_type="text"`（纯文本模型）时跳过所有图片块，避免 400 错误。

## 十二、自定义模型配置

用户可通过 `/models` 添加自定义模型，持久化到 `~/.jarvis/settings.toml`：

```toml
[llm.custom_models."glm-4.7-flash"]
api_format = "zai"               # openai/anthropic/dashscope/dashscope_sdk/zai ...
base_url = ""
api_key = "sk-xxx"
model_type = "text"              # text 纯文本 / multimodal 多模态
vendor = "zhipu"                 # 故障转移按 vendor 匹配
```

启动时加载到 `settings.custom_models`，故障转移时按 `vendor` 查找备选模型
（`_try_failover` 优先使用 `api_format` 字段重建 provider，兼容旧 `provider_type`）。
API Key 同时存入系统 keyring（S-01），展示时经 [mask.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/utils/mask.py) 脱敏（S-03）。

## 十三、Prompt 缓存统计

各 Provider 均支持读取 LLM 服务端的 Prompt Cache 命中统计，统一记录到 `Usage.cache_read_tokens`：

| Provider | 缓存字段来源 | 说明 |
|---|---|---|
| OpenAI | `usage.prompt_tokens_details.cached_tokens` | OpenAI 兼容接口标准字段 |
| Anthropic | `usage.cache_read_input_tokens` | Anthropic 原生字段 |
| DashScope | `usage.prompt_cache_hit_tokens` 或 `prompt_tokens_details.cached_tokens` | 双字段兼容 |
| Zai | `usage.prompt_tokens_details.cached_tokens` | 兼容 OpenAI 字段 |

### 协议语义差异（重要）

同一厂商可能提供多种兼容端点，`input_tokens` 语义**完全相反**：

| 协议 | input_tokens 含义 | 缓存命中字段 | 命中率分母 |
|---|---|---|---|
| OpenAI 兼容 | **含**缓存命中部分 | `prompt_cache_hit_tokens` | `input_tokens` |
| Anthropic 兼容 | **不含**缓存命中部分 | `cache_read_input_tokens` | `input_tokens + cache_read_tokens` |

典型场景：DeepSeek 同时提供 OpenAI 兼容端点（如 `deepseek-v4-pro`）和 Anthropic 兼容
端点（如 `deepseek-v4-flash`），两者 `input_tokens` 语义相反。

### parse_cache_usage 协议适配

[cache_policy.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/cache_policy.py) 的 `parse_cache_usage` 通过 `input_includes_cache` 参数区分协议：

```python
def parse_cache_usage(usage_obj, policy, input_tokens=0, *,
                      input_includes_cache: bool = True) -> tuple[int, int]:
    # 仅当 input_tokens 含缓存时做累计值合理性校验
    # Anthropic 协议下 input_tokens 不含缓存，命中数可能远大于 input_tokens（正常）
    if input_includes_cache and input_tokens and v > input_tokens * 3:
        continue  # 丢弃累计值异常
```

- `True`（默认，OpenAI/DashScope）：保留累计值校验
- `False`（Anthropic Provider 调用时传）：跳过校验

### /cost 命中率分母自动判断

[core_commands.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/commands/handlers/core_commands.py) 用数据特征判断协议，而非厂商名：

```python
if session.cache_read_tokens > session.input_tokens:
    # Anthropic 协议：input_tokens 不含缓存，分母需加上缓存命中
    denom = session.input_tokens + session.cache_read_tokens
else:
    # OpenAI/DashScope 协议：input_tokens 已含缓存
    denom = session.input_tokens
```

### Usage 数据结构

```python
@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0       # 缓存命中 token 数
    cache_creation_tokens: int = 0   # 缓存创建 token 数（Anthropic）
```

### 用途

- `--verbose` 模式下在终端显示缓存命中率
- 帮助用户评估 System Prompt 缓存效果（缓存命中越高，费用越低、延迟越小）
- 故障转移后缓存统计重置

## 十四、Mock Provider

[mock.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/mock.py) 提供无网络依赖的测试 Provider：

- 返回固定/回显式响应
- 用于单元测试、离线开发、CI 环境
- `api_format = "mock"` 时自动选择

## 十五、设计取舍

| 决策 | 选择 | 理由 |
|---|---|---|
| 抽象层 | Protocol + ABC | 统一接口，多协议共存 |
| 装配 | 注册表配置表（A-04） | 新增厂商加一行配置，不改代码 |
| 思考控制 | 配置表（A-02） | 各厂商参数差异收敛到一张表 |
| 事件模型 | Union of dataclass | 类型安全，pattern matching 友好 |
| 工具调用 | 完整 input dict | 简化处理，不发参数 delta |
| 思考过滤 | Provider 层过滤 | 统一接口，QueryLoop 不关心 |
| 错误提示 | 分类 + 脱敏（E-01） | 用户看到可操作的提示而非原始 HTML |
| 多端点 | DashScope 双端点 | 适配多模态 vs 纯文本模型 |
| 智谱接入 | 原生 SDK（zai） | 绕开兼容层，工具调用后稳定续生成 |
| 故障转移 | vendor 匹配 | 主模型失败自动切备选 |
