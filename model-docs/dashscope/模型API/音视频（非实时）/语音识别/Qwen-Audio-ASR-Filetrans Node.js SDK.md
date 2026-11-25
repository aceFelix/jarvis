> ## Documentation Index
> Fetch the complete documentation index at: https://docs.bailian.console.aliyun.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Qwen-Audio-ASR-Filetrans Node.js SDK

> 通过语音 Node.js SDK 调用 Qwen-Audio-ASR-Filetrans，提交录音文件 URL、查询异步任务并下载转写结果。

## 安装 SDK <span id="installation" />

需要 Node.js 18 或以上版本。下载 [SDK 0.1.0 安装包](https://g-adoc.alcasset.com/media/maas_docs/sfm-cn/common/files/6a4b3c2d1e0f90c7.tgz)后，在项目目录运行：

```bash
npm install ./dashscope-sdk-nodejs-0.1.0.tgz
```

## 快速开始 <span id="quick-start" />

在[控制台](https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key)获取 API Key，并配置为环境变量 `DASHSCOPE_API_KEY`。

将以下示例保存为 `example.mjs`，运行 `node example.mjs`。

```js expandable
import { TranscriptionClient } from 'dashscope-sdk-nodejs/asr'

const client = new TranscriptionClient({
  apiKey: process.env.DASHSCOPE_API_KEY,
  workspaceId: process.env.DASHSCOPE_WORKSPACE_ID,
  baseUrl: 'dashscope.aliyuncs.com',
  model: 'qwen-audio-3.1-asr-flash-filetrans',
})
const job = await client.transcribe(
  'https://dashscope.oss-cn-beijing.aliyuncs.com/audios/welcome.mp3',
)
for (const result of job.results) {
  if (result.subtaskStatus === 'SUCCEEDED') {
    console.log('Transcript:', result.text)
  } else {
    console.error(result.code, result.message)
    process.exitCode = 1
  }
}
```

## 接口参考 <span id="api-reference" />

### 构造参数 <span id="constructor" />

`TranscriptionClient` 的构造参数如下。

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "22%" }} />

    <col style={{ width: "18%" }} />

    <col style={{ width: "8%" }} />

    <col style={{ width: "52%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        参数
      </th>

      <th style={{ verticalAlign: "top" }}>
        类型
      </th>

      <th style={{ verticalAlign: "top" }}>
        必填
      </th>

      <th style={{ verticalAlign: "top" }}>
        说明
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `apiKey`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        API Key。默认读取 `DASHSCOPE_API_KEY`。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `workspaceId`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        业务空间 ID。默认读取 `DASHSCOPE_WORKSPACE_ID`。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `baseUrl`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        服务域名，默认 `dashscope.aliyuncs.com`（北京）。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `model`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        模型 ID，默认 `qwen-audio-3.1-asr-flash-filetrans`。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `pollIntervalMs`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `number`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        查询间隔，默认 `3000` ms。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `pollTimeoutMs`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `number`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        轮询超时（ms），默认 `600000`。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `requestTimeoutMs`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `number`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        单次 HTTP 请求超时（ms），默认 `30000`；`0` 表示不限制单次请求。
      </td>
    </tr>
  </tbody>
</table>

### 转写参数 <span id="transcription-parameters" />

`transcribe()` 和 `submitTask()` 的第二个参数为 `parameters`，第三个参数可传入 `context`。模型参数的完整说明见 [HTTP API](/zh/model-studio/qwen-audio-asr-filetrans-http-api)。

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "22%" }} />

    <col style={{ width: "18%" }} />

    <col style={{ width: "8%" }} />

    <col style={{ width: "52%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        参数
      </th>

      <th style={{ verticalAlign: "top" }}>
        类型
      </th>

      <th style={{ verticalAlign: "top" }}>
        必填
      </th>

      <th style={{ verticalAlign: "top" }}>
        说明
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `fileUrls`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string | string[]`
      </td>

      <td style={{ verticalAlign: "top" }}>
        是
      </td>

      <td style={{ verticalAlign: "top" }}>
        公网可访问的音频 URL；每次提交一个文件。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `parameters.keep_dialect`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `boolean`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        保留方言表达，默认 `false`。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `parameters.vocabulary_id`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        预编译热词列表 ID。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `parameters.vocabulary`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `Record<string, number>`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        即时热词：键为词，值为权重 `[1,5]` 或 `50`。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `parameters.channel_id`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `number[]`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        音轨索引，默认 `[0]`。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `parameters.special_word_filter`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string | Record<string, unknown>`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        敏感词过滤配置，可传 JSON 字符串或对象。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `parameters.diarization_enabled`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `boolean`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        单声道音频的说话人分离，默认 `false`。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `parameters.speaker_count`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `number`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        说话人数量参考值，范围 `[2,100]`，需启用说话人分离。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `parameters.language_hints`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string[]`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        识别语言提示，最多使用前 4 个值；未设置时自动识别。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `callOpts.context / submitOpts.context`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `AsrContextMessage[]`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        识别上下文。消息格式为 `{ role, content: [{ type: "input_text", text }] }`，`role` 为 `user` 或 `assistant`；每种角色最多 5 条，文本共少于 400 字符，按对话轮次排列。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `callOpts.onProgress`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `(status: string, response: QueryTaskResponse) => void`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        查询进度回调，接收 `status` 和原始响应 `response`。
      </td>
    </tr>
  </tbody>
</table>

### 主要方法 <span id="methods" />

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "40%" }} />

    <col style={{ width: "18%" }} />

    <col style={{ width: "42%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        方法
      </th>

      <th style={{ verticalAlign: "top" }}>
        返回值
      </th>

      <th style={{ verticalAlign: "top" }}>
        说明
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `transcribe(fileUrls: string | string[], parameters?: TranscriptionParameters, callOpts?: TranscribeCallOptions)`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `Promise<TranscriptionJobResult>`
      </td>

      <td style={{ verticalAlign: "top" }}>
        提交音频、轮询并下载结果；`callOpts` 可设置上下文和进度回调。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `submitTask(fileUrls: string | string[], parameters?: TranscriptionParameters, submitOpts?: TranscriptionSubmitOptions)`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `Promise<{ taskId: string; taskStatus: string }>`
      </td>

      <td style={{ verticalAlign: "top" }}>
        提交任务并返回任务 ID、初始状态；`submitOpts.context` 设置识别上下文。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `fetchTask(taskId: string, requestTimeoutMs?: number)`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `Promise<QueryTaskResponse>`
      </td>

      <td style={{ verticalAlign: "top" }}>
        查询一次任务，返回状态和结果下载链接。
      </td>
    </tr>
  </tbody>
</table>

### 返回结果 <span id="results" />

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "22%" }} />

    <col style={{ width: "26%" }} />

    <col style={{ width: "52%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        字段
      </th>

      <th style={{ verticalAlign: "top" }}>
        类型
      </th>

      <th style={{ verticalAlign: "top" }}>
        说明
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `taskId`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        任务 ID。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `taskStatus`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        任务状态。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `results`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `FileTranscriptionResult[]`
      </td>

      <td style={{ verticalAlign: "top" }}>
        文件结果列表，包含 `subtaskStatus`、`text`、`transcription`；失败项含 `code`、`message`。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `usage`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `RecognitionUsage | undefined`
      </td>

      <td style={{ verticalAlign: "top" }}>
        服务端返回的用量统计。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `raw`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `QueryTaskResponse`
      </td>

      <td style={{ verticalAlign: "top" }}>
        原始任务查询响应。
      </td>
    </tr>
  </tbody>
</table>
