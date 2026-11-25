> ## Documentation Index
> Fetch the complete documentation index at: https://docs.bailian.console.aliyun.com/llms.txt
> Use this file to discover all available pages before exploring further.

# 非实时语音合成CosyVoice Python SDK参考

> 本文介绍非实时语音合成CosyVoice的Python SDK调用方法，支持非流式和流式两种调用模式。

<meta property="og:title" content="非实时语音合成CosyVoice Python SDK参考" />

<Tip>
  本文描述的功能仅在华北2（北京）地域可用。
</Tip>

<Tip>
  阿里云百炼为华北2（北京）地域推出了业务空间专属域名，能够为推理请求提供卓越的性能和更高的稳定性，建议从 `dashscope.aliyuncs.com` 迁移至 `{WorkspaceId}.cn-beijing.maas.aliyuncs.com`。
</Tip>

## 前提条件 <span id="h2-cp1a2b3d" />

- 已[获取与配置 API Key](/zh/model-studio/get-api-key)并将其配置到环境变量
- 已安装符合版本要求的DashScope Python SDK，建议[安装最新版](/zh/model-studio/install-sdk)，SDK版本需≥2.0.1

## 核心资源（`client.audio.tts.http`） <span id="h2-cp2a3b4d" />

<strong>资源路径</strong>：`client.audio.tts.http`

<strong>功能</strong>：基于HTTP的语音合成，`synthesize`方法为非流式调用（音频以URL返回），`stream`方法为流式调用（SSE逐片返回音频数据）。

### synthesize() / stream() - 语音合成调用 <span id="h3-cp2a3b5b" />

<strong>方法签名</strong>：

```python expandable
def synthesize(self,
               text: str,
               *,
               model: str,
               voice: str,
               format: str = None,
               sample_rate: int = None,
               language_type: str = None,
               parameters: Dict = None,
               extra_input: Dict = None,
               timeout: float = None,
               ...) -> HttpSynthesis

def stream(self,
           text: str,
           *,
           model: str,
           voice: str,
           format: str = None,
           sample_rate: int = None,
           language_type: str = None,
           parameters: Dict = None,
           extra_input: Dict = None,
           timeout: float = None,
           ...) -> HttpSynthesisStream
```

<strong>参数说明</strong>（`model`、`text`、`voice`、`format`、`sample_rate`为具名参数，其余输入参数通过`extra_input`字典传入）：

<table style={{ display: "table", tableLayout: "fixed", width: "100%", overflowWrap: "anywhere" }}>
  <colgroup>
    <col style={{ width: "18%" }} />

    <col style={{ width: "12%" }} />

    <col style={{ width: "8%" }} />

    <col style={{ width: "62%" }} />
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
        <strong>必填</strong>
      </th>

      <th style={{ verticalAlign: "top" }}>
        <strong>说明</strong>
      </th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>model</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>str</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>是</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>语音合成模型。</span>

        取值范围：

        - cosyvoice-v3.5-plus
        - cosyvoice-v3.5-flash
        - cosyvoice-v3-plus
        - cosyvoice-v3-flash
        - cosyvoice-v2
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>text</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>str</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>是</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>待合成文本。</span>

        支持 SSML 和 LaTeX 格式输入。将待合成文本替换为对应格式即可。

        - 使用 SSML 时，需同时将 `enable_ssml` 设置为 `True`。支持的 SSML 标签及用法，请参见[SSML 与 LaTeX](/zh/model-studio/ssml-latex-user-guide)。
        - 使用 LaTeX 时，将待合成文本替换为 LaTeX 格式即可，无需额外配置。支持的 LaTeX 语法及用法，请参见[LaTeX 公式转语音](/zh/model-studio/latex-capability-support-description)。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>voice</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>str</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>是</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>音色。</span>

        取值范围：

        - 系统音色：参见[CosyVoice音色列表](/zh/model-studio/cosyvoice-voice-list)
        - 声音复刻音色：如何创建音色请参见<Link href="/zh/model-studio/voice-clone-design-http-api">声音复刻HTTP API参考</Link>
        - 声音设计音色：如何创建音色请参见<Link href="/zh/model-studio/voice-design-api-references">声音设计API参考</Link>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>format</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>str</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>音频编码格式。</span>

        默认值：mp3。

        取值范围：

        - mp3
        - pcm
        - wav
        - opus
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>sample\_rate</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>int</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>音频采样率（Hz）。</span>

        取值范围：8000, 16000, 22050（默认）, 24000, 44100, 48000。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>volume</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>int</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>音量。</span>

        默认值：50。

        取值范围：\[0, 100]。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>rate</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>float</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>语速。</span>

        默认值：1.0。

        取值范围：\[0.5, 2.0]。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>pitch</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>float</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>音调。</span>

        默认值：1.0。

        取值范围：\[0.5, 2.0]。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>bit\_rate</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>int</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>音频码率（单位：kbps）。</span>

        默认值：32。

        取值范围：\[6, 510]。

        <Tip>
          仅在`format`为`opus`时支持使用该参数。
        </Tip>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>enable\_ssml</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>bool</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>是否开启SSML功能。当<code>text</code>使用SSML格式时，需设为<code>True</code>。默认为<code>False</code>。支持的SSML标签及用法，请参考<Link href="/zh/model-studio/ssml-latex-user-guide">SSML 与 LaTeX</Link>。SSML 的使用限制（支持的模型、音色和接口），请参见<Link href="/zh/model-studio/ssml-latex-user-guide#sl01_constraint_h3">使用限制</Link>。</span>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>word\_timestamp\_enabled</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>bool</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>是否开启字级别时间戳。</span>

        默认值：False。

        - True：开启。
        - False：关闭。

        仅在流式输出模式下可用。支持的音色范围：cosyvoice-v3.5-plus、cosyvoice-v3.5-flash、cosyvoice-v3-flash、cosyvoice-v3-plus、cosyvoice-v2模型的复刻音色，以及[CosyVoice音色列表](/zh/model-studio/cosyvoice-voice-list)中标记为支持的系统音色。其他模型的复刻音色不支持此功能。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>seed</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>int</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>生成时使用的随机数种子，使合成的效果产生变化。在模型版本、文本、音色及其他参数均相同的前提下，使用相同的seed可复现相同的合成结果。</span>

        默认值0。

        取值范围：\[0, 65535]。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>language\_hints</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>list</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <Tip>
          - 此参数为数组，但当前版本仅处理第一个元素，因此建议只传入一个值。
          - 此参数用于指定语音合成的目标语言，该设置与声音复刻时的样本音频的语种无关。如需设置复刻任务的源语言，请参见<Link href="/zh/model-studio/voice-clone-design-http-api#h2-create">声音复刻API参考</Link>。
        </Tip>

        指定语音合成的目标语言，提升合成效果。

        当数字、缩写、符号等朗读方式或者小语种合成效果不符合预期时使用，例如：

        - 数字朗读方式不符合预期，“hello, this is 110”读成“hello, this is one one zero”而非“hello, this is 幺幺零”
        - 符号朗读不准确，“@”读成“艾特”而非“at”
        - 小语种合成效果差，合成不自然

        <Accordion title="取值范围">
          * zh：中文
          * en：英语
          * fr：法语
          * de：德语
          * ja：日语
          * ko：韩语
          * ru：俄语
          * pt：葡萄牙语
          * th：泰语
          * id：印尼语
          * vi：越南语
          * es：西班牙语
          * it：意大利语
          * ms：马来西亚语
          * fil：菲律宾语
          * ar：阿拉伯语
        </Accordion>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>instruction</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>str</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>设置指令，用于控制方言、情感或角色等合成效果。</span>

        具体用法请参见[非实时语音合成](/zh/model-studio/non-realtime-tts-user-guide)。
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>enable\_aigc\_tag</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>bool</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>是否在生成的音频中添加AIGC隐性标识。设置为True时，会将隐性标识嵌入到支持格式（wav/mp3/opus）的音频中。</span>

        默认值：false。

        <Note>
          仅cosyvoice-v3-flash、cosyvoice-v3-plus、cosyvoice-v2支持该功能。
        </Note>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>aigc\_propagator</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>str</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>设置AIGC隐性标识中的 <code>ContentPropagator</code> 字段，用于标识内容的传播者。仅在 <code>enable\_aigc\_tag</code> 为 <code>True</code> 时生效。</span>

        默认值：阿里云UID。

        <Note>
          仅cosyvoice-v3-flash、cosyvoice-v3-plus、cosyvoice-v2支持该功能。
        </Note>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>aigc\_propagate\_id</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>str</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>设置AIGC隐性标识中的 <code>PropagateID</code> 字段，用于唯一标识一次具体的传播行为。仅在 <code>enable\_aigc\_tag</code> 为 <code>True</code> 时生效。</span>

        默认值：本次语音合成请求Request ID。

        <Note>
          仅cosyvoice-v3-flash、cosyvoice-v3-plus、cosyvoice-v2支持该功能。
        </Note>
      </td>
    </tr>

    <tr>
      <td style={{ verticalAlign: "top" }}>
        <code>enable\_markdown\_filter</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <code>bool</code>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>否</span>
      </td>

      <td style={{ verticalAlign: "top" }}>
        <span>是否启用 Markdown 过滤。启用后，服务会在合成前将 Markdown 转换成适合朗读的文本。</span>

        <Note>
          cosyvoice-v2 不支持此功能。
        </Note>

        默认值：False。

        取值范围：

        - True：启用Markdown过滤
        - False：禁用Markdown过滤
      </td>
    </tr>
  </tbody>
</table>

<strong>返回值</strong>：

- <strong>非流式模式</strong>（`synthesize`）：返回结果对象（`HttpSynthesis`），包含以下属性：

  - `audio_url`：音频下载URL（有效期有限）。
  - `audio_id`：音频ID。
  - `expires_at`：URL过期时间。
  - `usage`：用量信息。
- <strong>流式模式</strong>（`stream`）：返回迭代器（`HttpSynthesisStream`），每个元素（`HttpSynthesisChunk`）包含以下属性：

  - `audio`：当前分片的音频二进制数据（bytes，已解码）。
  - `sentence`：句子级别的合成信息（如有）。
  - `is_final`：是否为收尾分片（`finish_reason == "stop"`）。
  - `usage`：用量信息（收尾分片携带）。

  <Tip>
    流式模式下，收尾分片（`is_final`为`True`）会额外携带`audio_url`（完整音频的下载地址）、`audio_id`与`expires_at`。遍历迭代器拼接音频时，以`chunk.audio`是否非空决定是否写入，避免同一段内容重复播放。
  </Tip>

## 示例代码 <span id="h2-cp4a5b6b" />

以下示例展示CosyVoice语音合成的非流式和流式调用方式。运行前请确保已设置环境变量`DASHSCOPE_API_KEY`。

<Note>
  不同模型版本需使用对应版本的音色。例如`cosyvoice-v3-flash`和`cosyvoice-v3-plus`使用`longanhuan`等音色，`cosyvoice-v2`使用`longxiaochun_v2`等音色。更换模型时请同步更换为对应版本的音色。此外，每个音色支持的语言不同，合成非中文语言时，需选择支持对应语言的音色。具体的模型与音色对应关系，请参见[CosyVoice音色列表](/zh/model-studio/cosyvoice-voice-list)。
</Note>

<Tabs>
  <Tab title="非流式调用">
    <span id="h3-cp4a5b7b" />

    非流式调用使用`synthesize`方法，等待合成完成后返回音频URL，通过URL下载音频文件。

    ```python expandable
    # -*- coding: utf-8 -*-
    import os
    import dashscope

    # 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
    client = dashscope.Client(
        # 若没有将API Key配置到环境变量中，请将下行替换为自己的API Key：api_key="sk-xxx"
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        workspace="{WorkspaceId}",
        region="cn-beijing",
    )

    # 非流式调用，返回音频URL
    result = client.audio.tts.http.synthesize(
        "今天是个好日子，适合构建人们喜爱的产品！",
        model="cosyvoice-v3-plus",  # 更换模型时，需同步更换为对应版本的音色
        voice="longanyang",
        format="wav",
        sample_rate=24000,
    )

    # 获取音频URL
    print(f"音频 URL: {result.audio_url}")
    print(f"音频 ID: {result.audio_id}")
    if result.audio_id:
        request_id = result.audio_id.removeprefix("audio_")
        print(f"请求 Id: {request_id}")
    print(f"过期时间: {result.expires_at}")
    ```
  </Tab>

  <Tab title="流式调用">
    <span id="h3-cp4a5b8b" />

    流式调用使用`stream`方法，返回迭代器，逐段获取音频数据。适用于对首包延迟有要求的实时播放场景。流式模式下，收尾分片（`chunk.is_final`为`True`）会额外返回完整音频的`audio_url`，拼接时只写入`chunk.audio`非空的数据分片即可，避免音频内容重复。

    ```python expandable
    # -*- coding: utf-8 -*-
    import os
    import dashscope

    # 以下为华北2（北京）地域的配置，调用时请将"{WorkspaceId}"替换为真实的业务空间ID，各地域的配置不同。
    client = dashscope.Client(
        # 若没有将API Key配置到环境变量中，请将下行替换为自己的API Key：api_key="sk-xxx"
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        workspace="{WorkspaceId}",
        region="cn-beijing",
    )

    # 流式调用，逐段返回音频数据
    stream_result = client.audio.tts.http.stream(
        "今天是个好日子，适合构建人们喜爱的产品！",
        model="cosyvoice-v3-plus",  # 更换模型时，需同步更换为对应版本的音色
        voice="longanyang",
        format="wav",
        sample_rate=24000,
    )

    # 遍历迭代器，逐段接收音频数据
    audio_chunks = []
    for chunk in stream_result:
        if chunk.audio:  # 只写入非空的音频分片，避免音频重复
            audio_chunks.append(chunk.audio)
            print(f"收到音频数据块，大小: {len(chunk.audio)} bytes")

        if chunk.sentence:
            print(f"句子信息: {chunk.sentence}")

        if chunk.is_final:  # 收尾分片：携带完整音频的下载地址与用量
            print(f"Audio ID: {chunk.audio_id}")
            request_id = chunk.audio_id.removeprefix("audio_")
            print(f"请求 Id: {request_id}")
            print(f"音频 URL: {chunk.audio_url}")
            print(f"用量: {chunk.usage}")

    # 合并所有音频数据并保存
    full_audio = b"".join(audio_chunks)
    print(f"总音频大小: {len(full_audio)} bytes")

    with open("output.wav", "wb") as f:
        f.write(full_audio)
    print("音频已保存到 output.wav")
    ```
  </Tab>
</Tabs>
