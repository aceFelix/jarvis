> ## Documentation Index
> Fetch the complete documentation index at: https://docs.bailian.console.aliyun.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Qwen-Audio-ASR-Filetrans非实时语音识别Android SDK

> 本文档提供了Qwen-Audio-ASR-Filetrans非实时语音识别Android SDK的详细使用指南，帮助您将语音转换为文本。

<meta property="og:title" content="Qwen-Audio-ASR-Filetrans非实时语音识别Android SDK" />

关于支持的音频格式、文件大小限制、时长限制等输入要求，请参见[音频规格](/zh/model-studio/asr-model#asr_audio_spec02)。

## 快速开始 <span id="abf215f434v5x" />

1. <strong>获取API Key：</strong>[获取API Key](/zh/model-studio/get-api-key)，为安全起见，推荐将API Key配置到环境变量。
2. <strong>下载SDK并运行示例代码：</strong>

   - [下载最新SDK整合包](/zh/model-studio/sdk-selection-and-download)。
   - 解压 ZIP 包。在 `app/libs` 目录中获取 AAR 格式 SDK，并添加到项目依赖。<br />需要 Android CPP 接入时，使用 ZIP 包内的 `android_libs` 与 `android_include` 获取动态库和头文件。
   - 用 Android Studio 打开工程。示例代码位于`DashFunAsrFileTranscriberActivity.java`，替换 API Key 后体验功能。

### 调用步骤 <span id="0701a2d0b75vm" />

<Tabs>
  <Tab title="同步模式">
    1. 初始化 SDK
    2. 按业务需求配置相关参数
    3. 当 `async_request` 设为 `false` 时，调用 `startFileTranscriber` 发送非实时语音识别请求，并等待结果返回。
    4. 在`onFileTransEventCallback`接口中监听`EVENT_FILE_TRANS_RESULT` 事件，获取最终识别结果
    5. 调用 `release` 释放 SDK 资源
  </Tab>

  <Tab title="异步模式">
    1. 初始化 SDK
    2. 按业务需求配置相关参数
    3. 当 `async_request` 设为 `true` 时，调用 `startFileTranscriber` 发送非实时语音识别请求。
    4. 调用 `queryFileTranscriber` 主动查询识别进度/结果
    5. 在`onFileTransEventCallback`接口中监听`EVENT_FILE_TRANS_QUERY_RESULT`事件，获取当前查询结果
    6. 在`onFileTransEventCallback`接口中监听 `EVENT_FILE_TRANS_RESULT` 事件，获取最终识别结果
    7. 调用 `release` 释放 SDK 资源
  </Tab>
</Tabs>

## 请求参数 <span id="a96bfa6340jdd" />

### 连接与控制参数 <span id="57acf5ecc1w8j" />

通过在`initialize`接口的`parameters`参数中传入一个JSON字符串来配置。

**参数示例**

以下为 JSON 字符串示例，参数未完整列出。请按实际需求在编码时补充：

```json
{
    "url": "wss://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription",
    "apikey": "st-****",
    "device_id": "my_device_id",
    "service_mode": "1"
}
```

<strong>参数说明</strong>

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "15.33%" }} />

    <col style={{ width: "12.26%" }} />

    <col style={{ width: "9.02%" }} />

    <col style={{ width: "63.39%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>类型</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>是否必须</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>说明</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `url`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        是
      </td>

      <td style={{ verticalAlign: "top" }}>
        服务地址：

        <ul>
          <li>
            <code>wss\://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription</code>
          </li>

          <li>
            华北2（北京）：<code>wss\://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/asr/transcription</code>
          </li>

          <li>
            新加坡：<code>wss\://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api/v1/services/audio/asr/transcription</code>
          </li>
        </ul>

        调用时，请将 <code>{WorkspaceId}</code> 替换为真实的 <Link href="/zh/model-studio/regions#h2_migrate_domain">Workspace ID</Link>。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `apikey`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        是
      </td>

      <td style={{ verticalAlign: "top" }}>
        API Key。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `service_mode`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        是
      </td>

      <td style={{ verticalAlign: "top" }}>
        运行模式。非实时语音识别固定为 <code>"1"</code>。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `device_id`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        是
      </td>

      <td style={{ verticalAlign: "top" }}>
        用于标识终端用户的唯一字符串，可设为应用内用户ID或客户端生成的设备唯一标识符。此ID主要用于日志追踪和问题排查。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `debug_path`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        日志文件的存储路径。

        此参数仅在调用<code>initialize</code>接口时将<code>save\_log</code>设为true时生效。此时必须设置日志文件路径，否则将报错。

        本地最多保留两个日志文件。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `max_log_file_size`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `int`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        设定日志文件的最大字节数。

        此参数仅在调用<code>initialize</code>接口时将<code>save\_log</code>设为true时生效。

        默认值：104857600（100 \* 1024 \* 1024 字节, 即 100MiB）。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `log_track_level`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `int`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        控制通过日志回调（<code>onFileTransLogTrackCallback</code>）对外发送的日志内容的过滤级别。

        默认值：2。

        取值范围：

        <ul>
          <li>
            0：LOG\_LEVEL\_VERBOSE
          </li>

          <li>
            1：LOG\_LEVEL\_DEBUG
          </li>

          <li>
            2：LOG\_LEVEL\_INFO
          </li>

          <li>
            3：LOG\_LEVEL\_WARNING
          </li>

          <li>
            4：LOG\_LEVEL\_ERROR
          </li>

          <li>
            5：LOG\_LEVEL\_NONE（表示关闭此功能）
          </li>
        </ul>

        注意：<code>log\_track\_level</code>与<code>level</code>（通过<code>initialize</code>接口设置）共同决定最终回调的日志。一条日志的级别数值必须同时大于或等于<code>log\_track\_level</code>和<code>level</code>的值，才会被回调。例如，<code>log\_track\_level</code>设为2 (INFO)，<code>level</code>设为3 (WARNING)，则只有WARNING及以上级别（数值>=3）的日志才会被回调。
      </td>
    </tr>
  </tbody>
</table>

### 语音识别效果参数 <span id="d20cce9518kla" />

通过`startFileTranscriber`接口配置所有语音识别效果参数。

**参数示例**

以下为 JSON 字符串示例，参数未完整列出。请按实际需求在编码时补充：

```json expandable
{
    "apikey": "st-****",
    "file_urls": [
        "{YOUR_AUDIO_URL}"
    ],
    "async_request": false,
    "nls_config": {
        "model":"qwen-audio-3.0-asr-flash-filetrans",
        "diarization_enabled": false
    }
}
```

<strong>参数说明</strong>

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "26.33%" }} />

    <col style={{ width: "13.35%" }} />

    <col style={{ width: "9.83%" }} />

    <col style={{ width: "50.49%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>类型</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>是否必须</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>说明</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `file_urls`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `array[string]`
      </td>

      <td style={{ verticalAlign: "top" }}>
        是
      </td>

      <td style={{ verticalAlign: "top" }}>
        音视频文件转写的URL列表，支持HTTP / HTTPS协议，单次请求仅支持1个URL。关于支持的音频格式、文件大小限制、时长限制等输入要求，请参见[音频规格](/zh/model-studio/asr-model#asr_audio_spec02)。

        若录音文件存储在阿里云OSS，使用RESTful API方式支持使用以`oss://`为前缀的临时 URL，使用SDK方式不支持使用以 oss\://为前缀的临时 URL。

        <Tip>
          - 临时 URL 有效期48小时，过期后无法使用，<strong>请勿用于生产环境。</strong>
          - 文件上传凭证接口限流为 100 QPS 且不支持扩容，<strong>请勿用于生产环境、高并发及压测场景。</strong>
          - 生产环境建议使用[阿里云OSS](https://help.aliyun.com/zh/oss/user-guide/what-is-oss) 等稳定存储，确保文件长期可用并规避限流问题。
          - 录音文件URL设置成OSS临时公网访问不通该如何处理？请求头中将`X-DashScope-OssResourceResolve`设为`enable`（不推荐该方式）。

            SDK不支持对请求头进行配置。
        </Tip>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `async_request`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `boolean`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        语音识别是否为异步请求。

        默认值：`false`。

        取值范围：

        - true：异步请求
        - false：同步请求
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `apikey`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        如果连接与控制参数的`apikey`使用的是[临时API Key](/zh/model-studio/generate-temporary-api-key)，可在此处进行更新，以免超时失效。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `nls_config`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `object`
      </td>

      <td style={{ verticalAlign: "top" }}>
        是
      </td>

      <td style={{ verticalAlign: "top" }}>
        语音识别核心配置对象，包含模型选择、识别效果控制等关键参数。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `nls_config.model`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        是
      </td>

      <td style={{ verticalAlign: "top" }}>
        模型名称。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `nls_config.special_word_filter`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `object`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        指定在语音识别过程中需要处理的敏感词，并支持对不同敏感词设置不同的处理方式。详情请参见[敏感词过滤](/zh/model-studio/non-realtime-speech-recognition-user-guide#nrt03_sensitive_h3)。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `nls_config.channel_id`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `array[integer]`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        指定在多音轨音频文件中需要识别的音轨索引，索引从 0 开始。例如，\[0] 表示识别第一个音轨，\[0, 1] 表示同时识别第一和第二个音轨。如果省略此参数，则默认处理第一个音轨。

        <Tip>
          指定的每一个音轨都将独立计费。例如，为单个文件请求 \[0, 1] 会产生两笔独立的费用。
        </Tip>

        默认值：\[0]。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `nls_config.diarization_enabled`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `boolean`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        是否启用说话人分离，默认关闭。

        仅适用于单声道音频，多声道音频不支持说话人分离。

        启用该功能后，识别结果中将显示`speaker_id`字段，用于区分不同说话人。

        <Note>
          如果启用说话人分离功能，建议音频时长不超过2小时，否则可能导致识别失败或超时。
        </Note>

        默认值：false。

        有关`speaker_id`的示例，请参见[识别结果说明](/zh/model-studio/qwen-audio-asr-filetrans-python-sdk#a9021178ccl7s)。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `nls_config.speaker_count`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `integer`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        <Tip>
          仅在开启说话人分离功能（`diarization_enabled`设置为`true`）时生效。
        </Tip>

        说话人数量参考值。取值范围为2至100的整数（包含2和100）。

        默认自动判断说话人数量，如果配置此项，只能辅助算法尽量输出指定人数，无法保证一定会输出此人数。

        无默认值。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `nls_config.vocabulary_id`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `string`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        预编译热词列表 ID。

        需预先调用创建热词列表接口生成，识别时传入该 ID 即可使用列表中的热词。

        适用于词汇已知且相对稳定、需要跨请求复用同一词表的场景。

        使用方法请参见[预编译热词](/zh/model-studio/improve-asr-accuracy#hw_precompiled_h3)。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `nls_config.input_context`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `array`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        消息列表，包含用于提升识别效果的可选对话上下文。

        <Tip>
          上下文功能用于提升专有词汇的识别准确率，使用方法请参见<Link href="/zh/model-studio/improve-asr-accuracy#ctx_enhance_h2">上下文增强</Link>。

          上下文消息（<code>input\_text</code> 和 <code>text</code> 类型）各最多 5 条，超出时保留最近的 5 条。每轮上下文文本总长度（<code>user</code> 和 <code>assistant</code> 的 <code>text</code> 字段长度之和）不超过 400 个字符，每个字符计为 1，超出部分从末尾截断。
        </Tip>

        <Tip>
          携带上下文时，<code>messages</code> 中的消息必须按对话轮次排列，每轮中 <code>user</code>（<code>input\_text</code> 类型）必须在对应的 <code>assistant</code>（<code>text</code> 类型）之前；包含 <code>input\_audio</code> 的 <code>user</code> 消息必须放在 <code>messages</code> 数组的最后。
        </Tip>

        ```json expandable
        [
          {
            "role": "user",
            "content": [
              {
                "type": "input_text",
                "text": "你好啊，我是通义千问，有什么可以帮助你的？"
              }
            ]
          }
        ]
        ```
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `nls_config.instant_vocabulary`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `object`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        即时热词，以键值对形式传入：键为热词文本（<code>string</code>），值为热词权重（<code>integer</code>），无需预先创建热词列表。适用于临时性、会话级别的热词优化。

        权重取值范围为 1 至 5 或 50。取 1 至 5 时，值越大，模型越倾向于输出该词；取 50 时为超级热词，召回率大幅提升，但超级热词最多不超过 50 个。

        与预编译热词同时配置时，系统会合并两类热词；合并后超过 2000 个时，随机选择 2000 个使用。使用方法请参见<Link href="/zh/model-studio/improve-asr-accuracy#hw_instant_h3">即时热词</Link>。

        <Tip>
          即时热词的适用模型及限制请参见[即时热词](/zh/model-studio/improve-asr-accuracy#hw_instant_h3)。
        </Tip>

        ```json
        {
          "张三": 5,
          "李四": 5
        }
        ```
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `nls_config.language_hints`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `array[string]`
      </td>

      <td style={{ verticalAlign: "top" }}>
        否
      </td>

      <td style={{ verticalAlign: "top" }}>
        设置待识别语言代码。如果无法提前确定语种，可不设置，模型会自动识别语种。

        最多支持设置 4 个值，超出时仅前 4 个生效。

        <Accordion title="点击查看支持的语言代码">
          - zh: 中文
          - en: 英文
          - ja: 日语
          - ko：韩语
          - vi：越南语
          - th：泰语
          - id：印尼语
          - ms：马来语
          - tl：菲律宾语
          - hi：印地语
          - ar：阿拉伯语
          - fr：法语
          - de：德语
          - es：西班牙语
          - pt：葡萄牙语
          - ru：俄语
          - it：意大利语
          - nl：荷兰语
          - sv：瑞典语
          - da：丹麦语
          - fi：芬兰语
          - no：挪威语
          - el：希腊语
          - pl：波兰语
          - cs：捷克语
          - hu：匈牙利语
          - ro：罗马尼亚语
          - bg：保加利亚语
          - hr：克罗地亚语
          - sk：斯洛伐克语
        </Accordion>
      </td>
    </tr>
  </tbody>
</table>

## 关键接口 <span id="737b46fda6cbf" />

### NativeNui <span id="4bed3999d9kx0" />

#### initialize <span id="ae6d7dd9cfad3" />

初始化语音识别SDK实例。SDK为单例模式，在调用`release`前禁止重复初始化。

此接口会引起阻塞，应在非UI线程调用。

<strong>方法签名</strong>

```java
public synchronized int initialize(final INativeFileTransCallback callback,
                                   String parameters,
                                   final Constants.LogLevel level,
                                   final boolean save_log)
```

<strong>参数说明</strong>

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "25.88%" }} />

    <col style={{ width: "25.7%" }} />

    <col style={{ width: "48.42%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>类型</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>说明</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `callback`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `INativeFileTransCallback`
      </td>

      <td style={{ verticalAlign: "top" }}>
        事件和数据回调接口的实现。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `parameters`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        JSON字符串，包含鉴权、连接和调试参数。参见连接与控制参数。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `level`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `Constants.LogLevel`
      </td>

      <td style={{ verticalAlign: "top" }}>
        控制SDK自身日志的打印级别。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `save_log`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `boolean`
      </td>

      <td style={{ verticalAlign: "top" }}>
        是否保存本地日志。若为<code>true</code>，须在连接与控制参数中通过<code>debug\_path</code>指定路径，并可通过<code>max\_log\_file\_size</code>设置文件大小。
      </td>
    </tr>
  </tbody>
</table>

#### setParams <span id="a23e0d85d7ymt" />

此接口用于独立设置或更新 `nls_config` 参数。如果所有参数都在`startFileTranscriber`中一次性提供，则无需调用此方法。

<strong>方法签名</strong>

```java
public synchronized int setParams(String params);
```

<strong>参数说明</strong>

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "25.88%" }} />

    <col style={{ width: "25.7%" }} />

    <col style={{ width: "48.42%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>类型</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>说明</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `params`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        语音识别效果参数中的`nls_config`参数，`nls_config`之外的参数不支持通过该方法进行设置。

        示例：

        ```json
        {
            "nls_config": {
                "model":"qwen-audio-3.0-asr-flash-filetrans",
                "diarization_enabled": false
            }
        }
        ```
      </td>
    </tr>
  </tbody>
</table>

#### startFileTranscriber <span id="7d33691bdb32v" />

开始识别。

<strong>方法签名</strong>

```java
public synchronized int startFileTranscriber(String params, byte[] task_id)
```

<strong>参数说明</strong>

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "25.88%" }} />

    <col style={{ width: "25.7%" }} />

    <col style={{ width: "48.42%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>类型</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>说明</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `params`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        语音识别效果参数。

        示例：

        ```json expandable
        {
            "file_urls": [
                "{YOUR_AUDIO_URL}"
            ],
            "async_request": false,
            "nls_config": {
                "model":"qwen-audio-3.0-asr-flash-filetrans",
                "diarization_enabled": false
            }
        }
        ```
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `task_id`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `byte[]`
      </td>

      <td style={{ verticalAlign: "top" }}>
        任务ID，SDK内部生成随机字符串，在此接口调用成功后可获得task\_id。
      </td>
    </tr>
  </tbody>
</table>

#### queryFileTranscriber <span id="047417083bahi" />

此接口用于主动查询一个异步任务的当前状态和结果。调用成功后，结果将通过`onFileTransEventCallback`回调中的 `EVENT_FILE_TRANS_QUERY_RESULT` 事件返回。

<Note>
  此处传入的 `task_id` 从 `EVENT_FILE_TRANS_UPLOADED` 事件中获取。
</Note>

<strong>方法签名</strong>

```java
public synchronized int queryFileTranscriber(String task_id)
```

<strong>参数说明</strong>

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "25.88%" }} />

    <col style={{ width: "25.7%" }} />

    <col style={{ width: "48.42%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>类型</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>说明</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `task_id`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        待查询的任务 ID，从 <code>EVENT\_FILE\_TRANS\_UPLOADED</code> 事件中获取。
      </td>
    </tr>
  </tbody>
</table>

#### cancelFileTranscriber <span id="8c464959f9lm9" />

立即取消当前任务。

<strong>方法签名</strong>

```java
public synchronized int cancelFileTranscriber(String task_id)
```

<strong>参数说明</strong>

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "25.88%" }} />

    <col style={{ width: "25.7%" }} />

    <col style={{ width: "48.42%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>类型</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>说明</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `task_id`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        待取消的任务 ID，从 <code>EVENT\_FILE\_TRANS\_UPLOADED</code> 事件中获取。
      </td>
    </tr>
  </tbody>
</table>

#### release <span id="44bf12ed9a4g9" />

释放SDK所有内部资源。此方法调用后，SDK实例将变为不可用状态，如需再次使用，必须重新调用`initialize`进行初始化。

<strong>方法签名</strong>

```java
public synchronized int release();
```

#### GetVersion <span id="36cbf0689bzon" />

获得当前SDK版本信息。

<strong>方法签名</strong>

```java
public synchronized String GetVersion();
```

<strong>返回值说明</strong>

当前SDK版本信息。

### INativeFileTransCallback：监听回调 <span id="8b030fec74e01" />

#### onFileTransEventCallback：监听事件和语音识别结果 <span id="163c1ef871tqt" />

<strong>方法签名</strong>

```java
void onFileTransEventCallback(NuiEvent event, final int resultCode, final int arg2, AsrResult asrResult, String taskId);
```

<strong>参数说明</strong>

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "25.88%" }} />

    <col style={{ width: "25.7%" }} />

    <col style={{ width: "48.42%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>参数</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>类型</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>说明</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `event`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `NuiEvent`
      </td>

      <td style={{ verticalAlign: "top" }}>
        回调事件。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `resultCode`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `int`
      </td>

      <td style={{ verticalAlign: "top" }}>
        仅在出现 <code>EVENT\_ASR\_ERROR</code> 事件时有效。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `asrResult`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `AsrResult`
      </td>

      <td style={{ verticalAlign: "top" }}>
        语音识别结果。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `taskId`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `String`
      </td>

      <td style={{ verticalAlign: "top" }}>
        任务ID。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `arg2`
      </td>

      <td style={{ verticalAlign: "top" }}>
        `int`
      </td>

      <td style={{ verticalAlign: "top" }}>
        保留参数。
      </td>
    </tr>
  </tbody>
</table>

#### onFileTransLogTrackCallback：监听追踪日志 <span id="9c10968457gc6" />

此回调用于接收 SDK 内部的详细日志，方便进行问题定位和调试。

```java
default void onFileTransLogTrackCallback(Constants.LogLevel level, String log)
```

### `NuiEvent`：事件类型 <span id="981ff433acpmr" />

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "36.43%" }} />

    <col style={{ width: "63.57%" }} />
  </colgroup>

  <thead>
    <tr>
      <th style={{ verticalAlign: "top" }}>
        <strong>事件</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>说明</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        `EVENT_FILE_TRANS_CONNECTED`
      </td>

      <td style={{ verticalAlign: "top" }}>
        连接服务成功。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `EVENT_FILE_TRANS_UPLOADED`
      </td>

      <td style={{ verticalAlign: "top" }}>
        上传待识别音频文件成功，此时可获得当前任务的 <code>task\_id</code>。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `EVENT_FILE_TRANS_QUERY_RESULT`
      </td>

      <td style={{ verticalAlign: "top" }}>
        查询任务结果。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `EVENT_FILE_TRANS_RESULT`
      </td>

      <td style={{ verticalAlign: "top" }}>
        识别最终结果。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        `EVENT_ASR_ERROR`
      </td>

      <td style={{ verticalAlign: "top" }}>
        语音识别过程中出现错误。
      </td>
    </tr>
  </tbody>
</table>
