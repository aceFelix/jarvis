> ## Documentation Index
> Fetch the complete documentation index at: https://platform.minimaxi.com/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# AI SDK

> 通过 AI SDK 调用 MiniMax 模型

为了满足开发者对 [AI SDK](https://ai-sdk.dev) 生态的使用需求，MiniMax 提供了官方社区 Provider。通过简单的配置，即可将 MiniMax 的能力接入到 AI SDK 生态中。

## 快速开始

### 1. 安装 AI SDK 和 MiniMax Provider

<CodeGroup>
  ```bash npm theme={null}
  npm install ai vercel-minimax-ai-provider
  ```

  ```bash pnpm theme={null}
  pnpm add ai vercel-minimax-ai-provider
  ```
</CodeGroup>

### 2. 配置环境变量

```bash theme={null}
export MINIMAX_API_KEY=${YOUR_API_KEY}
```

### 3. 调用 API

```typescript TypeScript theme={null}
import { minimax } from 'vercel-minimax-ai-provider';
import { generateText } from 'ai';

const { text, reasoning } = await generateText({
  model: minimax('MiniMax-M3'),
  system: 'You are a helpful assistant.',
  prompt: 'Hi, how are you?',
});

if (reasoning) {
  console.log(`Thinking:\n${reasoning}\n`);
}
console.log(`Text:\n${text}\n`);
```

### 4. 特别注意

在多轮 Function Call 对话中，必须将完整的模型返回（即 assistant 消息）添加到对话历史，以保持思维链的连续性：

* 将完整的 `result.response.messages` 添加到消息历史（包含所有 assistant 和 tool 消息）

## 支持的模型

使用 AI SDK 时，支持 `MiniMax-M3` `MiniMax-M2.7` `MiniMax-M2.7-highspeed` `MiniMax-M2.5` `MiniMax-M2.5-highspeed` `MiniMax-M2.1` `MiniMax-M2.1-highspeed` `MiniMax-M2` 模型：

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
  AI SDK 兼容接口支持 `MiniMax-M3` `MiniMax-M2.7` `MiniMax-M2.7-highspeed` `MiniMax-M2.5` `MiniMax-M2.5-highspeed` `MiniMax-M2.1` `MiniMax-M2.1-highspeed` `MiniMax-M2`
  模型。如需使用其他模型，请使用标准的 MiniMax API 接口。
</Note>

## 兼容性说明

### 支持的参数

在使用 AI SDK 接入时，我们支持以下输入参数：

| 参数          | 支持状态 | 说明                                                         |
| :------------ | :------- | :----------------------------------------------------------- |
| `model`       | 完全支持 | 支持 `MiniMax-M3` `MiniMax-M2.7` `MiniMax-M2.7-highspeed` `MiniMax-M2.5` `MiniMax-M2.5-highspeed` `MiniMax-M2.1` `MiniMax-M2.1-highspeed` `MiniMax-M2` 模型 |
| `messages`    | 部分支持 | 支持文本和工具调用，不支持图像和文档输入                     |
| `maxTokens`   | 完全支持 | 最大生成 token 数                                            |
| `system`      | 完全支持 | 系统提示词                                                   |
| `temperature` | 完全支持 | 取值范围 \[0, 2]，控制输出随机性，建议取值 1                 |
| `toolChoice`  | 完全支持 | 工具选择策略                                                 |
| `tools`       | 完全支持 | 工具定义                                                     |
| `topP`        | 完全支持 | 核采样参数，取值范围 \[0, 1]，`MiniMax-M3` 默认值 0.95，`MiniMax-M2.x` 系列默认值 0.9 |

### Messages 字段支持

| 字段类型             | 支持状态 | 说明             |
| :------------------- | :------- | :--------------- |
| `role="user"`        | 完全支持 | 用户文本消息     |
| `role="assistant"`   | 完全支持 | 助手响应         |
| `role="tool"`        | 完全支持 | 工具调用结果     |
| `type="text"`        | 完全支持 | 文本内容         |
| `type="tool-call"`   | 完全支持 | 工具调用         |
| `type="tool-result"` | 完全支持 | 工具调用结果     |
| `type="image"`       | 不支持   | 暂不支持图像输入 |
| `type="file"`        | 不支持   | 暂不支持文件输入 |

## 示例代码

### 流式响应

```typescript TypeScript theme={null}
import { minimax } from 'vercel-minimax-ai-provider';
import { streamText } from 'ai';

console.log("Starting stream response...\n");
console.log("=".repeat(60));
console.log("Thinking Process:");
console.log("=".repeat(60));

const result = streamText({
  model: minimax('MiniMax-M3'),
  system: 'You are a helpful assistant.',
  prompt: 'Hi, how are you?',
  onError({ error }) {
    console.error(error);
  },
});

let inText = false;

for await (const part of result.fullStream) {
  if (part.type === 'reasoning') {
    // 流式输出思考过程
    process.stdout.write(part.text);
  } else if (part.type === 'text') {
    if (!inText) {
      inText = true;
      console.log("\n" + "=".repeat(60));
      console.log("Response Content:");
      console.log("=".repeat(60));
    }
    // 流式输出文本内容
    process.stdout.write(part.text);
  }
}

console.log("\n");
```

## 注意事项

<Warning>
  1. AI SDK 兼容接口目前支持 `MiniMax-M3` `MiniMax-M2.7` `MiniMax-M2.7-highspeed` `MiniMax-M2.5` `MiniMax-M2.5-highspeed` `MiniMax-M2.1` `MiniMax-M2.1-highspeed` `MiniMax-M2` 模型

  2. `temperature` 参数取值范围为 \[0, 2]，超出范围会返回错误

  3. 当前不支持图像和文档类型的输入

  4. 默认的 `minimax` Provider 实例使用 Anthropic 兼容 API 格式。如需使用 OpenAI 兼容格式，请使用 `minimaxOpenAI`。

  5. 更多信息请参阅 [MiniMax AI Provider on AI SDK](https://ai-sdk.dev/providers/community-providers/minimax) 和 [GitHub 仓库](https://github.com/MiniMax-AI/vercel-minimax-ai-provider)
</Warning>