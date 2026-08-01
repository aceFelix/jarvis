# J.A.R.V.I.S 厂商接入指南

> 各 LLM 厂商的接入方式、环境变量、API 格式。

---

## 快速方式：`jarvis --init`

```bash
jarvis --init
```

交互式引导：选厂商 → 确认模型 → 选多模态/纯文本 → 输 Key → 自动测试连接 → 保存。一行命令搞定所有配置。

---

## 各厂商详情

### 阿里云 DashScope（通义千问）

| 项 | 值 |
|---|---|
| 环境变量 | `DASHSCOPE_API_KEY` |
| API 格式 | `openai`（OpenAI 兼容） |
| base_url | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 推荐模型 | `qwen3.7-plus`（多模态视觉） |
| 实时语音 | ✅ 支持（需 `dashscope_api_key`） |
| 深度思考 | ✅ 支持 |

```toml
# configs/settings.toml
provider = "dashscope"
api_format = "openai"
model = "qwen3.7-plus"
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
```

---

### DeepSeek

| 项 | 值 |
|---|---|
| 环境变量 | `DEEPSEEK_API_KEY` |
| API 格式 | `openai`（OpenAI 兼容） |
| base_url | `https://api.deepseek.com/v1` |
| 推荐模型 | `deepseek-v4-flash` |
| 深度思考 | ✅ 支持（thinking={"type":"enabled"}） |

```toml
provider = "deepseek"
api_format = "openai"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com/v1"
```

---

### OpenAI

| 项 | 值 |
|---|---|
| 环境变量 | `OPENAI_API_KEY` |
| API 格式 | `openai`（原生） |
| base_url | `https://api.openai.com/v1` |
| 推荐模型 | `gpt-5.5` |
| 深度思考 | ❌ 不支持 |

```toml
provider = "openai"
api_format = "openai"
model = "gpt-5.5"
```

---

### 智谱 AI（GLM）

支持两种接入方式：

#### 方式 A：OpenAI 兼容（推荐）

| 项 | 值 |
|---|---|
| 环境变量 | `ZAI_API_KEY` |
| API 格式 | `openai`（OpenAI 兼容） |
| base_url | `https://open.bigmodel.cn/api/paas/v4` |
| 推荐模型 | `glm-4.7-flash（免费模型）` |
| 深度思考 | ✅ GLM-4.7 以上支持 |

```toml
provider = "zhipu"
api_format = "openai"
model = "glm-4.7-flash"
base_url = "https://open.bigmodel.cn/api/paas/v4"
```

#### 方式 B：原生 SDK

| 项 | 值 |
|---|---|
| 环境变量 | `ZAI_API_KEY` |
| API 格式 | `zai_sdk`（智谱原生 SDK） |
| base_url | 不需要（SDK 自动连接） |
| 推荐模型 | `glm-5.2` |

```bash
pip install zai-sdk
```

```toml
provider = "zai"
api_format = "zai_sdk"
model = "glm-5.2"
```

> 原生 SDK 更稳定，绕过 OpenAI 兼容层。

---

### Anthropic（Claude）

| 项 | 值 |
|---|---|
| 环境变量 | `ANTHROPIC_API_KEY` |
| API 格式 | `anthropic`（原生协议） |
| base_url | 不需要（SDK 自动连接） |
| 推荐模型 | `claude-sonnet-4.8` |
| 深度思考 | ✅ 原生 `thinking` 块 |
| 上下文缓存 | ✅ 显式断点标记，90% 折扣 |

```toml
provider = "anthropic"
api_format = "anthropic"
model = "claude-sonnet-4.8"
```

---

### Moonshot（Kimi）

| 项 | 值 |
|---|---|
| 环境变量 | `KIMI_API_KEY` |
| API 格式 | `openai`（OpenAI 兼容） |
| base_url | `https://api.moonshot.cn/v1` |
| 推荐模型 | `kimi-k3` |

---

### MiniMax

| 项 | 值 |
|---|---|
| 环境变量 | `MINIMAX_API_KEY` |
| API 格式 | `openai` |
| base_url | `https://api.minimax.chat/v1` |
| 推荐模型 | `minimax-m3` |

---

### 小米 MiMo

| 项 | 值 |
|---|---|
| 环境变量 | `MIMO_API_KEY` |
| API 格式 | `openai` |
| base_url | `https://api.mimo.chat/v1` |
| 推荐模型 | `mimo-v2.5` |

---

### SiliconFlow（硅基流动）

| 项 | 值 |
|---|---|
| API 格式 | `openai` |
| base_url | `https://api.siliconflow.cn/v1` |
| 推荐模型 | `deepseek-ai/deepseek-v4-flash` |

> 开源模型托管平台，支持 DeepSeek/Qwen/Llama 等。

---

### Google Gemini

| 项 | 值 |
|---|---|
| API 格式 | `openai` |
| base_url | `https://generativelanguage.googleapis.com/v1beta/openai` |
| 推荐模型 | `gemini-2.5-flash` |

---

### 其他 OpenAI 兼容服务

任何支持 OpenAI `/v1/chat/completions` 接口的服务都可接入：

```toml
provider = "openai_compatible"
api_format = "openai"
model = "your-model-name"
base_url = "https://your-api-endpoint.com/v1"
```

包括但不限于：Ollama 本地模型、vLLM、Groq、Together AI、Fireworks 等。

---

## 添加自定义模型到 /models 列表

```toml
# ~/.jarvis/settings.toml

[llm.custom_models."my-custom-model"]
name = "my-custom-model"
base_url = "https://api.example.com/v1"
api_key = "sk-xxx"
provider_type = "openai"
model_type = "multimodal"
vendor = "openai"
```

或在 REPL 中运行 `/models` → 选择「+ 添加其他模型」交互式添加。

---

## 切换模型

```bash
# REPL 内
/model deepseek-v4-flash       # 前缀匹配
/models                     # 交互式列表选择

# CLI
jarvis --model deepseek-v4-flash
```
