> ## Documentation Index
> Fetch the complete documentation index at: https://platform.minimaxi.com/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# Anthropic SDK

> 通过 Anthropic SDK 调用 MiniMax 模型

为了满足开发者对 Anthropic API 生态的使用需求，我们的 API 新增了对 Anthropic API 格式的支持。通过简单的配置，即可将 MiniMax 的能力接入到 Anthropic API 生态中。

## 快速开始

### 1. 安装 Anthropic SDK

<CodeGroup>
  ```bash Python theme={null}
  pip install anthropic
  ```

  ```bash Node.js theme={null}
  npm install @anthropic-ai/sdk
  ```
</CodeGroup>

### 2. 配置环境变量

```bash theme={null}
export ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic
export ANTHROPIC_API_KEY=${YOUR_API_KEY}
```

### 3. 调用 API

```python Python theme={null}
import anthropic

client = anthropic.Anthropic()

message = client.messages.create(
    model="MiniMax-M3",
    max_tokens=1000,
    system="You are a helpful assistant.",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Hi, how are you?"
                }
            ]
        }
    ]
)

for block in message.content:
    if block.type == "thinking":
        print(f"Thinking:\n{block.thinking}\n")
    elif block.type == "text":
        print(f"Text:\n{block.text}\n")
```

### 4. 特别注意

在多轮 Function Call 对话中，必须将完整的模型返回（即 assistant 消息）添加到对话历史，以保持思维链的连续性：

* 将完整的 `response.content`（包含 thinking/text/tool\_use 等所有块）添加到消息历史
* `response.content` 是一个列表，包含多种类型的内容块，必须完整回传

## 支持的模型

使用 Anthropic SDK 时，支持 `MiniMax-M3` `MiniMax-M2.7` `MiniMax-M2.7-highspeed` `MiniMax-M2.5` `MiniMax-M2.5-highspeed` `MiniMax-M2.1` `MiniMax-M2.1-highspeed` `MiniMax-M2`  模型：

| 模型名称               | 上下文窗口 | 模型介绍                                                     |
| :--------------------- | :--------: | :----------------------------------------------------------- |
| MiniMax-M3             | 1,000,000  | **最新 M 系列语言模型，适用于 Agent 推理、工具调用、代码和长上下文任务** |
| MiniMax-M2.7           |  204,800   | **开启模型的自我迭代**（输出速度约 60 TPS）                  |
| MiniMax-M2.7-highspeed |  204,800   | **M2.7 极速版：效果不变，更快，更敏捷**（输出速度约 100 TPS） |
| MiniMax-M2.5           |  204,800   | **顶尖性能与极致性价比，轻松驾驭复杂任务**（输出速度约 60 TPS） |
| MiniMax-M2.5-highspeed |  204,800   | **M2.5 极速版：效果不变，更快，更敏捷**（输出速度约 100 TPS） |
| MiniMax-M2.1           |  204,800   | **强大多语言编程能力，全面升级编程体验**（输出速度约 60 TPS） |
| MiniMax-M2.1-highspeed |  204,800   | **M2.1 极速版：效果不变，更快，更敏捷**（输出速度约 100 TPS） |
| MiniMax-M2             |  204,800   | **专为高效编码与 Agent 工作流而生**                          |

<Note>
  TPS（Tokens Per Second）的计算方式详见[常见问题 > 接口相关](/docs/faq/about-apis#%E9%97%AE%E6%96%87%E6%9C%AC%E6%A8%A1%E5%9E%8B%E7%9A%84-tpstokens-per-second%E6%98%AF%E5%A6%82%E4%BD%95%E8%AE%A1%E7%AE%97%E7%9A%84)。
</Note>

<Note>
  Anthropic API 兼容接口支持 `MiniMax-M3` `MiniMax-M2.7` `MiniMax-M2.7-highspeed` `MiniMax-M2.5` `MiniMax-M2.5-highspeed` `MiniMax-M2.1` `MiniMax-M2.1-highspeed` `MiniMax-M2`
  模型。如需使用其他模型，请使用标准的 MiniMax API 接口。
</Note>

## 兼容性说明

### 支持的参数

在使用 Anthropic SDK 接入时，我们支持以下输入参数：

| 参数                 | 支持状态 | 说明                                                         |
| :------------------- | :------- | :----------------------------------------------------------- |
| `model`              | 完全支持 | 支持 `MiniMax-M3` `MiniMax-M2.7` `MiniMax-M2.7-highspeed` `MiniMax-M2.5` `MiniMax-M2.5-highspeed` `MiniMax-M2.1` `MiniMax-M2.1-highspeed` `MiniMax-M2` 模型 |
| `messages`           | 部分支持 | `MiniMax-M3` 支持文本、图片、视频、工具调用、工具结果和 thinking 内容块。M2.7、M2.5、M2.1 和 M2 系列仅支持文本与工具调用相关内容块，不支持图片和视频输入 |
| `max_tokens`         | 完全支持 | 最大生成 token 数                                            |
| `stream`             | 完全支持 | 流式响应                                                     |
| `system`             | 完全支持 | 系统提示词                                                   |
| `temperature`        | 完全支持 | 取值范围 \[0, 2]，控制输出随机性，建议取值 1                 |
| `tool_choice`        | 完全支持 | 工具选择策略                                                 |
| `tools`              | 完全支持 | 工具定义                                                     |
| `top_p`              | 完全支持 | 核采样参数，取值范围 \[0, 1]，`MiniMax-M3` 默认值 0.95，M2.x 系列默认值 0.9 |
| `thinking`           | 完全支持 | MiniMax-M3 默认关闭 thinking，可通过 `adaptive` 开启。M2.x 模型的 thinking 无法关闭。 |
| `metadata`           | 完全支持 | 元信息                                                       |
| `service_tier`       | 完全支持 | 请求准入服务层级。支持的取值为 `standard` 和 `priority`；省略时默认使用 `standard`。`priority` 的[价格](/docs/guides/pricing-paygo)为 `standard` 的 1.5 倍，并会确保请求获得优先准入，使其排在其他请求之前处理，从而带来更快响应并减少失败。 |
| `top_k`              | 忽略     | 该参数会被忽略                                               |
| `stop_sequences`     | 忽略     | 该参数会被忽略                                               |
| `mcp_servers`        | 忽略     | 该参数会被忽略                                               |
| `context_management` | 忽略     | 该参数会被忽略                                               |
| `container`          | 忽略     | 该参数会被忽略                                               |

### Thinking 控制

对于 `MiniMax-M3`，`thinking` 参数用于控制模型是否可以输出 `thinking` 内容块。

* 如果省略 `thinking`，默认关闭 thinking，响应不会包含 `thinking` 内容块。
* 设置 `thinking: {"type": "adaptive"}` 可显式开启 thinking。对于 MiniMax-M3，`adaptive` 等同于开启 thinking。
* 设置 `thinking: {"type": "disabled"}` 可显式保持 MiniMax-M3 的 thinking 输出关闭。
* 对于 M2.x 模型，thinking 无法关闭；即使传入 `thinking: {"type": "disabled"}`，thinking 仍会保持开启。

当响应包含 `thinking` 内容块时，后续轮次中应原样保留这些内容块，尤其是在工具调用对话中。

### Messages 字段支持

| 字段类型             | 支持状态 | 说明                                                         |
| :------------------- | :------- | :----------------------------------------------------------- |
| `type="text"`        | 完全支持 | 文本消息                                                     |
| `type="image"`       | 仅 M3    | 通过 URL 或 base64 输入图片，支持 JPEG、PNG、GIF、WEBP       |
| `type="video"`       | 仅 M3    | 通过 URL、base64 或 `mm_file://{file_id}` 输入视频，支持 MP4、AVI、MOV、MKV |
| `type="tool_use"`    | 完全支持 | 工具调用                                                     |
| `type="tool_result"` | 完全支持 | 工具调用结果                                                 |
| `type="thinking"`    | 完全支持 | 推理内容。多轮 thinking 对话中需要原样回带                   |

对 `MiniMax-M3`，URL 或 base64 视频最大 50 MB，图片最大 10 MB，请求体最大 64 MB。更大的视频请通过 Files API 上传后传入 `mm_file://{file_id}`，Files API 视频最大 512 MB。

图片 token 用量会随图片尺寸和内容变化。以下是单张图片的粗略估算；准确用量以 `POST /anthropic/v1/messages/count_tokens` 或响应中的 `usage` 为准：

| `detail`  | 单张图片粗略 token 用量       |
| :-------- | :---------------------------- |
| `low`     | 通常为几百 token，最高约 600  |
| `default` | 通常约 1k-3k token，最高约 5k |
| `high`    | 通常为数千 token，最高约 15k+ |

Anthropic API 兼容接口也支持 `POST /anthropic/v1/messages/count_tokens`，可用于 `MiniMax-M3` 调用前预估输入 token 用量，不会生成模型输出。

## 示例代码

### 流式响应

```python Python theme={null}
import anthropic

client = anthropic.Anthropic()

print("Starting stream response...\n")
print("=" * 60)
print("Thinking Process:")
print("=" * 60)

stream = client.messages.create(
    model="MiniMax-M3",
    max_tokens=1000,
    system="You are a helpful assistant.",
    messages=[
        {"role": "user", "content": [{"type": "text", "text": "Hi, how are you?"}]}
    ],
    stream=True,
)

reasoning_buffer = ""
text_buffer = ""

for chunk in stream:
    if chunk.type == "content_block_start":
        if hasattr(chunk, "content_block") and chunk.content_block:
            if chunk.content_block.type == "text":
                print("\n" + "=" * 60)
                print("Response Content:")
                print("=" * 60)

    elif chunk.type == "content_block_delta":
        if hasattr(chunk, "delta") and chunk.delta:
            if chunk.delta.type == "thinking_delta":
                # 流式输出 thinking 过程
                new_thinking = chunk.delta.thinking
                if new_thinking:
                    print(new_thinking, end="", flush=True)
                    reasoning_buffer += new_thinking
            elif chunk.delta.type == "text_delta":
                # 流式输出文本内容
                new_text = chunk.delta.text
                if new_text:
                    print(new_text, end="", flush=True)
                    text_buffer += new_text

print("\n")
```

## 注意事项

如果在使用模型过程中遇到任何问题：

* 通过邮箱 [Model@minimaxi.com](mailto:Model@minimaxi.com) 等官方渠道联系我们的技术支持团队
* 在我们的 [Github](https://github.com/MiniMax-AI/MiniMax-M2/issues) 仓库提交Issue

<Warning>
  1. Anthropic API 兼容接口目前支持 `MiniMax-M3` `MiniMax-M2.7` `MiniMax-M2.7-highspeed` `MiniMax-M2.5` `MiniMax-M2.5-highspeed` `MiniMax-M2.1` `MiniMax-M2.1-highspeed` `MiniMax-M2`  模型

  2. `temperature` 参数取值范围为 \[0, 2]，推荐使用1.0，超出范围会返回错误

  3. 部分 Anthropic 参数（如 `top_k`、`stop_sequences`、`mcp_servers`、`context_management`、`container`）会被忽略

  4. `MiniMax-M3` 支持通过 Anthropic 兼容内容块输入图片和视频；M2.7、M2.5、M2.1 和 M2 系列仅支持文本与工具调用相关内容块
</Warning>